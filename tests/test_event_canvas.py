from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import backend.models  # noqa: F401  (registers all tables on Base)
from backend.database import Base, get_db
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
app.include_router(events_routes.router, prefix="/events")
app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)

ELEMENT = {
    "id": "e1",
    "type": "section",
    "x": 10,
    "y": 20,
    "w": 260,
    "h": 180,
    "label": "Balcony",
    "colour": "#8b44ff",
    "opts": {
        "admission": "reserved",
        "rows": 4,
        "cols": 6,
        "row_prefix": "letters",
        "seat_prefix": "numbers",
    },
}


def payload(**overrides):
    data = {
        "name": "Canvas Test",
        "artist": "",
        "venue": "Test Hall",
        "date": "2026-12-31",
        "time": "20:00",
        "rows": 4,
        "cols": 6,
        "zones": [{"name": "VIP", "rows": 4, "price": 100.0, "color": "#8b44ff"}],
        "layout_mode": "manual",
        "seat_layout": [],
        "canvas_elements": [ELEMENT],
    }
    data.update(overrides)
    return data


def test_create_and_get_keeps_canvas_map():
    created = client.post("/events/", json=payload()).json()
    assert created["canvas_elements"][0]["label"] == "Balcony"

    fetched = client.get(f"/events/{created['id']}").json()
    assert len(fetched["canvas_elements"]) == 1
    assert fetched["canvas_elements"][0]["opts"]["row_prefix"] == "letters"


def test_list_includes_canvas_map():
    created = client.post("/events/", json=payload(name="Listed")).json()
    listed = client.get("/events/").json()
    match = [e for e in listed if e["id"] == created["id"]][0]
    assert match["canvas_elements"][0]["label"] == "Balcony"


def test_update_replaces_canvas_map():
    created = client.post("/events/", json=payload()).json()
    changed = payload()
    changed["canvas_elements"] = [dict(ELEMENT, label="Balcony B")]
    updated = client.put(f"/events/{created['id']}", json=changed).json()
    assert updated["canvas_elements"][0]["label"] == "Balcony B"


def test_update_omitting_canvas_keeps_saved_map():
    created = client.post("/events/", json=payload()).json()
    omit = payload()
    del omit["canvas_elements"]
    updated = client.put(f"/events/{created['id']}", json=omit).json()
    assert len(updated["canvas_elements"]) == 1
    assert updated["canvas_elements"][0]["label"] == "Balcony"


def test_update_with_empty_list_clears_canvas_map():
    created = client.post("/events/", json=payload()).json()
    cleared = client.put(
        f"/events/{created['id']}", json=payload(canvas_elements=[])
    ).json()
    assert cleared["canvas_elements"] == []
