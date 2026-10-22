"""Excel export of an event's booking flow: who booked which seat.

Produces a workbook with three sheets:

1. "Bookings"   - one row per booking (ref, buyer, seats, zone(s), amount, status)
2. "Seats"      - one row per seat sold (ref, buyer, seat, zone, ticket code)
3. "Seat Map"   - the venue grid coloured by status (booked / held / available)

The seat map mirrors the public site's row-block zone logic and seat numbering
so the export and the customer-facing seat picker always agree.
"""

import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import Event, Booking
from backend.routes.bookings import _seat_zone_info
from backend.utils.xlsx import build_xlsx

router = APIRouter()

BOOKED_FILL = "FF22B573"     # green
HELD_FILL = "FFFF3E37"       # red
AVAILABLE_FILL = "FFE5E7EB"  # light grey
HEADER_FILL = "FFEDEFF3"     # neutral so the default dark text stays legible

HEADER_STYLE = "fill:%s" % HEADER_FILL


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9 _.-]", "", name or "event").strip() or "event"
    return re.sub(r"\s+", "-", cleaned)[:60]


def _event_seats(ev: Event):
    """Ordered seat ids for the event, mirroring the frontend numbering."""
    zones = list(ev.zones or [])
    if getattr(ev, "layout_mode", "auto") == "manual":
        return [s.get("id") for s in (ev.seat_layout or []) if s.get("id")]

    rows_total = int(ev.rows or 0) or sum(int(z.get("rows") or 1) for z in zones)
    cols = int(ev.cols or 0) or 12

    row_zone = {}
    r = 0
    for z in zones:
        for _ in range(int(z.get("rows") or 1)):
            if r >= rows_total:
                break
            row_zone[r] = z
            r += 1
    while r < rows_total:
        row_zone[r] = zones[-1] if zones else None
        r += 1

    zone_first_row = {}
    acc = 0
    for i, z in enumerate(zones):
        zone_first_row[i] = acc
        acc += int(z.get("rows") or 1)
    zone_index = {id(z): i for i, z in enumerate(zones)}

    seats = []
    for rz in range(rows_total):
        z = row_zone.get(rz)
        if z is None:
            continue
        zi = zone_index[id(z)]
        prefix = z.get("seat_prefix") or "A"
        start = int(z.get("seat_start") or 1)
        for c in range(cols):
            seats.append(f"{prefix}{start + (rz - zone_first_row[zi]) * cols + c}")
    return seats


def _seat_map_rows(ev: Event, booked: set, held: set):
    """Build the coloured grid: header row of column numbers + one row per row."""
    cols = int(ev.cols or 0) or 12
    zones = list(ev.zones or [])
    rows_total = int(ev.rows or 0) or sum(int(z.get("rows") or 1) for z in zones)

    all_seats = _event_seats(ev)
    if not all_seats:
        return [], []

    header = [{"value": "", "style": HEADER_STYLE}]
    for c in range(1, cols + 1):
        header.append({"value": c, "style": HEADER_STYLE})

    rows = [header]
    idx = 0
    for _ in range(rows_total):
        cells = [{"value": "", "style": 0}]
        for _c in range(cols):
            if idx >= len(all_seats):
                cells.append("")
                continue
            sid = all_seats[idx]
            idx += 1
            if sid in booked:
                colour, label = BOOKED_FILL, "B"
            elif sid in held:
                colour, label = HELD_FILL, "H"
            else:
                colour, label = AVAILABLE_FILL, ""
            cells.append({"value": label, "style": f"fill:{colour}"})
        rows.append(cells)

    widths = [4] + [4] * cols
    return rows, widths


@router.get("/event/{event_id}")
def export_event_bookings(event_id: int, db: Session = Depends(get_db)):
    """Download an .xlsx of the event's bookings, seats and seat map."""
    ev = db.query(Event).filter(Event.id == event_id).first()
    if not ev:
        raise HTTPException(status_code=404, detail="Event not found")

    bookings = (
        db.query(Booking)
        .filter(Booking.event_id == event_id)
        .order_by(Booking.id)
        .all()
    )
    # A held seat may belong to an abandoned checkout and have no Booking row.
    known_refs = {b.booking_ref for b in bookings if b.booking_ref}

    booked = list(ev.booked_seats or [])
    held = list(ev.held_seats or [])
    booked_set, held_set = set(booked), set(held)

    all_sold = sorted({s for b in bookings for s in (b.seats or [])})
    zone_info = _seat_zone_info(ev, all_sold) if all_sold else {}

    # ── Sheet 1: Bookings ────────────────────────────────────────────────
    b_header = [
        {"value": h, "style": HEADER_STYLE}
        for h in ("Booking Ref", "Buyer Email", "Seats", "Seat Count",
                  "Zones", "Add-ons", "Total Paid", "Status", "Booking ID")
    ]
    b_rows = [b_header]
    for b in bookings:
        seats = list(b.seats or [])
        zones = sorted({zone_info.get(s, {}).get("zone", "") for s in seats} - {""})
        addons = ev.addons or []
        addon_names = [
            a.get("name", "") for a in addons if a.get("id") in (b.addons or [])
        ]
        b_rows.append([
            b.booking_ref or "",
            b.email or "",
            ", ".join(seats),
            len(seats),
            ", ".join(zones),
            ", ".join(addon_names),
            float(b.total_paid or 0),
            b.status or "",
            b.id,
        ])

    # ── Sheet 2: Seats ───────────────────────────────────────────────────
    s_header = [
        {"value": h, "style": HEADER_STYLE}
        for h in ("Seat", "Booking Ref", "Buyer Email", "Zone", "Ticket Code", "Status")
    ]
    s_rows = [s_header]
    for b in bookings:
        for seat in (b.seats or []):
            s_rows.append([
                seat,
                b.booking_ref or "",
                b.email or "",
                zone_info.get(seat, {}).get("zone", ""),
                f"{b.booking_ref}-{seat}" if b.booking_ref else seat,
                b.status or "",
            ])
    # Seats flagged as booked but with no booking row (legacy/manual data).
    orphan = [s for s in booked if not any(s in (b.seats or []) for b in bookings)]
    for seat in sorted(orphan):
        s_rows.append([
            seat, "", "", zone_info.get(seat, {}).get("zone", ""),
            seat, "booked (no booking record)",
        ])

    # ── Sheet 3: Seat Map ────────────────────────────────────────────────
    map_rows, map_widths = _seat_map_rows(ev, booked_set, held_set)
    legend_rows = [
        [{"value": "Legend", "style": HEADER_STYLE}, ""],
        [{"value": "B", "style": f"fill:{BOOKED_FILL}"}, "Booked"],
        [{"value": "H", "style": f"fill:{HELD_FILL}"}, "Held (in checkout)"],
        [{"value": "", "style": f"fill:{AVAILABLE_FILL}"}, "Available"],
    ]
    if map_rows:
        map_sheet_rows = map_rows + [[]] + legend_rows
    else:
        map_sheet_rows = legend_rows

    meta_rows = [
        [{"value": "Event", "style": HEADER_STYLE}, ev.name or ""],
        [{"value": "Date", "style": HEADER_STYLE}, f"{ev.date or ''} {ev.time or ''}".strip()],
        [{"value": "Venue", "style": HEADER_STYLE}, ev.venue or ""],
        [{"value": "Status", "style": HEADER_STYLE}, ev.status or ""],
        [{"value": "Exported", "style": HEADER_STYLE}, datetime.now().strftime("%Y-%m-%d %H:%M")],
        [{"value": "Total seats", "style": HEADER_STYLE}, int(ev.total_seats or 0)],
        [{"value": "Booked seats", "style": HEADER_STYLE}, len(booked)],
        [{"value": "Held seats", "style": HEADER_STYLE}, len(held)],
        [{"value": "Bookings", "style": HEADER_STYLE}, len(bookings)],
        [{"value": "Unique booking refs", "style": HEADER_STYLE}, len(known_refs)],
    ]

    data = build_xlsx([
        {
            "name": "Bookings",
            "rows": b_rows,
            "widths": [16, 32, 22, 11, 20, 24, 12, 12, 10],
        },
        {
            "name": "Seats",
            "rows": s_rows,
            "widths": [10, 16, 32, 18, 20, 26],
        },
        {
            "name": "Seat Map",
            "rows": map_sheet_rows,
            "widths": (map_widths or [4] * 13),
            "freeze": False,
        },
        {
            "name": "Summary",
            "rows": meta_rows,
            "widths": [22, 44],
            "freeze": False,
        },
    ])

    filename = f"{_safe_filename(ev.name)}-seats.xlsx"
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
