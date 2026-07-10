"""Payments endpoints — dual provider dispatch (Mono / VelaFi stablecoin) + webhooks.

Flow:
  POST /payments/checkout   method=mono     -> Mono PSE collection intent, return redirectUrl
                            method=stablecoin -> VelaFi stablecoin payment link
  POST /payments/webhook          -> VelaFi order-status webhook (RSA-SHA256)
  POST /payments/webhook/mono     -> Mono Banking webhook (HMAC-SHA256)
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.models.order import Order, OrderItem, OrderStatus
from app.models.payment import Payment, PaymentMethod
from app.models.payout import FarmerBankAccount, FarmerPayout
from app.models.product import Product
from app.routers.auth import get_current_user
from app.services.base_client import PaymentProviderError
from app.services.providers import get_provider

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/payments", tags=["payments"])

# Terminal payment statuses — once reached, a Payment will not be updated again
# by a retried webhook event.
_TERMINAL_PAYMENT_STATUSES = frozenset({"paid", "failed", "refunded"})


class CheckoutBody(BaseModel):
    order_id: int
    method: PaymentMethod = PaymentMethod.MONO
    # stablecoin fields
    wallet_id: int | None = None
    currency: str = "USDT"
    # mono (PSE) fields
    redirect_url: str | None = None  # where Mono redirects the user after bank auth


# ---------------------------------------------------------------------------
# POST /payments/checkout
# ---------------------------------------------------------------------------


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

    reference = f"MD-{order.id}-{current.id}"
    client = get_provider(body.method.value)

    try:
        if body.method == PaymentMethod.MONO:
            intent_data = client.create_collection_intent(
                amount=round(order.total, 2),
                reference=reference,
                idempotency_key=f"collection-{order.id}",
            )
            payment = Payment(
                order_id=order.id,
                consumer_id=current.id,
                method=PaymentMethod.MONO.value,
                status="created",
                amount=order.total,
                currency="COP",
                reference=reference,
                mono_intent_id=intent_data.get("intent_id"),
            )
            db.add(payment)
            db.commit()
            return {
                "method": PaymentMethod.MONO.value,
                "redirectUrl": intent_data.get("redirect_url"),
                "reference": reference,
            }

        elif body.method == PaymentMethod.STABLECOIN:
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
                method=PaymentMethod.STABLECOIN.value,
                status="created",
                amount=order.total,
                currency=body.currency,
                reference=reference,
                velafi_payment_link=link_data.get("paymentLink"),
            )
            db.add(payment)
            db.commit()
            return {
                "method": PaymentMethod.STABLECOIN.value,
                "paymentLink": link_data.get("paymentLink"),
                "reference": reference,
            }

    except PaymentProviderError as e:
        raise HTTPException(status_code=502, detail=str(e))


# ---------------------------------------------------------------------------
# POST /payments/webhook  (VelaFi)
# ---------------------------------------------------------------------------


@router.post("/webhook")
async def webhook(request: Request, db: Session = Depends(get_db)):
    raw = await request.body()
    signature = request.headers.get("signature", "")
    from app.services.velafi import VelaFiClient

    if not VelaFiClient.verify_webhook(raw, signature):
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
    return _process_velafi_webhook(velafi_order_id, status_code, db)


def _process_velafi_webhook(
    velafi_order_id: str,
    status_code: str,
    db: Session,
) -> dict[str, str]:
    """VelaFi webhook logic — map status and apply transition."""
    if status_code in ("50", "60"):
        internal_status = OrderStatus.PAID
    elif status_code in ("70", "71", "72"):
        internal_status = OrderStatus.CANCELLED
    else:
        internal_status = OrderStatus.PROCESSING

    payment = db.query(Payment).filter(Payment.velafi_order_id == velafi_order_id).first()
    if not payment:
        return {"callbackStatus": "SUCCESS"}

    return _apply_status_transition(payment, internal_status, db)


# ---------------------------------------------------------------------------
# POST /payments/webhook/mono  (Mono Banking — HMAC-SHA256)
# ---------------------------------------------------------------------------


@router.post("/webhook/mono")
async def mono_webhook(request: Request, db: Session = Depends(get_db)):
    """Mono Banking webhook handler.

    Processes ``collection_intent_credited`` (payin) and
    ``bank_transfer_approved`` / ``bank_transfer_declined`` (payout) events.
    """
    raw = await request.body()
    signature = request.headers.get("x-mono-signature", "")
    mono_client = get_provider("mono")
    if not mono_client.verify_webhook(raw, signature):
        logger.warning("Mono webhook: bad HMAC signature")
        return {"callbackStatus": "FAIL"}

    try:
        event = await request.json()
    except Exception:
        logger.warning("Mono webhook: invalid JSON body")
        return {"callbackStatus": "FAIL"}

    event_type = event.get("event", "")
    data = event.get("data", {})

    if event_type == "collection_intent_credited":
        return _handle_mono_collection_credited(data, db)
    elif event_type == "bank_transfer_approved":
        return _handle_mono_transfer_status(data, "paid", db)
    elif event_type == "bank_transfer_declined":
        return _handle_mono_transfer_status(data, "failed", db)
    else:
        logger.info("Mono webhook: unhandled event_type=%s", event_type)
        return {"callbackStatus": "SUCCESS"}


def _handle_mono_collection_credited(data: dict[str, Any], db: Session) -> dict[str, str]:
    """Handle ``collection_intent_credited`` — pay the order and trigger farmer payouts."""
    intent_id = data.get("id", "")
    if not intent_id:
        logger.warning("Mono webhook: collection_intent_credited missing data.id")
        return {"callbackStatus": "FAIL"}

    payment = db.query(Payment).filter(Payment.mono_intent_id == intent_id).first()
    if not payment:
        # Fallback: try matching by reference
        ref = data.get("reference", "")
        if ref:
            payment = db.query(Payment).filter(Payment.reference == ref).first()
    if not payment:
        logger.info("Mono webhook: no Payment found for intent_id=%s", intent_id)
        return {"callbackStatus": "SUCCESS"}

    # Idempotency guard
    if payment.status in _TERMINAL_PAYMENT_STATUSES:
        logger.info(
            "Mono webhook: skipping terminal payment payment_id=%s status=%s",
            payment.id,
            payment.status,
        )
        return {"callbackStatus": "SUCCESS"}

    order = db.get(Order, payment.order_id)
    if not order:
        return {"callbackStatus": "FAIL"}

    # Mark Payment + Order as paid
    payment.status = "paid"
    order.status = OrderStatus.PAID

    # Commit reservation (take from available, clear reserved)
    for oi in order.items:
        product = db.get(Product, oi.product_id)
        if product:
            product.quantity_available -= oi.qty
            product.quantity_reserved -= oi.qty

    db.flush()

    # --- Create farmer payouts + trigger transfers ---
    _create_farmer_payouts(order, db)

    db.commit()
    logger.info("Mono webhook: order %s paid via intent %s", order.id, intent_id)
    return {"callbackStatus": "SUCCESS"}


def _create_farmer_payouts(order: Order, db: Session) -> None:
    """Create pending FarmerPayout rows and call Mono create_transfer for each farmer."""
    farmer_totals: dict[int, float] = {}
    for oi in order.items:
        product = db.get(Product, oi.product_id)
        if product:
            farmer_totals[product.farmer_id] = (
                farmer_totals.get(product.farmer_id, 0.0) + oi.price
            )

    mono_client = get_provider("mono")
    for farmer_id, amount in farmer_totals.items():
        bank_account = (
            db.query(FarmerBankAccount)
            .filter(FarmerBankAccount.user_id == farmer_id)
            .first()
        )
        idempotency_key = f"payout-{order.id}-{farmer_id}"

        if not bank_account:
            logger.warning(
                "Mono payout: no bank account for farmer %s order %s — skipping transfer",
                farmer_id,
                order.id,
            )
            payout = FarmerPayout(
                order_id=order.id,
                farmer_id=farmer_id,
                amount=amount,
                status="failed",
                reference=idempotency_key,
            )
            db.add(payout)
            continue

        try:
            transfer_result = mono_client.create_transfer(
                dest_account=bank_account.account_number,
                amount=amount,
                idempotency_key=idempotency_key,
                routing={
                    "bank_name": bank_account.bank_name,
                    "account_type": bank_account.account_type,
                },
            )
            transfer_id = transfer_result.get("transfer_id", transfer_result.get("id", ""))
            payout = FarmerPayout(
                order_id=order.id,
                farmer_id=farmer_id,
                amount=amount,
                farmer_bank_account_id=bank_account.id,
                mono_transfer_id=transfer_id,
                status="pending",
                reference=idempotency_key,
            )
        except PaymentProviderError as exc:
            logger.error(
                "Mono create_transfer failed for farmer %s order %s: %s",
                farmer_id,
                order.id,
                exc,
            )
            payout = FarmerPayout(
                order_id=order.id,
                farmer_id=farmer_id,
                amount=amount,
                farmer_bank_account_id=bank_account.id,
                status="failed",
                reference=idempotency_key,
            )

        db.add(payout)


def _handle_mono_transfer_status(
    data: dict[str, Any],
    new_status: str,
    db: Session,
) -> dict[str, str]:
    """Handle ``bank_transfer_approved`` / ``bank_transfer_declined``."""
    transfer_id = data.get("id", "")
    if not transfer_id:
        logger.warning("Mono webhook: transfer event missing data.id")
        return {"callbackStatus": "FAIL"}

    payout = (
        db.query(FarmerPayout)
        .filter(FarmerPayout.mono_transfer_id == transfer_id)
        .first()
    )
    if not payout:
        # Fallback: reference
        ref = data.get("reference", "")
        if ref and ref.startswith("payout-"):
            parts = ref.split("-")
            if len(parts) >= 3:
                order_id = int(parts[1])
                farmer_id = int(parts[2])
                payout = (
                    db.query(FarmerPayout)
                    .filter(
                        FarmerPayout.order_id == order_id,
                        FarmerPayout.farmer_id == farmer_id,
                    )
                    .first()
                )
    if not payout:
        logger.info("Mono webhook: no FarmerPayout found for transfer_id=%s", transfer_id)
        return {"callbackStatus": "SUCCESS"}

    # Idempotency guard for terminal states
    if payout.status in ("paid", "failed"):
        logger.info(
            "Mono webhook: skipping terminal payout payout_id=%s status=%s",
            payout.id,
            payout.status,
        )
        return {"callbackStatus": "SUCCESS"}

    payout.status = new_status
    db.flush()

    # If all farmers' payouts are done for this order, mark order as completed
    if new_status == "paid":
        _check_order_completion(payout.order_id, db)

    db.commit()
    logger.info(
        "Mono webhook: payout %s → %s for transfer %s",
        payout.id,
        new_status,
        transfer_id,
    )
    return {"callbackStatus": "SUCCESS"}


def _check_order_completion(order_id: int, db: Session) -> None:
    """If every farmer payout for *order_id* is terminal, mark order as completed."""
    pending_count = (
        db.query(FarmerPayout)
        .filter(
            FarmerPayout.order_id == order_id,
            FarmerPayout.status == "pending",
        )
        .count()
    )
    if pending_count == 0:
        order = db.get(Order, order_id)
        if order and order.status == OrderStatus.PAID:
            order.status = OrderStatus.PROCESSING  # "completed" in a future state machine


# ---------------------------------------------------------------------------
# Shared status transition logic (used by both VelaFi and Mono webhooks)
# ---------------------------------------------------------------------------


def _apply_status_transition(
    payment: Payment,
    internal_status: str,
    db: Session,
) -> dict[str, str]:
    """Apply a valid status transition or return FAIL.

    Shared between VelaFi and Mono webhook handlers.
    """
    # Idempotency guard: skip if Payment is already in a terminal state.
    if payment.status in _TERMINAL_PAYMENT_STATUSES:
        logger.info(
            "Webhook: skipping event for terminal payment "
            "payment_id=%s current_status=%s incoming_status=%s",
            payment.id,
            payment.status,
            internal_status,
        )
        return {"callbackStatus": "SUCCESS"}

    prev_pay_status = payment.status
    order = db.get(Order, payment.order_id)
    if not order:
        return {"callbackStatus": "FAIL"}

    order_was_paid = order.status == OrderStatus.PAID
    order_was_cancelled = order.status == OrderStatus.CANCELLED

    # ---- Valid state transitions ----
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

    elif internal_status == OrderStatus.PROCESSING:
        order.status = OrderStatus.PROCESSING
        payment.status = "created"

    db.commit()
    return {"callbackStatus": "SUCCESS"}
