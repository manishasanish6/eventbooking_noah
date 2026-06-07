import logging
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session
from backend.database import get_db
from backend.models import User
from backend.utils.email import send_booking_confirmation
from pydantic import BaseModel
import pyotp
import os
from jose import jwt

logger = logging.getLogger("noah.auth")
router = APIRouter()
SECRET = os.getenv("SECRET_KEY", "change-me")

class EmailBody(BaseModel):
    email: str

class TotpBody(BaseModel):
    email: str
    code: str

@router.post("/check-email")
def check_email(body: EmailBody, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == body.email).first()
    if not user:
        raise HTTPException(status_code=404, detail="Invalid user")

    if not user.totp_secret:
        secret = pyotp.random_base32()
        user.totp_secret = secret
        db.commit()
        uri = pyotp.totp.TOTP(secret).provisioning_uri(
            name=user.email, issuer_name="NoahEvents"
        )
        return {"status": "setup", "provisioning_uri": uri}

    return {"status": "verify"}

@router.post("/setup-totp")
def setup_totp(body: TotpBody, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == body.email).first()
    if not user or not user.totp_secret:
        raise HTTPException(status_code=400, detail="Setup required")

    if not pyotp.TOTP(user.totp_secret).verify(body.code, valid_window=1):
        raise HTTPException(status_code=401, detail="Invalid code")

    token = jwt.encode({"sub": str(user.id)}, SECRET, algorithm="HS256")
    return {"access_token": token, "user_id": user.id, "email": user.email}

@router.post("/verify-totp")
def verify_totp(body: TotpBody, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == body.email).first()
    if not user or not user.totp_secret:
        raise HTTPException(status_code=400, detail="Invalid user")

    if not pyotp.TOTP(user.totp_secret).verify(body.code, valid_window=1):
        raise HTTPException(status_code=401, detail="Invalid code")

    token = jwt.encode({"sub": str(user.id)}, SECRET, algorithm="HS256")
    return {"access_token": token, "user_id": user.id, "email": user.email}

@router.post("/test-email")
def test_email(body: EmailBody, bg: BackgroundTasks):
    bg.add_task(
        send_booking_confirmation,
        to_email=body.email,
        event_name="TEST EMAIL",
        seats=["A1", "A2"],
        total=0.00,
        venue="Noah Events Test",
        date="N/A",
        time="",
        booking_id=0,
        ticket_codes={"A1": "NOAH-0-A1", "A2": "NOAH-0-A2"}
    )
    return {"status": "ok", "message": f"Test email queued to {body.email}"}
