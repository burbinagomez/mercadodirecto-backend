"""Product listing endpoints."""
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.product import Product
from app.routers.auth import get_current_user
from app.schemas.product import ProductIn, ProductOut

router = APIRouter(prefix="/products", tags=["products"])


@router.post("", response_model=ProductOut)
def create_product(
    payload: ProductIn,
    current=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if current.role != "farmer":
        raise HTTPException(status_code=403, detail="Only farmers can list products")
    data = payload.model_dump()
    data["image_urls"] = json.dumps(data["image_urls"])
    product = Product(farmer_id=current.id, **data)
    db.add(product)
    db.commit()
    db.refresh(product)
    return product


@router.get("", response_model=list[ProductOut])
def list_products(
    q: str | None = Query(None),
    category: str | None = Query(None),
    department: str | None = Query(None),
    db: Session = Depends(get_db),
):
    query = db.query(Product)
    if q:
        query = query.filter(Product.name.ilike(f"%{q}%"))
    if category:
        query = query.filter(Product.category == category)
    if department:
        query = query.filter(Product.department == department)
    return query.all()


@router.get("/{product_id}", response_model=ProductOut)
def get_product(product_id: int, db: Session = Depends(get_db)):
    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Not found")
    return product
