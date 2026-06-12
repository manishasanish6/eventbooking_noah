import logging
import random
import string
from datetime import datetime, timedelta
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session
from backend.database import get_db
from backend.models import User
from backend.utils.email import send_otp_email
from pydantic import BaseModel
from jose import jwt
import os

logger = logging.getLogger("noah.auth")
router = APIRouter()
SECRET = os.getenv("SECRET_KEY", "change-me")


class EmailBody(BaseModel):
    email: str

class OtpBody(BaseModel):
    email: str
    code: str


@router.post("/check-email")
def check_email(body: EmailBody, bg: BackgroundTasks, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == body.email).first()
    if not user:
        user = User(
            email=body.email,
            hashed_password="otp_only",
            totp_secret=None,
            otp_code=None,
            otp_expires_at=None,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    otp = "".join(random.choices(string.digits, k=6))
    user.otp_code = otp
    user.otp_expires_at = (datetime.utcnow() + timedelta(minutes=5)).isoformat()
    db.commit()

    bg.add_task(send_otp_email, to_email=body.email, otp_code=otp)

    return {"status": "otp_sent"}


@router.post("/verify-otp")
def verify_otp(body: OtpBody, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == body.email).first()
    if not user or not user.otp_code or not user.otp_expires_at:
        raise HTTPException(status_code=400, detail="No OTP requested")

    expires = datetime.fromisoformat(user.otp_expires_at)
    if datetime.utcnow() > expires:
        user.otp_code = None
        user.otp_expires_at = None
        db.commit()
        raise HTTPException(status_code=401, detail="OTP expired")

    if user.otp_code != body.code:
        raise HTTPException(status_code=401, detail="Invalid code")

    user.otp_code = None
    user.otp_expires_at = None
    db.commit()

    token = jwt.encode({"sub": str(user.id)}, SECRET, algorithm="HS256")
    return {"access_token": token, "user_id": user.id, "email": user.email}
