"""Pydantic schemas for cart + orders."""
from pydantic import BaseModel
from typing import Optional


class CartItemIn(BaseModel):
    product_id: int
    qty: int = 1


class CheckoutRequest(BaseModel):
    items: list[CartItemIn]


class OrderItemOut(BaseModel):
    product_id: int
    qty: int
    price: float


class OrderOut(BaseModel):
    id: int
    consumer_id: int
    status: str
    total: float
    items: list[OrderItemOut] = []
