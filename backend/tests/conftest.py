from __future__ import annotations

import os
import pathlib
import sys
import tempfile

import pytest

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

_TEST_DIR = pathlib.Path(tempfile.mkdtemp(prefix="podrift-pytest-"))
os.environ["PODRIFT_DB"] = str(_TEST_DIR / "podrift-test.db")
os.environ.setdefault("PODRIFT_SQLITE_SYNCHRONOUS", "OFF")

from fastapi.testclient import TestClient  # noqa: E402

from app.db import connect  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import seed_if_empty  # noqa: E402


def _reset_database() -> None:
    db_path = pathlib.Path(os.environ["PODRIFT_DB"])
    for suffix in ("", "-wal", "-shm"):
        side = pathlib.Path(str(db_path) + suffix)
        side.unlink(missing_ok=True)
    seed_if_empty()


@pytest.fixture
def client():
    _reset_database()
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def top_quota_client(client):
    response = client.post("/api/配额/顶格")
    assert response.status_code == 200
    body = response.json()
    assert body["顶格"] is True
    return client


@pytest.fixture
def db_conn():
    _reset_database()
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()
