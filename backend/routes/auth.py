import logging, random, re
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from backend.database import get_db
from backend.models import User
from backend.utils.email import send_otp_email
from pydantic import BaseModel, EmailStr
from jose import jwt
import os

logger = logging.getLogger("noah.auth")
router = APIRouter()
SECRET = os.getenv("SECRET_KEY", "change-me")
OTP_EXPIRE_MINUTES = 5

KNOWN_DOMAINS = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com",
    "live.com", "icloud.com", "protonmail.com", "aol.com",
    "mail.com", "yandex.com", "zoho.com", "rediffmail.com"
}

COMMON_TYPOS = {
    "gamil.com": "gmail.com",
    "gmial.com": "gmail.com",
    "gnail.com": "gmail.com",
    "gmil.com": "gmail.com",
    "gmal.com": "gmail.com",
    "gmai.com": "gmail.com",
    "hotmai.com": "hotmail.com",
    "hotmal.com": "hotmail.com",
    "yaho.com": "yahoo.com",
    "outloo.com": "outlook.com",
    "outlok.com": "outlook.com",
}

class SendOtpBody(BaseModel):
    email: str

class VerifyOtpBody(BaseModel):
    email: str
    otp: str

@router.post("/send-otp")
def send_otp(body: SendOtpBody, db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Valid email required")

    # Check for common domain typos
    parts = email.split("@", 1)
    if len(parts) == 2:
        domain = parts[1].strip()
        if domain in COMMON_TYPOS:
            suggestion = COMMON_TYPOS[domain]
            raise HTTPException(
                status_code=400,
                detail=f"Did you mean @{suggestion}? You typed @{domain}"
            )

    otp = str(random.randint(100000, 999999))
    expires_at = (datetime.utcnow() + timedelta(minutes=OTP_EXPIRE_MINUTES)).isoformat()

    user = db.query(User).filter(User.email == email).first()
    if not user:
        user = User(email=email, hashed_password="otp_only")
        db.add(user)

    user.otp_code = otp
    user.otp_expires_at = expires_at
    db.commit()

    send_otp_email(email, otp)
    logger.info("OTP sent to %s", email)
    return {"message": "OTP sent"}

@router.post("/verify-otp")
def verify_otp(body: VerifyOtpBody, db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    otp = body.otp.strip()

    user = db.query(User).filter(User.email == email).first()
    if not user:
        raise HTTPException(status_code=401, detail="Invalid OTP")

    if not user.otp_code or not user.otp_expires_at:
        raise HTTPException(status_code=401, detail="No OTP requested")

    if user.otp_code != otp:
        raise HTTPException(status_code=401, detail="Invalid OTP")

    try:
        expires = datetime.fromisoformat(user.otp_expires_at)
        if datetime.utcnow() > expires:
            raise HTTPException(status_code=401, detail="OTP expired")
    except Exception:
        raise HTTPException(status_code=401, detail="OTP expired")

    user.otp_code = None
    user.otp_expires_at = None
    db.commit()

    token = jwt.encode({"sub": str(user.id)}, SECRET, algorithm="HS256")
    logger.info("User %s verified successfully", email)
    return {"access_token": token, "user_id": user.id, "username": email}
