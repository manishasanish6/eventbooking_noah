import logging, base64, os
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import Response
from sqlalchemy import text
from backend.database import engine, Base, SessionLocal
from backend.models import User
from backend.routes import events, bookings, auth

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")

Base.metadata.create_all(bind=engine)

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

    if 'addons' not in cols_b:
        conn.execute(text("ALTER TABLE bookings ADD COLUMN addons TEXT DEFAULT '[]'"))

app = FastAPI(title="STAGEFRONT API", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # tighten this in production
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def basic_auth_middleware(request: Request, call_next):
    if request.method == "OPTIONS":
        return await call_next(request)
    auth = request.headers.get("Authorization")
    if auth and auth.startswith("Basic "):
        try:
            decoded = base64.b64decode(auth[6:]).decode("utf-8")
            email, password = decoded.split(":", 1)
            db = SessionLocal()
            user = db.query(User).filter(User.email == email).first()
            db.close()
            if user and password == os.getenv("BASIC_AUTH_PASSWORD"):
                return await call_next(request)
        except:
            pass
    return Response(status_code=401, headers={"WWW-Authenticate": "Basic"})

app.include_router(auth.router,     prefix="/auth",     tags=["Auth"])
app.include_router(events.router,   prefix="/events",   tags=["Events"])
app.include_router(bookings.router, prefix="/bookings", tags=["Bookings"])

# Serve frontend files
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")