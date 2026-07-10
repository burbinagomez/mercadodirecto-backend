"""Product listing model."""
from datetime import datetime

from sqlalchemy import String, Float, Integer, ForeignKey, Text, Date, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    farmer_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(100), index=True)
    price_per_kg: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(20), default="kg")
    quantity_available: Mapped[int] = mapped_column(Integer, default=0)
    quantity_reserved: Mapped[int] = mapped_column(Integer, default=0)
    harvest_date: Mapped[str] = mapped_column(Date, nullable=True)
    image_urls: Mapped[str] = mapped_column(Text, default="[]")  # JSON list
    department: Mapped[str] = mapped_column(String(100), index=True, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=datetime.utcnow
    )
