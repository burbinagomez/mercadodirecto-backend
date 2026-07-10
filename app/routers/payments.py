"""Payments endpoints — bridge internal orders to VelaFi or Mono.

Flow:
  POST /payments/checkout          -> takes an internal order id, calls VelaFi
                                      (stablecoin) or Mono (PSE collection),
                                      stores a Payment row, returns the
                                      redirect link / order id.
  POST /payments/webhook           -> VelaFi push (backward compat).
  POST /payments/webhook/velafi    -> VelaFi push (canonical).
  POST /payments/webhook/mono      -> Mono push (HMAC).
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.models.order import Order, OrderItem, OrderStatus
from app.models.payment import Payment
from app.models.product import Product
from app.routers.auth import get_current_user
from app.services.mono import MonoClient
from app.services import velafi as velafi_svc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/payments", tags=["payments"])

# Terminal payment statuses — once reached, a Payment is considered
# final.  The idempotency guard below (``payment.status == pay_status``)
# prevents duplicate events from re-processing while still allowing
# legitimate cross-status transitions (e.g. PAID -> CANCELLED refund).
_TERMINAL_PAYMENT_STATUSES = frozenset({"paid", "failed", "refunded"})


class CheckoutBody(BaseModel):
    order_id: int
    method: str = "fiat_to_fiat"  # fiat_to_fiat | stablecoin
    # fiat_to_fiat fields
    on_ramp_country: str = "Colombia"
    on_ramp_fiat: str = "COP"
    off_ramp_country: str = "Colombia"
    off_ramp_fiat: str = "COP"
    on_ramp_payment_id: int | None = None
    off_ramp_payment_id: int | None = None
    # stablecoin fields
    wallet_id: int | None = None
    currency: str = "USDT"


@router.post("/checkout")
def checkout(
    body: CheckoutBody,
    current=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if current.role not in ("consumer", "restaurant"):
        raise HTTPException(status_code=403, detail="Only consumers and restaurants can pay")
    order = db.get(Order, body.order_id)
    if not order or order.consumer_id != current.id:
        raise HTTPException(status_code=404, detail="Order not found")

    client = velafi_svc.VelaFiClient()
    reference = f"MD-{order.id}-{current.id}"
    try:
        if body.method == "stablecoin":
            if not body.wallet_id:
                raise HTTPException(status_code=400, detail="wallet_id required for stablecoin")
            link_data = client.create_payment_link(
                userId=current.id,
                merchantId=1,
                amount=round(order.total, 2),
                currency=body.currency,
                walletId=body.wallet_id,
                reference=reference,
                expireSeconds=1800,
            )
            payment = Payment(
                order_id=order.id,
                consumer_id=current.id,
                method="stablecoin",
                status="created",
                amount=order.total,
                currency=body.currency,
                reference=reference,
                velafi_payment_link=link_data.get("paymentLink"),
            )
            db.add(payment)
            db.commit()
            return {"method": "stablecoin", "paymentLink": link_data.get("paymentLink"), "reference": reference}
        else:
            order_id = client.create_fiat_to_fiat_order(
                clientId=reference,
                onRampCountry=body.on_ramp_country,
                onRampFiat=body.on_ramp_fiat,
                onRampFiatAmount=round(order.total, 2),
                onRampPaymentId=body.on_ramp_payment_id,
                offRampCountry=body.off_ramp_country,
                offRampFiat=body.off_ramp_fiat,
                offRampPaymentId=body.off_ramp_payment_id,
            )
            payment = Payment(
                order_id=order.id,
                consumer_id=current.id,
                method="fiat_to_fiat",
                status="created",
                amount=order.total,
                currency=body.on_ramp_fiat,
                reference=reference,
                velafi_order_id=str(order_id),
            )
            db.add(payment)
            db.commit()
            return {"method": "fiat_to_fiat", "velafiOrderId": order_id, "reference": reference}
    except velafi_svc.VelaFiError as e:
        raise HTTPException(status_code=502, detail=f"VelaFi: {e}")


# ---------------------------------------------------------------------------
# Shared state-transition logic for both VelaFi and Mono webhooks
# ---------------------------------------------------------------------------
def _apply_state_transition(
    payment: Payment,
    order: Order,
    internal_status: str,
    pay_status: str,
    db: Session,
) -> None:
    """Apply the valid state transition, mutating *payment* and *order*
    in-place.  Caller is responsible for ``db.commit()`` after this.

    Raises nothing — if no transition matches the current state, the
    function is a no-op (the webhook ack's SUCCESS either way).
    """
    prev_pay_status = payment.status
    order_was_paid = order.status == OrderStatus.PAID
    order_was_cancelled = order.status == OrderStatus.CANCELLED

    # PENDING -> PAID: commit reservation
    if (
        internal_status == OrderStatus.PAID
        and not order_was_paid
        and not order_was_cancelled
        and prev_pay_status != "paid"
    ):
        order.status = OrderStatus.PAID
        payment.status = "paid"
        for oi in order.items:
            product = db.get(Product, oi.product_id)
            if product:
                product.quantity_available -= oi.qty
                product.quantity_reserved -= oi.qty

    # PENDING/PAID -> CANCELLED: release reservation (or refund)
    elif (
        internal_status == OrderStatus.CANCELLED
        and not order_was_cancelled
        and prev_pay_status != "failed"
    ):
        order.status = OrderStatus.CANCELLED
        payment.status = "failed"
        for oi in order.items:
            product = db.get(Product, oi.product_id)
            if product:
                if order_was_paid:
                    product.quantity_available += oi.qty
                else:
                    product.quantity_reserved -= oi.qty

    # Intermediate processing status — no inventory side effect.
    elif internal_status == OrderStatus.PROCESSING:
        order.status = OrderStatus.PROCESSING
        payment.status = "created"


# ---------------------------------------------------------------------------
# VelaFi webhook handler (shared by /webhook and /webhook/velafi)
# ---------------------------------------------------------------------------
async def _handle_velafi_webhook(request: Request, db: Session) -> dict[str, str]:
    raw = await request.body()
    signature = request.headers.get("signature", "")
    if not velafi_svc.VelaFiClient.verify_webhook(raw, signature):
        client_host = request.client.host if request.client else None
        logger.warning(
            "VelaFi webhook: bad signature client_host=%s signature_prefix=%s",
            client_host,
            signature[:20] if len(signature) > 20 else signature,
        )
        return {"callbackStatus": "FAIL"}

    try:
        event = await request.json()
    except Exception:
        logger.warning("VelaFi webhook: invalid JSON body")
        return {"callbackStatus": "FAIL"}

    velafi_order_id = str(event.get("orderId", ""))
    status_code = str(event.get("orderStatus", ""))
    # Map VelaFi status -> internal
    if status_code in ("50", "60"):
        internal_status = OrderStatus.PAID
        pay_status = "paid"
    elif status_code in ("70", "71", "72"):
        internal_status = OrderStatus.CANCELLED
        pay_status = "failed"
    else:
        internal_status = OrderStatus.PROCESSING
        pay_status = "created"

    payment = db.query(Payment).filter(Payment.velafi_order_id == velafi_order_id).first()
    if not payment:
        return {"callbackStatus": "SUCCESS"}

    # Idempotency guard: skip if payment is already in the state this
    # event would set (duplicate retry).  Legitimate cross-status
    # transitions (e.g. PAID -> CANCELLED refund) are still allowed.
    if payment.status == pay_status:
        logger.info(
            "VelaFi webhook: skipping duplicate event for payment "
            "payment_id=%s velafi_order_id=%s current_status=%s",
            payment.id,
            velafi_order_id,
            payment.status,
        )
        return {"callbackStatus": "SUCCESS"}

    order = db.get(Order, payment.order_id)
    if not order:
        return {"callbackStatus": "FAIL"}

    _apply_state_transition(payment, order, internal_status, pay_status, db)
    db.commit()
    return {"callbackStatus": "SUCCESS"}


@router.post("/webhook")
async def webhook(request: Request, db: Session = Depends(get_db)):
    """VelaFi push (backward compat alias for /webhook/velafi)."""
    return await _handle_velafi_webhook(request, db)


@router.post("/webhook/velafi")
async def webhook_velafi(request: Request, db: Session = Depends(get_db)):
    """VelaFi push (canonical endpoint)."""
    return await _handle_velafi_webhook(request, db)


# ---------------------------------------------------------------------------
# Mono webhook handler
# ---------------------------------------------------------------------------
@router.post("/webhook/mono")
async def webhook_mono(request: Request, db: Session = Depends(get_db)):
    """Mono push — HMAC-verified webhook for PSE collection events.

    Event types handled:
      - ``collection_intent_credited`` -> Payment=paid, Order=paid
      - All other events are acknowledged as SUCCESS without mutation.
    """
    raw = await request.body()
    signature = request.headers.get("x-mono-signature", "")
    client = MonoClient()
    if not client.verify_webhook(raw, signature):
        logger.warning("Mono webhook: bad signature")
        return {"callbackStatus": "FAIL"}

    try:
        event = await request.json()
    except Exception:
        logger.warning("Mono webhook: invalid JSON body")
        return {"callbackStatus": "FAIL"}

    event_type = event.get("event", "")
    data = event.get("data", {})
    intent = data.get("intent", {})
    reference = intent.get("reference", "")

    # Map Mono event types to internal statuses
    if event_type == "collection_intent_credited":
        internal_status = OrderStatus.PAID
        pay_status = "paid"
    else:
        # Unknown event — ack silently (no-op)
        logger.info("Mono webhook: unhandled event_type=%s — acking as SUCCESS", event_type)
        return {"callbackStatus": "SUCCESS"}

    # Look up payment by reference (MD-{order}-{user})
    payment = db.query(Payment).filter(Payment.reference == reference).first()
    if not payment:
        return {"callbackStatus": "SUCCESS"}

    # Idempotency guard: skip if payment is already in the state this
    # event would set (duplicate retry).
    if payment.status == pay_status:
        logger.info(
            "Mono webhook: skipping duplicate event for payment "
            "payment_id=%s reference=%s current_status=%s",
            payment.id,
            reference,
            payment.status,
        )
        return {"callbackStatus": "SUCCESS"}

    order = db.get(Order, payment.order_id)
    if not order:
        return {"callbackStatus": "FAIL"}

    _apply_state_transition(payment, order, internal_status, pay_status, db)
    db.commit()
    return {"callbackStatus": "SUCCESS"}
