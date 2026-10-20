import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
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
OTP_EXPIRE_MINUTES = 5


def get_allowed_login_email() -> str:
    return (os.getenv("ALLOWED_LOGIN_EMAIL", "info@noahevents.com") or "info@noahevents.com").strip().lower()


def is_allowed_login_email(email: str) -> bool:
    return email.strip().lower() == get_allowed_login_email()


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

    if not is_allowed_login_email(email):
        raise HTTPException(status_code=403, detail="Not authorized")

    domain = email.split("@", 1)[1]
    if domain in COMMON_TYPOS:
        raise HTTPException(
            status_code=400,
            detail=f"Did you mean @{COMMON_TYPOS[domain]}? You typed @{domain}",
        )

    otp = f"{secrets.randbelow(1_000_000):06d}"
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=OTP_EXPIRE_MINUTES)).isoformat()

    user = db.query(User).filter(User.email == email).first()
    if not user:
        user = User(email=email, hashed_password="otp_only")
        db.add(user)
    user.otp_code = otp
    user.otp_expires_at = expires_at
    db.commit()

    if not send_otp_email(email, otp):
        raise HTTPException(
            status_code=500,
            detail="Could not send the login email. Please try again.",
        )

    logger.info("Login code sent to %s", email)
    return {"message": f"Login code sent to {email}"}


@router.post("/verify-otp")
def verify_otp(body: VerifyOtpBody, db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    otp = body.otp.strip()

    user = db.query(User).filter(User.email == email).first()
    if not user or not user.otp_code or not user.otp_expires_at:
        raise HTTPException(status_code=401, detail="Invalid or expired code")

    try:
        expires = datetime.fromisoformat(user.otp_expires_at)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid or expired code")
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)

    if datetime.now(timezone.utc) > expires:
        user.otp_code = None
        user.otp_expires_at = None
        db.commit()
        raise HTTPException(status_code=401, detail="Code expired — request a new one")

    if not secrets.compare_digest(user.otp_code, otp):
        raise HTTPException(status_code=401, detail="Invalid code")

    user.otp_code = None
    user.otp_expires_at = None
    db.commit()

    token = jwt.encode({"sub": str(user.id)}, SECRET, algorithm="HS256")
    logger.info("User %s verified successfully", email)
    return {"access_token": token, "user_id": user.id, "username": email}
