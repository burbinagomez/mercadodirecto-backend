"""Cleanup worker for abandoned Mono PSE collection intents.

PSE (PSE) collection intents that were created (status ``created``) but never
completed by the consumer are periodically expired — the Payment is set to
``failed`` so stale rows don't accumulate.

The abandonment threshold is configurable via ``ABANDONED_MINUTES``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.order import Order, OrderStatus
from app.models.payment import Payment
from app.models.product import Product

logger = logging.getLogger(__name__)

# Payments older than this many minutes with status='created' are abandoned.
ABANDONED_MINUTES = 30


def expire_abandoned_intents(db: Session) -> int:
    """Mark ``mono`` Payments stuck in ``created`` as ``failed``.

    For each abandoned payment the associated Order is also cancelled
    (inventory reservation released).

    Parameters
    ----------
    db : Session
        SQLAlchemy session (must be bound to an active transaction).

    Returns
    -------
    int
        Number of payments expired.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=ABANDONED_MINUTES)

    stmt = (
        select(Payment)
        .where(
            Payment.method == "mono",
            Payment.status == "created",
            Payment.created_at < cutoff,
        )
        .order_by(Payment.created_at.asc())
    )
    payments = list(db.execute(stmt).scalars().all())

    if not payments:
        logger.info("No abandoned PSE intents to expire.")
        return 0

    for pmt in payments:
        logger.info(
            "Expiring abandoned PSE intent payment_id=%s order_id=%s created_at=%s",
            pmt.id,
            pmt.order_id,
            pmt.created_at,
        )
        pmt.status = "failed"

        # Cancel the associated order (releases inventory reservations).
        order = db.get(Order, pmt.order_id)
        if order and order.status == OrderStatus.PENDING:
            order.status = OrderStatus.CANCELLED
            # Release reserved quantity
            for oi in order.items:
                product = db.get(Product, oi.product_id)
                if product:
                    product.quantity_reserved -= oi.qty

    db.commit()
    logger.info("Expired %d abandoned PSE intent(s).", len(payments))
    return len(payments)
