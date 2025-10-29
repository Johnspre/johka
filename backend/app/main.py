# main.py
# ============================================
# JOHKA LIVE - COMPLETE BACKEND (STABLE)
# ============================================

import os
import re
import time
import base64
import shutil
from io import BytesIO
from datetime import datetime
from typing import Optional, List

# ---------- FastAPI & Security ----------
from fastapi import (
    FastAPI, Depends, HTTPException, status, Header,
    UploadFile, File, Request, Body
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer
from starlette.responses import JSONResponse

# ---------- DB ----------
import psycopg2
from sqlalchemy import (
    create_engine, Column, Integer, String, DateTime, func,
    ForeignKey, UniqueConstraint, text, or_
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



# ============================================
# ENV & CONFIG
# ============================================
JWT_SECRET = os.getenv("JWT_SECRET", "MyUltraSecretKey")
JWT_ALGORITHM = "HS256"

# DB (Docker service 'postgres' in compose)
SQLALCHEMY_URL = os.getenv(
    "SQLALCHEMY_URL",
    "postgresql+psycopg2://johka:SuperSterkWachtwoord123@postgres:5432/johka",
)
PSYCOPG_URL = os.getenv(
    "PSYCOPG_URL",
    "postgresql://johka:SuperSterkWachtwoord123@postgres:5432/johka",
)

# LiveKit
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY", "johka_live_key")
LIVEKIT_API_SECRET = os.getenv(
    "LIVEKIT_API_SECRET",
    "d7f6c8b14d3a4e52b9e8a6f9c1b7a3d5f4e2a8c6d9b3f1e4a7c2b5e6d8a9f3c1",
)
LIVEKIT_URL = os.getenv("LIVEKIT_URL", "wss://live.johka.be")

# Upload dirs
UPLOAD_ROOT = "/app/static/uploads"
AVATAR_DIR = os.path.join(UPLOAD_ROOT, "avatars")
GALLERY_DIR = os.path.join(UPLOAD_ROOT, "gallery")
PREVIEW_DIR = os.path.join(UPLOAD_ROOT, "previews")
os.makedirs(AVATAR_DIR, exist_ok=True)
os.makedirs(GALLERY_DIR, exist_ok=True)
os.makedirs(PREVIEW_DIR, exist_ok=True)

# ============================================
# APP & CORS
# ============================================
app = FastAPI(title="Johka Live API", version="1.0")

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
)

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
    # dynamic columns (avatar_url, bio, gallery_json, wallet_balance) kunnen later via ALTER worden toegevoegd
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
    balance = Column(Integer, default=100)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    owner = relationship("UserDB", back_populates="wallet")


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
def get_current_user(
    request: Request,
    authorization: Optional[str] = Header(None),
    s: Session = Depends(db),
) -> UserDB:
    token_header = authorization or request.headers.get("authorization")
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


# ============================================
# STARTUP (migraties / basis)
# ============================================
@app.on_event("startup")
def on_startup():
    Base.metadata.create_all(engine)
    # Zorg dat iedereen een wallet heeft
    with SessionLocal() as s:
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


@app.post("/api/register", response_model=MeOut)
def register(payload: RegisterIn, s: Session = Depends(db)):
    if len(payload.username) < 3 or len(payload.password) < 6:
        raise HTTPException(400, "Ongeldige invoer")
    u = UserDB(
        username=payload.username.strip(),
        email=payload.email.strip().lower(),
        password_hash=hash_password(payload.password),
    )
    s.add(u)
    try:
        s.flush()
    except IntegrityError:
        s.rollback()
        raise HTTPException(409, "Username of email bestaat al")

    slug = base = slugify(payload.username)
    i = 1
    while s.query(RoomDB).filter_by(slug=slug).first() is not None:
        i += 1
        slug = f"{base}-{i}"
    r = RoomDB(user_id=u.id, name=f"{u.username}'s room", slug=slug)
    s.add(r)
    s.commit()
    return MeOut(id=u.id, username=u.username, email=u.email)


@app.post("/api/login")
def login(payload: LoginIn, s: Session = Depends(db)):
    u = s.query(UserDB).filter(UserDB.username == payload.username.strip()).first()
    if not u or not verify_password(payload.password, u.password_hash):
        raise HTTPException(401, "Ongeldige login")
    token = create_access_token({"sub": str(u.id), "username": u.username})
    return {"access_token": token, "token_type": "bearer"}


@app.get("/api/me", response_model=MeOut)
def me(user: UserDB = Depends(get_current_user)):
    return MeOut(id=user.id, username=user.username, email=user.email)


@app.get("/api/rooms", response_model=List[RoomOut])
def list_rooms(s: Session = Depends(db)):
    rows = s.query(RoomDB).join(UserDB, RoomDB.user_id == UserDB.id).all()
    return [RoomOut(slug=r.slug, name=r.name, owner=r.owner.username) for r in rows]


# ==========================================
# 🎥 LIVEKIT TOKEN GENERATOR (v1.0.17)
# ==========================================
# =========================================================
# 🎥 LIVEKIT TOKEN GENERATOR (v3 – compatibel met LiveKit 1.9.x)
# =========================================================
import time, os, jwt
from fastapi import Depends

LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY", "johka_live_key")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET", "d7f6c8b14d3a4e52b9e8a6f9c1b7a3d5f4e2a8c6d9b3f1e4a7c2b5e6d8a9f3c1")
LIVEKIT_URL = os.getenv("LIVEKIT_URL", "wss://live.johka.be")

@app.post("/api/livekit-token")
async def create_livekit_token(user: UserDB = Depends(get_current_user)):
    identity = user.username
    room_name = f"{identity}-room"

    now = int(time.time())
    exp = now + (12 * 3600)

    # ✅ Dit formaat verwacht LiveKit v1.9.x
    grants = {
        "room": room_name,
        "roomJoin": True,
        "roomCreate": True,
        "canPublish": True,
        "canSubscribe": True,
        "canPublishData": True,
    }

    payload = {
        "iss": LIVEKIT_API_KEY,
        "sub": identity,
        "nbf": now - 10,
        "exp": exp,
        "video": grants,  # ⚠️ sleutelnaam hier terug naar "video"
    }

    token = jwt.encode(payload, LIVEKIT_API_SECRET, algorithm="HS256")
    print(f"🎥 LiveKit token voor user={identity}, room={room_name}")
    return {"token": token, "room": room_name, "url": LIVEKIT_URL}



# ============================================
# GO-LIVE / END-LIVE + VIEWERS
# (Postgres tabellen: live_sessions, live_viewers verwacht)
# ============================================
@app.post("/api/go-live")
def go_live(user: UserDB = Depends(get_current_user)):
    room_slug = user.username.lower().replace(" ", "_")
    with engine.connect() as conn:
        conn.execute(
            text(
                """
            INSERT INTO live_sessions (user_id, room_slug, started_at, viewers)
            VALUES (:uid, :slug, NOW(), COALESCE((SELECT viewers FROM live_sessions WHERE user_id=:uid),0))
            ON CONFLICT (user_id)
            DO UPDATE SET room_slug=:slug, started_at=NOW(), ended_at=NULL
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
               SET ended_at = NOW()
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
        "INSERT INTO live_viewers (session_id, viewer_ip) VALUES (%s,%s)",
        (session_id, ip),
    )
    cur.execute(
        "UPDATE live_sessions SET viewers = viewers + 1 WHERE id=%s",
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
    if sess:
        session_id = sess[0]
        cur.execute(
            "UPDATE live_sessions SET viewers = GREATEST(viewers - 1, 0) WHERE id=%s",
            (session_id,),
        )
        cur.execute(
            "UPDATE live_viewers SET left_at=NOW() WHERE session_id=%s AND viewer_ip=%s AND left_at IS NULL",
            (session_id, ip),
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
                SELECT u.username, s.room_slug AS room, COALESCE(s.viewers, 0) AS viewers,
                       COALESCE(u.avatar_url, 'https://picsum.photos/seed/' || u.username || '/400/300') AS thumb
                  FROM live_sessions s
                  JOIN users u ON u.id = s.user_id
                 WHERE s.ended_at IS NULL
                 ORDER BY s.started_at DESC
            """
                )
            )
            out = [
                {
                    "username": r._mapping["username"],
                    "room": r._mapping["room"],
                    "viewers": r._mapping["viewers"],
                    "thumb": r._mapping["thumb"],
                }
                for r in rows
            ]
            if out:
                return out
    except Exception:
        pass

    # fallback demo
    return [
        {
            "username": "Luna",
            "room": "luna-room",
            "thumb": "https://picsum.photos/seed/luna/400/300",
            "viewers": 48,
        },
        {
            "username": "Bruno",
            "room": "bruno-room",
            "thumb": "https://picsum.photos/seed/bruno/400/300",
            "viewers": 112,
        },
        {
            "username": "Milan",
            "room": "milan-room",
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
        setattr(u, "bio", data["bio"])

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

    return {
        "username": u.username,
        "bio": getattr(u, "bio", ""),
        "avatar": getattr(u, "avatar_url", ""),
        "banner": "",
        "gallery": gallery,
        "room_slug": room.slug if room else None,
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
# END
# ============================================
print("🟢 Backend loaded (Johka Live, full)")
