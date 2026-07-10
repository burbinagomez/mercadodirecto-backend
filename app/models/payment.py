"""Payment records linking internal orders to VelaFi/Mono transactions."""
from __future__ import annotations

from enum import Enum

from sqlalchemy import Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PaymentMethod(str, Enum):
    MONO = "mono"
    STABLECOIN = "stablecoin"


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    consumer_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    method: Mapped[str] = mapped_column(String(20), default=PaymentMethod.MONO.value)  # mono | stablecoin
    status: Mapped[str] = mapped_column(String(20), default="created")  # created | paid | failed | refunded
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    currency: Mapped[str] = mapped_column(String(10), default="COP")
    velafi_order_id: Mapped[str] = mapped_column(String(64), nullable=True, index=True)
    velafi_payment_link: Mapped[str] = mapped_column(Text, nullable=True)
    mono_intent_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    mono_transfer_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reference: Mapped[str] = mapped_column(String(64), nullable=True)
    created_at: Mapped[str] = mapped_column(Text, default="now()")
