"""Per-event booking references: NOAH-<CODE>-<seq>.

The sequence restarts at 1 for every event, is unique per booking within
that event, and never reuses a number even after deletions.
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
from backend.models import Booking, Event
from backend.routes.bookings import router, event_code_for, next_booking_ref
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
    """Never send real confirmation emails during these tests."""
    with patch("backend.routes.bookings.send_booking_confirmation"):
        yield


def _create_event(name):
    db = TestingSession()
    ev = Event(
        name=name, venue="Hall", date="2026-12-31",
        zones=[{"name": "VIP", "rows": 1, "price": 10.0, "color": "#8b44ff",
                "seat_prefix": "A"}],
        booked_seats=[], held_seats=[],
    )
    db.add(ev)
    db.commit()
    db.refresh(ev)
    db.close()
    return ev.id


def _book(event_id, seat):
    return client.post(
        "/bookings/",
        json={"event_id": event_id, "seats": [seat], "email": "c@x.com"},
    ).json()


def test_event_code_derivation():
    assert event_code_for("NoahOnamNight") == "ONAM"
    assert event_code_for("Christmas Gala") == "CHRI"
    assert event_code_for("onam night!") == "ONAM"
    assert event_code_for("") == "EVT"
    assert event_code_for("!!") == "EVT"


def test_ref_is_sequential_per_event():
    ev = _create_event("Test Event")
    r1 = _book(ev, "A1")
    r2 = _book(ev, "A2")
    assert r1["booking_ref"] == "NOAH-TEST-1"
    assert r2["booking_ref"] == "NOAH-TEST-2"
    assert r1["booking_ref"] != r2["booking_ref"]  # unique per booking


def test_ref_resets_for_each_event():
    ev1 = _create_event("First Show")
    ev2 = _create_event("Second Show")
    assert _book(ev1, "A1")["booking_ref"] == "NOAH-FIRS-1"
    assert _book(ev1, "A2")["booking_ref"] == "NOAH-FIRS-2"
    # new event starts over at 1
    assert _book(ev2, "A1")["booking_ref"] == "NOAH-SECO-1"


def test_ref_never_reuses_a_number_after_delete():
    ev = _create_event("Delete Demo")
    first = _book(ev, "A1")
    _book(ev, "A2")
    db = TestingSession()
    db.query(Booking).filter(Booking.id == first["booking_id"]).delete()
    db.commit()
    db.close()
    # max remaining seq is 2 -> next is 3, NOT a reused 2
    assert _book(ev, "A3")["booking_ref"] == "NOAH-DELE-3"


def test_next_booking_ref_handles_legacy_rows_without_ref():
    ev = _create_event("Legacy Event")
    db = TestingSession()
    db.add(Booking(event_id=ev, seats=["A1"], total_paid=1, email="a@b.c"))
    db.commit()
    db.close()
    # legacy row has empty ref -> sequence starts at 1
    assert next_booking_ref(TestingSession(), ev, "Legacy Event") == "NOAH-LEGA-1"


def test_email_shows_per_event_ref_and_ticket_codes():
    with patch("backend.utils.email.send_graph_email", return_value=True) as m:
        ok = send_booking_confirmation(
            to_email="c@x.com", event_name="NoahOnamNight", seats=["A1", "A2"],
            total=21.6, venue="Hall", venue_address=None,
            date="2026-12-31", time="20:00",
            booking_id=99, booking_ref="NOAH-ONAM-7",
        )
    assert ok is True
    body = m.call_args[0][2]
    assert "REF: NOAH-ONAM-7" in body
    assert "NOAH-ONAM-7-A1" in body
    assert "NOAH-ONAM-7-A2" in body


def test_email_falls_back_to_global_id_without_ref():
    with patch("backend.utils.email.send_graph_email", return_value=True) as m:
        send_booking_confirmation(
            to_email="c@x.com", event_name="E", seats=["A1"],
            total=10, venue="Hall", venue_address=None,
            date="2026-12-31", time="20:00", booking_id=42,
        )
    body = m.call_args[0][2]
    assert "REF: NOAH-42" in body
    assert "NOAH-42-A1" in body
