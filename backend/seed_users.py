from backend.database import engine, Base
from backend.models import User
from sqlalchemy.orm import sessionmaker
import pyotp
import urllib.parse

Base.metadata.create_all(bind=engine)
Session = sessionmaker(bind=engine)
db = Session()

emails = ["manisha61090@gmail.com", "sanish.jony@gmail.com", "accounts@noahmarinegroup.com"]

for email in emails:
    existing = db.query(User).filter(User.email == email).first()
    if existing:
        print(f"User {email} already exists, skipping.")
        continue

    user = User(
        email=email,
        hashed_password="totp_only",
        totp_secret=None
    )
    db.add(user)
    db.commit()
    print(f"Created user: {email}")
    print(f"TOTP secret will be generated on first login via QR code")
    print()

db.close()
