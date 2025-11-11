from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from database import engine, get_db
from models import PrivateMessage, UserDB
from auth import get_current_user  # ⚠️ pas aan naar jouw daadwerkelijke auth-helper



router = APIRouter(prefix="/api/dm", tags=["Direct Messages"])

class MessageIn(BaseModel):
    to_username: str
    message: str

@router.post("/send")
def send_dm(data: MessageIn, user: UserDB = Depends(get_current_user), s: Session = Depends(get_db)):
    target = s.query(UserDB).filter_by(username=data.to_username).first()
    if not target:
        raise HTTPException(404, "Gebruiker niet gevonden")

    msg = PrivateMessage(sender_id=user.id, receiver_id=target.id, message=data.message)
    s.add(msg)
    s.commit()
    return {"ok": True, "message": "Bericht verzonden"}

@router.get("/inbox")
def get_inbox(user: UserDB = Depends(get_current_user), s: Session = Depends(get_db)):
    msgs = (
        s.query(PrivateMessage)
        .filter(PrivateMessage.receiver_id == user.id)
        .order_by(PrivateMessage.created_at.desc())
        .limit(50)
        .all()
    )
    return [
        {
            "from": m.sender.username,
            "message": m.message,
            "time": m.created_at.isoformat(),
            "read": m.read
        }
        for m in msgs
    ]
