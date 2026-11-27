from __future__ import annotations
from datetime import datetime, timezone


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def row_dict(row) -> dict:
    return dict(row) if row is not None else {}


def rows(cur) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]

