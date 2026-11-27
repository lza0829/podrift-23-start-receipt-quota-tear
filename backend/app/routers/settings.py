from __future__ import annotations
from fastapi import APIRouter
from pydantic import BaseModel
from app.db import db
from app.services.common import rows

router = APIRouter(tags=["舱规"])


class SettingIn(BaseModel):
    key: str
    value: str


@router.get("/舱规")
def list_settings():
    with db() as conn:
        return rows(conn.execute("SELECT * FROM settings ORDER BY key"))


@router.post("/舱规")
def set_setting(body: SettingIn):
    with db() as conn:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (body.key, body.value),
        )
        return {"ok": True}


@router.get("/事件")
def list_events():
    with db() as conn:
        return rows(conn.execute("SELECT * FROM events ORDER BY id DESC LIMIT 80"))

