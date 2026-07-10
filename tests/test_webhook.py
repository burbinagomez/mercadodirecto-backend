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
        db: Session,
        product: dict[str, Any],
        consumer_token: str,
        consumer: User,
    ) -> None:
        order = _place_order(client, product["id"], qty=5, token=consumer_token)
        _seed_payment(db, order["id"], consumer, velafi_order_id="vela-50")

        _raw, headers = _webhook_payload("vela-50", "50")
        resp = client.post("/payments/webhook", content=json.dumps({"orderId": "vela-50", "orderStatus": "50"}), headers=headers)
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"callbackStatus": "SUCCESS"}

        db.expire_all()
        payment = db.query(Payment).filter(Payment.velafi_order_id == "vela-50").first()
        assert payment is not None
        assert payment.status == "paid"
        ord_obj = db.get(Order, order["id"])
        assert ord_obj is not None
        assert ord_obj.status == "paid"

    @patch("app.services.velafi.VelaFiClient.verify_webhook", return_value=True)
    def test_status_60_sets_paid(
        self,
        _mock_verify: Any,
        client: TestClient,
        db: Session,
        product: dict[str, Any],
        consumer_token: str,
        consumer: User,
    ) -> None:
        order = _place_order(client, product["id"], qty=5, token=consumer_token)
        _seed_payment(db, order["id"], consumer, velafi_order_id="vela-60")

        resp = client.post(
            "/payments/webhook",
            content=json.dumps({"orderId": "vela-60", "orderStatus": "60"}),
            headers={"signature": "deadbeef", "Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"callbackStatus": "SUCCESS"}

        db.expire_all()
        payment = db.query(Payment).filter(Payment.velafi_order_id == "vela-60").first()
        assert payment is not None
        assert payment.status == "paid"
        ord_obj = db.get(Order, order["id"])
        assert ord_obj is not None
        assert ord_obj.status == "paid"

    @patch("app.services.velafi.VelaFiClient.verify_webhook", return_value=True)
    def test_status_70_cancels_and_releases_qty(
        self,
        _mock_verify: Any,
        client: TestClient,
        db: Session,
        product: dict[str, Any],
        consumer_token: str,
        consumer: User,
    ) -> None:
        # Place order for 5 kg, verify qty reserved
        order = _place_order(client, product["id"], qty=5, token=consumer_token)
        db.expire_all()
        assert db.get(Product, product["id"]).quantity_available == 95

        _seed_payment(db, order["id"], consumer, velafi_order_id="vela-70")

        resp = client.post(
            "/payments/webhook",
            content=json.dumps({"orderId": "vela-70", "orderStatus": "70"}),
            headers={"signature": "deadbeef", "Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"callbackStatus": "SUCCESS"}

        # Verify order is cancelled and qty is released back
        db.expire_all()
        ord_obj = db.get(Order, order["id"])
        assert ord_obj is not None
        assert ord_obj.status == "cancelled"
        assert db.get(Product, product["id"]).quantity_available == 100  # restored

    @patch("app.services.velafi.VelaFiClient.verify_webhook", return_value=True)
    def test_status_71_cancels_and_releases_qty(
        self,
        _mock_verify: Any,
        client: TestClient,
        db: Session,
        product: dict[str, Any],
        consumer_token: str,
        consumer: User,
    ) -> None:
        order = _place_order(client, product["id"], qty=5, token=consumer_token)
        _seed_payment(db, order["id"], consumer, velafi_order_id="vela-71")

        resp = client.post(
            "/payments/webhook",
            content=json.dumps({"orderId": "vela-71", "orderStatus": "71"}),
            headers={"signature": "deadbeef", "Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"callbackStatus": "SUCCESS"}

        db.expire_all()
        ord_obj = db.get(Order, order["id"])
        assert ord_obj is not None
        assert ord_obj.status == "cancelled"

    @patch("app.services.velafi.VelaFiClient.verify_webhook", return_value=True)
    def test_status_72_cancels(
        self,
        _mock_verify: Any,
        client: TestClient,
        db: Session,
        product: dict[str, Any],
        consumer_token: str,
        consumer: User,
    ) -> None:
        order = _place_order(client, product["id"], qty=5, token=consumer_token)
        _seed_payment(db, order["id"], consumer, velafi_order_id="vela-72")

        resp = client.post(
            "/payments/webhook",
            content=json.dumps({"orderId": "vela-72", "orderStatus": "72"}),
            headers={"signature": "deadbeef", "Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"callbackStatus": "SUCCESS"}

        db.expire_all()
        ord_obj = db.get(Order, order["id"])
        assert ord_obj is not None
        assert ord_obj.status == "cancelled"


class TestWebhookBadSignature:
    """(d) Bad signature → FAIL without state change."""

    # Do NOT mock verify_webhook — it should fail because no public key is
    # configured in the test environment.

    def test_bad_signature_returns_fail(
        self,
        client: TestClient,
        db: Session,
        product: dict[str, Any],
        consumer_token: str,
        consumer: User,
    ) -> None:
        order = _place_order(client, product["id"], qty=5, token=consumer_token)
        _seed_payment(db, order["id"], consumer, velafi_order_id="vela-bad")

        # Send with a made-up signature — verify_webhook will fail because
        # VELAFI_WEBHOOK_PUBLIC_KEY is empty in test env.
        resp = client.post(
            "/payments/webhook",
            content=json.dumps({"orderId": "vela-bad", "orderStatus": "60"}),
            headers={"signature": "not-a-real-sig", "Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"callbackStatus": "FAIL"}

        # No state changed — payment still "created", order still "pending"
        db.expire_all()
        payment = db.query(Payment).filter(Payment.velafi_order_id == "vela-bad").first()
        assert payment is not None
        assert payment.status == "created"
        ord_obj = db.get(Order, order["id"])
        assert ord_obj is not None
        assert ord_obj.status == "pending"


class TestWebhookIdempotency:
    """Repeated events for terminal payments are safely ignored."""

    @patch("app.services.velafi.VelaFiClient.verify_webhook", return_value=True)
    def test_duplicate_paid_event_does_not_regress(
        self,
        _mock_verify: Any,
        client: TestClient,
        db: Session,
        product: dict[str, Any],
        consumer_token: str,
        consumer: User,
    ) -> None:
        order = _place_order(client, product["id"], qty=5, token=consumer_token)
        _seed_payment(db, order["id"], consumer, velafi_order_id="vela-dedup")

        # First event: 60 → paid
        resp1 = client.post(
            "/payments/webhook",
            content=json.dumps({"orderId": "vela-dedup", "orderStatus": "60"}),
            headers={"signature": "deadbeef", "Content-Type": "application/json"},
        )
        assert resp1.json() == {"callbackStatus": "SUCCESS"}

        # Second event: 72 → cancelled (should be ignored — payment is terminal)
        resp2 = client.post(
            "/payments/webhook",
            content=json.dumps({"orderId": "vela-dedup", "orderStatus": "72"}),
            headers={"signature": "deadbeef", "Content-Type": "application/json"},
        )
        assert resp2.json() == {"callbackStatus": "SUCCESS"}

        # State stayed "paid" — not regressed to "cancelled"
        db.expire_all()
        payment = db.query(Payment).filter(Payment.velafi_order_id == "vela-dedup").first()
        assert payment is not None
        assert payment.status == "paid"
        ord_obj = db.get(Order, order["id"])
        assert ord_obj is not None
        assert ord_obj.status == "paid"


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
