from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import backend.models  # noqa: F401  (registers all tables on Base)
from backend.database import Base, get_db
from backend.routes import gallery

# 1x1 transparent PNG as a data URL
PNG_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)

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
app.include_router(gallery.router, prefix="/gallery")
app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)


def test_list_starts_empty():
    resp = client.get("/gallery/")
    assert resp.status_code == 200
    assert resp.json() == []


def test_upload_and_list_photo():
    resp = client.post("/gallery/", json={"title": "Onam Night", "image": PNG_DATA_URL})
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "Onam Night"
    assert body["id"]

    photos = client.get("/gallery/").json()
    assert len(photos) == 1
    assert photos[0]["image"] == PNG_DATA_URL
    assert photos[0]["title"] == "Onam Night"


def test_upload_accepts_missing_title():
    resp = client.post("/gallery/", json={"image": PNG_DATA_URL})
    assert resp.status_code == 200
    assert resp.json()["title"] == ""


def test_upload_rejects_non_image():
    resp = client.post("/gallery/", json={"title": "x", "image": "not-an-image"})
    assert resp.status_code == 400


def test_delete_photo():
    pid = client.post("/gallery/", json={"title": "temp", "image": PNG_DATA_URL}).json()["id"]
    assert client.delete(f"/gallery/{pid}").status_code == 200
    remaining = [p["id"] for p in client.get("/gallery/").json()]
    assert pid not in remaining


def test_delete_missing_photo_is_404():
    assert client.delete("/gallery/999999").status_code == 404
