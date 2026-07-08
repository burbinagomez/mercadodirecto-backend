"""Farmer profile endpoints."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import FarmerProfile
from app.routers.auth import get_current_user
from app.schemas.user import FarmerProfileIn

router = APIRouter(prefix="/farmers", tags=["farmers"])


@router.post("/me")
def upsert_profile(
    payload: FarmerProfileIn,
    current=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if current.role != "farmer":
        raise HTTPException(status_code=403, detail="Only farmers")
    prof = db.get(FarmerProfile, current.id)
    if not prof:
        prof = FarmerProfile(user_id=current.id)
        db.add(prof)
    for k, v in payload.model_dump().items():
        setattr(prof, k, v)
    db.commit()
    db.refresh(prof)
    return prof


@router.get("/me")
def get_profile(current=Depends(get_current_user), db: Session = Depends(get_db)):
    prof = db.get(FarmerProfile, current.id)
    if not prof:
        raise HTTPException(status_code=404, detail="Profile not found")
    return prof
