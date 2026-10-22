import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from backend.database import engine, Base
from backend.routes import events, bookings, auth, contact, gallery, export as export_routes

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")

Base.metadata.create_all(bind=engine)

SQL_MIGRATIONS = {
    "events": [
        ("end_time", "VARCHAR DEFAULT ''"),
        ("held_seats", "TEXT DEFAULT '[]'"),
        ("promotions", "TEXT"),
        ("layout_mode", "VARCHAR DEFAULT 'auto'"),
        ("seat_layout", "TEXT DEFAULT '[]'"),
        ("venue_address", "TEXT"),
        ("addons", "TEXT DEFAULT '[]'"),
        ("created_by", "VARCHAR DEFAULT ''"),
        ("editors", "TEXT DEFAULT '[]'"),
        ("images", "TEXT DEFAULT '[]'"),
        ("artist_bio", "VARCHAR DEFAULT ''"),
        ("artist_photo", "VARCHAR DEFAULT ''"),
        ("artist_instagram", "VARCHAR DEFAULT ''"),
        ("artist_facebook", "VARCHAR DEFAULT ''"),
        ("artist_website", "VARCHAR DEFAULT ''"),
        ("artist_details", "VARCHAR DEFAULT ''"),
        ("organiser_name", "VARCHAR DEFAULT ''"),
        ("organiser_contact", "VARCHAR DEFAULT ''"),
        ("organiser_email", "VARCHAR DEFAULT ''"),
        ("organiser_logo", "VARCHAR DEFAULT ''"),
        ("organiser_address", "VARCHAR DEFAULT ''"),
        ("organiser_summary", "VARCHAR DEFAULT ''"),
    ],
    "bookings": [
        ("email", "VARCHAR"),
        ("addons", "TEXT DEFAULT '[]'"),
        ("booking_ref", "VARCHAR DEFAULT ''"),
    ],
    "users": [
        ("totp_secret", "VARCHAR"),
        ("otp_code", "VARCHAR"),
        ("otp_expires_at", "VARCHAR"),
    ],
}

with engine.connect() as conn:
    for table, columns in SQL_MIGRATIONS.items():
        existing = {
            row[0]
            for row in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = :t"
                ),
                {"t": table},
            )
        }
        for col_name, col_type in columns:
            if col_name not in existing:
                conn.execute(
                    text(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}")
                )
                logging.info("Added column %s.%s", table, col_name)
    conn.commit()


def _backfill_booking_refs():
    """Give pre-existing bookings their per-event reference (NOAH-CODE-n)."""
    from collections import defaultdict

    from backend.database import SessionLocal
    from backend.models import Booking, Event
    from backend.routes.bookings import event_code_for

    db = SessionLocal()
    try:
        pending = (
            db.query(Booking)
            .filter((Booking.booking_ref.is_(None)) | (Booking.booking_ref == ""))
            .order_by(Booking.id.asc())
            .all()
        )
        if not pending:
            return
        names = {e.id: e.name for e in db.query(Event).all()}
        by_event = defaultdict(list)
        for b in pending:
            by_event[b.event_id].append(b)
        for event_id, rows in by_event.items():
            code = event_code_for(names.get(event_id, ""))
            seq = 0
            for (ref,) in db.query(Booking.booking_ref).filter(Booking.event_id == event_id):
                try:
                    seq = max(seq, int(str(ref or "").rsplit("-", 1)[-1]))
                except ValueError:
                    continue
            for b in rows:  # id order -> sequential numbers
                seq += 1
                b.booking_ref = f"NOAH-{code}-{seq}"
        db.commit()
        logging.info("Backfilled booking_ref for %d booking(s)", len(pending))
    except Exception:
        logging.exception("booking_ref backfill failed")
    finally:
        db.close()


_backfill_booking_refs()

app = FastAPI(title="STAGEFRONT API", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router,     prefix="/auth",     tags=["Auth"])
app.include_router(events.router,   prefix="/events",   tags=["Events"])
app.include_router(bookings.router, prefix="/bookings", tags=["Bookings"])
app.include_router(contact.router,  prefix="/contact",  tags=["Contact"])
app.include_router(gallery.router,  prefix="/gallery",  tags=["Gallery"])
app.include_router(export_routes.router, prefix="/export", tags=["Export"])

app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
