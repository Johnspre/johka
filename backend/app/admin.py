from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
import os

# --- Database connectie ---
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg2://johka:SuperSterkWachtwoord123@postgres:5432/johka")
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- Router ---
router = APIRouter(prefix="/api/admin", tags=["Admin"])

# --- Admin verificatie ---
def verify_admin(adminkey: str = Header(None)):
    if adminkey != os.getenv("ADMIN_KEY", "Admin"):
        raise HTTPException(status_code=401, detail="Unauthorized admin key")
    return True

# === ROUTES ===

# ✅ 1. Gebruikerslijst met saldo
@router.get("/users")
def get_users(s: Session = Depends(get_db), auth: bool = Depends(verify_admin)):
    rows = s.execute(text("""
        SELECT u.id, u.username, u.email, u.created_at, u.is_verified,
               COALESCE(w.balance, 0) AS balance
        FROM users u
        LEFT JOIN wallets w ON u.id = w.user_id
        ORDER BY u.created_at DESC
    """)).fetchall()
    return [dict(r._mapping) for r in rows]


# ✅ 2. Transactiegeschiedenis (wallet_history)
@router.get("/history")
def get_wallet_history(s: Session = Depends(get_db), auth: bool = Depends(verify_admin)):
    rows = s.execute(text("""
        SELECT h.id, u.username, h.change, h.reason, h.created_at
        FROM wallet_history h
        JOIN users u ON u.id = h.user_id
        ORDER BY h.created_at DESC
        LIMIT 100
    """)).fetchall()
    # 👇 hier wordt username → user gezet
    return [
        {
            "id": r.id,
            "user": r.username,  # frontend verwacht dit veld
            "change": r.change,
            "reason": r.reason,
            "created_at": r.created_at
        }
        for r in rows
    ]


# ✅ 3. Saldo direct instellen
@router.post("/set_balance")
def set_balance(data: dict, s: Session = Depends(get_db), auth: bool = Depends(verify_admin)):
    user_id = data.get("user_id")
    new_balance = data.get("new_balance", 0)
    if not user_id:
        raise HTTPException(400, "user_id is required")

    s.execute(text("UPDATE wallets SET balance = :b WHERE user_id = :u"), {"b": new_balance, "u": user_id})
    s.execute(text("""
        INSERT INTO wallet_history (user_id, change, reason)
        VALUES (:u, :c, 'Admin aanpassing')
    """), {"u": user_id, "c": new_balance})
    s.commit()
    return {"detail": f"Saldo aangepast naar {new_balance}"}


# ✅ 4. Tokens toevoegen via admin
@router.post("/add-tokens")
def admin_add_tokens(data: dict, s: Session = Depends(get_db), auth: bool = Depends(verify_admin)):
    user_id = data.get("user_id")
    amount = int(data.get("amount", 0))

    if not user_id or amount <= 0:
        raise HTTPException(status_code=400, detail="Invalid amount or user_id")

    # ✅ Wallet ophalen
    wallet = s.execute(text("SELECT id, balance FROM wallets WHERE user_id = :uid"), {"uid": user_id}).fetchone()
    if not wallet:
        raise HTTPException(status_code=404, detail="Wallet not found")

    # ✅ Update saldo
    new_balance = wallet.balance + amount
    s.execute(text("UPDATE wallets SET balance = :bal WHERE user_id = :uid"),
              {"bal": new_balance, "uid": user_id})

    # ✅ Log transactie
    s.execute(text("""
        INSERT INTO wallet_history (user_id, change, reason)
        VALUES (:uid, :chg, 'Admin toevoeging')
    """), {"uid": user_id, "chg": amount})
    s.commit()

    return {"status": "ok", "message": f"{amount} tokens toegevoegd aan gebruiker {user_id}"}


# ✅ 5. Gebruiker verwijderen
@router.delete("/delete/{user_id}")
def delete_user(user_id: int, s: Session = Depends(get_db), auth: bool = Depends(verify_admin)):
    s.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
    s.commit()
    return {"detail": f"Gebruiker {user_id} verwijderd."}

# 🔍 Gebruiker zoeken
@router.get("/search")
def search_users(q: str, s: Session = Depends(get_db), auth: bool = Depends(verify_admin)):
    rows = s.execute(text("""
        SELECT u.id, u.username, u.email, u.is_verified, u.blocked,
               COALESCE(w.balance, 0) AS balance
        FROM users u
        LEFT JOIN wallets w ON u.id = w.user_id
        WHERE u.username ILIKE :q OR u.email ILIKE :q
        ORDER BY u.username ASC
    """), {"q": f"%{q}%"}).fetchall()
    return [dict(r._mapping) for r in rows]


# ✏️ Gebruiker bijwerken
@router.post("/update-user")
def update_user(data: dict, s: Session = Depends(get_db), auth: bool = Depends(verify_admin)):
    user_id = data.get("id")
    email = data.get("email")
    bio = data.get("bio")
    verified = data.get("is_verified")

    if not user_id:
        raise HTTPException(400, "user_id is required")

    s.execute(text("""
        UPDATE users
        SET email = COALESCE(:email, email),
            bio = COALESCE(:bio, bio),
            is_verified = COALESCE(:verified, is_verified)
        WHERE id = :id
    """), {"email": email, "bio": bio, "verified": verified, "id": user_id})
    s.commit()
    return {"detail": f"Gebruiker {user_id} bijgewerkt."}


# 🚫 Blokkeren / deblokkeren
@router.post("/block-user")
def block_user(data: dict, s: Session = Depends(get_db), auth: bool = Depends(verify_admin)):
    user_id = data.get("id")
    blocked = data.get("blocked", True)
    if not user_id:
        raise HTTPException(400, "user_id is required")

    s.execute(text("UPDATE users SET blocked = :b WHERE id = :id"), {"b": blocked, "id": user_id})
    s.commit()
    return {"detail": f"Gebruiker {user_id} {'geblokkeerd' if blocked else 'gedeblokkeerd'}."}


# 📊 Statistieken
@router.get("/stats")
def admin_stats(s: Session = Depends(get_db), auth: bool = Depends(verify_admin)):
    stats = {}
    stats["users"] = s.execute(text("SELECT COUNT(*) FROM users")).scalar()
    stats["blocked"] = s.execute(text("SELECT COUNT(*) FROM users WHERE blocked = true")).scalar()
    stats["verified"] = s.execute(text("SELECT COUNT(*) FROM users WHERE is_verified = true")).scalar()
    stats["wallet_total"] = s.execute(text("SELECT SUM(balance) FROM wallets")).scalar() or 0
    stats["transactions"] = s.execute(text("SELECT COUNT(*) FROM wallet_history")).scalar()
    return stats


# === ROOM LOGS & STATISTIEKEN ===

from sqlalchemy import text
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
import os

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg2://johka:SuperSterkWachtwoord123@postgres:5432/johka")
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Zorg dat dit in dezelfde router zit als je andere admin routes
# (dus: router = APIRouter(prefix="/api/admin", tags=["Admin"]))

# 📜 Route 1: Laatste room logs
@router.get("/room-logs")
def get_room_logs(s: Session = Depends(get_db)):
    """Toont de laatste 100 gebeurtenissen in rooms."""
    rows = s.execute(text("""
        SELECT l.id, l.created_at, u.username, r.slug, l.action, l.info
        FROM room_logs l
        LEFT JOIN users u ON u.id = l.user_id
        LEFT JOIN rooms r ON r.id = l.room_id
        ORDER BY l.created_at DESC
        LIMIT 100
    """)).fetchall()

    return [dict(r._mapping) for r in rows]


# 📊 Route 2: Algemene room-statistieken
@router.get("/room-stats")
def get_room_stats(s: Session = Depends(get_db)):
    """Geeft algemene statistieken over rooms, streams en tips."""
    stats = s.execute(text("""
        SELECT
            (SELECT COUNT(*) FROM rooms) AS total_rooms,
            (SELECT COUNT(DISTINCT room_id) FROM room_logs WHERE action='start_stream') AS total_streams,
            (SELECT COUNT(DISTINCT user_id) FROM room_logs WHERE action='tip_sent') AS active_tippers,
            (SELECT COUNT(*) FROM room_logs WHERE action='tip_sent') AS total_tips,
            (SELECT COUNT(DISTINCT user_id) FROM users WHERE is_verified=true) AS verified_users
    """)).fetchone()

    return dict(stats._mapping)
