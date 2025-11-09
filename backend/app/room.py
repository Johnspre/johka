from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session
from db import get_db  # pas dit aan naar je bestaande get_db()

router = APIRouter(prefix="/api/room", tags=["Rooms"])

# 🟠 Tijdelijk wijzigen van room subject
@router.post("/set-subject")
def set_room_subject(data: dict, s: Session = Depends(get_db)):
    room_id = data.get("room_id")
    subject = data.get("subject", "").strip()

    if not room_id:
        raise HTTPException(status_code=400, detail="room_id required")

    if len(subject) > 100:
        raise HTTPException(status_code=400, detail="Subject too long")

    s.execute(text("UPDATE rooms SET temp_subject = :subj WHERE id = :id"), {"subj": subject, "id": room_id})
    s.commit()
    return {"detail": "Temporary subject set", "subject": subject}

# 🟢 Ophalen van huidige roomtitel (valt terug op originele name)
@router.get("/current/{room_id}")
def get_room_subject(room_id: int, s: Session = Depends(get_db)):
    row = s.execute(text("""
        SELECT COALESCE(temp_subject, name) AS subject
        FROM rooms WHERE id = :id
    """), {"id": room_id}).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Room not found")
    return {"subject": row.subject}

# 🔵 Resetten (bij einde uitzending)
@router.post("/reset-subject")
def reset_room_subject(data: dict, s: Session = Depends(get_db)):
    room_id = data.get("room_id")
    if not room_id:
        raise HTTPException(status_code=400, detail="room_id required")

    s.execute(text("UPDATE rooms SET temp_subject = NULL WHERE id = :id"), {"id": room_id})
    s.commit()
    return {"detail": "Room subject reset to default"}
