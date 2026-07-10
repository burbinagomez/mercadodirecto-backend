"""Tests for app.services.payout_worker — retry of pending/failed farmer payouts.

Covers:
  - retry_pending_payouts with no pending payouts -> 0
  - retry of pending payout succeeds, calls Mono create_transfer
  - retry of failed payout after create_transfer error
  - retry respects MAX_RETRIES (skips exhausted payouts)
  - retry without bank account -> failed with error_message
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy.orm import Session

from app.models.order import Order, OrderStatus
from app.models.payment import Payment
from app.models.payout import FarmerBankAccount, FarmerPayout
from app.models.product import Product
from app.models.user import User
from app.services.payout_worker import retry_pending_payouts


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_payment(db: Session, order_id: int, consumer_id: int) -> Payment:
    pmt = Payment(
        order_id=order_id,
        consumer_id=consumer_id,
        method="mono",
        status="paid",
        amount=100.0,
        currency="COP",
        mono_intent_id="int_payout_test",
        reference=f"MD-{order_id}-{consumer_id}",
    )
    db.add(pmt)
    db.flush()
    return pmt


def _seed_payout(
    db: Session,
    payment: Payment,
    order_id: int,
    farmer_id: int,
    status: str = "pending",
    retry_count: int = 0,
    farmer_bank_account_id: int | None = None,
) -> FarmerPayout:
    po = FarmerPayout(
        payment_id=payment.id,
        order_id=order_id,
        farmer_id=farmer_id,
        amount=100.0,
        farmer_bank_account_id=farmer_bank_account_id,
        status=status,
        retry_count=retry_count,
        reference=f"payout-{order_id}-{farmer_id}",
    )
    db.add(po)
    db.flush()
    return po


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPayoutRetryNoPending:
    def test_no_pending_payouts_returns_zero(
        self,
        db: Session,
    ) -> None:
        """No pending/failed payouts -> returns 0."""
        count = retry_pending_payouts(db)
        assert count == 0

    def test_all_payouts_already_paid_returns_zero(
        self,
        db: Session,
        farmer: User,
        consumer: User,
        sample_product: Product,
        farmer_bank_account: FarmerBankAccount,
    ) -> None:
        """Only paid payouts -> returns 0."""
        order = Order(consumer_id=consumer.id, total=100.0, status=OrderStatus.PAID)
        db.add(order)
        db.flush()

        pmt = _seed_payment(db, order.id, consumer.id)

        # Create a paid payout (not pending/failed)
        _seed_payout(db, pmt, order.id, farmer.id, status="paid",
                     farmer_bank_account_id=farmer_bank_account.id)

        db.commit()
        count = retry_pending_payouts(db)
        assert count == 0


class TestPayoutRetryPending:
    @patch("app.services.mono.MonoClient.create_transfer")
    def test_retry_pending_succeeds(
        self,
        mock_transfer: Any,
        db: Session,
        farmer: User,
        consumer: User,
        sample_product: Product,
        farmer_bank_account: FarmerBankAccount,
    ) -> None:
        """Pending payout is retried and marked paid."""
        mock_transfer.return_value = {
            "id": "trf_retry_001",
            "transfer_id": "trf_retry_001",
        }

        order = Order(consumer_id=consumer.id, total=100.0, status=OrderStatus.PAID)
        db.add(order)
        db.flush()

        pmt = _seed_payment(db, order.id, consumer.id)

        # Seed a pending payout
        _seed_payout(db, pmt, order.id, farmer.id, status="pending",
                     farmer_bank_account_id=farmer_bank_account.id)

        db.commit()

        count = retry_pending_payouts(db)
        assert count == 1

        db.expire_all()
        payout = (
            db.query(FarmerPayout)
            .filter(FarmerPayout.order_id == order.id)
            .first()
        )
        assert payout is not None
        assert payout.status == "paid"
        assert payout.mono_transfer_id == "trf_retry_001"
        assert payout.retry_count == 1

        mock_transfer.assert_called_once()

    @patch("app.services.mono.MonoClient.create_transfer")
    def test_retry_failed_succeeds(
        self,
        mock_transfer: Any,
        db: Session,
        farmer: User,
        consumer: User,
        sample_product: Product,
        farmer_bank_account: FarmerBankAccount,
    ) -> None:
        """A previously failed payout is retried and marked paid."""
        mock_transfer.return_value = {
            "id": "trf_retry_fail_001",
            "transfer_id": "trf_retry_fail_001",
        }

        order = Order(consumer_id=consumer.id, total=100.0, status=OrderStatus.PAID)
        db.add(order)
        db.flush()

        pmt = _seed_payment(db, order.id, consumer.id)

        # Seed a failed payout (retry_count=2 to simulate prior failures)
        _seed_payout(db, pmt, order.id, farmer.id, status="failed", retry_count=2,
                     farmer_bank_account_id=farmer_bank_account.id)

        db.commit()

        count = retry_pending_payouts(db)
        assert count == 1

        db.expire_all()
        payout = (
            db.query(FarmerPayout)
            .filter(FarmerPayout.order_id == order.id)
            .first()
        )
        assert payout is not None
        assert payout.status == "paid"
        assert payout.retry_count == 3  # incremented from 2

    @patch("app.services.mono.MonoClient.create_transfer")
    def test_retry_skips_exhausted(
        self,
        mock_transfer: Any,
        db: Session,
        farmer: User,
        consumer: User,
        sample_product: Product,
        farmer_bank_account: FarmerBankAccount,
    ) -> None:
        """Payout at MAX_RETRIES is skipped."""
        from app.services.payout_worker import MAX_RETRIES

        order = Order(consumer_id=consumer.id, total=100.0, status=OrderStatus.PAID)
        db.add(order)
        db.flush()

        pmt = _seed_payment(db, order.id, consumer.id)

        _seed_payout(db, pmt, order.id, farmer.id, status="pending",
                     retry_count=MAX_RETRIES,
                     farmer_bank_account_id=farmer_bank_account.id)

        db.commit()

        count = retry_pending_payouts(db)
        assert count == 0  # not processed
        mock_transfer.assert_not_called()


class TestPayoutRetryNoBankAccount:
    def test_no_bank_account_marks_failed(
        self,
        db: Session,
        farmer: User,
        consumer: User,
        sample_product: Product,
    ) -> None:
        """Payout without bank account -> failed with error_message (no Mono call)."""
        order = Order(consumer_id=consumer.id, total=100.0, status=OrderStatus.PAID)
        db.add(order)
        db.flush()

        pmt = _seed_payment(db, order.id, consumer.id)

        # No farmer_bank_account_id set
        _seed_payout(db, pmt, order.id, farmer.id, status="pending")

        db.commit()

        with patch("app.services.mono.MonoClient.create_transfer") as mock_transfer:
            count = retry_pending_payouts(db)
            assert count == 1
            mock_transfer.assert_not_called()

        db.expire_all()
        payout = (
            db.query(FarmerPayout)
            .filter(FarmerPayout.order_id == order.id)
            .first()
        )
        assert payout is not None
        assert payout.status == "failed"
        assert payout.error_message is not None
        assert "No bank account" in payout.error_message


class TestPayoutRetryMonoError:
    @patch("app.services.mono.MonoClient.create_transfer")
    def test_mono_api_error_sets_failed(
        self,
        mock_transfer: Any,
        db: Session,
        farmer: User,
        consumer: User,
        sample_product: Product,
        farmer_bank_account: FarmerBankAccount,
    ) -> None:
        """When Mono API raises, payout is marked failed with error_message."""
        mock_transfer.side_effect = Exception("Mono is down")

        order = Order(consumer_id=consumer.id, total=100.0, status=OrderStatus.PAID)
        db.add(order)
        db.flush()

        pmt = _seed_payment(db, order.id, consumer.id)

        _seed_payout(db, pmt, order.id, farmer.id, status="pending",
                     farmer_bank_account_id=farmer_bank_account.id)

        db.commit()

        count = retry_pending_payouts(db)
        assert count == 1
        mock_transfer.assert_called_once()

        db.expire_all()
        payout = (
            db.query(FarmerPayout)
            .filter(FarmerPayout.order_id == order.id)
            .first()
        )
        assert payout is not None
        assert payout.status == "failed"
        assert payout.error_message is not None
        assert "Mono is down" in payout.error_message
        assert payout.retry_count == 1
