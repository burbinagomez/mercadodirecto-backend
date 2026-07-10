"""Payment records linking internal orders to payment providers + farmer payouts."""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import String, Float, Integer, ForeignKey, Text, DateTime, func, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class PaymentMethod(str, enum.Enum):
    """Supported payment methods."""

    MONO = "mono"
    STABLECOIN = "stablecoin"


class Payment(Base):
    """A payment attempt linking an internal order to a provider transaction."""

    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    consumer_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    method: Mapped[str] = mapped_column(
        String(20), default=PaymentMethod.MONO.value
    )  # mono | stablecoin
    status: Mapped[str] = mapped_column(
        String(20), default="created"
    )  # created | paid | failed | refunded
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    currency: Mapped[str] = mapped_column(String(10), default="COP")
    reference: Mapped[str] = mapped_column(String(64), nullable=True)

    # VelaFi (stablecoin) fields
    velafi_order_id: Mapped[str] = mapped_column(String(64), nullable=True, index=True)
    velafi_payment_link: Mapped[str] = mapped_column(Text, nullable=True)

    # Mono fields
    mono_intent_id: Mapped[str] = mapped_column(String(64), nullable=True, index=True)
    mono_transfer_id: Mapped[str] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=datetime.utcnow
    )


class FarmerBankAccount(Base):
    """A farmer's bank account for receiving payouts via Mono Transfers."""

    __tablename__ = "farmer_bank_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    farmer_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), index=True, nullable=False
    )
    bank_name: Mapped[str] = mapped_column(String(200))
    account_number: Mapped[str] = mapped_column(String(50))
    account_type: Mapped[str] = mapped_column(
        String(30), default="savings"
    )  # savings | checking
    verified: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=datetime.utcnow
    )


class FarmerPayout(Base):
    """A pending/paid/failed payout to a farmer for an order.

    Created automatically when an order is paid via Mono and the funds
    are transferred to the farmer's bank account.
    """

    __tablename__ = "farmer_payouts"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    farmer_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    farmer_bank_account_id: Mapped[int] = mapped_column(
        ForeignKey("farmer_bank_accounts.id"), nullable=True
    )
    mono_transfer_id: Mapped[str] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), default="pending"
    )  # pending | paid | failed
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=datetime.utcnow
    )
