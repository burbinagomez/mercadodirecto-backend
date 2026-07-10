"""Order + checkout endpoints."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.order import Order, OrderItem
from app.models.product import Product
from app.routers.auth import get_current_user
from app.schemas.order import CheckoutRequest, OrderOut

router = APIRouter(prefix="/orders", tags=["orders"])


@router.post("", response_model=OrderOut)
def checkout(
    payload: CheckoutRequest,
    current=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if current.role not in ("consumer", "restaurant"):
        raise HTTPException(status_code=403, detail="Only consumers and restaurants can order")
    total = 0.0
    order = Order(consumer_id=current.id, status="pending", total=0.0)
    db.add(order)
    db.flush()
    for item in payload.items:
        product = db.get(Product, item.product_id)
        if not product or product.quantity_available < item.qty:
            raise HTTPException(status_code=400, detail=f"Unavailable: {item.product_id}")
        line = product.price_per_kg * item.qty
        total += line
        db.add(OrderItem(order_id=order.id, product_id=item.product_id, qty=item.qty, price=line))
        product.quantity_available -= item.qty
    order.total = total
    db.commit()
    db.refresh(order)
    return order


@router.get("/mine", response_model=list[OrderOut])
def my_orders(current=Depends(get_current_user), db: Session = Depends(get_db)):
    return db.query(Order).filter(Order.consumer_id == current.id).all()


@router.get("/farmer", response_model=list[OrderOut])
def farmer_orders(current=Depends(get_current_user), db: Session = Depends(get_db)):
    if current.role != "farmer":
        raise HTTPException(status_code=403, detail="Only farmers")
    # Orders containing this farmer's products
    rows = (
        db.query(Order)
        .join(OrderItem, OrderItem.order_id == Order.id)
        .join(Product, Product.id == OrderItem.product_id)
        .filter(Product.farmer_id == current.id)
        .distinct()
        .all()
    )
    return rows
