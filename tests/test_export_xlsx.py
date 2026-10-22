"""Excel export of an event's booking flow (/export/event/{id}).

The workbook is a dependency-free .xlsx (zip of XML), so these tests open it
with the standard library and assert on the sheets, rows and seat-map colours.
"""

import io
import zipfile
from xml.dom import minidom

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import backend.models  # noqa: F401
from backend.database import Base, get_db
from backend.models import Booking, Event
from backend.routes.export import router, _event_seats, _safe_filename
from backend.utils.xlsx import build_xlsx

engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
TestingSession = sessionmaker(bind=engine, autocommit=False, autoflush=False)
Base.metadata.create_all(bind=engine)


def override_get_db():
    db = TestingSession()
    try:
        yield db
    finally:
        db.close()


app = FastAPI()
app.include_router(router, prefix="/export")
app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)


def _event(**kw):
    db = TestingSession()
    defaults = dict(
        name="Export Demo",
        venue="Grand Hall",
        date="2026-12-31",
        time="20:00",
        status="live",
        rows=3,
        cols=4,
        total_seats=12,
        zones=[{"name": "VIP", "rows": 1, "price": 50.0, "color": "#8b44ff",
                "seat_prefix": "A"}],
        booked_seats=[],
        held_seats=[],
    )
    defaults.update(kw)
    ev = Event(**defaults)
    db.add(ev)
    db.commit()
    db.refresh(ev)
    event_id = ev.id
    db.close()
    return event_id


def _book(event_id, seats, email="buyer@example.com", ref="NOAH-EXPO-1", **kw):
    db = TestingSession()
    b = Booking(event_id=event_id, seats=list(seats), email=email,
                total_paid=kw.pop("total_paid", 100.0),
                status=kw.pop("status", "confirmed"),
                booking_ref=ref, **kw)
    db.add(b)
    db.commit()
    ev = db.query(Event).filter(Event.id == event_id).first()
    ev.booked_seats = list(ev.booked_seats or []) + list(seats)
    db.add(ev)
    db.commit()
    db.close()


def _sheet_colours(zf, index):
    """Map each cell in a worksheet to its fill colour (or None).

    fillId is an absolute index into the <fills> table, so resolve it directly.
    """
    import re
    styles = zf.read("xl/styles.xml").decode()
    cell_xfs = re.search(r"<cellXfs.*?</cellXfs>", styles, re.S)
    xfs = re.findall(r"<xf [^>]*?>", cell_xfs.group(0)) if cell_xfs else []
    xfs = [re.search(r'fillId="(\d+)"', x).group(1) if re.search(r'fillId="(\d+)"', x) else "0"
           for x in xfs]
    fills = re.findall(r"<fill>(.*?)</fill>", styles, re.S)

    def colour_for(style_id):
        if style_id == "" or style_id is None:
            return None
        sid = int(style_id)
        if sid >= len(xfs):
            return None
        fid = int(xfs[sid])
        if fid >= len(fills):
            return None
        m = re.search(r'rgb="([0-9A-Fa-f]{8})"', fills[fid])
        return m.group(1).upper() if m else None

    xml = zf.read(f"xl/worksheets/sheet{index}.xml").decode()
    doc = minidom.parseString(xml)
    out = {}
    for c in doc.getElementsByTagName("c"):
        ref = c.getAttribute("r")
        s = c.getAttribute("s")
        t = c.getElementsByTagName("t")
        v = c.getElementsByTagName("v")
        value = (t[0].firstChild.data if t and t[0].firstChild
                 else v[0].firstChild.data if v and v[0].firstChild else "")
        out[ref] = {"value": value, "colour": colour_for(s)}
    return out


def _sheet_names(zf):
    wb = zf.read("xl/workbook.xml").decode()
    doc = minidom.parseString(wb)
    return [s.getAttribute("name") for s in doc.getElementsByTagName("sheet")]


def _sheet_text(zf, index):
    """All inline string values from a worksheet, as a flat list."""
    xml = zf.read(f"xl/worksheets/sheet{index}.xml").decode()
    doc = minidom.parseString(xml)
    return [t.firstChild.data if t.firstChild else ""
            for t in doc.getElementsByTagName("t")]


# ── writer ───────────────────────────────────────────────────────────────

def test_build_xlsx_is_a_valid_zip_of_xml():
    data = build_xlsx([{"name": "S", "rows": [["a", 1, None]]}])
    assert data[:2] == b"PK"
    zf = zipfile.ZipFile(io.BytesIO(data))
    assert zf.testzip() is None
    for part in zf.namelist():
        if part.endswith((".xml", ".rels")):
            minidom.parseString(zf.read(part))  # raises if malformed


def test_build_xlsx_escapes_and_strips_invalid_control_chars():
    data = build_xlsx([{"name": "S", "rows": [["a<b>&c", "bad\x00\x07char"]]}])
    zf = zipfile.ZipFile(io.BytesIO(data))
    text = _sheet_text(zf, 1)
    assert "a<b>&c" in text          # escaped in XML, round-trips as text
    assert "badchar" in text         # control chars removed


def test_build_xlsx_sanitises_and_deduplicates_sheet_names():
    data = build_xlsx([
        {"name": "a/b:c*d?e[f]g" * 5, "rows": [["x"]]},
        {"name": "a/b:c*d?e[f]g" * 5, "rows": [["y"]]},
    ])
    zf = zipfile.ZipFile(io.BytesIO(data))
    names = _sheet_names(zf)
    assert len(names) == 2
    assert names[0] != names[1]                     # deduplicated
    assert all(len(n) <= 31 for n in names)         # Excel limit
    assert all(not any(c in n for c in "[]:*?/\\") for n in names)


# ── helpers ─────────────────────────────────────────────────────────────

def test_safe_filename():
    assert _safe_filename("Noah Onam Night 2026") == "Noah-Onam-Night-2026"
    assert _safe_filename("../../etc/passwd") == "....etcpasswd"
    assert _safe_filename("") == "event"
    assert _safe_filename("!!!") == "event"


def test_event_seats_follows_zone_row_blocks():
    db = TestingSession()
    ev = Event(
        name="Seats", rows=3, cols=4, zones=[
            {"name": "VIP", "rows": 1, "price": 50, "seat_prefix": "A"},
            {"name": "Std", "rows": 2, "price": 20, "seat_prefix": "B"},
        ],
    )
    db.add(ev)
    db.commit()
    db.refresh(ev)
    seats = _event_seats(ev)
    # VIP row -> A1..A4, then two standard rows -> B1..B4, B5..B8
    assert seats == ["A1", "A2", "A3", "A4",
                     "B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8"]
    db.close()


def test_event_seats_manual_mode_reads_layout():
    db = TestingSession()
    ev = Event(
        name="Manual", layout_mode="manual",
        seat_layout=[{"id": "X9", "zone": "VIP"}, {"id": "X2", "zone": "VIP"}],
        zones=[{"name": "VIP", "rows": 1, "price": 50}],
    )
    db.add(ev)
    db.commit()
    db.refresh(ev)
    assert _event_seats(ev) == ["X9", "X2"]   # socket order preserved
    db.close()


# ── endpoint ────────────────────────────────────────────────────────────

def test_export_returns_xlsx_attachment():
    ev = _event()
    _book(ev, ["A1", "A2"], email="alice@example.com", ref="NOAH-EXPO-1")
    res = client.get(f"/export/event/{ev}")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert "attachment" in res.headers["content-disposition"]
    assert ".xlsx" in res.headers["content-disposition"]
    assert res.content[:2] == b"PK"


def test_export_404_for_missing_event():
    assert client.get("/export/event/999999").status_code == 404


def test_export_has_four_named_sheets():
    ev = _event()
    _book(ev, ["A1"], ref="NOAH-EXPO-1")
    zf = zipfile.ZipFile(io.BytesIO(client.get(f"/export/event/{ev}").content))
    assert _sheet_names(zf) == ["Bookings", "Seats", "Seat Map", "Summary"]


def test_bookings_sheet_lists_ref_buyer_and_seats():
    ev = _event()
    _book(ev, ["A1", "A2"], email="alice@example.com", ref="NOAH-EXPO-7")
    zf = zipfile.ZipFile(io.BytesIO(client.get(f"/export/event/{ev}").content))
    text = _sheet_text(zf, 1)
    for expected in ("Booking Ref", "Buyer Email", "Seats", "NOAH-EXPO-7",
                     "alice@example.com", "A1, A2", "VIP"):
        assert expected in text, expected


def test_seats_sheet_has_one_row_per_seat_with_ticket_codes():
    ev = _event()
    _book(ev, ["A1", "A2"], ref="NOAH-EXPO-3")
    zf = zipfile.ZipFile(io.BytesIO(client.get(f"/export/event/{ev}").content))
    text = _sheet_text(zf, 2)
    assert "NOAH-EXPO-3-A1" in text     # per-seat ticket code
    assert "NOAH-EXPO-3-A2" in text


def test_seats_sheet_flags_seats_without_a_booking_record():
    """Legacy/manual seats in booked_seats still appear, clearly marked."""
    ev = _event(booked_seats=["A1", "A4"])
    _book(ev, ["A1"], ref="NOAH-EXPO-1")
    zf = zipfile.ZipFile(io.BytesIO(client.get(f"/export/event/{ev}").content))
    text = _sheet_text(zf, 2)
    assert "A4" in text
    assert "booked (no booking record)" in text


def test_seat_map_colours_booked_held_and_available():
    ev = _event(held_seats=["A3"])
    _book(ev, ["A1"], ref="NOAH-EXPO-1")
    content = client.get(f"/export/event/{ev}").content
    zf = zipfile.ZipFile(io.BytesIO(content))
    cells = _sheet_colours(zf, 3)

    # legend present
    legend = _sheet_text(zf, 3)
    assert "Booked" in legend
    assert "Held (in checkout)" in legend
    assert "Available" in legend

    # every "B" is green and every "H" is red — labels and colours must agree
    green, red, grey = "FF22B573", "FFFF3E37", "FFE5E7EB"
    b_cells = [c for c in cells.values() if c["value"] == "B"]
    h_cells = [c for c in cells.values() if c["value"] == "H"]
    assert b_cells and all(c["colour"] == green for c in b_cells)
    assert h_cells and all(c["colour"] == red for c in h_cells)

    # A1 was booked, A3 held: both marked, and available seats are grey
    assert cells["B2"]["value"] == "B"        # row 1, col 1 under the header row
    assert cells["B2"]["colour"] == green
    greys = [c for c in cells.values() if c["colour"] == grey]
    assert len(greys) > 0


def test_header_fill_resolves_even_when_outside_the_sheet_palette():
    """Regression: a colour not listed in the seat palette must still map.

    The bold header uses its own tint; when fills were resolved by per-sheet
    position this produced off-by-one colours (labels mismatched their fill).
    """
    data = build_xlsx([{
        "name": "S",
        "rows": [
            [{"value": "Head", "style": "fill:FFEDEFF3"}],
            [{"value": "B", "style": "fill:FF22B573"}],
            [{"value": "H", "style": "fill:FFFF3E37"}],
        ],
    }])
    zf = zipfile.ZipFile(io.BytesIO(data))
    cells = _sheet_colours(zf, 1)
    assert cells["A1"]["colour"] == "FFEDEFF3"
    assert cells["A2"]["colour"] == "FF22B573"
    assert cells["A3"]["colour"] == "FFFF3E37"


def test_every_coloured_cell_keeps_its_own_colour_across_sheets():
    """Fills are shared globally, so identical colours agree on every sheet."""
    data = build_xlsx([
        {"name": "One", "rows": [[{"value": "x", "style": "fill:FF22B573"}]]},
        {"name": "Two", "rows": [[{"value": "y", "style": "fill:FF22B573"}],
                                 [{"value": "z", "style": "fill:FFE5E7EB"}]]},
    ])
    zf = zipfile.ZipFile(io.BytesIO(data))
    one = _sheet_colours(zf, 1)
    two = _sheet_colours(zf, 2)
    assert one["A1"]["colour"] == "FF22B573"
    assert two["A1"]["colour"] == "FF22B573"
    assert two["A2"]["colour"] == "FFE5E7EB"


def test_summary_sheet_counts_match_the_event():
    ev = _event(held_seats=["A3", "A4"])
    _book(ev, ["A1"], ref="NOAH-EXPO-1")
    _book(ev, ["A2"], email="bob@example.com", ref="NOAH-EXPO-2")
    zf = zipfile.ZipFile(io.BytesIO(client.get(f"/export/event/{ev}").content))
    text = _sheet_text(zf, 4)
    assert "Export Demo" in text
    assert "Grand Hall" in text
    assert "Unique booking refs" in text
    assert text.count("NOAH-EXPO") == 0     # summary counts, not raw refs


def test_export_works_for_event_with_no_bookings():
    ev = _event(name="Empty Show")
    res = client.get(f"/export/event/{ev}")
    assert res.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(res.content))
    assert "Bookings" in _sheet_names(zf)
    assert "No events" not in _sheet_text(zf, 1)   # header row still written


def test_export_filename_derives_from_event_name():
    ev = _event(name="🎉 NOAH ONAM LIVE 2026")
    res = client.get(f"/export/event/{ev}")
    disp = res.headers["content-disposition"]
    assert "NOAH-ONAM-LIVE-2026-seats.xlsx" in disp
