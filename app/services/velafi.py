"""VelaFi (V2) payments client.

Wraps the VelaFi REST API: fiat-to-fiat orders, stablecoin payment links,
order confirmation/retrieval, webhook subscription, and webhook signature
verification. Reference: ~/.hermes/profiles/backend-coder/skills/velafi-payments.
"""
from __future__ import annotations

import binascii
import hashlib
from typing import Any

import httpx
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import load_pem_public_key

from app.core.config import settings


class VelaFiError(RuntimeError):
    pass


class VelaFiClient:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 30.0,
    ):
        self.base_url = (base_url or settings.velafi_base_url).rstrip("/")
        self.api_key = api_key if api_key is not None else settings.velafi_api_key
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            headers={
                "X-BH-TOKEN": self.api_key,
                "Content-Type": "application/json",
            },
        )

    def _post(self, path: str, json: dict[str, Any]) -> dict[str, Any]:
        if not self.api_key:
            raise VelaFiError("VELAFI_API_KEY is not configured")
        resp = self._client.post(path, json=json)
        try:
            data = resp.json()
        except Exception:
            raise VelaFiError(f"Non-JSON response {resp.status_code}: {resp.text[:200]}")
        if data.get("code") != 200 or data.get("msg") != "SUCCESS":
            raise VelaFiError(f"VelaFi error {data.get('code')}: {data.get('msg')} {data.get('data')}")
        return data

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self.api_key:
            raise VelaFiError("VELAFI_API_KEY is not configured")
        resp = self._client.get(path, params=params)
        data = resp.json()
        if data.get("code") != 200 or data.get("msg") != "SUCCESS":
            raise VelaFiError(f"VelaFi error {data.get('code')}: {data.get('msg')}")
        return data

    # --- Orders ---
    def create_fiat_to_fiat_order(self, **params: Any) -> int:
        """Returns VelaFi orderId."""
        data = self._post("/v2/order/fiat_to_fiat", params)
        return int(data["data"]["orderId"])

    def confirm_order(self, order_id: int, order_type: str, direction: str | None = None) -> bool:
        body = {"orderId": order_id, "orderType": order_type}
        if direction:
            body["direction"] = direction
        return bool(self._post("/v2/order/confirm", body)["data"])

    def get_order(self, order_id: int, order_type: str) -> dict[str, Any]:
        return self._get("/v2/order/detail", {"orderId": order_id, "orderType": order_type})["data"]

    # --- Stablecoin payment links ---
    def create_payment_link(self, **params: Any) -> dict[str, Any]:
        return self._post("/v2/payments/link", params)["data"]

    # --- Webhooks ---
    def register_webhook(self, event_type: str, url: str) -> dict[str, Any]:
        return self._post("/v2/webhook", {"eventType": event_type, "url": url})["data"]

    @staticmethod
    def verify_webhook(raw_body: bytes, signature_hex: str, public_key_pem: str | None = None) -> bool:
        """Verify a VelaFi webhook: RSA-SHA256 of the raw body vs hex signature."""
        pem = public_key_pem or settings.velafi_webhook_public_key
        if not pem:
            # No key configured yet (e.g. local dev) — fail closed but allow opt-out.
            return False
        pub = load_pem_public_key(pem.encode())
        try:
            pub.verify(
                binascii.unhexlify(signature_hex),
                raw_body,
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
            return True
        except Exception:
            return False

    def close(self):
        self._client.close()


def get_velafi() -> VelaFiClient:
    return VelaFiClient()
