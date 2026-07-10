"""Farmer bank accounts and payout tracking with retry support."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class FarmerBankAccount(Base):
    """A farmer's registered bank account for receiving payouts via Mono Transfers."""

    __tablename__ = "farmer_bank_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    bank_name: Mapped[str] = mapped_column(String(255))
    account_number: Mapped[str] = mapped_column(String(255))
    account_type: Mapped[str] = mapped_column(String(50))
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=datetime.utcnow
    )


class FarmerPayout(Base):
    """A payout from the platform to a farmer via Mono Transfers.

    Created when a Payment transitions to ``paid`` (Mono PSE collection
    credited).  A ``create_transfer`` call is made to Mono; status tracks
    the outcome and supports automatic retries via
    :mod:`app.services.payout_worker`.
    """

    __tablename__ = "farmer_payouts"

    id: Mapped[int] = mapped_column(primary_key=True)
    payment_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("payments.id"), nullable=True, index=True
    )
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    farmer_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    farmer_bank_account_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("farmer_bank_accounts.id"), nullable=True
    )
    amount: Mapped[float] = mapped_column(Float)
    mono_transfer_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), default="pending"
    )  # pending | paid | failed
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    reference: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=datetime.utcnow
    )

    payment: Mapped[Optional["Payment"]] = relationship(back_populates="payouts")
