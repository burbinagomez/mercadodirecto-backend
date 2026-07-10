"""Pydantic schemas for products."""
import json
from pydantic import BaseModel, field_validator
from typing import Optional


class ProductIn(BaseModel):
    name: str
    category: str
    price_per_kg: float
    unit: str = "kg"
    quantity_available: int = 0
    quantity_reserved: int = 0
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
    quantity_reserved: int
    harvest_date: Optional[str]
    image_urls: list[str]
    department: str

    @field_validator("image_urls", mode="before")
    @classmethod
    def parse_image_urls(cls, v):
        if isinstance(v, str):
            try:
                return json.loads(v)
            except (json.JSONDecodeError, TypeError):
                return []
        return v if isinstance(v, list) else []
