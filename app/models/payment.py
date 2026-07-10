"""Payment records linking internal orders to VelaFi / Mono transactions.

Models
------
- Payment        — Payin record (VelaFi or Mono PSE collection)
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import Float, ForeignKey, Integer, String, Text, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class PaymentMethod(str, Enum):
    MONO = "mono"
    STABLECOIN = "stablecoin"


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    consumer_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    method: Mapped[str] = mapped_column(String(20), default="mono")  # mono | stablecoin
    status: Mapped[str] = mapped_column(String(20), default="created")  # created | paid | failed | refunded
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    currency: Mapped[str] = mapped_column(String(10), default="COP")

    # VelaFi (stablecoin) fields
    velafi_order_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    velafi_payment_link: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Mono (PSE) fields
    mono_intent_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    mono_transfer_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    reference: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=datetime.utcnow
    )

    payouts: Mapped[list["FarmerPayout"]] = relationship(
        back_populates="payment", cascade="all, delete-orphan"
    )
