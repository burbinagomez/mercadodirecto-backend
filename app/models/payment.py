"""Payment records linking internal orders to VelaFi transactions."""
from datetime import datetime

from sqlalchemy import String, Float, Integer, ForeignKey, Text, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    consumer_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    method: Mapped[str] = mapped_column(String(20), default="fiat_to_fiat")  # fiat_to_fiat | stablecoin
    status: Mapped[str] = mapped_column(String(20), default="created")  # created | paid | failed | refunded
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    currency: Mapped[str] = mapped_column(String(10), default="COP")
    velafi_order_id: Mapped[str] = mapped_column(String(64), nullable=True, index=True)
    velafi_payment_link: Mapped[str] = mapped_column(Text, nullable=True)
    reference: Mapped[str] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=datetime.utcnow
    )
