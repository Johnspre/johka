import os
import sys
from importlib import reload
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def app_module(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path/'test.db'}"
    monkeypatch.setenv("SQLALCHEMY_URL", db_url)
    monkeypatch.setenv("PSYCOPG_URL", db_url)

    from backend.app import main

    reload(main)

    main.Base.metadata.drop_all(main.engine)
    main.Base.metadata.create_all(main.engine)

    yield main


def test_wallet_history_returns_entries(app_module):
    main = app_module
    password = main.pwd_ctx.hash("secret")

    with main.SessionLocal() as session:
        user = main.UserDB(username="alice", email="alice@example.com", password_hash=password)
        session.add(user)
        session.commit()

        wallet = main.Wallet(user_id=user.id, balance=250)
        session.add(wallet)
        session.add(main.WalletHistory(user_id=user.id, change=50, reason="mollie"))
        session.commit()

        user_id = user.id
        username = user.username

    token = main.create_access_token({"sub": str(user_id), "username": username})

    with TestClient(main.app) as client:
        response = client.get(
            "/api/wallet/history",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    assert body[0]["change"] == 50
    assert body[0]["reason"] == "mollie"


def test_wallet_history_includes_tip_entries(app_module):
    main = app_module
    password = main.pwd_ctx.hash("secret")

    with main.SessionLocal() as session:
        sender = main.UserDB(username="alice", email="alice@example.com", password_hash=password)
        receiver = main.UserDB(username="bob", email="bob@example.com", password_hash=password)
        session.add_all([sender, receiver])
        session.commit()

        session.add_all(
            [
                main.Wallet(user_id=sender.id, balance=500),
                main.Wallet(user_id=receiver.id, balance=50),
            ]
        )
        session.commit()

        sender_id = sender.id
        receiver_id = receiver.id

    sender_token = main.create_access_token({"sub": str(sender_id), "username": "alice"})
    receiver_token = main.create_access_token({"sub": str(receiver_id), "username": "bob"})

    with TestClient(main.app) as client:
        tip_response = client.post(
            "/api/tip",
            json={"to_username": "bob", "amount": 25},
            headers={"Authorization": f"Bearer {sender_token}"},
        )
        assert tip_response.status_code == 200

        sender_history = client.get(
            "/api/wallet/history",
            headers={"Authorization": f"Bearer {sender_token}"},
        ).json()

        receiver_history = client.get(
            "/api/wallet/history",
            headers={"Authorization": f"Bearer {receiver_token}"},
        ).json()

    assert any(
        entry["reason"] == "tip:sent:bob" and entry["change"] == -25 for entry in sender_history
    )
    assert any(
        entry["reason"] == "tip:received:alice" and entry["change"] == 25
        for entry in receiver_history
    )