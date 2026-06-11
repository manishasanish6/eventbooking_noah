from sqlalchemy import Column, Integer, String, Float, Boolean, ForeignKey, JSON
from backend.database import Base

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    totp_secret = Column(String, nullable=True)

class Event(Base):
    __tablename__ = "events"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String)
    artist = Column(String)
    venue = Column(String)
    date = Column(String)
    time = Column(String)
    genre = Column(String)
    status = Column(String, default="draft")
    rows = Column(Integer)
    cols = Column(Integer)
    total_seats = Column(Integer)
    zones = Column(JSON)                # list of {name, rows, price, color}
    booked_seats = Column(JSON, default=[])
    end_time = Column(String, default="")
    held_seats = Column(JSON, default=[])
    promotions = Column(JSON, nullable=True)
    layout_mode = Column(String, default="auto")
    seat_layout = Column(JSON, default=[])
    venue_address = Column(JSON, default=None)
    addons = Column(JSON, default=[])   # list of {id, name, type, price, description}
    created_by = Column(String, default="")  # admin email who created the event
    editors = Column(JSON, default=[])   # list of admin emails who can also edit
    images = Column(JSON, default=[])   # list of base64 data URLs

class Booking(Base):
    __tablename__ = "bookings"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    event_id = Column(Integer, ForeignKey("events.id"))
    email = Column(String, index=True)
    seats = Column(JSON)      # ["A1", "A2"]
    total_paid = Column(Float)
    status = Column(String, default="confirmed")
    addons = Column(JSON, default=[])   # ["add_1", "add_3"]