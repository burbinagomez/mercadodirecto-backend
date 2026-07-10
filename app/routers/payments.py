"""Payments endpoints — bridge internal orders to VelaFi.

Flow:
  POST /payments/checkout  -> takes an internal order id, calls VelaFi
                              (fiat_to_fiat order OR stablecoin payment link),
                              stores a Payment row, returns the link / orderId.
  POST /payments/webhook   -> VelaFi pushes order status; verify RSA-SHA256
                              signature, then update Payment + Order status.
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
from app.services import velafi as velafi_svc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/payments", tags=["payments"])

# Terminal payment statuses — once reached, a Payment will not be updated again
# by a retried webhook event.
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


@router.post("/webhook")
async def webhook(request: Request, db: Session = Depends(get_db)):
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

    event = {}
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
    elif status_code in ("70", "71", "72"):
        internal_status = OrderStatus.CANCELLED
    else:
        internal_status = OrderStatus.PROCESSING

    payment = db.query(Payment).filter(Payment.velafi_order_id == velafi_order_id).first()
    if not payment:
        return {"callbackStatus": "SUCCESS"}

    # Idempotency guard: skip if Payment is already in a terminal state.
    if payment.status in _TERMINAL_PAYMENT_STATUSES:
        logger.info(
            "VelaFi webhook: skipping event for terminal payment "
            "payment_id=%s velafi_order_id=%s current_status=%s incoming_status=%s",
            payment.id,
            velafi_order_id,
            payment.status,
            pay_status,
        )
        return {"callbackStatus": "SUCCESS"}

    prev_pay_status = payment.status
    order = db.get(Order, payment.order_id)
    if not order:
        return {"callbackStatus": "FAIL"}

    order_was_paid = order.status == OrderStatus.PAID
    order_was_cancelled = order.status == OrderStatus.CANCELLED

    # ---- Valid state transitions ----
    # Guards prevent duplicate / stale / out-of-order webhooks from
    # corrupting inventory.  Only accept transitions that respect the
    # order lifecycle (PENDING -> PAID, PENDING/PAID -> CANCELLED).
    if (
        internal_status == OrderStatus.PAID
        and not order_was_paid
        and not order_was_cancelled
        and prev_pay_status != "paid"
    ):
        # PENDING -> PAID: commit reservation (take from available, clear reserved)
        order.status = OrderStatus.PAID
        payment.status = "paid"
        for oi in order.items:
            product = db.get(Product, oi.product_id)
            if product:
                product.quantity_available -= oi.qty
                product.quantity_reserved -= oi.qty

    elif (
        internal_status == OrderStatus.CANCELLED
        and not order_was_cancelled
        and prev_pay_status != "failed"
    ):
        # PENDING -> CANCELLED: release reservation back to available
        # PAID -> CANCELLED (refund): restore available inventory
        order.status = OrderStatus.CANCELLED
        payment.status = "failed"
        for oi in order.items:
            product = db.get(Product, oi.product_id)
            if product:
                if order_was_paid:
                    product.quantity_available += oi.qty
                else:
                    product.quantity_reserved -= oi.qty

    elif internal_status == OrderStatus.PROCESSING:
        # Intermediate processing status — no inventory side effect.
        order.status = OrderStatus.PROCESSING
        payment.status = "created"

