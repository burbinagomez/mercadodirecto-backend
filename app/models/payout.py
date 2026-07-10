"""Farmer bank accounts and payout tracking."""

from sqlalchemy import String, Float, Integer, ForeignKey, Text, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class FarmerBankAccount(Base):
    __tablename__ = "farmer_bank_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    bank_name: Mapped[str] = mapped_column(String(255))
    account_number: Mapped[str] = mapped_column(String(255))
    account_type: Mapped[str] = mapped_column(String(50))
    verified: Mapped[bool] = mapped_column(Boolean, default=False)


class FarmerPayout(Base):
    __tablename__ = "farmer_payouts"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    farmer_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    amount: Mapped[float] = mapped_column(Float)
    farmer_bank_account_id: Mapped[int] = mapped_column(ForeignKey("farmer_bank_accounts.id"))
    mono_transfer_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | paid | failed
    reference: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[str] = mapped_column(Text, default="now()")
