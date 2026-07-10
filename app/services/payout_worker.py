"""Retry worker for pending/failed farmer payouts.

Called periodically (cron) to re-attempt ``create_transfer`` calls to Mono
for :class:`app.models.payout.FarmerPayout` records that are stuck in
``pending`` or ``failed`` status.

Idempotency
-----------
The ``idempotency_key`` for a payout retry is ``payout-{order_id}-{farmer_id}`` —
the same key used in the initial ``create_transfer`` call.  Mono's API treats
a retry with an idempotency key that already succeeded as a no-op, so replay
is safe.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.payout import FarmerBankAccount, FarmerPayout
from app.services.mono import MonoClient

logger = logging.getLogger(__name__)

# Max retry attempts before we stop retrying automatically.
MAX_RETRIES = 5


def retry_pending_payouts(db: Session) -> int:
    """Retry all FarmerPayout records with status ``pending`` or ``failed``.

    Parameters
    ----------
    db : Session
        SQLAlchemy session (must already bound to an active transaction).

    Returns
    -------
    int
        Number of payouts processed (regardless of outcome).
    """
    stmt = select(FarmerPayout).where(
        FarmerPayout.status.in_(["pending", "failed"]),
        FarmerPayout.retry_count < MAX_RETRIES,
    )
    payouts = list(db.execute(stmt).scalars().all())

    if not payouts:
        logger.info("No pending/failed payouts to retry.")
        return 0

    client = MonoClient()
    processed = 0

    for payout in payouts:
        _retry_one(db, client, payout)
        processed += 1

    db.commit()
    client.close()

    logger.info("Retried %d payout(s).", processed)
    return processed


def _retry_one(db: Session, client: MonoClient, payout: FarmerPayout) -> None:
    """Attempt a single payout retry and update its status."""
    idempotency_key = f"payout-{payout.order_id}-{payout.farmer_id}"

    # Determine destination from the farmer bank account, if available.
    dest_account = _resolve_dest_account(db, payout)

    if not dest_account:
        logger.warning(
            "Payout %d (order %d): no bank account on file, marking failed.",
            payout.id,
            payout.order_id,
        )
        payout.status = "failed"
        payout.error_message = "No bank account configured for farmer."
        payout.retry_count += 1
        payout.updated_at = datetime.now(timezone.utc)
        return

    try:
        resp = client.create_transfer(
            dest_account=dest_account,
            amount=round(payout.amount, 2),
            idempotency_key=idempotency_key,
            routing={"bank_account_id": str(payout.farmer_bank_account_id)},
        )
    except Exception as exc:
        logger.error(
            "Payout %d (order %d): create_transfer failed: %s",
            payout.id,
            payout.order_id,
            exc,
        )
        payout.status = "failed"
        payout.error_message = str(exc)[:500]
        payout.retry_count += 1
        payout.updated_at = datetime.now(timezone.utc)
        return

    transfer_id = resp.get("transfer_id", resp.get("id", ""))
    payout.mono_transfer_id = transfer_id or payout.mono_transfer_id
    payout.status = "paid"
    payout.error_message = None
    payout.retry_count += 1
    payout.updated_at = datetime.now(timezone.utc)

    logger.info(
        "Payout %d (order %d): paid (transfer_id=%s).",
        payout.id,
        payout.order_id,
        transfer_id,
    )


def _resolve_dest_account(db: Session, payout: FarmerPayout) -> str | None:
    """Return the destination account identifier for *payout*.

    Uses ``farmer_bank_account_id`` when set; otherwise queries the first
    verified account for the farmer.
    """
    if payout.farmer_bank_account_id:
        acct = db.get(FarmerBankAccount, payout.farmer_bank_account_id)
        if acct:
            return acct.account_number

    # Fallback: first verified account (rare — every payout should have one).
    acct = (
        db.execute(
            select(FarmerBankAccount).where(
                FarmerBankAccount.user_id == payout.farmer_id,
                FarmerBankAccount.verified.is_(True),
            )
        )
        .scalars()
        .first()
    )
    return acct.account_number if acct else None
