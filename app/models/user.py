"""User + role profile models."""
from sqlalchemy import String, Enum as SAEnum, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(SAEnum("farmer", "consumer", name="user_role"))
    created_at: Mapped[str] = mapped_column(Text, default="now()")

    farmer_profile: Mapped["FarmerProfile"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    consumer_profile: Mapped["ConsumerProfile"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )


class FarmerProfile(Base):
    __tablename__ = "farmer_profiles"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    farm_name: Mapped[str] = mapped_column(String(200))
    department: Mapped[str] = mapped_column(String(100))  # e.g. Antioquia, Cundinamarca
    city: Mapped[str] = mapped_column(String(100))
    bio: Mapped[str] = mapped_column(Text, default="")
    produces: Mapped[str] = mapped_column(Text, default="")  # comma-separated list

    user: Mapped["User"] = relationship(back_populates="farmer_profile")


class ConsumerProfile(Base):
    __tablename__ = "consumer_profiles"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    full_name: Mapped[str] = mapped_column(String(200), default="")
    address: Mapped[str] = mapped_column(Text, default="")

    user: Mapped["User"] = relationship(back_populates="consumer_profile")
