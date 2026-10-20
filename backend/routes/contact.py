from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
router = APIRouter()

from typing import Optional

from backend.utils.email import send_enquiry_email

class EnquiryCreate(BaseModel):
    first_name: str
    last_name: str
    email: str
    contact_number: Optional[str] = ""
    enquiry_type: str
    message: str

@router.post("/")
def send_enquiry(data: EnquiryCreate):
    if not data.first_name.strip() or not data.last_name.strip() or not data.message.strip() or not data.enquiry_type.strip():
        raise HTTPException(status_code=400, detail="All enquiry fields are required")

    sent = send_enquiry_email(
        data.first_name.strip(), data.last_name.strip(), data.email.strip(),
        data.contact_number.strip(), data.enquiry_type.strip(), data.message.strip(),
    )
    if not sent:
        raise HTTPException(status_code=502, detail="Could not send your enquiry right now. Please try again or email us directly.")

    return {"status": "ok", "message": "Enquiry received"}
