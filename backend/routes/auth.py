import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from backend.database import get_db
from backend.models import User
from pydantic import BaseModel
from jose import jwt
import os

logger = logging.getLogger("noah.auth")
router = APIRouter()
SECRET = os.getenv("SECRET_KEY", "change-me")

VALID_USERS = {"admin1", "admin2", "admin3", "admin4"}
COMMON_PASSWORD = "admin"


class LoginBody(BaseModel):
    username: str
    password: str


@router.post("/login")
def login(body: LoginBody, db: Session = Depends(get_db)):
    name = body.username.strip().lower()
    if name not in VALID_USERS or body.password != COMMON_PASSWORD:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    user = db.query(User).filter(User.email == name).first()
    if not user:
        user = User(
            email=name,
            hashed_password="admin_fixed",
            otp_code=None,
            otp_expires_at=None,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    token = jwt.encode({"sub": str(user.id)}, SECRET, algorithm="HS256")
    return {"access_token": token, "user_id": user.id, "username": name}
