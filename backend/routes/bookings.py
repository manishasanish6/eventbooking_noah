import logging
import re
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


def event_code_for(name: str) -> str:
    """Short uppercase code from an event name: 'NoahOnamNight' -> 'ONAM'."""
    s = re.sub(r"[^A-Za-z0-9]", "", name or "")
    if s[:4].upper() == "NOAH":
        s = s[4:]
    s = s.upper() or "EVT"
    return s[:4]


def next_booking_ref(db, event_id: int, event_name: str) -> str:
    """Per-event booking reference: NOAH-<CODE>-<seq>.

    The sequence restarts at 1 for every event and never reuses a number
    (max existing seq + 1, so deletions can't cause collisions).
    """
    seq = 0
    for (ref,) in db.query(Booking.booking_ref).filter(Booking.event_id == event_id):
        try:
            seq = max(seq, int(str(ref or "").rsplit("-", 1)[-1]))
        except ValueError:
            continue
    return f"NOAH-{event_code_for(event_name)}-{seq + 1}"


def _seat_zone_info(ev, seats):
    """{seat_id: {"zone": name, "color": hex}} mirroring the public seat grid.

    Auto mode replicates the frontend's row-block zones and seat numbering
    (first/topmost row wins on ambiguity); manual mode reads seat_layout.
    Seats still unmatched fall back to the longest-prefix rule used by pricing.
    """
    zones = list(ev.zones or [])
    if not zones:
        return {}

    if getattr(ev, "layout_mode", "auto") == "manual":
        layout_map = {s.get("id"): s for s in (ev.seat_layout or [])}
        by_name = {z.get("name"): z for z in zones}
        return {
            sid: (lambda z: {"zone": z.get("name", ""), "color": z.get("color", "")})(
                by_name.get((layout_map.get(sid) or {}).get("zone")) or zones[0]
            )
            for sid in seats
        }

    rows_total = int(ev.rows or 0) or sum(int(z.get("rows") or 1) for z in zones)
    cols = int(ev.cols or 0) or 12

    # Row -> zone: contiguous blocks, exactly like buildZoneMapFromEvent()
    row_zone = {}
    r = 0
    for z in zones:
        for _ in range(int(z.get("rows") or 1)):
            if r >= rows_total:
                break
            row_zone[r] = z
            r += 1
    while r < rows_total:
        row_zone[r] = zones[-1]
        r += 1

    # Seat id -> zone: replicate getSeatId(); setdefault keeps the topmost row
    zone_first_row = {}
    acc = 0
    for i, z in enumerate(zones):
        zone_first_row[i] = acc
        acc += int(z.get("rows") or 1)
    zone_index = {id(z): i for i, z in enumerate(zones)}
    id_zone = {}
    for rz, z in row_zone.items():
        zi = zone_index[id(z)]
        prefix = z.get("seat_prefix") or "A"
        start = int(z.get("seat_start") or 1)
        for c in range(cols):
            sid = f"{prefix}{start + (rz - zone_first_row[zi]) * cols + c}"
            id_zone.setdefault(sid, z)

    prefixes = sorted([(z.get("seat_prefix") or "", z) for z in zones],
                      key=lambda x: -len(x[0]))
    out = {}
    for sid in seats:
        z = id_zone.get(sid)
        if not z:
            for pfx, zc in prefixes:
                if pfx and sid.startswith(pfx):
                    z = zc
                    break
        z = z or zones[-1]
        out[sid] = {"zone": z.get("name", ""), "color": z.get("color", "")}
    return out

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

    # Per-zone max_seats validation. Resolve each seat through the same layout
    # the public page uses (prefixes in auto mode, seat_layout in manual), so
    # max-per-booking also covers designer sections and tables.
    zone_by_name = {z.get("name"): z for z in (ev.zones or [])}
    seat_info = _seat_zone_info(ev, data.seats)
    zone_counts = {}
    for s in data.seats:
        zn = (seat_info.get(s) or {}).get("zone")
        if not zn:
            continue
        max_s = (zone_by_name.get(zn) or {}).get("max_seats")
        if not max_s:
            continue
        zone_counts[zn] = zone_counts.get(zn, 0) + 1
        if zone_counts[zn] > int(max_s):
            raise HTTPException(status_code=400, detail=f"Max {max_s} seats in {zn}")

    total, subtotal, fee, discount_items, raw_subtotal, addon_total, addon_items = _calc_total(ev, data.seats, data.addons)

    ev.booked_seats = booked + data.seats
    db.add(ev)

    booking_ref = next_booking_ref(db, data.event_id, ev.name)
    booking = Booking(
        event_id=data.event_id, seats=data.seats,
        total_paid=total, status="confirmed",
        user_id=data.user_id, email=data.email,
        addons=data.addons or [], booking_ref=booking_ref
    )
    db.add(booking); db.commit(); db.refresh(booking)

    ticket_codes = {s: f"{booking_ref}-{s}" for s in data.seats}

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
        booking_ref=booking_ref,
        ticket_codes=ticket_codes,
        addon_items=addon_items,
        seat_zones=_seat_zone_info(ev, data.seats),
        event_image=(ev.images or [None])[0] if getattr(ev, "images", None) else None
    )

    return {"booking_id": booking.id, "booking_ref": booking_ref, "seats": data.seats, "total": total, "status": "confirmed", "addon_total": addon_total}

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