"""Tests for app.services.pse_cleanup — abandon PSE collection intent expiry.

Covers:
  - No abandoned intents -> returns 0
  - Expire old 'created' mono payments, cancel order, release qty
  - Skips non-mono payments
  - Skips recently created payments
  - Skips payments already in terminal state (paid/failed)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from app.models.order import Order, OrderStatus
from app.models.payment import Payment
from app.models.product import Product
from app.models.user import User
from app.services.pse_cleanup import expire_abandoned_intents, ABANDONED_MINUTES


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_old_payment(
    db: Session,
    order_id: int,
    consumer_id: int,
    method: str = "mono",
    status: str = "created",
    age_minutes: int = 0,
) -> Payment:
    """Create a Payment with a specific age."""
    created_at = datetime.now(timezone.utc) - timedelta(minutes=age_minutes)
    pmt = Payment(
        order_id=order_id,
        consumer_id=consumer_id,
        method=method,
        status=status,
        amount=100.0,
        currency="COP",
        reference=f"MD-{order_id}-{consumer_id}",
        created_at=created_at,
    )
    db.add(pmt)
    db.flush()
    return pmt


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPseCleanupNoAbandoned:
    def test_no_payments_returns_zero(
        self,
        db: Session,
    ) -> None:
        """No payments at all -> returns 0."""
        count = expire_abandoned_intents(db)
        assert count == 0

    def test_recent_payment_not_expired(
        self,
        db: Session,
        consumer: User,
    ) -> None:
        """A recently created mono payment is not expired."""
        order = Order(consumer_id=consumer.id, total=100.0, status=OrderStatus.PENDING)
        db.add(order)
        db.flush()

        _seed_old_payment(db, order.id, consumer.id, age_minutes=1)  # only 1 min old

        count = expire_abandoned_intents(db)
        assert count == 0

    def test_paid_payment_not_expired(
        self,
        db: Session,
        consumer: User,
    ) -> None:
        """A paid payment is not expired even if old."""
        order = Order(consumer_id=consumer.id, total=100.0, status=OrderStatus.PAID)
        db.add(order)
        db.flush()

        _seed_old_payment(db, order.id, consumer.id, status="paid",
                          age_minutes=ABANDONED_MINUTES + 10)

        count = expire_abandoned_intents(db)
        assert count == 0


class TestPseCleanupAbandoned:
    def test_expires_old_mono_payment(
        self,
        db: Session,
        consumer: User,
        sample_product: Product,
    ) -> None:
        """Old mono 'created' payment is expired, order cancelled, qty released."""
        order = Order(consumer_id=consumer.id, total=100.0, status=OrderStatus.PENDING)
        db.add(order)
        db.flush()

        # Add an order item to test quantity release
        from app.models.order import OrderItem

        item = OrderItem(order_id=order.id, product_id=sample_product.id, qty=5, price=20.0)
        db.add(item)

        # Reserve qty (simulating checkout)
        sample_product.quantity_reserved = 5
        sample_product.quantity_available = 95
        db.flush()

        pmt = _seed_old_payment(
            db, order.id, consumer.id, method="mono",
            age_minutes=ABANDONED_MINUTES + 5,
        )

        count = expire_abandoned_intents(db)
        assert count == 1

        db.expire_all()
        payment = db.get(Payment, pmt.id)
        assert payment is not None
        assert payment.status == "failed"

        ord_obj = db.get(Order, order.id)
        assert ord_obj is not None
        assert ord_obj.status == OrderStatus.CANCELLED

        prod = db.get(Product, sample_product.id)
        assert prod is not None
        assert prod.quantity_reserved == 0  # released

    def test_does_not_expire_non_mono_payment(
        self,
        db: Session,
        consumer: User,
    ) -> None:
        """Old stablecoin 'created' payment is NOT expired."""
        order = Order(consumer_id=consumer.id, total=100.0, status=OrderStatus.PENDING)
        db.add(order)
        db.flush()

        _seed_old_payment(
            db, order.id, consumer.id, method="stablecoin",
            age_minutes=ABANDONED_MINUTES + 10,
        )

        count = expire_abandoned_intents(db)
        assert count == 0  # stablecoin not touched

    def test_cancels_only_pending_orders(
        self,
        db: Session,
        consumer: User,
    ) -> None:
        """If the order is already paid, only the payment is failed."""
        order = Order(consumer_id=consumer.id, total=100.0, status=OrderStatus.PAID)
        db.add(order)
        db.flush()

        _seed_old_payment(
            db, order.id, consumer.id, method="mono",
            age_minutes=ABANDONED_MINUTES + 10,
        )

        count = expire_abandoned_intents(db)
        assert count == 1

        db.expire_all()
        ord_obj = db.get(Order, order.id)
        assert ord_obj is not None
        assert ord_obj.status == OrderStatus.PAID  # unchanged
