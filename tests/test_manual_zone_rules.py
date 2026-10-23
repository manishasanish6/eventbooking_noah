"""Pricing and booking rules for Venue Designer sections and tables.

The designer turns every reserved section / table into a price zone and emits
a manual seat layout. These tests make sure the existing early-bird, group-buy
and max-per-booking rules work for that layout, not just prefix-based zones.
"""

from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import pytest

import backend.models  # noqa: F401  (registers all tables on Base)
from backend.database import Base, get_db
from backend.models import Event
from backend.routes.bookings import router, _seat_zone_info
from backend.utils.email import send_booking_confirmation

engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
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
app.include_router(router, prefix="/bookings")
app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)


@pytest.fixture(autouse=True)
def _no_real_email():
    with patch("backend.routes.bookings.send_booking_confirmation"):
        yield


ZONES = [
    {
        "name": "Front Row", "rows": 2, "price": 100.0, "color": "#8b44ff",
        "seat_prefix": "", "seat_start": 1, "max_seats": 2,
        "promotions": {
            "early_bird": {"enabled": True, "price": 80, "ends_at": "2999-12-31"},
            "group_buy": {"enabled": True, "min_seats": 2, "max_seats": 4,
                          "discount_percent": 10},
        },
    },
    {
        "name": "Booth 7", "rows": 1, "price": 50.0, "color": "#00a3ff",
        "seat_prefix": "", "seat_start": 1, "max_seats": 1,
        "promotions": {},
    },
]

SEATS = [
    {"id": "Front Row · A1", "x": 30, "y": 100, "zone": "Front Row"},
    {"id": "Front Row · A2", "x": 60, "y": 100, "zone": "Front Row"},
    {"id": "Front Row · A3", "x": 90, "y": 100, "zone": "Front Row"},
    {"id": "Booth 7 · S1", "x": 300, "y": 100, "zone": "Booth 7"},
    {"id": "Booth 7 · S2", "x": 330, "y": 100, "zone": "Booth 7"},
]


def _create_event(name="Designer Event"):
    db = TestingSession()
    ev = Event(
        name=name, venue="Hall", date="2026-12-31",
        rows=2, cols=3,
        zones=[dict(z) for z in ZONES],
        seat_layout=[dict(s) for s in SEATS],
        layout_mode="manual",
        booked_seats=[], held_seats=[],
    )
    db.add(ev)
    db.commit()
    db.refresh(ev)
    db_id = ev.id
    db.close()
    return db_id


def _book(event_id, seats):
    return client.post(
        "/bookings/",
        json={"event_id": event_id, "seats": seats, "email": "c@x.com"},
    )


def test_manual_seats_resolve_to_their_zone():
    db = TestingSession()
    ev = db.query(Event).filter(Event.id == _create_event()).first()
    info = _seat_zone_info(ev, ["Front Row · A1", "Booth 7 · S1"])
    db.close()
    assert info["Front Row · A1"]["zone"] == "Front Row"
    assert info["Booth 7 · S1"]["zone"] == "Booth 7"


def test_max_per_booking_applies_per_section_and_table():
    ev = _create_event()
    # Front Row allows 2 per booking, so a third seat is rejected.
    assert _book(ev, ["Front Row · A1", "Front Row · A2", "Front Row · A3"]).status_code == 400
    # Booth 7 allows 1 per booking.
    assert _book(ev, ["Booth 7 · S1", "Booth 7 · S2"]).status_code == 400


def test_early_bird_and_group_buy_apply_per_zone():
    ev = _create_event()
    r = _book(ev, ["Front Row · A1", "Front Row · A2", "Booth 7 · S1"])
    assert r.status_code == 200
    # Front Row: 2 x 80 early bird = 160, group buy -10% = 144.
    # Booth: 50. Subtotal 194 + 8% fee = 209.52.
    assert r.json()["total"] == 209.52


def test_table_and_section_book_together_without_interference():
    ev = _create_event()
    r = _book(ev, ["Front Row · A1", "Booth 7 · S1"])
    assert r.status_code == 200
    # One Front Row seat is below the group-buy minimum: 80 + 50 = 130, plus
    # the 8% fee = 140.4.
    assert r.json()["total"] == 140.4
