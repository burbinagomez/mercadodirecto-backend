"""Consumer profile endpoints."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import ConsumerProfile
from app.routers.auth import get_current_user
from app.schemas.user import ConsumerProfileIn

router = APIRouter(prefix="/consumers", tags=["consumers"])


@router.post("/me")
def upsert_profile(
    payload: ConsumerProfileIn,
    current=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if current.role != "consumer":
        raise HTTPException(status_code=403, detail="Only consumers")
    prof = db.get(ConsumerProfile, current.id)
    if not prof:
        prof = ConsumerProfile(user_id=current.id)
        db.add(prof)
    for k, v in payload.model_dump().items():
        setattr(prof, k, v)
    db.commit()
    db.refresh(prof)
    return prof


@router.get("/me")
def get_profile(current=Depends(get_current_user), db: Session = Depends(get_db)):
    prof = db.get(ConsumerProfile, current.id)
    if not prof:
        raise HTTPException(status_code=404, detail="Profile not found")
    return prof
