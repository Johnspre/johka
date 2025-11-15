import asyncio
import os
from pathlib import Path
import sys

import pytest

os.environ.setdefault("POSTGRES_PASSWORD", "test-password")
os.environ.setdefault("LIVEKIT_API_SECRET", "test-livekit-secret")
os.environ.setdefault("REDIS_PASSWORD", "test-redis-password")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

import room


def test_do_livekit_kick_uses_room_admin_token(monkeypatch):
    recorded = {}

    def fake_build(room_name=None):
        recorded["room_name"] = room_name
        return "token-123"

    monkeypatch.setattr(room, "build_livekit_server_token", fake_build)

    monkeypatch.setattr(room, "LIVEKIT_HTTP_BASE", "https://api.example")

    class DummyResponse:
        status_code = 200
        headers = {}
        content = b""

        def json(self):
            return {}

    class DummyClient:
        def __init__(self, *args, **kwargs):
            recorded["timeout"] = kwargs.get("timeout")

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, *, headers, json):
            recorded["url"] = url
            recorded["headers"] = headers
            recorded["json"] = json
            return DummyResponse()

    monkeypatch.setattr(room.httpx, "AsyncClient", DummyClient)

    asyncio.run(room.do_livekit_kick("demo-room", "viewer-1"))

    assert recorded["room_name"] == "demo-room"
    assert (
        recorded["url"]
        == "https://api.example/twirp/livekit.RoomService/RemoveParticipant"
    )
    assert recorded["json"] == {"room": "demo-room", "identity": "viewer-1"}
    assert recorded["headers"]["Authorization"] == "Bearer token-123"
    assert recorded["timeout"] == 10.0