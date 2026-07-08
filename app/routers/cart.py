"""Cart endpoints (in-memory style stub for MVP)."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.product import Product
from app.routers.auth import get_current_user
from app.schemas.order import CartItemIn

router = APIRouter(prefix="/cart", tags=["cart"])

# MVP: cart lives client-side; this endpoint validates availability server-side.
@router.post("/validate")
def validate_cart(
    items: list[CartItemIn],
    current=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    total = 0.0
    for item in items:
        product = db.get(Product, item.product_id)
        if not product or product.quantity_available < item.qty:
            raise HTTPException(status_code=400, detail=f"Unavailable: {item.product_id}")
        total += product.price_per_kg * item.qty
    return {"valid": True, "total": total}
