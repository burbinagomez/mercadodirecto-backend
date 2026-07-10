"""Cart endpoints — server-side cart persisted per consumer."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.order import CartItem
from app.models.product import Product
from app.routers.auth import get_current_user
from app.schemas.order import CartItemIn

router = APIRouter(prefix="/cart", tags=["cart"])


def _require_buyer(current):
    if current.role not in ("consumer", "restaurant"):
        raise HTTPException(status_code=403, detail="Only consumers and restaurants have a cart")
    return current


@router.get("")
def view_cart(current=Depends(get_current_user), db: Session = Depends(get_db)):
    _require_buyer(current)
    rows = db.query(CartItem).filter(CartItem.consumer_id == current.id).all()
    items, total = [], 0.0
    for row in rows:
        product = db.get(Product, row.product_id)
        if not product:
            continue
        line = product.price_per_kg * row.qty
        total += line
        items.append({
            "product_id": row.product_id,
            "name": product.name,
            "qty": row.qty,
            "unit_price": product.price_per_kg,
            "line_total": line,
        })
    return {"items": items, "total": round(total, 2)}


@router.post("/items")
def add_to_cart(
    payload: CartItemIn,
    current=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_buyer(current)
    product = db.get(Product, payload.product_id)
    if not product or product.quantity_available < payload.qty:
        raise HTTPException(status_code=400, detail=f"Unavailable: {payload.product_id}")
    existing = (
        db.query(CartItem)
        .filter(CartItem.consumer_id == current.id, CartItem.product_id == payload.product_id)
        .first()
    )
    if existing:
        existing.qty += payload.qty
    else:
        db.add(CartItem(consumer_id=current.id, product_id=payload.product_id, qty=payload.qty))
    db.commit()
    return {"ok": True}


@router.put("/items/{product_id}")
def update_cart_item(
    product_id: int,
    payload: CartItemIn,
    current=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_buyer(current)
    item = (
        db.query(CartItem)
        .filter(CartItem.consumer_id == current.id, CartItem.product_id == product_id)
        .first()
    )
    if not item:
        raise HTTPException(status_code=404, detail="Not in cart")
    if payload.qty <= 0:
        db.delete(item)
    else:
        item.qty = payload.qty
    db.commit()
    return {"ok": True}


@router.delete("/items/{product_id}")
def remove_cart_item(
    product_id: int,
    current=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_buyer(current)
    db.query(CartItem).filter(
        CartItem.consumer_id == current.id, CartItem.product_id == product_id
    ).delete()
    db.commit()
    return {"ok": True}


@router.post("/validate")
def validate_cart(
    items: list[CartItemIn],
    current=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_buyer(current)
    total = 0.0
    for item in items:
        product = db.get(Product, item.product_id)
        if not product or product.quantity_available < item.qty:
            raise HTTPException(status_code=400, detail=f"Unavailable: {item.product_id}")
        total += product.price_per_kg * item.qty
    return {"valid": True, "total": round(total, 2)}
