import os
from datetime import datetime, timedelta
from pathlib import Path
import sys

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("POSTGRES_PASSWORD", "test-password")
os.environ.setdefault("LIVEKIT_API_SECRET", "test-livekit-secret")
os.environ.setdefault("REDIS_PASSWORD", "test-redis-password")
os.environ.setdefault("SQLALCHEMY_URL", "sqlite:///:memory:")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from database import Base  # noqa: E402
from models import RoomBan, RoomDB, RoomTimeout, UserDB  # noqa: E402
import room  # noqa: E402


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    TestingSession = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()


def create_room(session):
    user = UserDB(username="creator", email="creator@example.com", password_hash="hashed")
    session.add(user)
    session.commit()
    room_db = RoomDB(user_id=user.id, name="Creator Room", slug="creator-room")
    session.add(room_db)
    session.commit()
    return room_db


def test_enforce_room_access_controls_blocks_banned_identity(db_session):
    room_db = create_room(db_session)
    ban = RoomBan(room_id=room_db.id, identity="viewer-1", username="Viewer")
    db_session.add(ban)
    db_session.commit()

    with pytest.raises(HTTPException) as exc:
        room.enforce_room_access_controls(
            db_session,
            room_db.id,
            "viewer-1",
            is_owner=False,
        )

    assert exc.value.status_code == 403
    assert "geblokkeerd" in exc.value.detail


def test_enforce_room_access_controls_blocks_active_timeout(db_session):
    room_db = create_room(db_session)
    timeout_entry = RoomTimeout(
        room_id=room_db.id,
        identity="viewer-2",
        username="Viewer",
        until=datetime.utcnow() + timedelta(minutes=5),
    )
    db_session.add(timeout_entry)
    db_session.commit()

    with pytest.raises(HTTPException) as exc:
        room.enforce_room_access_controls(
            db_session,
            room_db.id,
            "viewer-2",
            is_owner=False,
        )

    assert exc.value.status_code == 403
    assert "timeout" in exc.value.detail.lower()


def test_enforce_room_access_controls_allows_expired_timeout(db_session):
    room_db = create_room(db_session)
    timeout_entry = RoomTimeout(
        room_id=room_db.id,
        identity="viewer-3",
        username="Viewer",
        until=datetime.utcnow() - timedelta(minutes=1),
    )
    db_session.add(timeout_entry)
    db_session.commit()

    room.enforce_room_access_controls(
        db_session,
        room_db.id,
        "viewer-3",
        is_owner=False,
    )

    assert (
        db_session.query(RoomTimeout)
        .filter(RoomTimeout.room_id == room_db.id, RoomTimeout.identity == "viewer-3")
        .first()
        is None
    )