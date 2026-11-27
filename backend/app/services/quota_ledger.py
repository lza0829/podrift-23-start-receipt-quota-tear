from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.services.common import now_iso
from app.services.quota_policy import EPS, POLICY, POLICY_NAME, evaluate_start, serialize_quota


def quota_numbers(row: sqlite3.Row | dict[str, Any]) -> dict[str, float | int]:
    return {
        "cpu_used": float(row["cpu_used"]),
        "cpu_limit": float(row["cpu_limit"]),
        "mem_used_mb": int(round(float(row["mem_used_mb"]))),
        "mem_limit_mb": int(round(float(row["mem_limit_mb"]))),
    }


def add_ledger(
    conn: sqlite3.Connection,
    *,
    kind: str,
    decision: str,
    cpu_before: float,
    cpu_after: float,
    mem_before_mb: int,
    mem_after_mb: int,
    order_id: int | None = None,
    pod_id: int | None = None,
    top_hit: bool | int = False,
    detail: str | None = None,
    at: str | None = None,
) -> int:
    seq_row = conn.execute("SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM quota_ledger").fetchone()
    seq = int(seq_row["next_seq"])
    cur = conn.execute(
        """
        INSERT INTO quota_ledger(
          seq, order_id, kind, pod_id, cpu_delta, mem_delta_mb,
          cpu_before, cpu_after, mem_before_mb, mem_after_mb,
          decision, top_hit, detail, at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            seq,
            order_id,
            kind,
            pod_id,
            float(cpu_after) - float(cpu_before),
            int(mem_after_mb) - int(mem_before_mb),
            float(cpu_before),
            float(cpu_after),
            int(mem_before_mb),
            int(mem_after_mb),
            decision,
            1 if top_hit else 0,
            detail,
            at or now_iso(),
        ),
    )
    return int(cur.lastrowid)


def replay_quota(conn: sqlite3.Connection) -> dict[str, Any]:
    """Replay quota checkpoints and historical start decisions.

    Every ledger row carries its own before/after checkpoint, so an
    out-of-band fixture/SQL quota edit appears as a baseline jump rather than
    making historical start decisions unreplayable.
    """
    entries = [dict(r) for r in conn.execute("SELECT * FROM quota_ledger ORDER BY seq").fetchall()]
    decision_rows = conn.execute(
        """
        SELECT o.id, o.pod_id, o.status, o.quota_decision, o.quota_top_hit,
               o.quota_snapshot, p.cpu_quota, p.mem_quota_mb
        FROM start_orders o
        JOIN pods p ON p.id = o.pod_id
        WHERE o.action = '启动'
        ORDER BY o.id
        """
    ).fetchall()

    decision_replays: list[dict[str, Any]] = []
    decision_ok = True
    for row in decision_rows:
        snapshot = _parse_snapshot(row["quota_snapshot"])
        before = snapshot.get("before") if isinstance(snapshot.get("before"), dict) else None
        if not before:
            continue
        if row["quota_decision"] not in (POLICY_NAME, "配额内启动", "重复启动幂等"):
            # Image/probe/rollback outcomes are owned by their own flows; do
            # not reinterpret them as quota decisions during replay.
            continue
        stored_decision = snapshot.get("decision") if isinstance(snapshot.get("decision"), dict) else {}
        need_cpu = stored_decision.get("cpu_need", row["cpu_quota"])
        need_mem = stored_decision.get("mem_need_mb", row["mem_quota_mb"])
        recomputed = evaluate_start(before, need_cpu, need_mem)
        expected_top = bool(row["quota_top_hit"])
        row_ok = bool(recomputed["top_line_hit"]) == expected_top
        if row["quota_decision"] == POLICY_NAME:
            row_ok = row_ok and recomputed["code"] == "QUOTA_TOP_HARD_REJECTED" and row["status"] == "失败"
        elif row["quota_decision"] == "配额内启动":
            row_ok = row_ok and recomputed["quota_allowed"] is True and row["status"] == "已完成"
        decision_ok = decision_ok and row_ok
        decision_replays.append(
            {
                "order_id": row["id"],
                "ok": row_ok,
                "stored_status": row["status"],
                "stored_decision": row["quota_decision"],
                "stored_top_hit": expected_top,
                "replayed_code": recomputed["code"],
                "replayed_top_hit": recomputed["top_line_hit"],
                "allowed": recomputed["allowed"],
            }
        )

    decision_summary = {
        "可重放": True,
        "条数": len(decision_replays),
        "一致数": sum(1 for item in decision_replays if item["ok"]),
        "全部一致": decision_ok,
        "entries": decision_replays,
        "口径": POLICY_NAME,
        "policy": POLICY,
    }

    if not entries:
        q_row = conn.execute("SELECT * FROM quotas WHERE id=1").fetchone()
        current = serialize_quota(q_row) if q_row else {}
        return {
            "可重放": True,
            "replayable": True,
            "账本条数": 0,
            "ledger_entries": 0,
            "逐笔一致": True,
            "checkpoints_match": True,
            "链式衔接": True,
            "chain_continuous": True,
            "水位一致": decision_ok,
            "current_matches_replay": decision_ok,
            "当前水位已入账": True,
            "baseline_jumps": 0,
            "决策重放": decision_summary,
            "重放水位": {
                "cpu_used": current.get("cpu_used"),
                "mem_used_mb": current.get("mem_used_mb"),
            },
            "当前水位": {
                "cpu_used": current.get("cpu_used"),
                "mem_used_mb": current.get("mem_used_mb"),
            },
            "口径": POLICY_NAME,
            "policy": POLICY,
        }

    checkpoint_ok = True
    chain_continuous = True
    baseline_jumps = 0
    cumulative_cpu = float(entries[0]["cpu_before"])
    cumulative_mem = int(entries[0]["mem_before_mb"])
    replay_rows: list[dict[str, Any]] = []

    for index, entry in enumerate(entries):
        before_cpu = float(entry["cpu_before"])
        before_mem = int(entry["mem_before_mb"])
        if index > 0:
            chain_ok = abs(before_cpu - cumulative_cpu) <= EPS and before_mem == cumulative_mem
            if not chain_ok:
                chain_continuous = False
                baseline_jumps += 1
        cumulative_cpu = before_cpu
        cumulative_mem = before_mem

        replayed_cpu = before_cpu + float(entry["cpu_delta"])
        replayed_mem = before_mem + int(entry["mem_delta_mb"])
        row_ok = (
            abs(replayed_cpu - float(entry["cpu_after"])) <= EPS
            and replayed_mem == int(entry["mem_after_mb"])
        )
        checkpoint_ok = checkpoint_ok and row_ok
        cumulative_cpu = replayed_cpu
        cumulative_mem = replayed_mem
        replay_rows.append(
            {
                "seq": entry["seq"],
                "order_id": entry["order_id"],
                "kind": entry["kind"],
                "decision": entry["decision"],
                "replayed_cpu": replayed_cpu,
                "replayed_mem_mb": replayed_mem,
                "checkpoint_ok": row_ok,
            }
        )

    q_row = conn.execute("SELECT * FROM quotas WHERE id=1").fetchone()
    current = quota_numbers(q_row) if q_row else {}
    latest = entries[-1]
    current_matches_latest = (
        bool(current)
        and abs(float(latest["cpu_after"]) - float(current["cpu_used"])) <= EPS
        and int(latest["mem_after_mb"]) == int(current["mem_used_mb"])
    )
    water_ok = checkpoint_ok and current_matches_latest and decision_ok

    return {
        "可重放": True,
        "replayable": True,
        "账本条数": len(entries),
        "ledger_entries": len(entries),
        "逐笔一致": checkpoint_ok,
        "checkpoints_match": checkpoint_ok,
        "链式衔接": chain_continuous,
        "chain_continuous": chain_continuous,
        "baseline_jumps": baseline_jumps,
        "当前水位已入账": current_matches_latest,
        "latest_checkpoint_matches_current": current_matches_latest,
        "水位一致": water_ok,
        "current_matches_replay": water_ok,
        "决策重放": decision_summary,
        "重放水位": {"cpu_used": cumulative_cpu, "mem_used_mb": cumulative_mem},
        "当前水位": {
            "cpu_used": current.get("cpu_used"),
            "mem_used_mb": current.get("mem_used_mb"),
        },
        "entries": replay_rows,
        "口径": POLICY_NAME,
        "policy": POLICY,
    }


def _parse_snapshot(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    try:
        loaded = json.loads(value)
        return loaded if isinstance(loaded, dict) else {}
    except (TypeError, ValueError):
        return {}


def build_top_bucket(conn: sqlite3.Connection, q_row: sqlite3.Row | None = None) -> dict[str, Any]:
    if q_row is None:
        q_row = conn.execute("SELECT * FROM quotas WHERE id=1").fetchone()
    quota = serialize_quota(q_row) if q_row else {}

    rows = conn.execute(
        """
        SELECT o.*, p.name AS pod_name,
               l.seq AS ledger_seq, l.cpu_before AS ledger_cpu_before,
               l.cpu_after AS ledger_cpu_after,
               l.mem_before_mb AS ledger_mem_before,
               l.mem_after_mb AS ledger_mem_after
        FROM start_orders o
        JOIN pods p ON p.id = o.pod_id
        LEFT JOIN quota_ledger l ON l.order_id = o.id
        WHERE o.action = '启动'
          AND (
            COALESCE(o.quota_top_hit, 0) = 1
            OR o.quota_decision = ?
            OR (o.status = '失败' AND o.note LIKE '%配额%')
          )
        ORDER BY o.id DESC
        """,
        (POLICY_NAME,),
    ).fetchall()

    items: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    impossible_success: list[dict[str, Any]] = []

    for row in rows:
        item = dict(row)
        item["snapshot"] = _parse_snapshot(row["quota_snapshot"])
        item["顶格桶"] = True
        item["口径"] = POLICY_NAME
        # Hard-reject station: a top-bucket row can never be a completed start.
        item["计成功"] = False
        item["count_as_success"] = False
        items.append(item)
        if row["status"] == "已完成":
            impossible_success.append(item)
        else:
            rejected.append(item)

    pod_rows = conn.execute("SELECT * FROM pods ORDER BY id").fetchall()
    startable_ids: list[int] = []
    for pod in pod_rows:
        if pod["run_state"] == "运行中":
            continue
        cpu_after = float(quota.get("cpu_used", 0.0)) + float(pod["cpu_quota"])
        mem_after = int(quota.get("mem_used_mb", 0)) + int(pod["mem_quota_mb"])
        if cpu_after <= float(quota.get("cpu_limit", 0.0)) + EPS and mem_after <= int(quota.get("mem_limit_mb", 0)):
            startable_ids.append(int(pod["id"]))

    replay = replay_quota(conn)
    return {
        "桶名": "顶格桶",
        "口径": POLICY_NAME,
        "顶格口径": POLICY_NAME,
        "policy": POLICY,
        "mode": "hard_reject",
        "当前顶格": bool(quota.get("顶格")),
        "顶格": bool(quota.get("顶格")),
        "at_top": bool(quota.get("at_top")),
        "可启动舱体IDs": startable_ids,
        "可启动": bool(startable_ids),
        "总数": len(items),
        "total": len(items),
        "拒启动数": len(rejected),
        "失败数": len(rejected),
        "rejected_count": len(rejected),
        # This is intentionally always derived as zero for the hard-reject arm.
        "成功数": 0,
        "success_count": 0,
        "计成功数": 0,
        "违规成功数": len(impossible_success),
        "success_violation_count": len(impossible_success),
        "在途缺口数": 0,
        "缺口数": 0,
        "gap_count": 0,
        "顶格在途": [],
        "top_in_flight": [],
        "items": items,
        "明细": items,
        "订单": items,
        "顶格拒启动": rejected,
        "rejected_orders": rejected,
        "IDs": [int(x["id"]) for x in items],
        "order_ids": [int(x["id"]) for x in items],
        "违规成功订单": impossible_success,
        "replay": replay,
        "重放": replay,
    }
