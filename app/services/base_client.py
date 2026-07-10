"""Base payment client with shared HTTP transport and error types.

Defines BasePaymentClient (httpx.Client wrapper) and PaymentProviderError
used by all payment providers (VelaFi, Mono, etc.).
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.config import settings


class PaymentProviderError(RuntimeError):
    """Generic payment-provider error."""

    pass


class BasePaymentClient:
    """Abstract base for payment providers.

    Subclasses must call ``super().__init__()`` and then set any
    provider-specific auth headers on ``self._client.headers``.

    Subclasses **must** override :meth:`verify_webhook`.
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        # Concrete subclasses read the appropriate settings fields
        # when the caller passes None.
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            headers={"Content-Type": "application/json"},
        )

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    def _post(
        self,
        path: str,
        json: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        """POST JSON and return parsed response body.

        Extra keyword arguments are forwarded to ``httpx.Client.post``
        (e.g. ``headers=...`` for idempotency keys).
        """
        if not self.api_key:
            raise PaymentProviderError(
                f"{self.__class__.__name__} API key is not configured"
            )
        resp = self._client.post(path, json=json, **kwargs)
        try:
            return resp.json()
        except Exception:
            raise PaymentProviderError(
                f"Non-JSON response {resp.status_code}: {resp.text[:200]}"
            )

    def _get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """GET with query params and return parsed response body.

        Extra keyword arguments are forwarded to ``httpx.Client.get``.
        """
        if not self.api_key:
            raise PaymentProviderError(
                f"{self.__class__.__name__} API key is not configured"
            )
        resp = self._client.get(path, params=params, **kwargs)
        try:
            return resp.json()
        except Exception:
            raise PaymentProviderError(
                f"Non-JSON response {resp.status_code}: {resp.text[:200]}"
            )

    # ------------------------------------------------------------------
    # Webhook verification (override in subclass)
    # ------------------------------------------------------------------

    def verify_webhook(self, raw_body: bytes, signature: str) -> bool:
        """Verify a provider webhook signature.

        Subclasses **must** override this with the provider's specific
        algorithm (RSA-SHA256, HMAC-SHA256, etc.).
        """
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()
