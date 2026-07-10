"""Payment provider dispatch — returns the right client for a given method.

Usage::

    from app.services.providers import get_provider

    client = get_provider("mono")
    intent = client.create_collection_intent(...)
"""

from __future__ import annotations

from app.services.base_client import BasePaymentClient


def get_provider(method: str) -> BasePaymentClient:
    """Return a payment-client instance matching *method*.

    Parameters
    ----------
    method : str
        One of ``"mono"`` or ``"stablecoin"``.

    Returns
    -------
    BasePaymentClient
        A configured client instance (``MonoClient`` or ``VelaFiClient``).

    Raises
    ------
    ValueError
        If *method* is not a recognised provider.
    """
    # Import PaymentMethod at runtime so this module loads independently
    # of the model layer (avoids import-order issues).
    try:
        from app.models.payment import PaymentMethod
    except ImportError:
        # Fallback when the model enum is not yet available (T1 not merged).
        pass
    else:
        # Validate against the enum when it exists.
        if method not in PaymentMethod._value2member_map_:
            valid = ", ".join(m.value for m in PaymentMethod)
            raise ValueError(f"Unknown payment method {method!r}; expected {valid}")

    if method == "mono":
        from app.services.mono import MonoClient

        return MonoClient()
    elif method == "stablecoin":
        from app.services.velafi import VelaFiClient

        return VelaFiClient()
    else:
        raise ValueError(
            f"Unknown payment method {method!r}; expected 'mono' or 'stablecoin'"
        )
