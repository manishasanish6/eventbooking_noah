import logging
from datetime import date as dt_date
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session
from backend.database import get_db
from backend.models import Event, Booking
from backend.utils.email import send_booking_confirmation
from pydantic import BaseModel
from typing import List, Optional

logger = logging.getLogger("noah.bookings")
router = APIRouter()

class BookingCreate(BaseModel):
    event_id: int
    seats: List[str]
    user_id: Optional[int] = None
    email: str
    addons: Optional[list] = None

def _calc_addon_total(ev, addon_ids, seat_count):
    """Return (addon_total, addon_items) for selected addons."""
    total = 0
    items = []
    for a in (ev.addons or []):
        if a["id"] in addon_ids:
            if a["type"] == "per_seat":
                cost = round(float(a["price"]) * seat_count, 2)
            else:
                cost = round(float(a["price"]), 2)
            total += cost
            items.append({"name": a["name"], "cost": cost, "type": a["type"]})
    return round(total, 2), items

def _calc_total(ev, seats, addon_ids=None):
    today = dt_date.today()

    # Manual mode: look up zone per seat from seat_layout
    if getattr(ev, "layout_mode", "auto") == "manual":
        layout_map = {}
        for s in (ev.seat_layout or []):
            layout_map[s["id"]] = s

        zone_list = ev.zones or []
        zone_by_name = {z["name"]: z for z in zone_list}

        zone_seats = {}
        for sid in seats:
            seat_data = layout_map.get(sid)
            if not seat_data:
                continue
            zn = seat_data.get("zone", zone_list[0]["name"] if zone_list else "General")
            zone = zone_by_name.get(zn, zone_list[0] if zone_list else None)
            if not zone:
                continue
            promos = zone.get("promotions") or {}
            eb = promos.get("early_bird") or {}
            price = float(zone["price"])
            if eb.get("enabled") and eb.get("price"):
                try:
                    ok = True
                    if eb.get("starts_at"):
                        ok = ok and dt_date.fromisoformat(eb["starts_at"]) <= today
                    if eb.get("ends_at"):
                        ok = ok and today <= dt_date.fromisoformat(eb["ends_at"])
                    if ok:
                        price = float(eb["price"])
                except Exception:
                    pass
            zone_seats.setdefault(zn, []).append({"seat": sid, "zone": zone, "price": price, "index": 0})

        raw_total = 0
        discount_items = []
        final_subtotal = 0
        for zn, items in zone_seats.items():
            zone = items[0]["zone"]
            promos = zone.get("promotions") or {}
            zone_raw = sum(float(items[0]["zone"]["price"]) for _ in items)
            raw_total += zone_raw
            z_eb = sum(it["price"] for it in items)
            gb = promos.get("group_buy") or {}
            z_discounted = z_eb
            if gb.get("enabled") and gb.get("discount_percent"):
                mn = int(gb.get("min_seats", 0))
                mx = int(gb.get("max_seats", 999))
                if mn <= len(items) <= mx:
                    pct = float(gb["discount_percent"])
                    amt = round(z_eb * pct / 100, 2)
                    z_discounted = round(z_eb - amt, 2)
                    discount_items.append({"label": f"{zn} Group Buy", "amount": amt})
            final_subtotal += z_discounted
        # Addon costs
        addon_total, addon_items = _calc_addon_total(ev, addon_ids or [], len(seats))
        final_subtotal = round(final_subtotal + addon_total, 2)
        fee = round(final_subtotal * 0.08, 2)
        total = round(final_subtotal + fee, 2)
        return total, final_subtotal, fee, discount_items, raw_total, addon_total, addon_items

    # Auto mode: prefix matching
    all_zones = list(ev.zones or [])
    zone_prefixes = sorted(
        [(z.get("seat_prefix") or "", i) for i, z in enumerate(all_zones)],
        key=lambda x: -len(x[0])
    )

    def find_zone(sid):
        for prefix, zi in zone_prefixes:
            if prefix and sid.startswith(prefix):
                return all_zones[zi]
        return all_zones[-1] if all_zones else None

    # Group seats by zone and compute per-zone price
    zone_seats = {}
    for s in seats:
        zone = find_zone(s)
        if not zone:
            continue
        zn = zone["name"]

        # Per-zone Early Bird
        promos = zone.get("promotions") or {}
        eb = promos.get("early_bird") or {}
        price = float(zone["price"])
        if eb.get("enabled") and eb.get("price"):
            try:
                ok = True
                if eb.get("starts_at"):
                    ok = ok and dt_date.fromisoformat(eb["starts_at"]) <= today
                if eb.get("ends_at"):
                    ok = ok and today <= dt_date.fromisoformat(eb["ends_at"])
                if ok:
                    price = float(eb["price"])
            except Exception:
                pass

        zone_seats.setdefault(zn, []).append({"seat": s, "zone": zone, "price": price, "index": 0})

    raw_total = 0
    discount_items = []
    final_subtotal = 0

    for zn, items in zone_seats.items():
        zone = items[0]["zone"]
        promos = zone.get("promotions") or {}
        zone_raw = sum(float(it["zone"]["price"]) for it in items)
        raw_total += zone_raw

        z_eb = sum(it["price"] for it in items)  # after early bird

        # Per-zone Group Buy
        gb = promos.get("group_buy") or {}
        z_discounted = z_eb
        if gb.get("enabled") and gb.get("discount_percent"):
            mn = int(gb.get("min_seats", 0))
            mx = int(gb.get("max_seats", 999))
            if mn <= len(items) <= mx:
                pct = float(gb["discount_percent"])
                amt = round(z_eb * pct / 100, 2)
                z_discounted = round(z_eb - amt, 2)
                discount_items.append({"label": f"{zn} Group Buy", "amount": amt})

        final_subtotal += z_discounted

    # Addon costs
    addon_total, addon_items = _calc_addon_total(ev, addon_ids or [], len(seats))
    final_subtotal = round(final_subtotal + addon_total, 2)
    fee = round(final_subtotal * 0.08, 2)
    total = round(final_subtotal + fee, 2)
    return total, final_subtotal, fee, discount_items, raw_total, addon_total, addon_items

@router.post("/")
def create_booking(data: BookingCreate, bg: BackgroundTasks, db: Session = Depends(get_db)):
    ev = db.query(Event).filter(Event.id == data.event_id).first()
    if not ev:
        raise HTTPException(status_code=404, detail="Event not found")

    booked = ev.booked_seats or []
    held = ev.held_seats or []
    conflicts = [s for s in data.seats if s in booked]
    held_conflicts = [s for s in data.seats if s in held]
    if conflicts:
        raise HTTPException(status_code=409, detail=f"Seats already taken: {conflicts}")
    if held_conflicts:
        raise HTTPException(status_code=409, detail=f"Seats are held and unavailable: {held_conflicts}")

    # Per-zone max_seats validation
    all_zones = list(ev.zones or [])
    zone_prefixes = sorted(
        [(z.get("seat_prefix") or "", z) for z in all_zones],
        key=lambda x: -len(x[0])
    )
    seat_zones = {}
    for s in data.seats:
        matched = None
        for prefix, z in zone_prefixes:
            if prefix and s.startswith(prefix):
                matched = z
                break
        if matched:
            zn = matched["name"]
            max_s = matched.get("max_seats")
            if zn not in seat_zones:
                seat_zones[zn] = {"count": 0, "max": max_s}
            seat_zones[zn]["count"] += 1
            if seat_zones[zn]["max"] and seat_zones[zn]["count"] > seat_zones[zn]["max"]:
                raise HTTPException(status_code=400, detail=f"Max {max_s} seats in {zn}")

    total, subtotal, fee, discount_items, raw_subtotal, addon_total, addon_items = _calc_total(ev, data.seats, data.addons)

    ev.booked_seats = booked + data.seats
    db.add(ev)

    booking = Booking(
        event_id=data.event_id, seats=data.seats,
        total_paid=total, status="confirmed",
        user_id=data.user_id, email=data.email,
        addons=data.addons or []
    )
    db.add(booking); db.commit(); db.refresh(booking)

    ticket_codes = {s: f"NOAH-{booking.id}-{s}" for s in data.seats}

    bg.add_task(
        send_booking_confirmation,
        to_email=data.email,
        event_name=ev.name,
        seats=data.seats,
        total=total,
        venue=ev.venue or "N/A",
        venue_address=ev.venue_address,
        date=ev.date or "N/A",
        time=ev.time or "",
        booking_id=booking.id,
        ticket_codes=ticket_codes,
        addon_items=addon_items
    )

    return {"booking_id": booking.id, "seats": data.seats, "total": total, "status": "confirmed", "addon_total": addon_total}

@router.get("/")
def list_bookings(db: Session = Depends(get_db)):
    return db.query(Booking).all()

@router.get("/event/{event_id}")
def event_bookings(event_id: int, db: Session = Depends(get_db)):
    return db.query(Booking).filter(Booking.event_id == event_id).all()

@router.get("/user/{user_id}")
def user_bookings(user_id: int, db: Session = Depends(get_db)):
    return db.query(Booking).filter(Booking.user_id == user_id).all()

@router.get("/status/{status}")
def bookings_by_status(status: str, db: Session = Depends(get_db)):
    return db.query(Booking).filter(Booking.status == status).all()