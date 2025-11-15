"""Room-specific API router.

This module centralizes every endpoint that is responsible for live rooms,
including LiveKit token management, view counters, snapshots and temporary
subjects.  Keeping the code together makes it easier to maintain and keeps
``main.py`` focused on generic application concerns.
"""

from __future__ import annotations

import base64
import json
import os
import time
from io import BytesIO
from typing import Optional
from uuid import uuid4

import psycopg2
from dotenv import load_dotenv
from fastapi import APIRouter, Body, Depends, HTTPException, Request
import httpx
from jose import JWTError, jwt
from PIL import Image
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from database import engine, get_db
from models import RoomDB, UserDB, Wallet, WalletHistory
from models import KickRequest, BanRequest, TimeoutRequest, ModRequest
from livekit.api import AccessToken, VideoGrants
from datetime import datetime, timedelta
from models import RoomTimeout
from models import RoomBan
from models import RoomModerator

load_dotenv()


def _get_env(name: str, default: Optional[str] = None, *, required: bool = False) -> Optional[str]:
    value = os.getenv(name, default)
    if required and (value is None or value == ""):
        raise RuntimeError(f"Environment variable '{name}' is required")
    return value

async def do_livekit_kick(room_name: str, identity: str):
    """Verwijder een gebruiker uit LiveKit met dezelfde logica als /mod/kick."""

    token = build_livekit_server_token(room_name)

    url = LIVEKIT_HTTP_BASE + "/twirp/livekit.RoomService/RemoveParticipant"

    body = {"room": room_name, "identity": identity}

    async with httpx.AsyncClient(timeout=10.0) as client:
        res = await client.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=body,
        )

    if res.status_code != 200:
        raise HTTPException(status_code=502, detail=res.text)

    maybe_json = None
    content_type = res.headers.get("content-type", "")
    if "json" in content_type.lower() and res.content:
        try:
            maybe_json = res.json()
        except ValueError:
            maybe_json = None

    if isinstance(maybe_json, dict) and "error" in maybe_json:
        detail = (
            maybe_json["error"].get("msg")
            if isinstance(maybe_json["error"], dict)
            else None
        )
        raise HTTPException(status_code=502, detail=detail or "LiveKit gaf een fout terug")

    return res
        


JWT_SECRET = _get_env("JWT_SECRET", "MyUltraSecretKey")
JWT_ALGORITHM = "HS256"

POSTGRES_USER = _get_env("POSTGRES_USER", "johka")
POSTGRES_PASSWORD = _get_env("POSTGRES_PASSWORD", required=True)
POSTGRES_HOST = _get_env("POSTGRES_HOST", "postgres")
POSTGRES_PORT = _get_env("POSTGRES_PORT", "5432")
POSTGRES_DB = _get_env("POSTGRES_DB", "johka")

PSYCOPG_URL = _get_env("PSYCOPG_URL") or (
    f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@"
    f"{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"
)

LIVEKIT_API_KEY = _get_env("LIVEKIT_API_KEY", "johka_live_key")
LIVEKIT_API_SECRET = _get_env("LIVEKIT_API_SECRET", required=True)
LIVEKIT_URL = _get_env("LIVEKIT_URL", "wss://live.johka.be")
LIVEKIT_FALLBACK_URL = os.getenv("LIVEKIT_FALLBACK_URL", "https://live.johka.be")

def _resolve_livekit_http_base() -> str:
    """Bepaal de HTTP-basis-URL voor LiveKit-beheer."""

    fallback = os.getenv("LIVEKIT_FALLBACK_URL")
    if fallback:
        return fallback.rstrip("/")

    if LIVEKIT_URL.startswith("wss://"):
        return "https://" + LIVEKIT_URL[len("wss://") :].rstrip("/")
    if LIVEKIT_URL.startswith("ws://"):
        return "http://" + LIVEKIT_URL[len("ws://") :].rstrip("/")
    return LIVEKIT_URL.rstrip("/")

def enforce_room_access_controls(
    s: Session,
    room_id: Optional[int],
    identity: Optional[str],
    *,
    is_owner: bool,
) -> None:
    """Verhindert dat gebande of getimede kijkers een nieuwe token krijgen."""

    if not room_id or not identity or is_owner:
        return

    banned = (
        s.query(RoomBan)
        .filter(RoomBan.room_id == room_id, RoomBan.identity == identity)
        .first()
    )
    if banned:
        raise HTTPException(
            status_code=403,
            detail="Je bent geblokkeerd in deze room.",
        )

    timeout_entry = (
        s.query(RoomTimeout)
        .filter(RoomTimeout.room_id == room_id, RoomTimeout.identity == identity)
        .first()
    )
    if timeout_entry:
        now_dt = datetime.utcnow()
        if timeout_entry.until and timeout_entry.until > now_dt:
            remaining_seconds = int((timeout_entry.until - now_dt).total_seconds())
            remaining_minutes = max((remaining_seconds + 59) // 60, 1)
            raise HTTPException(
                status_code=403,
                detail=(
                    "Je hebt een timeout. Probeer opnieuw over "
                    f"{remaining_minutes} minuten."
                ),
            )

        s.delete(timeout_entry)
        s.commit()


LIVEKIT_HTTP_BASE = _resolve_livekit_http_base()

UPLOAD_ROOT = "/app/static/uploads"
AVATAR_DIR = os.path.join(UPLOAD_ROOT, "avatars")
GALLERY_DIR = os.path.join(UPLOAD_ROOT, "gallery")
PREVIEW_DIR = os.path.join(UPLOAD_ROOT, "previews")

db = get_db


def slugify(s: str) -> str:
    import re

    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "room"


def _normalize_room_slug(raw_slug: str) -> str:
    slug = raw_slug.strip().lower()
    if slug.endswith("-room"):
        slug = slug[: -len("-room")]
    return slugify(slug)


def ensure_user_room(user: UserDB, s: Session) -> RoomDB:
    existing = s.query(RoomDB).filter(RoomDB.user_id == user.id).first()
    if existing:
        return existing

    base_title = user.username or f"Creator {user.id}"
    base_slug = slugify(base_title)[:60] or f"room-{user.id}"
    slug = base_slug
    suffix = 1
    while s.query(RoomDB).filter(RoomDB.slug == slug).first():
        slug = f"{base_slug}-{suffix}"
        suffix += 1

    room = RoomDB(user_id=user.id, name=base_title, slug=slug)
    s.add(room)
    try:
        s.commit()
    except IntegrityError:
        s.rollback()
        slug = f"{base_slug}-{uuid4().hex[:6]}"
        room = RoomDB(user_id=user.id, name=base_title, slug=slug)
        s.add(room)
        s.commit()

    s.refresh(room)
    return room


def _psql():
    return psycopg2.connect(PSYCOPG_URL)


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
    authorization: Optional[str] = None,
    s: Session = Depends(db),
) -> UserDB:
    token_header = authorization or request.headers.get("authorization")
    return _resolve_authorization_user(token_header, s)


def get_optional_user(
    request: Request,
    authorization: Optional[str] = None,
    s: Session = Depends(db),
) -> Optional[UserDB]:
    token_header = authorization or request.headers.get("authorization")
    if not token_header:
        return None
    return _resolve_authorization_user(token_header, s)


def log_room_action(
    s: Session,
    room_id: int,
    user_id: int,
    action: str,
    info: str = "",
) -> None:
    """Schrijft een gebeurtenis naar de room_logs-tabel."""
    try:
        s.execute(text("""
            INSERT INTO room_logs (room_id, user_id, action, info)
            VALUES (:r, :u, :a, :i)
        """), {"r": room_id, "u": user_id, "a": action, "i": info})
        s.commit()
    except Exception as e:
        print(f"⚠️  Kon room log niet schrijven: {e}")

# ===============================================================
# 🔌 Redis (heartbeat voor "go live" status)
# ===============================================================
RedisError = Exception
try:  # pragma: no cover - Redis is optioneel in sommige omgevingen
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
except Exception as exc:  # pragma: no cover - Redis optioneel
    print(f"⚠️  Redis disabled: {exc}")
    redis = None


# ===============================================================
# 🧭 Router setup
# ===============================================================
# Room endpoints gebruiken standaard het /api/room-prefix.
room_router = APIRouter(prefix="/api/room", tags=["Room"])

# Extra router zonder prefix voor endpoints die historisch buiten
# /api/room vallen (zoals /api/livekit-token).
_public_router = APIRouter(tags=["Room"])

# Dit is de router die in main.py wordt geregistreerd.
router = APIRouter()



# ===============================================================
# 🔧 Helpers
# ===============================================================
class LivekitTokenRequest(BaseModel):
    room_slug: Optional[str] = None



def _ensure_owner_room(room_id_value, user: UserDB, s: Session) -> RoomDB:
    """Validatie helper om te checken of de gebruiker eigenaar is van room."""

    if room_id_value is None:
        raise HTTPException(status_code=400, detail="room_id is vereist")

    try:
        room_id_int = int(room_id_value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Ongeldige room_id")

    room = s.get(RoomDB, room_id_int)
    if not room or room.user_id != user.id:
        raise HTTPException(status_code=403, detail="Geen toegang tot deze room")

    return room


# ===============================================================
# 🎥 LiveKit token
# ===============================================================
@_public_router.post("/api/livekit-token")
async def create_livekit_token(
    payload: Optional[LivekitTokenRequest] = Body(default=None),
    user: Optional[UserDB] = Depends(get_optional_user),
    s: Session = Depends(get_db),
):
    payload = payload or LivekitTokenRequest()

    requested_slug = payload.room_slug.strip() if payload.room_slug else None

    owner_room = None
    target_room = None
    is_owner = False
    can_chat = False
    room_id = None
    room_display_name = None
    room_subject = None
    room_slug_value = None

    if user:
        owner_room = s.query(RoomDB).filter(RoomDB.user_id == user.id).first()
        if not owner_room:
            owner_room = ensure_user_room(user, s)

    if user and not requested_slug:
        if not owner_room:
            raise HTTPException(status_code=500, detail="Kon room niet bepalen")

        room_name = f"{owner_room.slug}-room"
        room_id = owner_room.id
        room_display_name = owner_room.name
        room_slug_value = owner_room.slug
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
        room_id = target_room.id
        room_display_name = target_room.name
        room_slug_value = target_room.slug

        if user:
            is_owner = owner_room is not None and owner_room.id == target_room.id
            base_identity = user.username or f"user-{user.id}"
            identity = base_identity
            can_chat = True
        else:
            identity = f"gast-{uuid4().hex[:6]}"
            can_chat = False

    enforce_room_access_controls(s, room_id, identity, is_owner=is_owner)      

    metadata = None
    if user:
        metadata = json.dumps(
            {
                "display_name": user.username,
                "username": user.username,
                "gender": (user.gender or "unknown") if hasattr(user, "gender") else "unknown",
            }
        )

    if room_id:
        try:
            subject_row = s.execute(
                text(
                    """
                SELECT COALESCE(temp_subject, name) AS subject
                  FROM rooms
                 WHERE id = :id
                """
                ),
                {"id": room_id},
            ).fetchone()
            if subject_row:
                room_subject = subject_row._mapping.get("subject")
        except Exception:
            room_subject = None

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

    if metadata is not None:
        payload["metadata"] = metadata


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
        "room_id": room_id,
        "room_name": room_display_name,
        "room_subject": room_subject,
        "room_slug": (
            room_slug_value
            or (
                room_name[:-5] if room_name and room_name.endswith("-room") else room_name
            )
        ),
    }


# ===============================================================
# 🚀 Go live / einde uitzending
# ===============================================================
@_public_router.post("/api/go-live")
def go_live(user: UserDB = Depends(get_current_user), s: Session = Depends(get_db)):
    owner_room = s.query(RoomDB).filter(RoomDB.user_id == user.id).first()
    if not owner_room:
        owner_room = ensure_user_room(user, s)

    room_slug = owner_room.slug
    room_id = owner_room.id
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

@_public_router.post("/api/end-live")
def end_live(
    user: UserDB = Depends(get_current_user),
    s: Session = Depends(get_db),
):
    owner_room = s.query(RoomDB).filter(RoomDB.user_id == user.id).first()
    if not owner_room:
        owner_room = ensure_user_room(user, s)

    room_slug = owner_room.slug
    room_id = owner_room.id

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

# ===============================================================
# 👥 View counters
# ===============================================================
@room_router.post("/view-start")
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


@room_router.post("/view-end")
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


# ===============================================================
# 📝 Room subject bewerken
# ===============================================================
@room_router.post("/set-subject")
def set_room_subject(
    payload: dict,
    user: UserDB = Depends(get_current_user),
    s: Session = Depends(get_db),
):
    room = _ensure_owner_room(payload.get("room_id"), user, s)
    subject = (payload.get("subject") or "").strip()
    if len(subject) > 100:
        raise HTTPException(status_code=400, detail="Onderwerp te lang")

    room.temp_subject = subject or None
    s.commit()
    final_subject = room.temp_subject or room.name
    log_room_action(s, room.id, room.user_id, "subject_changed", subject)
    return {"subject": final_subject}

@room_router.post("/reset-subject")
def reset_room_subject(
    payload: dict,
    user: UserDB = Depends(get_current_user),
    s: Session = Depends(get_db),
):
    room = _ensure_owner_room(payload.get("room_id"), user, s)
    room.temp_subject = None
    s.commit()
    return {"subject": room.name}


@room_router.get("/current/{room_id}")
def get_room_subject(room_id: int, s: Session = Depends(get_db)):
    room = s.get(RoomDB, room_id)
    if not room:
        raise HTTPException(status_code=404, detail="Room niet gevonden")
    return {"subject": room.temp_subject or room.name}


# ===============================================================
# 📸 Snapshots (JPG + GIF)
# ===============================================================
@room_router.post("/snapshot")
async def upload_snapshot(request: Request, user: UserDB = Depends(get_current_user)):
    data = await request.json()
    img_b64 = data.get("image")
    if not img_b64:
        raise HTTPException(400, "No image provided")

    header, _, encoded = img_b64.partition(",")
    try:
        img_data = base64.b64decode(encoded or img_b64)
    except Exception:
        raise HTTPException(400, "Invalid image data")

    ext = "jpg"
    if header.endswith("gif"):
        ext = "gif"
    filename = f"{user.username}_{int(time.time())}.{ext}"
    filepath = os.path.join(PREVIEW_DIR, filename)
    with open(filepath, "wb") as f:
        f.write(img_data)

    conn = _psql()
    cur = conn.cursor()
    cur.execute(
        "UPDATE live_sessions SET snapshot=%s WHERE user_id=%s AND ended_at IS NULL",
        (filename, user.id),
    )
    conn.commit()
    conn.close()

    return {"status": "ok", "file": filename}


@room_router.post("/snapshot-seq")
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
        except Exception as exc:
            print("Frame fout:", exc)

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


# ===============================================================
# ❤️ Live heartbeat voor frontend
# ===============================================================
@_public_router.post("/api/live/start")
async def live_start(u: UserDB = Depends(get_current_user)):
    """Room.js heartbeat: streamer bevestigt dat die live is."""

    if redis:
        try:
            await redis.set(f"live:{u.username}", "1", ex=60)
        except Exception as exc:
            print(f"⚠️ Redis heartbeat failed: {exc}")

    return {"status": "live"}


@_public_router.post("/api/live/stop")
async def live_stop(u: UserDB = Depends(get_current_user)):
    if redis:
        try:
            await redis.delete(f"live:{u.username}")
        except RedisError as exc:
            print(f"⚠️  Redis cleanup failed: {exc}")
    return {"status": "stopped"}


@_public_router.get("/api/live/active")
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

from fastapi import Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

# --- inputmodel ---
class PrivateRoomIn(BaseModel):
    name: str
    access_mode: str                     # 'invite' | 'password' | 'token'
    access_key: str | None = None        # bij 'invite' of 'password'
    token_price: int | None = 0          # bij 'token'


@router.post("/api/room/create-private")
def create_private_room(
    data: PrivateRoomIn,
    user: UserDB = Depends(get_current_user),
    s: Session = Depends(db)
):
    if data.access_mode not in ("invite", "password", "token"):
        raise HTTPException(400, "Ongeldig access_mode")

    desired_name = data.name.strip()
    slug = slugify(desired_name)

    # Zorg dat de slug uniek is voor andere gebruikers, zodat de update niet faalt.
    slug_conflict = (
        s.query(RoomDB)
        .filter(RoomDB.slug == slug, RoomDB.user_id != user.id)
        .first()
    )
    if slug_conflict:
        raise HTTPException(400, "Deze roomnaam is al in gebruik")

    room = s.query(RoomDB).filter(RoomDB.user_id == user.id).first()
    if not room:
        room = ensure_user_room(user, s)

    room.name = desired_name or room.name
    room.slug = slug or room.slug
    room.is_private = True
    room.access_mode = data.access_mode
    room.access_key = data.access_key if data.access_mode in {"invite", "password"} else None
    room.token_price = (data.token_price or 0) if data.access_mode == "token" else 0

    s.commit()
    s.refresh(room)

    return {
        "ok": True,
        "room_id": room.id,
        "slug": room.slug,
        "access_mode": room.access_mode,
    }

class PrivateJoinIn(BaseModel):
    slug: str
    key: str | None = None


@router.post("/api/room/join-private")
def join_private_room(
    data: PrivateJoinIn,
    user: UserDB = Depends(get_current_user),
    s: Session = Depends(db)
):
    room = s.query(RoomDB).filter_by(slug=data.slug, is_private=True).first()
    if not room:
        raise HTTPException(404, "Privéroom niet gevonden")

    # --- toegangscontrole ---
    if room.access_mode == "password":
        if not data.key or data.key != room.access_key:
            raise HTTPException(403, "Ongeldig wachtwoord")

    elif room.access_mode == "invite":
        if not data.key or data.key != room.access_key:
            raise HTTPException(403, "Geen geldige uitnodiging")

    elif room.access_mode == "token":
        price = room.token_price or 0
        if price < 0:
            price = 0

        if price and user.id != room.user_id:
            viewer_wallet = s.query(Wallet).filter_by(user_id=user.id).first()
            if not viewer_wallet or viewer_wallet.balance < price:
                raise HTTPException(402, f"Niet genoeg tokens ({price} vereist)")

            owner_wallet = s.query(Wallet).filter_by(user_id=room.user_id).first()
            if not owner_wallet:
                owner_wallet = Wallet(user_id=room.user_id, balance=0)
                s.add(owner_wallet)

            viewer_wallet.balance -= price
            owner_wallet.balance += price

            s.add_all(
                [
                    WalletHistory(
                        user_id=user.id,
                        change=-price,
                        reason=f"room:private:join:{room.slug}",
                    ),
                    WalletHistory(
                        user_id=room.user_id,
                        change=price,
                        reason=f"room:private:earn:{room.slug}",
                    ),
                ]
            )

            s.commit()

            return {
                "ok": True,
                "room_slug": room.slug,
                "access_mode": room.access_mode,
                "new_balance": viewer_wallet.balance,
            }

    return {
        "ok": True,
        "room_slug": room.slug,
        "access_mode": room.access_mode,
    }

class RoomUpdateIn(BaseModel):
    slug: str
    access_mode: str = "public"     # public | invite | password | token
    access_key: str | None = None
    token_price: int | None = 0

@room_router.post("/update-access")
def update_room_access(
    data: RoomUpdateIn,
    user: UserDB = Depends(get_current_user),
    s: Session = Depends(db)
):
    room = s.query(RoomDB).filter_by(slug=data.slug, user_id=user.id).first()
    if not room:
        raise HTTPException(404, "Room niet gevonden of geen eigenaar")

    room.access_mode = data.access_mode
    room.access_key = data.access_key
    room.token_price = data.token_price or 0
    room.is_private = data.access_mode != "public"

    s.commit()
    return {"ok": True, "message": f"Room '{room.slug}' bijgewerkt"}

router.include_router(room_router)
router.include_router(_public_router)

def build_livekit_server_token(room_name: Optional[str] = None) -> str:
    """Maak een kortdurende token met room_admin-rechten."""

    grants = VideoGrants(room_admin=True, room=room_name or "")
    token = AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
    token.with_identity("room-moderator")
    token.with_grants(grants)
    return token.to_jwt()


@router.post("/mod/kick")
async def kick_user(
    payload: KickRequest = Body(...),
    user: UserDB = Depends(get_current_user),
    s: Session = Depends(db),
):
    # 1. input check
    if not payload.room or not payload.identity:
        raise HTTPException(status_code=400, detail="room en identity zijn verplicht")

    # 2. Find owner's room
    owner_room = (
        s.query(RoomDB)
        .filter(RoomDB.user_id == user.id)
        .first()
    )

    if not owner_room:
        raise HTTPException(403, "Geen room gevonden voor gebruiker")

    # 3. Validate room name
    expected_room_name = f"{owner_room.slug}-room"
    if payload.room != expected_room_name:
        raise HTTPException(
            403,
            "Je kan enkel kijkers uit je eigen room verwijderen",
        )

    # 4. Streamer mag NIET gekickt worden
    # target is de STREAMER als identity == owner username
    # (jij gebruikt identity als LiveKit identity, die == username)
    if payload.identity == owner_room.name:
        raise HTTPException(403, "Je kan de streamer niet kicken.")

    # 5. Check of target een moderator is
    is_target_mod = (
        s.query(RoomModerator)
        .filter(RoomModerator.room_id == owner_room.id,
                RoomModerator.identity == payload.identity)
        .first()
    )

    # Check of requester streamer is (streamer == room owner)
    requester_is_owner = (user.id == owner_room.user_id)

    # Een moderator mag GEEN andere moderator kicken
    if is_target_mod and not requester_is_owner:
        raise HTTPException(403, "Je kan geen moderator kicken.")

    # 6. Als iedereen ok is → LiveKit Kick
    await do_livekit_kick(payload.room, payload.identity)

    # 7. Logging
    log_room_action(s, owner_room.id, user.id, "kick", info=payload.identity)

    return {"status": "ok"}


@router.post("/mod/ban")
async def ban_user(
    payload: BanRequest = Body(...),
    user: UserDB = Depends(get_current_user),
    s: Session = Depends(db)
):
    # 1. Check of dit wel jouw eigen room is
    # payload.room = LiveKit room name, vb. "katty-room"
    slug = payload.room.replace("-room", "")

    owner_room = (
        s.query(RoomDB)
        .filter(RoomDB.user_id == user.id, RoomDB.slug == slug)
        .first()
    )

    if not owner_room:
        raise HTTPException(403, "Je kan enkel kijkers bannen uit je eigen room")

    # 2. Check of hij al geband is (voorkomt dubbele entries)
    existing = (
        s.query(RoomBan)
        .filter(RoomBan.room_id == owner_room.id, RoomBan.identity == payload.identity)
        .first()
    )
    if existing:
        # user is al geblokkeerd
        # maar we kunnen hem alsnog uit LiveKit kicken
        await do_livekit_kick(payload.room, payload.identity)
        return {"status": "ok", "message": f"{payload.identity} was al geblokkeerd"}

    # 3. Opslaan in DB
    ban = RoomBan(
        room_id=owner_room.id,
        identity=payload.identity,
        username=payload.username or payload.identity
    )
    s.add(ban)
    s.commit()

    # 4. User uit LiveKit gooien
    await do_livekit_kick(payload.room, payload.identity)

    return {"status": "ok", "message": f"{payload.username} is geblokkeerd"}


@router.post("/mod/timeout")
async def timeout_user(
    payload: TimeoutRequest = Body(...),
    user: UserDB = Depends(get_current_user),
    s: Session = Depends(db)
):
    slug = payload.room.replace("-room", "")
    owner_room = (
        s.query(RoomDB)
        .filter(RoomDB.user_id == user.id, RoomDB.slug == slug)
        .first()
    )

    if not owner_room:
        raise HTTPException(403, "Je kan enkel kijkers time-outen in je eigen room.")

    # einde van timeout berekenen
    until = datetime.utcnow() + timedelta(minutes=payload.minutes)

    # bestaande timeout verwijderen (vervangen)
    s.query(RoomTimeout).filter(
        RoomTimeout.room_id == owner_room.id,
        RoomTimeout.identity == payload.identity
    ).delete()

    timeout = RoomTimeout(
        room_id=owner_room.id,
        identity=payload.identity,
        username=payload.username or payload.identity,
        until=until
    )

    s.add(timeout)
    s.commit()

    # direct kicken
    await do_livekit_kick(payload.room, payload.identity)

    return {"status": "ok", "message": f"{payload.username} heeft een timeout van {payload.minutes} minuten"}

@router.post("/mod/unban")
async def unban_user(
    payload: BanRequest = Body(...),
    user: UserDB = Depends(get_current_user),
    s: Session = Depends(db)
):
    slug = payload.room.replace("-room", "")
    owner_room = (
        s.query(RoomDB)
        .filter(RoomDB.user_id == user.id, RoomDB.slug == slug)
        .first()
    )

    if not owner_room:
        raise HTTPException(403, "Geen rechten op deze room")

    # remove ban
    s.query(RoomBan).filter(
        RoomBan.room_id == owner_room.id,
        RoomBan.identity == payload.identity
    ).delete()

    # remove timeout
    s.query(RoomTimeout).filter(
        RoomTimeout.room_id == owner_room.id,
        RoomTimeout.identity == payload.identity
    ).delete()

    s.commit()

    return {"status": "ok", "message": f"{payload.username} is geunbanned"}

@router.post("/mod/addmod")
async def add_moderator(
    payload: ModRequest = Body(...),
    user: UserDB = Depends(get_current_user),
    s: Session = Depends(db)
):
    slug = payload.room.replace("-room", "")
    owner_room = (
        s.query(RoomDB)
        .filter(RoomDB.user_id == user.id, RoomDB.slug == slug)
        .first()
    )

    if not owner_room:
        raise HTTPException(403, "Je kan enkel moderators toevoegen in je eigen room.")

    # check of al moderator
    existing = (
        s.query(RoomModerator)
        .filter(RoomModerator.room_id == owner_room.id, RoomModerator.identity == payload.identity)
        .first()
    )
    if existing:
        return {"status": "ok", "message": f"{payload.username} is al moderator."}

    mod = RoomModerator(
        room_id=owner_room.id,
        identity=payload.identity,
        username=payload.username or payload.identity
    )
    s.add(mod)
    s.commit()

    return {"status": "ok", "message": f"{payload.username} is nu moderator."}


@router.post("/mod/removemod")
async def remove_moderator(
    payload: ModRequest = Body(...),
    user: UserDB = Depends(get_current_user),
    s: Session = Depends(db)
):
    slug = payload.room.replace("-room", "")
    owner_room = (
        s.query(RoomDB)
        .filter(RoomDB.user_id == user.id, RoomDB.slug == slug)
        .first()
    )

    if not owner_room:
        raise HTTPException(403, "Geen toegang tot deze room.")

    # het verwijderen
    deleted = s.query(RoomModerator).filter(
        RoomModerator.room_id == owner_room.id,
        RoomModerator.identity == payload.identity
    ).delete()

    s.commit()

    if deleted:
        return {"status": "ok", "message": f"{payload.username} is geen moderator meer."}
    else:
        return {"status": "ok", "message": f"{payload.username} was geen moderator."}


__all__ = ["router"]
