"""Mono Banking client — PSE collection (payin) + Transfers (payout).

Reference
---------
- PSE collection: https://docs.mono.la/guides/banking/flows/pse-collection
- Sending transfers: https://docs.mono.la/guides/banking/flows/sending-transfers
- Webhooks (HMAC): https://docs.mono.la/guides/banking/webhooks

Assumptions (to confirm against docs.mono.la during live integration)
----------------------------------------------------------------------
- Auth header: ``X-API-KEY`` (used below; some Mono API versions use
  ``Authorization: Bearer <token>`` — verify in
  ``/docs/guides/banking/api/authentication``).
- Webhook HMAC algorithm: HMAC-SHA256 over the raw request body, compared
  against the ``X-Mono-Signature`` header value (exact header name TBD).
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Any

from app.core.config import settings
from app.services.base_client import BasePaymentClient


class MonoClient(BasePaymentClient):
    """Client for the Mono Banking API (PSE payin + Transfers payout)."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        super().__init__(
            base_url or settings.mono_base_url,
            api_key if api_key is not None else settings.mono_api_key,
            timeout,
        )
        # NOTE: X-API-KEY header — confirm against docs.mono.la
        # during live integration. Some versions use Bearer token.
        self._client.headers["X-API-KEY"] = self.api_key

    # ------------------------------------------------------------------
    # PSE collection (payin)
    # ------------------------------------------------------------------

    def create_collection_intent(
        self,
        amount: float,
        reference: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Create a PSE collection intent.

        Returns dict with keys ``intent_id`` and ``redirect_url``.

        Parameters
        ----------
        amount : float
            Amount in COP.
        reference : str
            Merchant-defined reference (e.g. ``MD-{order}-{user}``).
        idempotency_key : str
            Unique key to make the request idempotent.
        """
        data = self._post(
            "/collection_intents",
            {"amount": amount, "reference": reference},
            headers={"X-Idempotency-Key": idempotency_key},
        )
        return {
            "intent_id": data.get("intent_id", data.get("id", "")),
            "redirect_url": data.get("redirect_url", data.get("redirectUrl", "")),
        }

    # ------------------------------------------------------------------
    # Transfers (payout)
    # ------------------------------------------------------------------

    def create_transfer(
        self,
        dest_account: str,
        amount: float,
        idempotency_key: str,
        routing: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send a transfer (payout) to a destination account.

        Returns the full response dict including ``transfer_id``.

        Parameters
        ----------
        dest_account : str
            Destination account identifier (bank account number or
            account id, as specified by Mono's transfer API).
        amount : float
            Amount in COP.
        idempotency_key : str
            Unique key for idempotency.
        routing : dict | None
            Optional routing info (bank code, account type, etc.).
        """
        body: dict[str, Any] = {
            "account": dest_account,
            "amount": amount,
        }
        if routing:
            body["routing"] = routing
        return self._post(
            "/transfers",
            body,
            headers={"X-Idempotency-Key": idempotency_key},
        )

    # ------------------------------------------------------------------
    # Webhook verification (HMAC-SHA256)
    # ------------------------------------------------------------------

    def verify_webhook(self, raw_body: bytes, signature: str) -> bool:
        """Verify a Mono webhook: HMAC-SHA256 over the raw body.

        NOTE
        ----
        Assumption — exact header name and algorithm should be confirmed
        against ``docs.mono.la/banking/webhooks`` during live integration.
        We use HMAC-SHA256 with the ``mono_webhook_secret`` from settings.
        The comparison uses ``hmac.compare_digest`` (constant-time).
        """
        secret = settings.mono_webhook_secret
        if not secret:
            return False
        expected = hmac.new(
            secret.encode(),
            raw_body,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    # ------------------------------------------------------------------
    # Lifecycle (inherited from BasePaymentClient)
    # ------------------------------------------------------------------
