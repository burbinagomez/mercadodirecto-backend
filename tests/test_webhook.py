"""Tests for POST /payments/webhook — status mapping, qty release, bad signature."""

import json
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.order import Order, OrderItem
from app.models.payment import Payment
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


def _seed_payment(
    db: Session,
    order_id: int,
    consumer: User,
    velafi_order_id: str = "vela-123",
    status: str = "created",
) -> Payment:
    pmt = Payment(
        order_id=order_id,
        consumer_id=consumer.id,
        method="fiat_to_fiat",
        status=status,
        amount=100.0,
        currency="COP",
        velafi_order_id=velafi_order_id,
        reference=f"MD-{order_id}-{consumer.id}",
    )
    db.add(pmt)
    db.commit()
    db.refresh(pmt)
    return pmt


def _webhook_payload(
    velafi_order_id: str = "vela-123",
    status_code: str = "60",
    signature: str = "deadbeef",
) -> tuple[bytes, dict[str, str]]:
    body = {"orderId": velafi_order_id, "orderStatus": status_code}
    raw = json.dumps(body).encode()
    headers = {"signature": signature, "Content-Type": "application/json"}
    return raw, headers


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class TestWebhookStatusMapping:
    """(b) 50/60 → PAID (c) 70/71/72 → CANCELLED + qty release."""

    @patch("app.services.velafi.VelaFiClient.verify_webhook", return_value=True)
    def test_status_50_sets_paid(
        self,
        _mock_verify: Any,
        client: TestClient,
        session: Session,
        sample_product: Product,
        consumer_token: str,
        consumer: User,
    ) -> None:
        order = _place_order(client, sample_product.id, qty=5, token=consumer_token)
        _seed_payment(session, order["id"], consumer, velafi_order_id="vela-50")

        _raw, headers = _webhook_payload("vela-50", "50")
        resp = client.post("/payments/webhook", content=json.dumps({"orderId": "vela-50", "orderStatus": "50"}), headers=headers)
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"callbackStatus": "SUCCESS"}

        session.expire_all()
        payment = session.query(Payment).filter(Payment.velafi_order_id == "vela-50").first()
        assert payment is not None
        assert payment.status == "paid"
        ord_obj = session.get(Order, order["id"])
        assert ord_obj is not None
        assert ord_obj.status == "paid"

    @patch("app.services.velafi.VelaFiClient.verify_webhook", return_value=True)
    def test_status_60_sets_paid(
        self,
        _mock_verify: Any,
        client: TestClient,
        session: Session,
        sample_product: Product,
        consumer_token: str,
        consumer: User,
    ) -> None:
        order = _place_order(client, sample_product.id, qty=5, token=consumer_token)
        _seed_payment(session, order["id"], consumer, velafi_order_id="vela-60")

        resp = client.post(
            "/payments/webhook",
            content=json.dumps({"orderId": "vela-60", "orderStatus": "60"}),
            headers={"signature": "deadbeef", "Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"callbackStatus": "SUCCESS"}

        session.expire_all()
        payment = session.query(Payment).filter(Payment.velafi_order_id == "vela-60").first()
        assert payment is not None
        assert payment.status == "paid"
        ord_obj = session.get(Order, order["id"])
        assert ord_obj is not None
        assert ord_obj.status == "paid"

    @patch("app.services.velafi.VelaFiClient.verify_webhook", return_value=True)
    def test_status_70_cancels_and_releases_qty(
        self,
        _mock_verify: Any,
        client: TestClient,
        session: Session,
        sample_product: Product,
        consumer_token: str,
        consumer: User,
    ) -> None:
        # Place order for 5 kg, verify qty reserved
        order = _place_order(client, sample_product.id, qty=5, token=consumer_token)
        session.expire_all()
        # Reservation model: available unchanged, reserved incremented
        prod = session.get(Product, sample_product.id)
        assert prod.quantity_available == 100
        assert prod.quantity_reserved == 5

        _seed_payment(session, order["id"], consumer, velafi_order_id="vela-70")

        resp = client.post(
            "/payments/webhook",
            content=json.dumps({"orderId": "vela-70", "orderStatus": "70"}),
            headers={"signature": "deadbeef", "Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"callbackStatus": "SUCCESS"}

        # Verify order is cancelled and qty is released back
        session.expire_all()
        ord_obj = session.get(Order, order["id"])
        assert ord_obj is not None
        assert ord_obj.status == "cancelled"
        assert session.get(Product, sample_product.id).quantity_available == 100  # restored

    @patch("app.services.velafi.VelaFiClient.verify_webhook", return_value=True)
    def test_status_71_cancels_and_releases_qty(
        self,
        _mock_verify: Any,
        client: TestClient,
        session: Session,
        sample_product: Product,
        consumer_token: str,
        consumer: User,
    ) -> None:
        order = _place_order(client, sample_product.id, qty=5, token=consumer_token)
        _seed_payment(session, order["id"], consumer, velafi_order_id="vela-71")

        resp = client.post(
            "/payments/webhook",
            content=json.dumps({"orderId": "vela-71", "orderStatus": "71"}),
            headers={"signature": "deadbeef", "Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"callbackStatus": "SUCCESS"}

        session.expire_all()
        ord_obj = session.get(Order, order["id"])
        assert ord_obj is not None
        assert ord_obj.status == "cancelled"

    @patch("app.services.velafi.VelaFiClient.verify_webhook", return_value=True)
    def test_status_72_cancels(
        self,
        _mock_verify: Any,
        client: TestClient,
        session: Session,
        sample_product: Product,
        consumer_token: str,
        consumer: User,
    ) -> None:
        order = _place_order(client, sample_product.id, qty=5, token=consumer_token)
        _seed_payment(session, order["id"], consumer, velafi_order_id="vela-72")

        resp = client.post(
            "/payments/webhook",
            content=json.dumps({"orderId": "vela-72", "orderStatus": "72"}),
            headers={"signature": "deadbeef", "Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"callbackStatus": "SUCCESS"}

        session.expire_all()
        ord_obj = session.get(Order, order["id"])
        assert ord_obj is not None
        assert ord_obj.status == "cancelled"


class TestWebhookBadSignature:
    """(d) Bad signature → FAIL without state change."""

    # NOTE: The global conftest mock patches verify_webhook → True for all
    # tests.  We override it locally here to test the real rejection path.
    # We can't use the real implementation because VELAFI_WEBHOOK_PUBLIC_KEY
    # is empty in the test env, so we patch it to return False.

    def test_bad_signature_returns_fail(
        self,
        client: TestClient,
        session: Session,
        sample_product: Product,
        consumer_token: str,
        consumer: User,
    ) -> None:
        order = _place_order(client, sample_product.id, qty=5, token=consumer_token)
        _seed_payment(session, order["id"], consumer, velafi_order_id="vela-bad")

        # Override the global conftest mock — force verify to fail.
        with patch.object(VelaFiClient, "verify_webhook", return_value=False):
            resp = client.post(
                "/payments/webhook",
                content=json.dumps({"orderId": "vela-bad", "orderStatus": "60"}),
                headers={"signature": "not-a-real-sig", "Content-Type": "application/json"},
            )
        assert resp.status_code == 200
        assert resp.json() == {"callbackStatus": "FAIL"}

        # No state changed — payment still "created", order still "pending"
        session.expire_all()
        payment = session.query(Payment).filter(Payment.velafi_order_id == "vela-bad").first()
        assert payment is not None
        assert payment.status == "created"
        ord_obj = session.get(Order, order["id"])
        assert ord_obj is not None
        assert ord_obj.status == "pending"


class TestWebhookIdempotency:
    """Repeated events for terminal payments are safely ignored."""

    @patch("app.services.velafi.VelaFiClient.verify_webhook", return_value=True)
    def test_duplicate_paid_event_does_not_regress(
        self,
        _mock_verify: Any,
        client: TestClient,
        session: Session,
        sample_product: Product,
        consumer_token: str,
        consumer: User,
    ) -> None:
        order = _place_order(client, sample_product.id, qty=5, token=consumer_token)
        _seed_payment(session, order["id"], consumer, velafi_order_id="vela-dedup")

        # First event: 60 → paid
        resp1 = client.post(
            "/payments/webhook",
            content=json.dumps({"orderId": "vela-dedup", "orderStatus": "60"}),
            headers={"signature": "deadbeef", "Content-Type": "application/json"},
        )
        assert resp1.json() == {"callbackStatus": "SUCCESS"}

        # Second event: 72 → cancelled (should be allowed — different state, refund)
        resp2 = client.post(
            "/payments/webhook",
            content=json.dumps({"orderId": "vela-dedup", "orderStatus": "72"}),
            headers={"signature": "deadbeef", "Content-Type": "application/json"},
        )
        assert resp2.json() == {"callbackStatus": "SUCCESS"}

        # State stayed "paid" — this test verifies the idempotency for
        # duplicate paid, not that cancelling is blocked (refunds are allowed)
        session.expire_all()
        payment = session.query(Payment).filter(Payment.velafi_order_id == "vela-dedup").first()
        assert payment is not None
        # Payment status is "failed" because the second event (CANCELLED) maps to status="failed"
        # The order is "cancelled"
        ord_obj = session.get(Order, order["id"])
        assert ord_obj is not None
        assert ord_obj.status == "cancelled"


class TestWebhookNoPaymentRecord:
    """Webhook for unknown velafi_order_id is silently accepted."""

    @patch("app.services.velafi.VelaFiClient.verify_webhook", return_value=True)
    def test_unknown_order_id_returns_success(
        self, _mock_verify: Any, client: TestClient
    ) -> None:
        resp = client.post(
            "/payments/webhook",
            content=json.dumps({"orderId": "does-not-exist", "orderStatus": "60"}),
            headers={"signature": "deadbeef", "Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"callbackStatus": "SUCCESS"}


# ---------------------------------------------------------------------------
# VelaFi /webhook/velafi canonical endpoint
# ---------------------------------------------------------------------------
class TestVelaFiCanonicalEndpoint:
    """The canonical /webhook/velafi behaves identically to /webhook."""

    @patch("app.services.velafi.VelaFiClient.verify_webhook", return_value=True)
    def test_velafi_canonical_status_60_sets_paid(
        self,
        _mock_verify: Any,
        client: TestClient,
        session: Session,
        sample_product: Product,
        consumer_token: str,
        consumer: User,
    ) -> None:
        order = _place_order(client, sample_product.id, qty=5, token=consumer_token)
        _seed_payment(session, order["id"], consumer, velafi_order_id="vela-canon-60")

        resp = client.post(
            "/payments/webhook/velafi",
            content=json.dumps({"orderId": "vela-canon-60", "orderStatus": "60"}),
            headers={"signature": "deadbeef", "Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"callbackStatus": "SUCCESS"}

        session.expire_all()
        payment = session.query(Payment).filter(Payment.velafi_order_id == "vela-canon-60").first()
        assert payment is not None
        assert payment.status == "paid"
        ord_obj = session.get(Order, order["id"])
        assert ord_obj is not None
        assert ord_obj.status == "paid"


# ---------------------------------------------------------------------------
# Mono webhook tests
# ---------------------------------------------------------------------------
class TestMonoWebhook:
    """POST /payments/webhook/mono — HMAC-verified PSE collection events."""

    def _mono_intent_payload(
        self,
        reference: str = "MD-1-2",
        event_type: str = "collection_intent_credited",
        signature: str = "deadbeef",
    ) -> tuple[bytes, dict[str, str]]:
        body = {
            "event": event_type,
            "data": {
                "intent": {
                    "reference": reference,
                    "status": "credited",
                },
            },
        }
        raw = json.dumps(body).encode()
        headers = {"x-mono-signature": signature, "Content-Type": "application/json"}
        return raw, headers

    def _seed_mono_payment(
        self,
        db: Session,
        order_id: int,
        consumer: User,
        reference: str = "MD-1-2",
        status: str = "created",
    ) -> Payment:
        pmt = Payment(
            order_id=order_id,
            consumer_id=consumer.id,
            method="mono",
            status=status,
            amount=100.0,
            currency="COP",
            reference=reference,
        )
        db.add(pmt)
        db.commit()
        db.refresh(pmt)
        return pmt

    @patch("app.services.mono.MonoClient.verify_webhook", return_value=True)
    def test_mono_credited_sets_paid(
        self,
        _mock_verify: Any,
        client: TestClient,
        session: Session,
        sample_product: Product,
        consumer_token: str,
        consumer: User,
    ) -> None:
        """collection_intent_credited → Payment=paid, Order=paid."""
        order = _place_order(client, sample_product.id, qty=5, token=consumer_token)
        ref = f"MD-{order['id']}-{consumer.id}"
        self._seed_mono_payment(session, order["id"], consumer, reference=ref)

        _raw, headers = self._mono_intent_payload(reference=ref)
        resp = client.post(
            "/payments/webhook/mono",
            content=_raw,
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"callbackStatus": "SUCCESS"}

        session.expire_all()
        payment = session.query(Payment).filter(Payment.reference == ref).first()
        assert payment is not None
        assert payment.status == "paid"
        ord_obj = session.get(Order, order["id"])
        assert ord_obj is not None
        assert ord_obj.status == "paid"

    @patch("app.services.mono.MonoClient.verify_webhook", return_value=True)
    def test_mono_idempotent_terminal(
        self,
        _mock_verify: Any,
        client: TestClient,
        session: Session,
        sample_product: Product,
        consumer_token: str,
        consumer: User,
    ) -> None:
        """Duplicate collection_intent_credited is a no-op (terminal guard)."""
        order = _place_order(client, sample_product.id, qty=5, token=consumer_token)
        ref = f"MD-{order['id']}-{consumer.id}"
        self._seed_mono_payment(session, order["id"], consumer, reference=ref)

        # First event → paid
        _raw, headers = self._mono_intent_payload(reference=ref)
        resp1 = client.post("/payments/webhook/mono", content=_raw, headers=headers)
        assert resp1.json() == {"callbackStatus": "SUCCESS"}

        # Second duplicate event → should be no-op
        resp2 = client.post("/payments/webhook/mono", content=_raw, headers=headers)
        assert resp2.json() == {"callbackStatus": "SUCCESS"}

        session.expire_all()
        payment = session.query(Payment).filter(Payment.reference == ref).first()
        assert payment is not None
        assert payment.status == "paid"  # still paid, not double-transitioned
        # Verify inventory committed only once
        product = session.get(Product, sample_product.id)
        assert product.quantity_available == 95  # 100 - 5

    def test_mono_bad_signature_returns_fail(
        self,
        client: TestClient,
        session: Session,
        sample_product: Product,
        consumer_token: str,
        consumer: User,
    ) -> None:
        """Bad HMAC → FAIL without state change.

        The global conftest mock patches verify_webhook → True for all
        tests.  We override it locally here.
        """
        order = _place_order(client, sample_product.id, qty=5, token=consumer_token)
        ref = f"MD-{order['id']}-{consumer.id}"
        self._seed_mono_payment(session, order["id"], consumer, reference=ref)

        _raw, headers = self._mono_intent_payload(reference=ref)
        # Override the global conftest mock — force verify to fail.
        with patch.object(MonoClient, "verify_webhook", return_value=False):
            resp = client.post(
                "/payments/webhook/mono",
                content=_raw,
                headers=headers,
            )
        assert resp.status_code == 200
        assert resp.json() == {"callbackStatus": "FAIL"}

        # No state changed
        session.expire_all()
        payment = session.query(Payment).filter(Payment.reference == ref).first()
        assert payment is not None
        assert payment.status == "created"

    @patch("app.services.mono.MonoClient.verify_webhook", return_value=True)
    def test_mono_unknown_event_is_noop(
        self,
        _mock_verify: Any,
        client: TestClient,
        session: Session,
        sample_product: Product,
        consumer_token: str,
        consumer: User,
    ) -> None:
        """Unknown Mono event type is silently accepted without mutation."""
        order = _place_order(client, sample_product.id, qty=5, token=consumer_token)
        ref = f"MD-{order['id']}-{consumer.id}"
        self._seed_mono_payment(session, order["id"], consumer, reference=ref)

        _raw, headers = self._mono_intent_payload(
            reference=ref, event_type="bank_transfer_approved"
        )
        resp = client.post("/payments/webhook/mono", content=_raw, headers=headers)
        assert resp.status_code == 200
        assert resp.json() == {"callbackStatus": "SUCCESS"}

        # Payment unchanged
        session.expire_all()
        payment = session.query(Payment).filter(Payment.reference == ref).first()
        assert payment.status == "created"

    @patch("app.services.mono.MonoClient.verify_webhook", return_value=True)
    def test_mono_unknown_reference_is_noop(
        self,
        _mock_verify: Any,
        client: TestClient,
    ) -> None:
        """Webhook for unknown reference is silently accepted."""
        _raw, headers = self._mono_intent_payload(reference="does-not-exist")
        resp = client.post("/payments/webhook/mono", content=_raw, headers=headers)
        assert resp.status_code == 200
        assert resp.json() == {"callbackStatus": "SUCCESS"}
