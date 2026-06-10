import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from backend.database import engine, Base
from backend.routes import events, bookings, auth

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")

Base.metadata.create_all(bind=engine)

# SQLite-only auto-migration for existing databases (PostgreSQL creates all columns fresh)
if 'sqlite' in engine.url.drivername:
    with engine.connect() as conn:
        cols_b = [row[1] for row in conn.execute(text("PRAGMA table_info(bookings)"))]
        if 'email' not in cols_b:
            conn.execute(text("ALTER TABLE bookings ADD COLUMN email VARCHAR"))

        cols_e = [row[1] for row in conn.execute(text("PRAGMA table_info(events)"))]
        if 'end_time' not in cols_e:
            conn.execute(text("ALTER TABLE events ADD COLUMN end_time VARCHAR DEFAULT ''"))
        if 'held_seats' not in cols_e:
            conn.execute(text("ALTER TABLE events ADD COLUMN held_seats TEXT DEFAULT '[]'"))
        if 'promotions' not in cols_e:
            conn.execute(text("ALTER TABLE events ADD COLUMN promotions TEXT"))
        if 'layout_mode' not in cols_e:
            conn.execute(text("ALTER TABLE events ADD COLUMN layout_mode VARCHAR DEFAULT 'auto'"))
        if 'seat_layout' not in cols_e:
            conn.execute(text("ALTER TABLE events ADD COLUMN seat_layout TEXT DEFAULT '[]'"))
        if 'venue_address' not in cols_e:
            conn.execute(text("ALTER TABLE events ADD COLUMN venue_address TEXT"))
        if 'addons' not in cols_e:
            conn.execute(text("ALTER TABLE events ADD COLUMN addons TEXT DEFAULT '[]'"))
        if 'created_by' not in cols_e:
            conn.execute(text("ALTER TABLE events ADD COLUMN created_by VARCHAR DEFAULT ''"))
        if 'editors' not in cols_e:
            conn.execute(text("ALTER TABLE events ADD COLUMN editors TEXT DEFAULT '[]'"))

        cols_u = [row[1] for row in conn.execute(text("PRAGMA table_info(users)"))]
        if 'totp_secret' not in cols_u:
            conn.execute(text("ALTER TABLE users ADD COLUMN totp_secret VARCHAR"))

        if 'addons' not in cols_b:
            conn.execute(text("ALTER TABLE bookings ADD COLUMN addons TEXT DEFAULT '[]'"))

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

app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")