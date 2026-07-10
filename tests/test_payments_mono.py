"""Tests for Mono checkout + webhook flows.

Coverage (per test plan):
  (a) checkout mono -> creates Payment(method=mono), calls create_collection_intent, returns redirectUrl
  (b) checkout stablecoin -> still calls VelaFi, no regression
  (c) webhook /mono credited -> Payment=paid, Order=paid, FarmerPayout(pending), create_transfer
  (d) webhook /mono idempotent -> second identical event is no-op
  (e) webhook /mono bad HMAC -> 401 / FAIL, no status change
  (f) payout bank_transfer_approved -> FarmerPayout=pending->paid
  (g) payout bank_transfer_declined -> FarmerPayout=pending->failed
  (h) webhook /velafi terminal guard -> duplicate terminal event is no-op (CRITICAL)
"""

import json
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.order import Order, OrderStatus
from app.models.payment import Payment, PaymentMethod
from app.models.payout import FarmerBankAccount, FarmerPayout
from app.models.product import Product
from app.models.user import User
from app.services.mono import MonoClient
from app.services.velafi import VelaFiClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _place_order(
    client: TestClient,
    product_id: int,
    qty: int = 5,
    token: str = "",
) -> dict[str, Any]:
    resp = client.post(
        "/orders",
        json={"items": [{"product_id": product_id, "qty": qty}]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _seed_mono_payment(
    db: Session,
    order_id: int,
    consumer_id: int,
    mono_intent_id: str = "int_abc123",
    status: str = "created",
) -> Payment:
    pmt = Payment(
        order_id=order_id,
        consumer_id=consumer_id,
        method=PaymentMethod.MONO.value,
        status=status,
        amount=100.0,
        currency="COP",
        mono_intent_id=mono_intent_id,
        reference=f"MD-{order_id}-{consumer_id}",
    )
    db.add(pmt)
    db.commit()
    db.refresh(pmt)
    return pmt


def _seed_velafi_payment(
    db: Session,
    order_id: int,
    consumer_id: int,
    velafi_order_id: str = "vela_guard_001",
    status: str = "created",
) -> Payment:
    pmt = Payment(
        order_id=order_id,
        consumer_id=consumer_id,
        method=PaymentMethod.STABLECOIN.value,
        status=status,
        amount=100.0,
        currency="USDT",
        velafi_order_id=velafi_order_id,
        reference=f"MD-{order_id}-{consumer_id}",
    )
    db.add(pmt)
    db.commit()
    db.refresh(pmt)
    return pmt


def _velafi_webhook_payload(
    velafi_order_id: str = "vela_guard_001",
    status_code: str = "60",
    signature: str = "deadbeef",
) -> tuple[bytes, dict[str, str]]:
    body = {"orderId": velafi_order_id, "orderStatus": status_code}
    raw = json.dumps(body).encode()
    headers = {"signature": signature, "Content-Type": "application/json"}
    return raw, headers


def _mono_webhook_payload(
    event_type: str = "collection_intent_credited",
    data: dict[str, Any] | None = None,
    signature: str = "valid-hmac-sig",
) -> tuple[bytes, dict[str, str]]:
    body = data or {}
    payload = {"event": event_type, "data": body}
    raw = json.dumps(payload).encode()
    headers = {"x-mono-signature": signature, "Content-Type": "application/json"}
    return raw, headers


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCheckoutMono:
    """(a) POST /payments/checkout method=mono."""

    @patch("app.services.mono.MonoClient.create_collection_intent")
    def test_mono_checkout_returns_redirect_url(
        self,
        mock_collect: Any,
        client: TestClient,
        db: Session,
        product: dict[str, Any],
        consumer_token: str,
        consumer: User,
    ) -> None:
        mock_collect.return_value = {
            "intent_id": "int_mono_001",
            "redirect_url": "https://mono.la/pse/int_mono_001",
        }

        order = _place_order(client, product["id"], qty=3, token=consumer_token)
        resp = client.post(
            "/payments/checkout",
            json={
                "order_id": order["id"],
                "method": PaymentMethod.MONO.value,
            },
            headers={"Authorization": f"Bearer {consumer_token}"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["method"] == PaymentMethod.MONO.value
        assert data["redirectUrl"] == "https://mono.la/pse/int_mono_001"
        assert "reference" in data

        # Verify Payment row
        mock_collect.assert_called_once()
        call_kwargs = mock_collect.call_args[1]  # keyword args
        assert "amount" in call_kwargs
        assert "reference" in call_kwargs
        assert "idempotency_key" in call_kwargs

        payment = db.query(Payment).filter(Payment.order_id == order["id"]).first()
        assert payment is not None
        assert payment.method == PaymentMethod.MONO.value
        assert payment.mono_intent_id == "int_mono_001"
        assert payment.status == "created"

    def test_mono_checkout_rejects_no_wallet_for_stablecoin(
        self,
        client: TestClient,
        product: dict[str, Any],
        consumer_token: str,
    ) -> None:
        order = _place_order(client, product["id"], qty=1, token=consumer_token)
        resp = client.post(
            "/payments/checkout",
            json={
                "order_id": order["id"],
                "method": PaymentMethod.STABLECOIN.value,
                # no wallet_id — should fail
            },
            headers={"Authorization": f"Bearer {consumer_token}"},
        )
        assert resp.status_code == 400
        assert "wallet_id required" in resp.text


class TestCheckoutStablecoin:
    """(b) POST /payments/checkout method=stablecoin still works."""

    @patch("app.services.velafi.VelaFiClient.create_payment_link")
    def test_stablecoin_checkout_returns_link(
        self,
        mock_link: Any,
        client: TestClient,
        db: Session,
        product: dict[str, Any],
        consumer_token: str,
        consumer: User,
    ) -> None:
        mock_link.return_value = {
            "paymentLink": "https://velafi.com/pay/link_xxx",
        }

        order = _place_order(client, product["id"], qty=2, token=consumer_token)
        resp = client.post(
            "/payments/checkout",
            json={
                "order_id": order["id"],
                "method": PaymentMethod.STABLECOIN.value,
                "wallet_id": 42,
                "currency": "USDT",
            },
            headers={"Authorization": f"Bearer {consumer_token}"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["method"] == PaymentMethod.STABLECOIN.value
        assert data["paymentLink"] == "https://velafi.com/pay/link_xxx"

        payment = db.query(Payment).filter(Payment.order_id == order["id"]).first()
        assert payment is not None
        assert payment.method == PaymentMethod.STABLECOIN.value
        assert payment.velafi_payment_link == "https://velafi.com/pay/link_xxx"
        assert payment.status == "created"


class TestWebhookMonoCredited:
    """(c) POST /payments/webhook/mono collection_intent_credited."""

    @patch("app.services.mono.MonoClient.create_transfer")
    @patch("app.services.mono.MonoClient.verify_webhook")
    def test_collection_credited_sets_paid_and_triggers_payout(
        self,
        mock_verify: Any,
        mock_transfer: Any,
        client: TestClient,
        db: Session,
        product: dict[str, Any],
        consumer_token: str,
        consumer: User,
        farmer: User,
        farmer_bank_account: FarmerBankAccount,
    ) -> None:
        mock_verify.return_value = True
        mock_transfer.return_value = {
            "id": "trf_farmer_001",
            "transfer_id": "trf_farmer_001",
            "status": "approved",
        }

        order = _place_order(client, product["id"], qty=5, token=consumer_token)
        _seed_mono_payment(db, order["id"], consumer.id, mono_intent_id="int_paid_001")

        _raw, headers = _mono_webhook_payload(
            "collection_intent_credited",
            {"id": "int_paid_001", "reference": f"MD-{order['id']}-{consumer.id}", "amount": 50000},
        )
        resp = client.post("/payments/webhook/mono", content=_raw, headers=headers)
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"callbackStatus": "SUCCESS"}

        # Payment = paid
        db.expire_all()
        payment = db.query(Payment).filter(Payment.mono_intent_id == "int_paid_001").first()
        assert payment is not None
        assert payment.status == "paid"

        # Order = paid
        ord_obj = db.get(Order, order["id"])
        assert ord_obj is not None
        assert ord_obj.status == OrderStatus.PAID

        # FarmerPayout created (pending)
        payout = db.query(FarmerPayout).filter(FarmerPayout.order_id == order["id"]).first()
        assert payout is not None
        assert payout.status == "pending"
        assert payout.mono_transfer_id == "trf_farmer_001"
        assert payout.farmer_id == farmer.id

        # create_transfer was called
        mock_transfer.assert_called_once()

    @patch("app.services.mono.MonoClient.create_transfer")
    @patch("app.services.mono.MonoClient.verify_webhook")
    def test_collection_credited_without_bank_account_fails_payout(
        self,
        mock_verify: Any,
        mock_transfer: Any,
        client: TestClient,
        db: Session,
        product: dict[str, Any],
        consumer_token: str,
        consumer: User,
        farmer: User,
    ) -> None:
        """Farmer without bank account: payout=failed, create_transfer NOT called."""
        mock_verify.return_value = True

        order = _place_order(client, product["id"], qty=3, token=consumer_token)
        _seed_mono_payment(db, order["id"], consumer.id, mono_intent_id="int_no_bank")

        _raw, headers = _mono_webhook_payload(
            "collection_intent_credited",
            {"id": "int_no_bank", "reference": f"MD-{order['id']}-{consumer.id}"},
        )
        resp = client.post("/payments/webhook/mono", content=_raw, headers=headers)
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"callbackStatus": "SUCCESS"}

        # Payout exists but in failed state (no bank account)
        payout = db.query(FarmerPayout).filter(FarmerPayout.order_id == order["id"]).first()
        assert payout is not None
        assert payout.status == "failed"
        mock_transfer.assert_not_called()

    @patch("app.services.mono.MonoClient.create_transfer")
    @patch("app.services.mono.MonoClient.verify_webhook")
    def test_collection_credited_by_reference_fallback(
        self,
        mock_verify: Any,
        mock_transfer: Any,
        client: TestClient,
        db: Session,
        product: dict[str, Any],
        consumer_token: str,
        consumer: User,
        farmer: User,
        farmer_bank_account: FarmerBankAccount,
    ) -> None:
        """When intent_id is unknown, the handler falls back to reference lookup."""
        mock_verify.return_value = True
        mock_transfer.return_value = {"id": "trf_ref_fallback", "transfer_id": "trf_ref_fallback"}

        order = _place_order(client, product["id"], qty=5, token=consumer_token)
        _seed_mono_payment(db, order["id"], consumer.id, mono_intent_id="int_ref_fallback")

        # Webhook with unknown intent_id but matching reference
        _raw, headers = _mono_webhook_payload(
            "collection_intent_credited",
            {
                "id": "some_other_intent",  # won't match mono_intent_id
                "reference": f"MD-{order['id']}-{consumer.id}",  # but this matches
            },
        )
        resp = client.post("/payments/webhook/mono", content=_raw, headers=headers)
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"callbackStatus": "SUCCESS"}

        db.expire_all()
        payment = db.query(Payment).filter(Payment.order_id == order["id"]).first()
        assert payment is not None
        assert payment.status == "paid"


class TestWebhookMonoIdempotency:
    """(d) Repeated events for terminal payments are safely ignored."""

    @patch("app.services.mono.MonoClient.create_transfer")
    @patch("app.services.mono.MonoClient.verify_webhook")
    def test_duplicate_collection_credited_is_noop(
        self,
        mock_verify: Any,
        mock_transfer: Any,
        client: TestClient,
        db: Session,
        product: dict[str, Any],
        consumer_token: str,
        consumer: User,
        farmer: User,
        farmer_bank_account: FarmerBankAccount,
    ) -> None:
        mock_verify.return_value = True
        mock_transfer.return_value = {"id": "trf_idem", "transfer_id": "trf_idem"}

        order = _place_order(client, product["id"], qty=5, token=consumer_token)
        _seed_mono_payment(db, order["id"], consumer.id, mono_intent_id="int_idem")

        # First webhook — should credit
        _raw, headers = _mono_webhook_payload(
            "collection_intent_credited",
            {"id": "int_idem", "reference": f"MD-{order['id']}-{consumer.id}"},
        )
        resp1 = client.post("/payments/webhook/mono", content=_raw, headers=headers)
        assert resp1.json() == {"callbackStatus": "SUCCESS"}

        # Second (duplicate) webhook — should no-op
        resp2 = client.post("/payments/webhook/mono", content=_raw, headers=headers)
        assert resp2.json() == {"callbackStatus": "SUCCESS"}

        db.expire_all()
        payment = db.query(Payment).filter(Payment.mono_intent_id == "int_idem").first()
        assert payment.status == "paid"

        # Only one transfer was created (not two)
        assert mock_transfer.call_count == 1


class TestWebhookMonoBadHMAC:
    """(e) Bad HMAC -> FAIL, no state change."""

    def test_bad_signature_returns_fail(
        self,
        client: TestClient,
        db: Session,
        product: dict[str, Any],
        consumer_token: str,
        consumer: User,
    ) -> None:
        order = _place_order(client, product["id"], qty=3, token=consumer_token)
        _seed_mono_payment(db, order["id"], consumer.id, mono_intent_id="int_badhmac")

        # Override the global conftest mock — force verify to fail.
        with patch.object(MonoClient, "verify_webhook", return_value=False):
            _raw, headers = _mono_webhook_payload(
                "collection_intent_credited",
                {"id": "int_badhmac", "reference": f"MD-{order['id']}-{consumer.id}"},
                signature="totally-fake-signature",
            )
            resp = client.post("/payments/webhook/mono", content=_raw, headers=headers)
            assert resp.status_code == 200, resp.text
            assert resp.json() == {"callbackStatus": "FAIL"}

        # State unchanged
        db.expire_all()
        payment = db.query(Payment).filter(Payment.mono_intent_id == "int_badhmac").first()
        assert payment.status == "created"


class TestWebhookMonoTransferApproved:
    """(f) bank_transfer_approved -> FarmerPayout=pending->paid."""

    @patch("app.services.mono.MonoClient.verify_webhook")
    def test_transfer_approved_sets_payout_paid(
        self,
        mock_verify: Any,
        client: TestClient,
        db: Session,
        product: dict[str, Any],
        consumer_token: str,
        consumer: User,
        farmer: User,
        farmer_bank_account: FarmerBankAccount,
    ) -> None:
        mock_verify.return_value = True

        order = _place_order(client, product["id"], qty=5, token=consumer_token)
        _seed_mono_payment(db, order["id"], consumer.id, mono_intent_id="int_trf_appr")

        # First: credit the collection so a farmer payout is created
        _raw_credit, headers_credit = _mono_webhook_payload(
            "collection_intent_credited",
            {"id": "int_trf_appr", "reference": f"MD-{order['id']}-{consumer.id}"},
        )
        with patch("app.services.mono.MonoClient.create_transfer", return_value={"id": "trf_appr_001", "transfer_id": "trf_appr_001"}):
            client.post("/payments/webhook/mono", content=_raw_credit, headers=headers_credit)

        # Now bank_transfer_approved arrives
        _raw_approve, headers_approve = _mono_webhook_payload(
            "bank_transfer_approved",
            {"id": "trf_appr_001", "reference": f"payout-{order['id']}-{farmer.id}"},
        )
        resp = client.post("/payments/webhook/mono", content=_raw_approve, headers=headers_approve)
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"callbackStatus": "SUCCESS"}

        db.expire_all()
        payout = (
            db.query(FarmerPayout)
            .filter(FarmerPayout.order_id == order["id"])
            .first()
        )
        assert payout is not None
        assert payout.status == "paid"
        assert payout.mono_transfer_id == "trf_appr_001"

    @patch("app.services.mono.MonoClient.verify_webhook")
    def test_transfer_approved_unknown_transfer_id_returns_success(
        self,
        mock_verify: Any,
        client: TestClient,
    ) -> None:
        """Unknown transfer_id -> SUCCESS (no-op, likely already processed)."""
        mock_verify.return_value = True
        _raw, headers = _mono_webhook_payload(
            "bank_transfer_approved",
            {"id": "unknown_trf", "reference": "payout-999-1"},
        )
        resp = client.post("/payments/webhook/mono", content=_raw, headers=headers)
        assert resp.status_code == 200
        assert resp.json() == {"callbackStatus": "SUCCESS"}


class TestWebhookMonoTransferDeclined:
    """(g) bank_transfer_declined -> FarmerPayout=pending->failed."""

    @patch("app.services.mono.MonoClient.verify_webhook")
    def test_transfer_declined_sets_payout_failed(
        self,
        mock_verify: Any,
        client: TestClient,
        db: Session,
        product: dict[str, Any],
        consumer_token: str,
        consumer: User,
        farmer: User,
        farmer_bank_account: FarmerBankAccount,
    ) -> None:
        mock_verify.return_value = True

        order = _place_order(client, product["id"], qty=5, token=consumer_token)
        _seed_mono_payment(db, order["id"], consumer.id, mono_intent_id="int_trf_decl")

        # Credit the collection to create the payout
        _raw_credit, headers_credit = _mono_webhook_payload(
            "collection_intent_credited",
            {"id": "int_trf_decl", "reference": f"MD-{order['id']}-{consumer.id}"},
        )
        with patch("app.services.mono.MonoClient.create_transfer", return_value={"id": "trf_decl_001", "transfer_id": "trf_decl_001"}):
            client.post("/payments/webhook/mono", content=_raw_credit, headers=headers_credit)

        # Now bank_transfer_declined arrives
        _raw_decline, headers_decline = _mono_webhook_payload(
            "bank_transfer_declined",
            {"id": "trf_decl_001", "reference": f"payout-{order['id']}-{farmer.id}"},
        )
        resp = client.post("/payments/webhook/mono", content=_raw_decline, headers=headers_decline)
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"callbackStatus": "SUCCESS"}

        db.expire_all()
        payout = (
            db.query(FarmerPayout)
            .filter(FarmerPayout.order_id == order["id"])
            .first()
        )
        assert payout is not None
        assert payout.status == "failed"

    @patch("app.services.mono.MonoClient.verify_webhook")
    def test_transfer_declined_idempotent(
        self,
        mock_verify: Any,
        client: TestClient,
        db: Session,
        product: dict[str, Any],
        consumer_token: str,
        consumer: User,
        farmer: User,
        farmer_bank_account: FarmerBankAccount,
    ) -> None:
        """Second declined webhook is a no-op."""
        mock_verify.return_value = True

        order = _place_order(client, product["id"], qty=5, token=consumer_token)
        _seed_mono_payment(db, order["id"], consumer.id, mono_intent_id="int_trf_idem_decl")

        _raw_credit, headers_credit = _mono_webhook_payload(
            "collection_intent_credited",
            {"id": "int_trf_idem_decl", "reference": f"MD-{order['id']}-{consumer.id}"},
        )
        with patch("app.services.mono.MonoClient.create_transfer", return_value={"id": "trf_idem_decl", "transfer_id": "trf_idem_decl"}):
            client.post("/payments/webhook/mono", content=_raw_credit, headers=headers_credit)

        # First declined
        _raw, headers = _mono_webhook_payload(
            "bank_transfer_declined",
            {"id": "trf_idem_decl", "reference": f"payout-{order['id']}-{farmer.id}"},
        )
        resp1 = client.post("/payments/webhook/mono", content=_raw, headers=headers)
        assert resp1.json() == {"callbackStatus": "SUCCESS"}

        # Second declined (duplicate)
        resp2 = client.post("/payments/webhook/mono", content=_raw, headers=headers)
        assert resp2.json() == {"callbackStatus": "SUCCESS"}

        db.expire_all()
        payout = (
            db.query(FarmerPayout)
            .filter(FarmerPayout.order_id == order["id"])
            .first()
        )
        assert payout.status == "failed"


# ---------------------------------------------------------------------------
# VelaFi terminal-state idempotency guard (CRITICAL)
# ---------------------------------------------------------------------------


class TestWebhookVelafiTerminalGuard:
    """(h) VelaFi /webhook terminal-state guard — duplicate events are no-ops."""

    def test_duplicate_velafi_paid_event_is_noop(
        self,
        client: TestClient,
        db: Session,
        product: dict[str, Any],
        consumer_token: str,
        consumer: User,
    ) -> None:
        """Same status=60 twice — second is idempotent no-op."""
        order = _place_order(client, product["id"], qty=5, token=consumer_token)
        _seed_velafi_payment(db, order["id"], consumer.id, velafi_order_id="vela_guard_60")

        _raw, headers = _velafi_webhook_payload("vela_guard_60", "60")

        # First: 60 → paid
        resp1 = client.post("/payments/webhook", content=_raw, headers=headers)
        assert resp1.status_code == 200, resp1.text
        assert resp1.json() == {"callbackStatus": "SUCCESS"}

        db.expire_all()
        payment = db.query(Payment).filter(Payment.velafi_order_id == "vela_guard_60").first()
        assert payment is not None
        assert payment.status == "paid"

        # Second (duplicate): same status 60 → no-op
        resp2 = client.post("/payments/webhook", content=_raw, headers=headers)
        assert resp2.status_code == 200, resp2.text
        assert resp2.json() == {"callbackStatus": "SUCCESS"}

        db.expire_all()
        payment2 = db.query(Payment).filter(Payment.velafi_order_id == "vela_guard_60").first()
        assert payment2.status == "paid"

    def test_duplicate_velafi_cancelled_event_is_noop(
        self,
        client: TestClient,
        db: Session,
        product: dict[str, Any],
        consumer_token: str,
        consumer: User,
    ) -> None:
        """Same status=72 twice — second is idempotent no-op."""
        order = _place_order(client, product["id"], qty=5, token=consumer_token)
        _seed_velafi_payment(db, order["id"], consumer.id, velafi_order_id="vela_guard_72")

        _raw, headers = _velafi_webhook_payload("vela_guard_72", "72")

        # First: 72 → cancelled
        resp1 = client.post("/payments/webhook", content=_raw, headers=headers)
        assert resp1.status_code == 200, resp1.text

        db.expire_all()
        payment = db.query(Payment).filter(Payment.velafi_order_id == "vela_guard_72").first()
        assert payment is not None
        assert payment.status == "failed"

        # Second (duplicate): same status 72 → no-op
        resp2 = client.post("/payments/webhook", content=_raw, headers=headers)
        assert resp2.status_code == 200, resp2.text
        assert resp2.json() == {"callbackStatus": "SUCCESS"}

        db.expire_all()
        payment2 = db.query(Payment).filter(Payment.velafi_order_id == "vela_guard_72").first()
        assert payment2.status == "failed"

    def test_velafi_canonical_terminal_guard(
        self,
        client: TestClient,
        db: Session,
        product: dict[str, Any],
        consumer_token: str,
        consumer: User,
    ) -> None:
        """Canonical /webhook/velafi also guards against duplicates."""
        order = _place_order(client, product["id"], qty=5, token=consumer_token)
        _seed_velafi_payment(db, order["id"], consumer.id, velafi_order_id="vela_canon_guard")

        _raw, headers = _velafi_webhook_payload("vela_canon_guard", "60")

        # First call
        resp1 = client.post("/payments/webhook/velafi", content=_raw, headers=headers)
        assert resp1.status_code == 200, resp1.text
        assert resp1.json() == {"callbackStatus": "SUCCESS"}

        # Duplicate — should no-op
        resp2 = client.post("/payments/webhook/velafi", content=_raw, headers=headers)
        assert resp2.status_code == 200, resp2.text
        assert resp2.json() == {"callbackStatus": "SUCCESS"}

        db.expire_all()
        payment = db.query(Payment).filter(Payment.velafi_order_id == "vela_canon_guard").first()
        assert payment is not None
        assert payment.status == "paid"
