"""Public gallery of past-event photos.

Photos are stored as base64 data URLs (same pattern as event images).
GET is public; POST/DELETE are used by the admin dashboard.
"""

from datetime import datetime, timezone
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from backend.database import get_db
from backend.models import GalleryPhoto
from pydantic import BaseModel

router = APIRouter()
logger = logging.getLogger("noah.gallery")


class GalleryCreate(BaseModel):
    title: str = ""
    image: str


@router.get("/")
def list_photos(db: Session = Depends(get_db)):
    photos = db.query(GalleryPhoto).order_by(GalleryPhoto.id.desc()).all()
    return [
        {
            "id": p.id,
            "title": p.title or "",
            "image": p.image,
            "created_at": p.created_at or "",
        }
        for p in photos
    ]


@router.post("/")
def add_photo(data: GalleryCreate, db: Session = Depends(get_db)):
    title = (data.title or "").strip()
    image = (data.image or "").strip()
    if not image.startswith("data:image"):
        raise HTTPException(status_code=400, detail="A valid image is required")
    try:
        photo = GalleryPhoto(
            title=title,
            image=image,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        db.add(photo)
        db.commit()
        db.refresh(photo)
        return {"id": photo.id, "title": photo.title, "created_at": photo.created_at}
    except Exception as e:
        db.rollback()
        logger.exception("Failed to upload gallery photo")
        raise HTTPException(status_code=500, detail=f"Could not save photo: {e}")


@router.delete("/{photo_id}")
def delete_photo(photo_id: int, db: Session = Depends(get_db)):
    photo = db.query(GalleryPhoto).filter(GalleryPhoto.id == photo_id).first()
    if not photo:
        raise HTTPException(status_code=404, detail="Photo not found")
    db.delete(photo)
    db.commit()
    return {"status": "ok"}
