import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from backend.database import engine, Base
from backend.routes import events, bookings, auth, contact

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

app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
