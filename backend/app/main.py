# main.py
# ============================================
# JOHKA LIVE - COMPLETE BACKEND (STABLE)
# ============================================

import os
from uuid import uuid4
import re
import time
import base64
import shutil
from io import BytesIO
from datetime import datetime
from typing import Optional, List

from dotenv import load_dotenv

# ---------- FastAPI & Security ----------
from fastapi import (
    FastAPI, Depends, HTTPException, status, Header,
    UploadFile, File, Request, Body
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer
from starlette.responses import JSONResponse
from fastapi import Header
# ---------- DB ----------
import psycopg2
from sqlalchemy import (
    create_engine, Column, Integer, String, DateTime, func,
    ForeignKey, UniqueConstraint, text, or_, Text,
)
from sqlalchemy.orm import sessionmaker, declarative_base, relationship, Session
from sqlalchemy.exc import IntegrityError

# ---------- Auth / Hashing ----------
from passlib.context import CryptContext
from jose import jwt, JWTError
from pydantic import BaseModel, EmailStr

# ---------- Images ----------
from PIL import Image

# ---------- LiveKit ----------
#   pip install livekit-api
#from livekit import AccessToken, VideoGrant
from fastapi.staticfiles import StaticFiles

load_dotenv()


def _get_env(name: str, default: Optional[str] = None, *, required: bool = False) -> Optional[str]:
    value = os.getenv(name, default)
    if required and (value is None or value == ""):
        raise RuntimeError(f"Environment variable '{name}' is required")
    return value


app = FastAPI(title="Johka Live API", version="1.0")


# ============================================
# ENV & CONFIG
# ============================================
JWT_SECRET = _get_env("JWT_SECRET", "MyUltraSecretKey")
JWT_ALGORITHM = "HS256"

# DB (Docker service 'postgres' in compose)
POSTGRES_USER = _get_env("POSTGRES_USER", "johka")
POSTGRES_PASSWORD = _get_env("POSTGRES_PASSWORD", required=True)
POSTGRES_HOST = _get_env("POSTGRES_HOST", "postgres")
POSTGRES_PORT = _get_env("POSTGRES_PORT", "5432")
POSTGRES_DB = _get_env("POSTGRES_DB", "johka")

SQLALCHEMY_URL = _get_env("SQLALCHEMY_URL") or (
    f"postgresql+psycopg2://{POSTGRES_USER}:{POSTGRES_PASSWORD}@"
    f"{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"
)
PSYCOPG_URL = _get_env("PSYCOPG_URL") or (
    f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@"
    f"{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"
)

# LiveKit
LIVEKIT_API_KEY = _get_env("LIVEKIT_API_KEY", "johka_live_key")
LIVEKIT_API_SECRET = _get_env("LIVEKIT_API_SECRET", required=True)
LIVEKIT_URL = _get_env("LIVEKIT_URL", "wss://live.johka.be")


# Upload dirs
UPLOAD_ROOT = "/app/static/uploads"
AVATAR_DIR = os.path.join(UPLOAD_ROOT, "avatars")
GALLERY_DIR = os.path.join(UPLOAD_ROOT, "gallery")
PREVIEW_DIR = os.path.join(UPLOAD_ROOT, "previews")
os.makedirs(AVATAR_DIR, exist_ok=True)
os.makedirs(GALLERY_DIR, exist_ok=True)
os.makedirs(PREVIEW_DIR, exist_ok=True)

def _preview_url(filename: Optional[str]) -> Optional[str]:
    if not filename:
        return None
    return f"https://api.johka.be/static/uploads/previews/{filename}"

ADMIN_KEY = _get_env("ADMIN_KEY", required=True)

def verify_admin(adminkey: str = Header(None), key: str = None):
    """Controleer of geldige admin key is meegegeven"""
    if adminkey == ADMIN_KEY or key == ADMIN_KEY:
        return True
    raise HTTPException(status_code=403, detail="Geen toegang: ongeldige admin key")


# ============================================
# APP & CORS
# ============================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://johka.be",
        "https://www.johka.be",
        "https://api.johka.be",
        "https://live.johka.be",
        "http://localhost:8000",
        "http://localhost:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],  # 👈 belangrijk: laat JS de response lezen
)
# 👇 serveer statische bestanden (previews, avatars, ...)
app.mount("/static", StaticFiles(directory="/app/static"), name="static")

# ============================================
# DATABASE
# ============================================
pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
engine = create_engine(SQLALCHEMY_URL, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


def db() -> Session:
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


# ============================================
# MODELS
# ============================================
class UserDB(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String(32), unique=True, nullable=False, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    bio = Column(Text, nullable=True, default="")
    # dynamic columns (avatar_url, gallery_json, wallet_balance) kunnen later via ALTER worden toegevoegd
    room = relationship("RoomDB", back_populates="owner", uselist=False)
    wallet = relationship("Wallet", back_populates="owner", uselist=False)


class RoomDB(Base):
    __tablename__ = "rooms"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True)
    name = Column(String(64), nullable=False)
    slug = Column(String(64), unique=True, nullable=False, index=True)
    created_at = Column(DateTime, server_default=func.now())
    owner = relationship("UserDB", back_populates="room")
    __table_args__ = (UniqueConstraint("slug", name="uq_room_slug"),)


class Wallet(Base):
    __tablename__ = "wallets"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True)
    balance = Column(Integer, default=0)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    owner = relationship("UserDB", back_populates="wallet")

class WalletHistory(Base):
    __tablename__ = "wallet_history"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    change = Column(Integer, nullable=False)
    reason = Column(String, default="mollie")
    created_at = Column(DateTime, server_default=func.now())


class Tip(Base):
    __tablename__ = "tips"
    id = Column(Integer, primary_key=True)
    from_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    to_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    amount = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    from_user = relationship("UserDB", foreign_keys=[from_user_id])
    to_user = relationship("UserDB", foreign_keys=[to_user_id])


# ============================================
# SCHEMAS
# ============================================
class RegisterIn(BaseModel):
    username: str
    email: EmailStr
    password: str


class LoginIn(BaseModel):
    username: str
    password: str


class MeOut(BaseModel):
    id: int
    username: str
    email: EmailStr
    bio: Optional[str] = ""
    room_slug: Optional[str] = None


class RoomOut(BaseModel):
    slug: str
    name: str
    owner: str


class TipIn(BaseModel):
    to_username: str
    amount: int


# ============================================
# HELPERS
# ============================================
def hash_password(p: str) -> str:
    return pwd_ctx.hash(p)


def verify_password(p: str, h: str) -> bool:
    return pwd_ctx.verify(p, h)


def slugify(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "room"


def create_access_token(payload: dict, expires_seconds: int = 60 * 60 * 12) -> str:
    now = int(time.time())
    to_encode = {"iat": now, "nbf": now, "exp": now + expires_seconds, **payload}
    return jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGORITHM)


def _psql():
    return psycopg2.connect(PSYCOPG_URL)


# ============================================
# AUTH – get_current_user
# ============================================
def _resolve_authorization_user(
    token_header: Optional[str],
    s: Session,
) -> UserDB:
    
    if not token_header:
        raise HTTPException(status_code=401, detail="Missing Authorization header")

    parts = token_header.strip().split(" ")
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="Invalid Authorization format")

    token = parts[1].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Empty token")

    try:
        data = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

    uid = data.get("sub")
    if not uid:
        raise HTTPException(status_code=401, detail="Invalid token payload")

    u = s.get(UserDB, int(uid))
    if not u:
        raise HTTPException(status_code=401, detail="User not found")
    return u
def get_current_user(
    request: Request,
    authorization: Optional[str] = Header(None),
    s: Session = Depends(db),
) -> UserDB:
    token_header = authorization or request.headers.get("authorization")
    return _resolve_authorization_user(token_header, s)


def get_optional_user(
    request: Request,
    authorization: Optional[str] = Header(None),
    s: Session = Depends(db),
) -> Optional[UserDB]:
    token_header = authorization or request.headers.get("authorization")
    if not token_header:
        return None
    return _resolve_authorization_user(token_header, s)



# ============================================
# STARTUP (migraties / basis)
# ============================================
@app.on_event("startup")
def on_startup():
    Base.metadata.create_all(engine)
    # Zorg dat iedereen een wallet heeft
    with SessionLocal() as s:
        try:
            s.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS bio TEXT"))
            s.commit()
        except Exception:
            s.rollback()
        users = s.query(UserDB).all()
        for u in users:
            if not u.wallet:
                s.add(Wallet(user_id=u.id, balance=100))
        s.commit()


# ============================================
# BASIC ROUTES
# ============================================
@app.get("/api/health")
def health():
    return {"status": "ok"}


# ============================================
# 🧩 Registratie + e-mailverificatie + login
# ============================================
from datetime import date
import secrets, asyncio, os
from fastapi_mail import FastMail, MessageSchema, ConnectionConfig
from fastapi import HTTPException, Depends
from sqlalchemy.orm import Session

# 📨 Mailconfig (leest uit .env)
conf = ConnectionConfig(
    MAIL_USERNAME=os.getenv("SMTP_USER"),
    MAIL_PASSWORD=os.getenv("SMTP_PASS"),
    MAIL_FROM=os.getenv("SMTP_FROM", "noreply@johka.be"),
    MAIL_PORT=int(os.getenv("SMTP_PORT", 2525)),
    MAIL_SERVER=os.getenv("SMTP_HOST", "sandbox.smtp.mailtrap.io"),
    MAIL_STARTTLS=True,
    MAIL_SSL_TLS=False,
    USE_CREDENTIALS=True,
)


def bereken_leeftijd(geboortedatum):
    vandaag = date.today()
    return vandaag.year - geboortedatum.year - (
        (vandaag.month, vandaag.day) < (geboortedatum.month, geboortedatum.day)
    )
# ============================================
# 📧 TEST ENDPOINT MAILTRAP
# ============================================

@app.get("/api/test-mail")
async def test_mail():
    try:
        fm = FastMail(conf)
        msg = MessageSchema(
            subject="✅ Testmail van Johka",
            recipients=["test@johka.be"],
            body="Als je dit bericht ziet in Mailtrap, werkt je SMTP-config perfect!",
            subtype="plain",
        )
        await fm.send_message(msg)
        return {"status": "ok", "message": "Testmail verzonden (check Mailtrap inbox)."}
    except Exception as e:
        print(f"❌ Mailfout: {e}")
        return {"status": "error", "detail": str(e)}

# ============================================
# 🧠 REGISTRATIE
# ============================================
@app.post("/api/register")
async def register_user(data: dict, s: Session = Depends(db)):
    # ✅ Controle: wachtwoorden moeten overeenkomen
    if data["password"] != data.get("password2"):
        raise HTTPException(status_code=400, detail="Wachtwoorden komen niet overeen.")

    # ✅ Controle: leeftijd
    geboortedatum_str = data.get("birthdate")
    if not geboortedatum_str:
        raise HTTPException(status_code=400, detail="Geboortedatum ontbreekt.")
    geboortedatum = date.fromisoformat(geboortedatum_str)
    if bereken_leeftijd(geboortedatum) < 18:
        raise HTTPException(status_code=400, detail="Je moet minstens 18 jaar oud zijn.")

    # ✅ Controle: dubbele gebruikers
    if s.query(User).filter_by(email=data["email"]).first():
        raise HTTPException(status_code=400, detail="E-mailadres is al geregistreerd.")
    if s.query(User).filter_by(username=data["username"]).first():
        raise HTTPException(status_code=400, detail="Gebruikersnaam is al in gebruik.")

    # ✅ Nieuw account
    token = secrets.token_urlsafe(32)
    user = User(
        username=data["username"],
        email=data["email"],
        hashed_pw=hash_password(data["password"]),
        birthdate=geboortedatum,
        verify_token=token,
        is_verified=False,
    )
    s.add(user)
    s.commit()

    # ✅ Verificatie-mail versturen
    link = f"https://api.johka.be/api/verify-email?token={token}"
    body = f"""Welkom bij Johka Live!

Klik op onderstaande link om je e-mail te bevestigen:
{link}

Als jij dit niet was, negeer dan deze mail."""

    fm = FastMail(conf)
    msg = MessageSchema(
        subject="Bevestig je Johka-account",
        recipients=[user.email],
        body=body,
        subtype="plain",
    )
    asyncio.create_task(fm.send_message(msg))

    return {"status": "pending", "message": "Verificatiemail verzonden. Controleer je inbox!"}


# ============================================
# ✉️ E-MAIL VERIFICATIE
# ============================================
@app.get("/api/verify-email")
def verify_email(token: str, s: Session = Depends(db)):
    user = s.query(User).filter_by(verify_token=token).first()
    if not user:
        raise HTTPException(status_code=400, detail="Ongeldige of verlopen verificatielink.")
    user.is_verified = True
    user.verify_token = None
    s.commit()
    return {"status": "ok", "message": "E-mail succesvol geverifieerd!"}


# ============================================
# 🔐 LOGIN
# ============================================
@app.post("/api/login")
def login_user(data: dict, s: Session = Depends(db)):
    user = s.query(User).filter_by(username=data["username"]).first()
    if not user or not verify_password(data["password"], user.hashed_pw):
        raise HTTPException(status_code=400, detail="Ongeldige login.")
    if not user.is_verified:
        raise HTTPException(status_code=403, detail="Verifieer eerst je e-mailadres.")
    token = generate_jwt_token(user)
    return {"status": "ok", "token": token, "username": user.username}



@app.get("/api/me", response_model=MeOut)
def me(user: UserDB = Depends(get_current_user)):
    room_slug = user.room.slug if user.room else None
    return MeOut(
        id=user.id,
        username=user.username,
        email=user.email,
        bio=user.bio or "",
        room_slug=room_slug,
    )


@app.get("/api/rooms", response_model=List[RoomOut])
def list_rooms(s: Session = Depends(db)):
    rows = s.query(RoomDB).join(UserDB, RoomDB.user_id == UserDB.id).all()
    return [RoomOut(slug=r.slug, name=r.name, owner=r.owner.username) for r in rows]

@app.get("/api/rooms/{slug}")
def get_room(slug: str, s: Session = Depends(db)):
    normalized = _normalize_room_slug(slug)
    room = (
        s.query(RoomDB)
        .join(UserDB, RoomDB.user_id == UserDB.id)
        .filter(RoomDB.slug == normalized)
        .first()
    )
    if not room:
        raise HTTPException(status_code=404, detail="Room niet gevonden")
    live_slug = None
    live_viewers = 0
    preview_url = None
    try:
        with engine.connect() as conn:
            res = conn.execute(
                text(
                    """
                SELECT room_slug, COALESCE(viewers, 0) AS viewers, snapshot
                  FROM live_sessions
                 WHERE user_id = :uid AND ended_at IS NULL
                 LIMIT 1
                """
                ),
                {"uid": room.owner.id},
            ).first()
            if res:
                live_slug = res._mapping["room_slug"]
                live_viewers = res._mapping["viewers"]
                preview_url = _preview_url(res._mapping["snapshot"])
    except Exception:
        pass
    return {
        "slug": room.slug,
        "name": room.name,
        "owner": room.owner.username,
        "livekit_room": f"{room.slug}-room",
        "live_slug": live_slug,
        "is_live": bool(live_slug),
        "viewers": live_viewers,
        "preview_url": preview_url,
    }


# ==========================================
# 🎥 LIVEKIT TOKEN GENERATOR (v1.0.17)
# ==========================================
# =========================================================
# 🎥 LIVEKIT TOKEN GENERATOR (v3 – compatibel met LiveKit 1.9.x)
# =========================================================


class LivekitTokenRequest(BaseModel):
    room_slug: Optional[str] = None


def _normalize_room_slug(raw_slug: str) -> str:
    """Normalize inkomende slug zodat die overeenkomt met RoomDB.slug."""

    slug = raw_slug.strip().lower()
    if slug.endswith("-room"):
        slug = slug[: -len("-room")]
    return slugify(slug)

@app.post("/api/livekit-token")
async def create_livekit_token(
    payload: Optional[LivekitTokenRequest] = Body(default=None),
    user: Optional[UserDB] = Depends(get_optional_user),
    s: Session = Depends(db),
):
    payload = payload or LivekitTokenRequest()
    
    requested_slug = payload.room_slug.strip() if payload.room_slug else None

    owner_room = None
    target_room = None
    is_owner = False
    can_chat = False

    if user:
        owner_room = s.query(RoomDB).filter(RoomDB.user_id == user.id).first()

    if user and not requested_slug:
        if owner_room:
            room_name = f"{owner_room.slug}-room"
        else:
            fallback_slug = slugify(user.username or "room")
            room_name = f"{fallback_slug}-room"
        room_owner_username = user.username
        is_owner = True
        base_identity = user.username or f"user-{user.id}"
        identity = base_identity
        can_chat = True
    else:
        if not requested_slug:
            raise HTTPException(status_code=400, detail="room_slug is vereist")
        normalized_slug = _normalize_room_slug(requested_slug)
        target_room = s.query(RoomDB).filter(RoomDB.slug == normalized_slug).first()
        if not target_room:
            raise HTTPException(status_code=404, detail="Room niet gevonden")
        room_owner_username = target_room.owner.username
        room_name = f"{target_room.slug}-room"
        if user:
            is_owner = owner_room is not None and owner_room.id == target_room.id
            base_identity = user.username or f"user-{user.id}"
            identity = base_identity
            can_chat = True
        else:
            identity = f"gast-{uuid4().hex[:6]}"
            can_chat = False

    if user and not is_owner:
        identity = f"{identity}#{uuid4().hex[:6]}"

    

    now = int(time.time())
    exp = now + (12 * 3600)

    
    grants = {
        "room": room_name,
        "roomJoin": True,
        "roomCreate": is_owner,
        "canPublish": is_owner,
        "canSubscribe": True,
        "canPublishData": can_chat,
    }

    payload = {
        "iss": LIVEKIT_API_KEY,
        "sub": identity,
        "nbf": now - 10,
        "exp": exp,
        "video": grants, 
    }

    token = jwt.encode(payload, LIVEKIT_API_SECRET, algorithm="HS256")
    print(
        "🎥 LiveKit token voor user=%s, room=%s (owner=%s, publish=%s)"
        % (identity, room_name, room_owner_username, is_owner)
    )
    return {
        "token": token,
        "room": room_name,
        "url": LIVEKIT_URL,
        "can_chat": can_chat,
        "identity": identity,
    }



# ============================================
# GO-LIVE / END-LIVE + VIEWERS
# (Postgres tabellen: live_sessions, live_viewers verwacht)
# ============================================
@app.post("/api/go-live")
def go_live(user: UserDB = Depends(get_current_user), s: Session = Depends(db)):
    owner_room = s.query(RoomDB).filter(RoomDB.user_id == user.id).first()
    if owner_room:
        room_slug = owner_room.slug
    else:
        room_slug = slugify(user.username)
    with engine.connect() as conn:
        conn.execute(
            text(
                """
            INSERT INTO live_sessions (user_id, room_slug, started_at, viewers, snapshot)
            VALUES (:uid, :slug, NOW(), 0, NULL)
            ON CONFLICT (user_id)
            DO UPDATE SET room_slug=:slug,
                          started_at=NOW(),
                          ended_at=NULL,
                          viewers=0,
                          snapshot=NULL
            """
            ),
            {"uid": user.id, "slug": room_slug},
        )
        conn.commit()
    return {"ok": True, "room": room_slug}


@app.post("/api/end-live")
def end_live(user: UserDB = Depends(get_current_user)):
    with engine.connect() as conn:
        conn.execute(
            text(
                """
            UPDATE live_sessions
               SET ended_at = NOW(), viewers = 0
             WHERE user_id = :uid
               AND ended_at IS NULL
            """
            ),
            {"uid": user.id},
        )
        conn.commit()
    return {"ok": True}


@app.post("/api/room/view-start")
async def room_view_start(request: Request):
    data = await request.json()
    room = data.get("room")
    if not room:
        raise HTTPException(400, "Missing room")
    ip = request.client.host

    conn = _psql()
    cur = conn.cursor()
    cur.execute(
        "SELECT id FROM live_sessions WHERE room_slug=%s AND ended_at IS NULL",
        (room,),
    )
    sess = cur.fetchone()
    if not sess:
        conn.close()
        raise HTTPException(404, "No active live found")
    session_id = sess[0]
    cur.execute(
        "SELECT id FROM live_viewers WHERE session_id=%s AND viewer_ip=%s AND left_at IS NULL",
        (session_id, ip),
    )
    if cur.fetchone():
        conn.close()
        return {"ok": True}

    cur.execute(
        "UPDATE live_viewers SET left_at=NULL, joined_at=NOW() WHERE session_id=%s AND viewer_ip=%s",
        (session_id, ip),
    )
    if cur.rowcount == 0:
        cur.execute(
            "INSERT INTO live_viewers (session_id, viewer_ip, joined_at) VALUES (%s,%s,NOW())",
            (session_id, ip),
        )
    cur.execute(
        "UPDATE live_sessions SET viewers = COALESCE(viewers, 0) + 1 WHERE id=%s",
        (session_id,),
    )
    conn.commit()
    conn.close()
    return {"ok": True}


@app.post("/api/room/view-end")
async def room_view_end(request: Request):
    data = await request.json()
    room = data.get("room")
    if not room:
        return {"ok": True}
    ip = request.client.host

    conn = _psql()
    cur = conn.cursor()

    cur.execute(
        "SELECT id FROM live_sessions WHERE room_slug=%s AND ended_at IS NULL",
        (room,),
    )
    
    sess = cur.fetchone()
    if not sess:
        conn.close()
        return {"ok": True}

    session_id = sess[0]
    cur.execute(
        "UPDATE live_viewers SET left_at=NOW() WHERE session_id=%s AND viewer_ip=%s AND left_at IS NULL",
        (session_id, ip),
    )
    if cur.rowcount:
        cur.execute(
            "UPDATE live_sessions SET viewers = GREATEST(COALESCE(viewers, 0) - 1, 0) WHERE id=%s",
            (session_id,),
        )
        conn.commit()
    conn.close()
    return {"ok": True}


# ============================================
# PUBLIEKE STREAMS (dummy + DB)
# ============================================
@app.get("/api/public/streams")
def public_streams():
    # Probeer DB, val terug op demo als tabel (nog) niet bestaat
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                SELECT u.username,
                       s.room_slug AS room,
                       COALESCE(s.viewers, 0) AS viewers,
                       s.snapshot,
                       COALESCE(u.avatar_url, '') AS avatar_url
                  FROM live_sessions s
                  JOIN users u ON u.id = s.user_id
                 WHERE s.ended_at IS NULL
                 ORDER BY s.started_at DESC
            """
                )
            )
            out = []
            for r in rows:
                snapshot = r._mapping["snapshot"]
                avatar = r._mapping["avatar_url"] or None
                fallback = avatar or f"https://picsum.photos/seed/{r._mapping['username']}/400/300"
                preview = _preview_url(snapshot) or fallback
                out.append(
                    {
                        "username": r._mapping["username"],
                        "room": r._mapping["room"],
                        "viewers": r._mapping["viewers"],
                        "snapshot": snapshot,
                        "preview_url": preview,
                        "thumb": fallback,
                    }
                )
            if out:
                return out
    except Exception:
        pass

    # fallback demo
    return [
        {
            "username": "Luna",
            "room": "luna-room",
            "snapshot": None,
            "preview_url": "https://picsum.photos/seed/luna/400/300",
            "thumb": "https://picsum.photos/seed/luna/400/300",
            "viewers": 48,
        },
        {
            "username": "Bruno",
            "room": "bruno-room",
            "snapshot": None,
            "preview_url": "https://picsum.photos/seed/bruno/400/300",
            "thumb": "https://picsum.photos/seed/bruno/400/300",
            "viewers": 112,
        },
        {
            "username": "Milan",
            "room": "milan-room",
            "snapshot": None,
            "preview_url": "https://picsum.photos/seed/milan/400/300",
            "thumb": "https://picsum.photos/seed/milan/400/300",
            "viewers": 23,
        },
    ]


# ============================================
# PROFIEL UPDATE / AVATAR / GALLERY
# (houdt rekening met ontbrekende kolommen)
# ============================================
@app.post("/api/me/update")
def update_profile(
    data: dict,
    user: UserDB = Depends(get_current_user),
    s: Session = Depends(db),
):
    u = s.get(UserDB, user.id)

    # Dynamische kolommen toevoegen waar nodig
    def ensure_column(sql):
        try:
            s.execute(text(sql))
            s.commit()
        except Exception:
            s.rollback()

    if "username" in data and data["username"]:
        u.username = data["username"].strip()
    if "email" in data and data["email"]:
        u.email = data["email"].strip().lower()
    if "password" in data and data["password"]:
        u.password_hash = hash_password(data["password"])
    if "bio" in data:
        ensure_column("ALTER TABLE users ADD COLUMN IF NOT EXISTS bio TEXT")
        u.bio = (data["bio"] or "").strip()

    s.commit()
    return {"status": "ok", "message": "Profile updated"}


@app.post("/api/me/avatar")
def upload_avatar(
    file: UploadFile = File(...),
    user: UserDB = Depends(get_current_user),
    s: Session = Depends(db),
):
    filename = f"{user.id}_{file.filename}"
    path = os.path.join(AVATAR_DIR, filename)
    with open(path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    public_url = f"https://api.johka.be/static/uploads/avatars/{filename}"

    try:
        s.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar_url TEXT"))
        s.commit()
    except Exception:
        s.rollback()

    u = s.get(UserDB, user.id)
    setattr(u, "avatar_url", public_url)
    s.commit()
    return {"url": public_url}


@app.post("/api/me/gallery")
def upload_gallery(
    file: UploadFile = File(...),
    user: UserDB = Depends(get_current_user),
    s: Session = Depends(db),
):
    user_dir = os.path.join(GALLERY_DIR, str(user.id))
    os.makedirs(user_dir, exist_ok=True)
    path = os.path.join(user_dir, file.filename)
    with open(path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    public_url = f"https://api.johka.be/static/uploads/gallery/{user.id}/{file.filename}"

    try:
        s.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS gallery_json TEXT"))
        s.commit()
    except Exception:
        s.rollback()

    import json

    u = s.get(UserDB, user.id)
    gallery = []
    if getattr(u, "gallery_json", None):
        try:
            gallery = json.loads(u.gallery_json or "[]")
        except Exception:
            gallery = []
    gallery.append(public_url)
    u.gallery_json = json.dumps(gallery)
    s.commit()

    return {"url": public_url}


@app.get("/api/creator/{username}")
def public_creator(username: str, s: Session = Depends(db)):
    u = s.query(UserDB).filter(UserDB.username == username).first()
    if not u:
        raise HTTPException(404, "Gebruiker niet gevonden")

    room = s.query(RoomDB).filter(RoomDB.user_id == u.id).first()
    gallery = []
    import json

    if getattr(u, "gallery_json", None):
        try:
            gallery = json.loads(u.gallery_json)
        except Exception:
            gallery = []

    live_slug = None
    live_viewers = 0
    try:
        with engine.connect() as conn:
            res = conn.execute(
                text(
                    """
                SELECT room_slug, COALESCE(viewers, 0) AS viewers, snapshot
                  FROM live_sessions
                 WHERE user_id = :uid AND ended_at IS NULL
                 LIMIT 1
                """
                ),
                {"uid": u.id},
            ).first()
            if res:
                live_slug = res._mapping["room_slug"]
                live_viewers = res._mapping["viewers"]
                preview_url = _preview_url(res._mapping["snapshot"])
            else:
                preview_url = None
    except Exception:
        preview_url = None


    return {
        "username": u.username,
        "bio": getattr(u, "bio", ""),
        "avatar": getattr(u, "avatar_url", ""),
        "banner": "",
        "gallery": gallery,
        "room_slug": live_slug,
        "is_live": bool(live_slug),
        "viewers": live_viewers,
        "preview_url": preview_url or getattr(u, "avatar_url", ""),
        "default_room": room.slug if room else None,
    }


# ============================================
# WALLET + TIP
# ============================================
@app.get("/api/wallet")
def get_wallet(user: UserDB = Depends(get_current_user), s: Session = Depends(db)):
    w = s.query(Wallet).filter_by(user_id=user.id).first()
    if not w:
        w = Wallet(user_id=user.id, balance=100)
        s.add(w)
        s.commit()
    return {"username": user.username, "balance": w.balance}


@app.post("/api/wallet/topup")
def topup_wallet(
    amount: int,
    user: UserDB = Depends(get_current_user),
    s: Session = Depends(db),
):
    if amount <= 0:
        raise HTTPException(400, "Ongeldig bedrag")
    w = s.query(Wallet).filter_by(user_id=user.id).first()
    if not w:
        w = Wallet(user_id=user.id, balance=0)
        s.add(w)
    w.balance += amount
    s.commit()
    return {"message": f"Wallet opgewaardeerd met {amount}", "balance": w.balance}


@app.post("/api/tip")
def send_tip(data: TipIn, user: UserDB = Depends(get_current_user), s: Session = Depends(db)):
    if data.amount <= 0:
        raise HTTPException(400, "Bedrag moet positief zijn")

    target = s.query(UserDB).filter_by(username=data.to_username.strip()).first()
    if not target:
        raise HTTPException(404, "Doelgebruiker niet gevonden")
    if target.id == user.id:
        raise HTTPException(400, "Je kan jezelf niet tippen")

    sender_wallet = s.query(Wallet).filter_by(user_id=user.id).first()
    receiver_wallet = s.query(Wallet).filter_by(user_id=target.id).first()

    if not sender_wallet or sender_wallet.balance < data.amount:
        raise HTTPException(400, "Onvoldoende saldo")

    sender_wallet.balance -= data.amount
    if not receiver_wallet:
        receiver_wallet = Wallet(user_id=target.id, balance=0)
        s.add(receiver_wallet)
    receiver_wallet.balance += data.amount

    t = Tip(from_user_id=user.id, to_user_id=target.id, amount=data.amount)
    s.add(t)

    s.add_all(
        [
            WalletHistory(
                user_id=user.id,
                change=-data.amount,
                reason=f"tip:sent:{target.username}",
            ),
            WalletHistory(
                user_id=target.id,
                change=data.amount,
                reason=f"tip:received:{user.username}",
            ),
        ]
    )

    s.commit()

    return {
        "message": f"Je hebt {data.amount} tokens gestuurd naar {target.username}",
        "new_balance": sender_wallet.balance,
    }


@app.get("/api/tips")
def get_tips(user: UserDB = Depends(get_current_user), s: Session = Depends(db)):
    tips = (
        s.query(Tip)
        .filter(or_(Tip.from_user_id == user.id, Tip.to_user_id == user.id))
        .order_by(Tip.created_at.desc())
        .limit(50)
        .all()
    )
    return [
        {
            "from": t.from_user.username,
            "to": t.to_user.username,
            "amount": t.amount,
            "when": t.created_at.isoformat(),
        }
        for t in tips
    ]


# ============================================
# SNAPSHOTS (JPG + GIF)
# ============================================
@app.post("/api/room/snapshot")
async def upload_snapshot(request: Request, user: UserDB = Depends(get_current_user)):
    data = await request.json()
    img_b64 = data.get("image")
    if not img_b64:
        raise HTTPException(400, "Geen afbeelding ontvangen")

    img_data = base64.b64decode(img_b64.split(",")[1])
    filename = f"{user.username}_{int(time.time())}.jpg"
    filepath = os.path.join(PREVIEW_DIR, filename)
    with open(filepath, "wb") as f:
        f.write(img_data)

    # DB update (optioneel)
    conn = _psql()
    cur = conn.cursor()
    cur.execute(
        "UPDATE live_sessions SET snapshot=%s WHERE user_id=%s AND ended_at IS NULL",
        (filename, user.id),
    )
    conn.commit()
    conn.close()

    return {"status": "ok", "file": filename}


@app.post("/api/room/snapshot-seq")
async def upload_snapshot_sequence(
    request: Request, user: UserDB = Depends(get_current_user)
):
    data = await request.json()
    frames_b64 = data.get("frames", [])
    if not frames_b64:
        raise HTTPException(400, "Geen frames ontvangen")

    frames = []
    for img_b64 in frames_b64:
        try:
            img_data = base64.b64decode(img_b64.split(",")[1])
            frame = Image.open(BytesIO(img_data)).convert("RGB")
            frames.append(frame)
        except Exception as e:
            print("Frame fout:", e)

    if not frames:
        raise HTTPException(400, "Geen geldige frames ontvangen")

    gif_filename = f"{user.username}_{int(time.time())}.gif"
    gif_path = os.path.join(PREVIEW_DIR, gif_filename)

    frames[0].save(
        gif_path, save_all=True, append_images=frames[1:], duration=500, loop=0
    )

    conn = _psql()
    cur = conn.cursor()
    cur.execute(
        "UPDATE live_sessions SET snapshot=%s WHERE user_id=%s AND ended_at IS NULL",
        (gif_filename, user.id),
    )
    conn.commit()
    conn.close()

    return {"status": "ok", "file": gif_filename}
# ============================================
# LIVE PRESENCE (Redis heartbeat for room.js)
# ============================================
RedisError = Exception
try:
    from redis.asyncio import Redis
    from redis.exceptions import RedisError

    REDIS_URL = _get_env("REDIS_URL")
    REDIS_HOST = _get_env("REDIS_HOST", "redis")
    REDIS_PORT = int(_get_env("REDIS_PORT", "6379"))
    REDIS_PASSWORD = _get_env("REDIS_PASSWORD", required=True)

    if REDIS_URL:
        redis = Redis.from_url(REDIS_URL, decode_responses=True)
    else:
        redis = Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            password=REDIS_PASSWORD or None,
            decode_responses=True,
        )
except Exception as exc:  # pragma: no cover - Redis optional in some envs
    print(f"⚠️  Redis disabled: {exc}")
    redis = None


@app.post("/api/live/start")
async def live_start(u: UserDB = Depends(get_current_user)):
    """Room.js heartbeat: streamer bevestigt dat die live is."""
    if redis:
        try:
            # Bewaar username 60 seconden als "live"
            await redis.set(f"live:{u.username}", "1", ex=60)
        except Exception as exc:
            print(f"⚠️ Redis heartbeat failed: {exc}")

    return {"status": "live"}

@app.post("/api/live/stop")
async def live_stop(u: UserDB = Depends(get_current_user)):
    if redis:
        try:
            await redis.delete(f"live:{u.username}")
        except RedisError as exc:
            print(f"⚠️  Redis cleanup failed: {exc}")
    return {"status": "stopped"}


@app.get("/api/live/active")
async def live_active():
    """Frontend kan zien wie live is (UI future use)."""
    if not redis:
        return []
    try:
        keys = await redis.keys("live:*")
    except RedisError as exc:
        print(f"⚠️  Redis fetch failed: {exc}")
        return []
    return [k.split(":")[1] for k in keys]

# =============================================
#  Laatste stukje van je main.py
# =============================================

# 🧹 Cleanup functie voor oude previews
import threading, time
from pathlib import Path

def cleanup_previews(folder="/app/static/uploads/previews", max_age_hours=6):
    now = time.time()
    limit = max_age_hours * 3600
    deleted = 0

    for file in Path(folder).glob("*.jpg"):
        try:
            if now - file.stat().st_mtime > limit:
                file.unlink()
                deleted += 1
        except Exception as e:
            print(f"❌ Fout bij verwijderen {file}: {e}")

    if deleted:
        print(f"🧹 {deleted} oude preview(s) verwijderd uit {folder}")

def schedule_cleanup():
    cleanup_previews()
    threading.Timer(3600, schedule_cleanup).start()  # elk uur herhalen

schedule_cleanup()

from mollie.api.client import Client

@app.post("/api/wallet/create-payment")
def create_payment(data: dict, user: UserDB = Depends(get_current_user)):
    """Maak een nieuwe Mollie-betaling aan en bewaar die tijdelijk."""
    try:
        mollie = Client()
        mollie.set_api_key(os.getenv("MOLLIE_API_KEY"))

        amount = data.get("amount", 0)
        if amount < 1:
            raise HTTPException(400, "Ongeldig bedrag")
        redirect_origin = (data.get("redirect_origin") or "https://johka.be").strip()
        allowed_origins = {
            "https://johka.be",
            "https://www.johka.be",
            "https://api.johka.be",
            "http://localhost:8000",
            "http://localhost:5173",
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        }
        if redirect_origin not in allowed_origins:
            redirect_origin = "https://johka.be"

        redirect_url = redirect_origin.rstrip("/") + "/wallet.html?success=1"

        # Maak betaling aan bij Mollie
        payment = mollie.payments.create({
            "amount": {"currency": "EUR", "value": f"{amount:.2f}"},
            "description": f"Opwaarderen Johka Wallet ({user.username})",
            "redirectUrl": redirect_url,
            "webhookUrl": f"https://api.johka.be/api/wallet/webhook",
            "metadata": {"user_id": user.id}
        })

        return {"payment_id": payment.id, "checkout_url": payment.checkout_url}
    except Exception as e:
        raise HTTPException(500, f"Betaling aanmaken mislukt: {e}")
    

# ============================================
# 🔐 LiveKit user herkenning + wallet history
# ============================================
import jwt  # pip install PyJWT
from fastapi import Header, HTTPException
from sqlalchemy import text


def current_user(Authorization: str = Header(None), s: Session = Depends(db)):
    """
    Haal huidige gebruiker uit de LiveKit-token.
    De token wordt meegegeven in de Authorization-header:
        Authorization: Bearer <livekit_token>
    """
    if not Authorization or not Authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Geen geldige Authorization-header")

    token = Authorization.split(" ")[1]

    try:
        # LiveKit-tokens zijn JWT's – we hoeven de handtekening niet te verifiëren
        payload = jwt.decode(token, options={"verify_signature": False})
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Ongeldig token: {e}")

    # Zoek identity of username veld in token
    identity = payload.get("sub") or payload.get("name") or payload.get("identity")
    if not identity:
        raise HTTPException(status_code=401, detail="Geen identity in LiveKit-token")

    # Haal gebruiker op uit DB
    user = s.query(UserDB).filter_by(username=identity).first()
    if not user:
        raise HTTPException(status_code=404, detail=f"Gebruiker '{identity}' niet gevonden")

    return user


# ============================================
# 💾 Transactiegeschiedenis ophalen
# ============================================
@app.get("/api/wallet/history")
def wallet_history(user: UserDB = Depends(get_current_user), s: Session = Depends(db)):

    """
    Geef laatste transacties (wallet_history) van de huidige gebruiker terug.
    """
    rows = s.execute(
        text("""
            SELECT change, reason, created_at
            FROM wallet_history
            WHERE user_id = :uid
            ORDER BY created_at DESC
            LIMIT 50
        """),
        {"uid": user.id}
    ).fetchall()

    return [
        {
            "change": r.change,
            "reason": r.reason,
            "created_at": r.created_at,
        }
        for r in rows
    ]


# ============================================
# 💳 Mollie webhook – saldo bijwerken + loggen
# ============================================
@app.post("/api/wallet/webhook")
async def mollie_webhook(request: Request, s: Session = Depends(db)):
    """Webhook die Mollie aanroept bij statuswijziging (paid, failed, etc.)"""
    from mollie.api.client import Client
    import os

    mollie = Client()
    mollie.set_api_key(os.getenv("MOLLIE_API_KEY"))

    data = await request.form()
    payment_id = data.get("id")

    if not payment_id:
        print("❌ Geen payment_id in webhook ontvangen")
        return {"status": "ignored"}

    try:
        payment = mollie.payments.get(payment_id)
    except Exception as e:
        print(f"❌ Fout bij ophalen Mollie betaling: {e}")
        return {"status": "error"}

    if payment.is_paid():
        user_id = payment.metadata.get("user_id")
        if user_id:
            wallet = s.query(Wallet).filter_by(user_id=user_id).first()
            if wallet:
                amount_tokens = int(float(payment.amount["value"]) * 10)  # 💰 1 EUR = 10 tokens
                wallet.balance += amount_tokens
                s.commit()
                print(f"✅ Mollie betaling voltooid voor user {user_id} (+{amount_tokens} tokens)")

                # 📜 Log transactie in wallet_history
                s.execute(
                    text("""
                        INSERT INTO wallet_history (user_id, change, reason)
                        VALUES (:uid, :amount, 'mollie')
                    """),
                    {"uid": user_id, "amount": amount_tokens}
                )
                s.commit()
                print(f"📘 Log toegevoegd aan wallet_history voor user {user_id}")

        else:
            print("⚠️ Geen user_id in metadata gevonden")
    else:
        print(f"❌ Mollie status: {payment.status}")

    return {"status": "ok"}

# ✅ 1. Alle users en hun saldo
@app.get("/api/admin/users")
def admin_users(s: Session = Depends(db), auth=Depends(verify_admin)):
    rows = s.execute(text("""
        SELECT u.id, u.username, w.balance
        FROM users u
        LEFT JOIN wallets w ON w.user_id = u.id
        ORDER BY u.id
    """)).fetchall()
    return [{"id": r.id, "username": r.username, "balance": r.balance or 0} for r in rows]


# ✅ 2. Transactielog opvragen
@app.get("/api/admin/history")
def admin_history(s: Session = Depends(db), auth=Depends(verify_admin)):
    rows = s.execute(text("""
        SELECT h.id, u.username, h.change, h.reason, h.created_at
        FROM wallet_history h
        JOIN users u ON u.id = h.user_id
        ORDER BY h.created_at DESC
        LIMIT 100
    """)).fetchall()
    return [
        {
            "id": r.id,
            "user": r.username,
            "change": r.change,
            "reason": r.reason,
            "created_at": r.created_at
        } for r in rows
    ]


# ✅ 3. Tokens handmatig aanpassen
@app.post("/api/admin/add-tokens")
def admin_add_tokens(data: dict, s: Session = Depends(db), auth=Depends(verify_admin)):
    user_id = data.get("user_id")
    amount = data.get("amount")
    if not user_id or not amount:
        raise HTTPException(status_code=400, detail="user_id en amount vereist")

    wallet = s.query(Wallet).filter_by(user_id=user_id).first()
    if not wallet:
        raise HTTPException(status_code=404, detail="Wallet niet gevonden")

    wallet.balance += int(amount)
    s.add(WalletHistory(user_id=user_id, change=amount, reason="admin-aanpassing"))
    s.commit()
    return {"status": "ok", "new_balance": wallet.balance}

# =============================================
# 🩺 HEALTHCHECK ENDPOINT
# =============================================

@app.get("/health")
def health_check():
    """
    Eenvoudige healthcheck voor Docker / load balancer.
    Geeft altijd status 200 OK terug.
    """
    return {"status": "ok", "service": "johka-backend"}


# ============================================
# END
# ============================================
print("🟢 Backend loaded (Johka Live, full)")
