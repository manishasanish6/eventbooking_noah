from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session
from backend.database import get_db
from backend.models import Event
from pydantic import BaseModel
from typing import Optional, List

router = APIRouter()

class AddonSchema(BaseModel):
    id: str
    name: str
    type: str           # "per_seat" | "per_booking"
    price: float
    description: Optional[str] = ""

class ZoneSchema(BaseModel):
    name: str
    rows: int
    price: float
    color: str
    promotions: Optional[dict] = None
    seat_prefix: Optional[str] = None
    seat_start: Optional[int] = None
    max_seats: Optional[int] = None
    walkway_mode: Optional[str] = "none"
    walkway_width: Optional[int] = 1
    walkway_numbering: Optional[str] = "flow"

class EventCreate(BaseModel):
    name: str
    artist: Optional[str] = ""
    venue: str
    date: str
    time: Optional[str] = "20:00"
    end_time: Optional[str] = ""
    genre: Optional[str] = "Other"
    status: Optional[str] = "draft"
    rows: int
    cols: int
    zones: List[ZoneSchema]
    held_seats: Optional[list] = []
    layout_mode: Optional[str] = "auto"
    seat_layout: Optional[list] = []
    venue_address: Optional[dict] = None
    addons: Optional[list] = []
    created_by: Optional[str] = ""
    editors: Optional[list] = []
    images: Optional[list] = []
    artist_bio: Optional[str] = ""
    artist_photo: Optional[str] = ""
    artist_instagram: Optional[str] = ""
    artist_facebook: Optional[str] = ""
    artist_website: Optional[str] = ""
    artist_details: Optional[str] = ""
    organiser_name: Optional[str] = ""
    organiser_contact: Optional[str] = ""
    organiser_email: Optional[str] = ""
    organiser_logo: Optional[str] = ""
    organiser_address: Optional[str] = ""
    organiser_summary: Optional[str] = ""

@router.get("/")
def list_events(accessible_by: Optional[str] = None, db: Session = Depends(get_db)):
    all_events = db.query(Event).all()
    if accessible_by:
        result = []
        for ev in all_events:
            editors = ev.editors or []
            if ev.created_by == accessible_by or accessible_by in editors or not ev.created_by:
                result.append(ev)
        return jsonable_encoder(result)
    return jsonable_encoder(all_events)

@router.get("/{event_id}")
def get_event(event_id: int, db: Session = Depends(get_db)):
    ev = db.query(Event).filter(Event.id == event_id).first()
    if not ev:
        raise HTTPException(status_code=404, detail="Event not found")
    return jsonable_encoder(ev)

@router.post("/")
def create_event(data: EventCreate, db: Session = Depends(get_db)):
    ev = Event(
        name=data.name, artist=data.artist, venue=data.venue,
        date=data.date, time=data.time, end_time=data.end_time,
        genre=data.genre, status=data.status,
        rows=data.rows, cols=data.cols,
        total_seats=data.rows * data.cols if data.layout_mode != "manual" else len(data.seat_layout or []),
        zones=[z.dict() for z in data.zones],
        booked_seats=[], held_seats=data.held_seats or [],
        layout_mode=data.layout_mode or "auto",
        seat_layout=data.seat_layout or [],
        venue_address=data.venue_address,
        addons=data.addons or [],
        created_by=data.created_by or "",
        editors=data.editors or [],
        images=data.images or [],
        artist_bio=data.artist_bio or "",
        artist_photo=data.artist_photo or "",
        artist_instagram=data.artist_instagram or "",
        artist_facebook=data.artist_facebook or "",
        artist_website=data.artist_website or "",
        artist_details=data.artist_details or "",
        organiser_name=data.organiser_name or "",
        organiser_contact=data.organiser_contact or "",
        organiser_email=data.organiser_email or "",
        organiser_logo=data.organiser_logo or "",
        organiser_address=data.organiser_address or "",
        organiser_summary=data.organiser_summary or ""
    )
    db.add(ev); db.commit(); db.refresh(ev)
    return jsonable_encoder(ev)

@router.put("/{event_id}")
def update_event(event_id: int, data: EventCreate, db: Session = Depends(get_db)):
    ev = db.query(Event).filter(Event.id == event_id).first()
    if not ev:
        raise HTTPException(status_code=404, detail="Not found")
    for k, v in data.dict().items():
        if k == "zones":
            setattr(ev, "zones", [z if isinstance(z, dict) else z.dict() for z in v])
        elif k in ("layout_mode", "seat_layout", "venue_address", "addons", "editors", "images"):
            setattr(ev, k, v)
        else:
            setattr(ev, k.replace("cols","cols").replace("rows","rows"), v)
    ev.total_seats = data.rows * data.cols if data.layout_mode != "manual" else len(data.seat_layout or [])
    db.commit(); db.refresh(ev)
    return jsonable_encoder(ev)

@router.patch("/{event_id}/status")
def toggle_status(event_id: int, status: str, db: Session = Depends(get_db)):
    ev = db.query(Event).filter(Event.id == event_id).first()
    if not ev:
        raise HTTPException(status_code=404, detail="Not found")
    ev.status = status
    db.commit()
    return {"status": ev.status}

@router.delete("/{event_id}")
def delete_event(event_id: int, db: Session = Depends(get_db)):
    ev = db.query(Event).filter(Event.id == event_id).first()
    if not ev:
        raise HTTPException(status_code=404, detail="Not found")
    db.delete(ev); db.commit()
    return {"deleted": True}