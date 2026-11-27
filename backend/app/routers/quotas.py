from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from app.db import db, write_db
from app.services.common import now_iso
from app.services.quota_ledger import build_top_bucket, add_ledger, replay_quota
from app.services.quota_policy import POLICY_NAME, serialize_quota

router = APIRouter(tags=["配额条"])


class QuotaIn(BaseModel):
    model_config = ConfigDict(extra="allow")

    cpu_used: float | None = None
    cpu_limit: float | None = None
    mem_used_mb: float | int | None = None
    mem_limit_mb: float | int | None = None
    expected_version: int | None = None
    version: int | None = None
    preset: str | None = None
    top: bool | None = None


def _first(raw: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in raw and raw[name] is not None:
            return raw[name]
    return None


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(round(float(value)))


@router.get("/配额")
def get_quota():
    with db() as conn:
        q_row = conn.execute("SELECT * FROM quotas WHERE id=1").fetchone()
        q = serialize_quota(q_row)
        pods = [dict(r) for r in conn.execute("SELECT * FROM pods ORDER BY id").fetchall()]
        occupancy = []
        for pod in pods:
            running = pod["run_state"] == "运行中"
            occupancy.append(
                {
                    "pod_id": pod["id"],
                    "name": pod["name"],
                    "run_state": pod["run_state"],
                    "占用中": running,
                    "cpu_occupied": float(pod["cpu_quota"]) if running else 0.0,
                    "mem_occupied_mb": int(pod["mem_quota_mb"]) if running else 0,
                    "cpu_quota": float(pod["cpu_quota"]),
                    "mem_quota_mb": int(pod["mem_quota_mb"]),
                }
            )
        q["舱体占用行"] = occupancy
        q["占用行"] = occupancy
        q["顶格桶"] = build_top_bucket(conn, q_row)
        q["replay"] = replay_quota(conn)
        q["重放"] = q["replay"]
        return q


def update_quota(body: QuotaIn) -> dict[str, Any]:
    raw = body.model_dump()
    extra = getattr(body, "__pydantic_extra__", None) or {}
    raw.update(extra)
    nested = raw.get("quota") if isinstance(raw.get("quota"), dict) else raw.get("配额")
    if isinstance(nested, dict):
        for key, value in nested.items():
            raw.setdefault(key, value)

    preset = _first(raw, "preset", "预设", "top_preset", "顶格预设")
    top_flag = _first(raw, "top", "顶格", "at_top", "打到顶格")
    set_top = top_flag is True or preset in ("顶格", "top", "TOP", "at_top", "hard_reject")

    with write_db() as conn:
        row = conn.execute("SELECT * FROM quotas WHERE id=1").fetchone()
        if not row:
            conn.execute(
                """
                INSERT INTO quotas(id, cpu_used, cpu_limit, mem_used_mb, mem_limit_mb, version)
                VALUES (1, 0, 1, 0, 1024, 0)
                """
            )
            row = conn.execute("SELECT * FROM quotas WHERE id=1").fetchone()

        current_version = int(row["version"]) if "version" in row.keys() else 0
        expected_version = _as_int(_first(raw, "expected_version", "expectedVersion"))
        if expected_version is None:
            maybe_version = _as_int(raw.get("version"))
            # `version` is only an optimistic-concurrency expectation when the
            # caller explicitly supplies it to the write endpoint.
            expected_version = maybe_version
        if expected_version is not None and expected_version != current_version:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "QUOTA_VERSION_CONFLICT",
                    "message": "配额已被另一笔修改推进，请重读后重试",
                    "current_version": current_version,
                    "expected_version": expected_version,
                },
            )

        cpu_limit = _as_float(_first(raw, "cpu_limit", "cpuLimit", "limit_cpu", "limitCpu", "CPU上限", "cpu上限", "cpu_total"))
        mem_limit = _as_int(_first(raw, "mem_limit_mb", "memLimitMb", "mem_limit", "memory_limit_mb", "memoryLimitMb", "内存上限", "memory_limit"))
        cpu_used = _as_float(_first(raw, "cpu_used", "cpuUsed", "used_cpu", "usedCpu", "CPU已用", "cpu已用"))
        mem_used = _as_int(_first(raw, "mem_used_mb", "memUsedMb", "mem_used", "memory_used_mb", "memoryUsedMb", "内存已用"))

        new_cpu_limit = float(row["cpu_limit"]) if cpu_limit is None else cpu_limit
        new_mem_limit = int(row["mem_limit_mb"]) if mem_limit is None else mem_limit
        new_cpu_used = float(row["cpu_used"]) if cpu_used is None else cpu_used
        new_mem_used = int(row["mem_used_mb"]) if mem_used is None else mem_used

        if set_top:
            new_cpu_used = new_cpu_limit
            new_mem_used = new_mem_limit

        if min(new_cpu_used, new_cpu_limit) < 0 or min(new_mem_used, new_mem_limit) < 0:
            raise HTTPException(status_code=422, detail="配额不能为负数")

        before = {
            "cpu_used": float(row["cpu_used"]),
            "mem_used_mb": int(row["mem_used_mb"]),
        }
        conn.execute(
            """
            UPDATE quotas
            SET cpu_used=?, cpu_limit=?, mem_used_mb=?, mem_limit_mb=?, version=version+1
            WHERE id=1
            """,
            (new_cpu_used, new_cpu_limit, new_mem_used, new_mem_limit),
        )
        after_row = conn.execute("SELECT * FROM quotas WHERE id=1").fetchone()
        now = now_iso()
        add_ledger(
            conn,
            kind="配额改额" if not set_top else "配额打到顶格",
            decision=POLICY_NAME,
            cpu_before=before["cpu_used"],
            cpu_after=new_cpu_used,
            mem_before_mb=before["mem_used_mb"],
            mem_after_mb=new_mem_used,
            top_hit=serialize_quota(after_row).get("顶格", False),
            detail="人工/夹具修改配额",
            at=now,
        )
        conn.execute(
            "INSERT INTO events(kind, ref, detail, at) VALUES (?,?,?,?)",
            ("配额条", "1", f"配额更新；口径={POLICY_NAME}", now),
        )
        result = serialize_quota(after_row)
        result["ok"] = True
        result["success"] = True
        result["replay"] = replay_quota(conn)
        result["重放"] = result["replay"]
        return result


for method in ("post", "put", "patch"):
    getattr(router, method)("/配额")(update_quota)
    getattr(router, method)("/配额/1")(update_quota)
    getattr(router, method)("/配额/修改")(update_quota)
    getattr(router, method)("/配额/调整")(update_quota)


@router.get("/配额/1")
def get_quota_one_alias():
    return get_quota()


@router.post("/配额/顶格")
def set_quota_top():
    return update_quota(QuotaIn(preset="顶格", top=True))


@router.post("/配额/打到顶格")
def hit_quota_top_alias():
    return update_quota(QuotaIn(preset="顶格", top=True))


@router.get("/配额/重放")
def get_quota_replay():
    with db() as conn:
        return replay_quota(conn)
