import logging
from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel
from backend.utils.email import send_enquiry_email

logger = logging.getLogger("noah.contact")
router = APIRouter()

from typing import Optional

class EnquiryCreate(BaseModel):
    first_name: str
    last_name: str
    email: str
    contact_number: Optional[str] = ""
    enquiry_type: str
    message: str

@router.post("/")
def send_enquiry(data: EnquiryCreate, bg: BackgroundTasks):
    if not data.first_name.strip() or not data.last_name.strip() or not data.message.strip() or not data.enquiry_type.strip():
        raise HTTPException(status_code=400, detail="All enquiry fields are required")

    bg.add_task(
        send_enquiry_email,
        user_email=data.email.strip(),
        first_name=data.first_name.strip(),
        last_name=data.last_name.strip(),
        contact_number=data.contact_number.strip() if data.contact_number else "",
        enquiry_type=data.enquiry_type.strip(),
        enquiry_message=data.message.strip()
    )

    return {"status": "ok", "message": "Enquiry received"}
