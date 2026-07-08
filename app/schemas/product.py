"""Pydantic schemas for products."""
from pydantic import BaseModel
from typing import Optional


class ProductIn(BaseModel):
    name: str
    category: str
    price_per_kg: float
    unit: str = "kg"
    quantity_available: int = 0
    harvest_date: Optional[str] = None
    image_urls: list[str] = []
    department: str = ""


class ProductOut(BaseModel):
    id: int
    farmer_id: int
    name: str
    category: str
    price_per_kg: float
    unit: str
    quantity_available: int
    harvest_date: Optional[str]
    image_urls: list[str]
    department: str
