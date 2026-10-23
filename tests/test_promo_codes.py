"""Promo code rules: percentage discount + a cap on tickets sold with the code."""

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
from backend.routes.bookings import router
from backend.routes import events as events_routes

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
app.include_router(events_routes.router, prefix="/events")
app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)


@pytest.fixture(autouse=True)
def _no_real_email():
    with patch("backend.routes.bookings.send_booking_confirmation"):
        yield


SEATS = [
    {"id": "Hall · A1", "x": 30, "y": 100, "zone": "Hall"},
    {"id": "Hall · A2", "x": 60, "y": 100, "zone": "Hall"},
    {"id": "Hall · A3", "x": 90, "y": 100, "zone": "Hall"},
    {"id": "Hall · A4", "x": 120, "y": 100, "zone": "Hall"},
]


def _create_event(promo_codes, addons=None):
    db = TestingSession()
    ev = Event(
        name="Promo Event", venue="Hall", date="2026-12-31",
        rows=1, cols=4,
        zones=[{
            "name": "Hall", "rows": 1, "price": 100.0, "color": "#8b44ff",
            "seat_prefix": "", "seat_start": 1, "max_seats": 10,
        }],
        seat_layout=[dict(s) for s in SEATS],
        layout_mode="manual",
        promo_codes=promo_codes,
        addons=addons or [],
        booked_seats=[], held_seats=[],
    )
    db.add(ev)
    db.commit()
    db.refresh(ev)
    event_id = ev.id
    db.close()
    return event_id


def _book(event_id, seats, promo_code=None, addons=None):
    body = {"event_id": event_id, "seats": seats, "email": "buyer@example.com"}
    if promo_code is not None:
        body["promo_code"] = promo_code
    if addons is not None:
        body["addons"] = addons
    return client.post("/bookings/", json=body)


def test_promo_percentage_applies_to_ticket_subtotal():
    ev = _create_event([{"code": "SAVE10", "percent": 10, "max_tickets": 50}])
    r = _book(ev, ["Hall · A1", "Hall · A2"], "save10")  # case-insensitive
    assert r.status_code == 200
    # 2 x 100 = 200, less 10% = 180, plus 8% fee = 194.4
    assert r.json()["total"] == 194.4
    assert r.json()["promo_code"] == "SAVE10"
    assert r.json()["promo_discount"] == 20.0


def test_promo_does_not_discount_addons():
    addons = [{"id": "parking", "name": "Parking", "type": "per_booking", "price": 50}]
    ev = _create_event(
        [{"code": "SAVE10", "percent": 10, "max_tickets": 50}], addons=addons
    )
    r = _book(ev, ["Hall · A1", "Hall · A2"], "SAVE10", addons=["parking"])
    assert r.status_code == 200
    # Tickets 200 - 20 promo = 180; add-on 50 is untouched. 230 + 8% = 248.4
    assert r.json()["total"] == 248.4
    assert r.json()["addon_total"] == 50.0
    assert r.json()["promo_discount"] == 20.0


def test_invalid_promo_code_is_rejected():
    ev = _create_event([{"code": "SAVE10", "percent": 10, "max_tickets": 50}])
    r = _book(ev, ["Hall · A1"], "NOPE")
    assert r.status_code == 400
    assert "Invalid promo code" in r.json()["detail"]


def test_promo_max_tickets_is_cumulative():
    ev = _create_event([{"code": "TWO", "percent": 20, "max_tickets": 3}])
    assert _book(ev, ["Hall · A1", "Hall · A2"], "TWO").status_code == 200
    assert _book(ev, ["Hall · A3"], "TWO").status_code == 200
    # Only 3 tickets may use the code, so the next one fails even though the
    # seat itself is free.
    r = _book(ev, ["Hall · A4"], "TWO")
    assert r.status_code == 400
    assert "sold out" in r.json()["detail"]


def test_promo_rejects_a_party_larger_than_the_remaining_limit():
    ev = _create_event([{"code": "ONE", "percent": 50, "max_tickets": 2}])
    r = _book(ev, ["Hall · A1", "Hall · A2", "Hall · A3"], "ONE")
    assert r.status_code == 400
    assert "Only 2 ticket(s) left" in r.json()["detail"]


def test_booking_without_promo_is_unaffected():
    ev = _create_event([{"code": "SAVE10", "percent": 10, "max_tickets": 1}])
    r = _book(ev, ["Hall · A1", "Hall · A2"])
    assert r.status_code == 200
    assert r.json()["promo_code"] == ""
    assert r.json()["promo_discount"] == 0
    assert r.json()["total"] == 216.0


def test_event_api_reports_live_promo_usage():
    ev = _create_event([{"code": "SAVE10", "percent": 10, "max_tickets": 50}])
    assert _book(ev, ["Hall · A1", "Hall · A2"], "SAVE10").status_code == 200
    r = client.get(f"/events/{ev}")
    assert r.status_code == 200
    promo = r.json()["promo_codes"][0]
    assert promo["code"] == "SAVE10"
    assert promo["used"] == 2
    assert promo["max_tickets"] == 50
