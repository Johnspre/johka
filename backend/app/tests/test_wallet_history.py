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