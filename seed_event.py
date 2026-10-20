import base64, json, sys, os
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv(override=True)

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("ERROR: DATABASE_URL not set in .env"); sys.exit(1)

if "sslmode=" not in DATABASE_URL and "localhost" not in DATABASE_URL and "127.0.0.1" not in DATABASE_URL:
    DATABASE_URL += "?sslmode=require"

IMG_PATH = Path(__file__).parent / "frontend" / "Noah_events1.jpeg"
if not IMG_PATH.exists():
    print(f"ERROR: Image not found at {IMG_PATH}"); sys.exit(1)

with open(IMG_PATH, "rb") as f:
    img_b64 = base64.b64encode(f.read()).decode()
ext = IMG_PATH.suffix.lstrip(".").lower()
mime = "jpeg" if ext in ("jpg", "jpeg") else ext
image_data_url = f"data:image/{mime};base64,{img_b64}"

event = {
    "name": "\U0001f389 NOAH ONAM LIVE 2026",
    "artist": "Siddharth, Jyotsna, Pepe, Siju Wilson, Sanah, Saniya and Midhun",
    "venue": "Quantum Arena, Punggol",
    "date": "2026-08-01",
    "time": "18:00",
    "end_time": "22:00",
    "genre": "Indie",
    "status": "live",
    "rows": 1,
    "cols": 1,
    "total_seats": 1,
    "zones": json.dumps([{
        "name": "General",
        "rows": 1,
        "price": 35,
        "color": "#8b44ff",
        "seat_prefix": "A",
        "seat_start": 1,
        "promotions": {"early_bird": {"enabled": False}, "group_buy": {"enabled": False}},
        "walkway_mode": "none",
        "walkway_width": 1,
        "walkway_numbering": "flow"
    }]),
    "booked_seats": json.dumps([]),
    "held_seats": json.dumps([]),
    "layout_mode": "auto",
    "seat_layout": json.dumps([]),
    "venue_address": json.dumps({
        "address1": "Quantum Arena",
        "address2": "Punggol",
        "address3": "",
        "postal_code": "828649"
    }),
    "addons": json.dumps([]),
    "created_by": "info@noahevents.com",
    "editors": json.dumps([]),
    "images": json.dumps([image_data_url]),
    "artist_bio": "",
    "artist_photo": "",
    "artist_instagram": "",
    "artist_facebook": "",
    "artist_website": "",
    "artist_details": "",
    "organiser_name": "Noah Events",
    "organiser_contact": "+65 92300773",
    "organiser_email": "info@noahevents.com",
    "organiser_logo": "",
    "organiser_address": "#15-01 Robinson Road, 18 Robinson, Singapore 048547",
    "organiser_summary": ""
}

engine = create_engine(DATABASE_URL)

with engine.begin() as conn:
    existing = conn.execute(
        text("SELECT id FROM events WHERE name = :name"),
        {"name": event["name"]}
    ).fetchone()

    if existing:
        print(f"Event '{event['name']}' already exists (id={existing[0]}). Updating...")
        conn.execute(
            text("UPDATE events SET status = 'live' WHERE id = :id"),
            {"id": existing[0]}
        )
        print(f"Done. Event id={existing[0]} set to live.")
    else:
        cols = ", ".join(event.keys())
        placeholders = ", ".join(f":{k}" for k in event.keys())
        result = conn.execute(
            text(f"INSERT INTO events ({cols}) VALUES ({placeholders}) RETURNING id"),
            event
        )
        new_id = result.fetchone()[0]
        print(f"Event created successfully! id={new_id}")
        print(f"  Name:     NOAH ONAM LIVE 2026")
        print(f"  Date:     {event['date']}")
        print(f"  Venue:    {event['venue']}")
        print(f"  Status:   {event['status']}")
