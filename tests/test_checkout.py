"""Tests for POST /orders (checkout) — order creation + quantity reservation."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.order import Order
from app.models.product import Product


class TestCheckoutHappyPath:
    """(a) Checkout creates an order, persists items, and reserves quantity."""

    def test_creates_order_with_correct_total(
        self, client: TestClient, product: dict[str, Any], consumer_token: str
    ) -> None:
        resp = client.post(
            "/orders",
            json={"items": [{"product_id": product["id"], "qty": 3}]},
            headers={"Authorization": f"Bearer {consumer_token}"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "pending"
        assert data["total"] == 3 * 2500.0  # 3 kg × 2500
        assert len(data["items"]) == 1
        assert data["items"][0]["qty"] == 3
        assert data["items"][0]["price"] == 7500.0

    def test_reserves_quantity(
        self, client: TestClient, db: Session, product: dict[str, Any], consumer_token: str
    ) -> None:
        client.post(
            "/orders",
            json={"items": [{"product_id": product["id"], "qty": 10}]},
            headers={"Authorization": f"Bearer {consumer_token}"},
        )
        db.expire_all()
        prod = db.get(Product, product["id"])
        assert prod is not None
        # Starting qty_available was 100, reserved 10.
        assert prod.quantity_available == 90

    def test_multiple_items_in_one_order(
        self, client: TestClient, product: dict[str, Any], farmer_token: str, consumer_token: str
    ) -> None:
        # Create a second product
        resp2 = client.post(
            "/products",
            json={
                "name": "Pera",
                "category": "Frutas",
                "price_per_kg": 1800.0,
                "quantity_available": 50,
                "department": "Cundinamarca",
            },
            headers={"Authorization": f"Bearer {farmer_token}"},
        )
        prod2 = resp2.json()

        resp = client.post(
            "/orders",
            json={
                "items": [
                    {"product_id": product["id"], "qty": 2},
                    {"product_id": prod2["id"], "qty": 5},
                ]
            },
            headers={"Authorization": f"Bearer {consumer_token}"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert len(data["items"]) == 2
        expected_total = 2 * 2500.0 + 5 * 1800.0  # 5000 + 9000 = 14000
        assert data["total"] == expected_total


class TestCheckoutErrors:
    """Checkout error cases: auth, role, stock."""

    def test_unauthenticated(self, client: TestClient) -> None:
        resp = client.post(
            "/orders", json={"items": [{"product_id": 1, "qty": 1}]}
        )
        assert resp.status_code == 401

    def test_only_consumer_can_order(
        self, client: TestClient, farmer_token: str
    ) -> None:
        resp = client.post(
            "/orders",
            json={"items": [{"product_id": 1, "qty": 1}]},
            headers={"Authorization": f"Bearer {farmer_token}"},
        )
        assert resp.status_code == 403
        assert "Only consumers" in resp.text

    def test_insufficient_stock(
        self, client: TestClient, product: dict[str, Any], consumer_token: str
    ) -> None:
        resp = client.post(
            "/orders",
            json={"items": [{"product_id": product["id"], "qty": 999}]},
            headers={"Authorization": f"Bearer {consumer_token}"},
        )
        assert resp.status_code == 400
        assert "Unavailable" in resp.text

    def test_nonexistent_product(
        self, client: TestClient, consumer_token: str
    ) -> None:
        resp = client.post(
            "/orders",
            json={"items": [{"product_id": 99999, "qty": 1}]},
            headers={"Authorization": f"Bearer {consumer_token}"},
        )
        assert resp.status_code == 400
        assert "Unavailable" in resp.text
