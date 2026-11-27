from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from typing import Any

from app.db import write_db
from app.services.common import now_iso
from app.services.quota_ledger import add_ledger, quota_numbers
from app.services.quota_policy import POLICY_NAME, evaluate_start, serialize_quota

FaultHook = Callable[[str, sqlite3.Connection], None] | None


def _json_snapshot(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _insert_order(
    conn: sqlite3.Connection,
    *,
    pod_id: int,
    action: str,
    target_image_id: int | None,
    status: str,
    note: str,
    now: str,
    quota_decision: str | None = None,
    top_hit: bool = False,
    snapshot: dict[str, Any] | None = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO start_orders(
          pod_id, action, target_image_id, status, note, created_at,
          quota_decision, quota_top_hit, quota_snapshot
        ) VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (
            pod_id,
            action,
            target_image_id,
            status,
            note,
            now,
            quota_decision,
            1 if top_hit else 0,
            _json_snapshot(snapshot or {}),
        ),
    )
    order_id = int(cur.lastrowid)
    conn.execute(
        "INSERT INTO events(kind, ref, detail, at) VALUES (?,?,?,?)",
        ("启停单", str(order_id), f"{action} → {status} {note}", now),
    )
    return order_id


def _receipt(
    order_id: int,
    status: str,
    note: str,
    *,
    pod_id: int,
    action: str,
    code: str,
    decision: dict[str, Any] | None = None,
    before: dict[str, Any] | None = None,
    after_row: sqlite3.Row | None = None,
    top_hit: bool | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    after = serialize_quota(after_row) if after_row is not None else {}
    is_success = status == "已完成"
    hit = decision.get("top_line_hit") if decision else None
    if top_hit is not None:
        hit = top_hit
    message = f"顶格硬拒：{note}" if hit and not is_success else note
    receipt: dict[str, Any] = {
        "id": order_id,
        "order_id": order_id,
        "pod_id": pod_id,
        "action": action,
        "status": status,
        "note": note,
        "message": message,
        "回执": message,
        "ok": is_success,
        "success": is_success,
        "error": not is_success,
        "rejected": bool(hit and not is_success),
        "成功": is_success,
        "code": code,
        "reason": (decision or {}).get("reason", note),
        "口径": POLICY_NAME,
        "policy": "hard_reject_at_top",
        "顶格": bool(hit),
        "top_hit": bool(hit),
        "quota_decision": (decision or {}).get("code", code),
        "decision": (decision or {}).get("code", code),
        "配额前": before or {},
        "配额后": {
            "cpu_used": after.get("cpu_used"),
            "cpu_limit": after.get("cpu_limit"),
            "mem_used_mb": after.get("mem_used_mb"),
            "mem_limit_mb": after.get("mem_limit_mb"),
            "顶格": after.get("顶格"),
        },
        "quota": after,
    }
    if decision:
        receipt["判定"] = decision
    if extra:
        receipt.update(extra)
    return receipt


def create_order(
    *,
    pod_id: int,
    action: str,
    target_image_id: int | None = None,
    fault: FaultHook = None,
) -> dict[str, Any]:
    """Create an order with one linearized quota/start transaction."""
    with write_db() as conn:
        return _create_order_in_tx(
            conn,
            pod_id=pod_id,
            raw_action=action,
            target_image_id=target_image_id,
            fault=fault,
        )


def _create_order_in_tx(
    conn: sqlite3.Connection,
    *,
    pod_id: int,
    raw_action: str,
    target_image_id: int | None,
    fault: FaultHook,
) -> dict[str, Any]:
    now = now_iso()
    pod = conn.execute("SELECT * FROM pods WHERE id=?", (pod_id,)).fetchone()
    if not pod:
        return {"错误": "舱体不存在", "ok": False, "status": "失败", "code": "POD_NOT_FOUND"}

    action_text = (raw_action or "").strip()
    if action_text in ("启动", "start", "Start"):
        stored_action = "启动"
    elif action_text in ("停止", "stop", "Stop"):
        stored_action = "停止"
    else:
        stored_action = action_text

    img_id = target_image_id if target_image_id is not None else pod["image_id"]
    q_row = conn.execute("SELECT * FROM quotas WHERE id=1").fetchone()
    before = quota_numbers(q_row) if q_row else {}
    if stored_action == "启动" and q_row is None:
        return {
            "ok": False,
            "status": "失败",
            "code": "QUOTA_NOT_INITIALIZED",
            "note": "配额行未初始化",
            "口径": POLICY_NAME,
            "顶格": False,
        }

    if stored_action == "启动":
        img = conn.execute("SELECT * FROM images WHERE id=?", (img_id,)).fetchone() if img_id is not None else None
        if not img or img["pull_state"] != "已就绪":
            snapshot = {"before": before, "image_id": img_id, "reason": "镜像未就绪"}
            order_id = _insert_order(
                conn,
                pod_id=pod_id,
                action=stored_action,
                target_image_id=img_id,
                status="失败",
                note="镜像未就绪",
                now=now,
                quota_decision="镜像未就绪",
                snapshot=snapshot,
            )
            add_ledger(
                conn,
                kind="启动未执行",
                decision="镜像未就绪",
                order_id=order_id,
                pod_id=pod_id,
                cpu_before=before["cpu_used"],
                cpu_after=before["cpu_used"],
                mem_before_mb=before["mem_used_mb"],
                mem_after_mb=before["mem_used_mb"],
                at=now,
            )
            return _receipt(
                order_id,
                "失败",
                "镜像未就绪",
                pod_id=pod_id,
                action=stored_action,
                code="IMAGE_NOT_READY",
                before=before,
                after_row=q_row,
                top_hit=False,
            )

        already_running = pod["run_state"] == "运行中"
        # Quota-top legality is checked before duplicate-start handling so a
        # top-line write always receives the site-wide hard rejection.
        decision = evaluate_start(
            q_row,
            pod["cpu_quota"],
            pod["mem_quota_mb"],
            already_running=False,
        )

        if not decision["quota_allowed"]:
            snapshot = {"before": before, "decision": decision, "policy": POLICY_NAME}
            order_id = _insert_order(
                conn,
                pod_id=pod_id,
                action=stored_action,
                target_image_id=img_id,
                status="失败",
                note=decision["note"],
                now=now,
                quota_decision=POLICY_NAME,
                top_hit=True,
                snapshot=snapshot,
            )
            add_ledger(
                conn,
                kind="启动顶格硬拒",
                decision=POLICY_NAME,
                order_id=order_id,
                pod_id=pod_id,
                cpu_before=before["cpu_used"],
                cpu_after=before["cpu_used"],
                mem_before_mb=before["mem_used_mb"],
                mem_after_mb=before["mem_used_mb"],
                top_hit=True,
                detail=decision["reason"],
                at=now,
            )
            return _receipt(
                order_id,
                "失败",
                decision["note"],
                pod_id=pod_id,
                action=stored_action,
                code=decision["code"],
                decision=decision,
                before=before,
                after_row=q_row,
                top_hit=True,
            )

        if already_running:
            decision["already_running"] = True
            decision["code"] = "POD_ALREADY_RUNNING"
            order_id = _insert_order(
                conn,
                pod_id=pod_id,
                action=stored_action,
                target_image_id=img_id,
                status="已完成",
                note="舱体已在运行中",
                now=now,
                quota_decision="重复启动幂等",
                snapshot={"before": before, "decision": decision},
            )
            add_ledger(
                conn,
                kind="重复启动幂等",
                decision="重复启动幂等",
                order_id=order_id,
                pod_id=pod_id,
                cpu_before=before["cpu_used"],
                cpu_after=before["cpu_used"],
                mem_before_mb=before["mem_used_mb"],
                mem_after_mb=before["mem_used_mb"],
                at=now,
            )
            return _receipt(
                order_id,
                "已完成",
                "舱体已在运行中",
                pod_id=pod_id,
                action=stored_action,
                code="POD_ALREADY_RUNNING",
                decision=decision,
                before=before,
                after_row=q_row,
                top_hit=False,
            )

        need_cpu = float(pod["cpu_quota"])
        need_mem = int(pod["mem_quota_mb"])

        # Explicit savepoint: a fault after the transient 启动中 row or after
        # the quota increment rolls the resource writes all the way back, while
        # still allowing a failure receipt to be committed afterwards.
        conn.execute("SAVEPOINT start_resource_write")
        try:
            conn.execute("UPDATE pods SET run_state=? WHERE id=?", ("启动中", pod_id))
            if fault:
                fault("after_starting", conn)

            conn.execute(
                "UPDATE quotas SET cpu_used=cpu_used+?, mem_used_mb=mem_used_mb+?, version=version+1 WHERE id=1",
                (need_cpu, need_mem),
            )
            if fault:
                fault("after_quota_increment", conn)

            conn.execute(
                "UPDATE pods SET run_state=?, image_id=? WHERE id=?",
                ("运行中", img_id, pod_id),
            )
            if fault:
                fault("before_commit", conn)
        except Exception as exc:
            conn.execute("ROLLBACK TO SAVEPOINT start_resource_write")
            after_fault_row = conn.execute("SELECT * FROM quotas WHERE id=1").fetchone()
            after_fault = quota_numbers(after_fault_row)
            rolled_back_pod = conn.execute("SELECT run_state FROM pods WHERE id=?", (pod_id,)).fetchone()
            note = f"启动中断，已回滚：{type(exc).__name__}"
            order_id = _insert_order(
                conn,
                pod_id=pod_id,
                action=stored_action,
                target_image_id=img_id,
                status="失败",
                note=note,
                now=now,
                quota_decision="半截脏启动回滚",
                snapshot={
                    "before": before,
                    "after_rollback": after_fault,
                    "restored_run_state": rolled_back_pod["run_state"] if rolled_back_pod else None,
                    "error": str(exc),
                },
            )
            add_ledger(
                conn,
                kind="启动异常回滚",
                decision="半截脏启动回滚",
                order_id=order_id,
                pod_id=pod_id,
                cpu_before=before["cpu_used"],
                cpu_after=after_fault["cpu_used"],
                mem_before_mb=before["mem_used_mb"],
                mem_after_mb=after_fault["mem_used_mb"],
                detail=str(exc),
                at=now,
            )
            return _receipt(
                order_id,
                "失败",
                note,
                pod_id=pod_id,
                action=stored_action,
                code="START_DIRTY_WRITE_ROLLED_BACK",
                decision=decision,
                before=before,
                after_row=after_fault_row,
                top_hit=False,
                extra={"rolled_back": True, "restored_run_state": rolled_back_pod["run_state"] if rolled_back_pod else None},
            )
        else:
            conn.execute("RELEASE SAVEPOINT start_resource_write")

        after_row = conn.execute("SELECT * FROM quotas WHERE id=1").fetchone()
        after = quota_numbers(after_row) if after_row else {}
        order_id = _insert_order(
            conn,
            pod_id=pod_id,
            action=stored_action,
            target_image_id=img_id,
            status="已完成",
            note="启动成功",
            now=now,
            quota_decision="配额内启动",
            snapshot={"before": before, "after": after, "decision": decision},
        )
        add_ledger(
            conn,
            kind="启动占用",
            decision="配额内启动",
            order_id=order_id,
            pod_id=pod_id,
            cpu_before=before["cpu_used"],
            cpu_after=after["cpu_used"],
            mem_before_mb=before["mem_used_mb"],
            mem_after_mb=after["mem_used_mb"],
            detail=f"+{need_cpu} CPU / +{need_mem}MB",
            at=now,
        )
        return _receipt(
            order_id,
            "已完成",
            "启动成功",
            pod_id=pod_id,
            action=stored_action,
            code="STARTED",
            decision=decision,
            before=before,
            after_row=after_row,
            top_hit=False,
        )

    if stored_action == "停止":
        was_running = pod["run_state"] == "运行中"
        need_cpu = float(pod["cpu_quota"])
        need_mem = int(pod["mem_quota_mb"])
        if was_running:
            conn.execute("UPDATE pods SET run_state=? WHERE id=?", ("已停止", pod_id))
            conn.execute(
                "UPDATE quotas SET cpu_used=MAX(0, cpu_used-?), mem_used_mb=MAX(0, mem_used_mb-?), version=version+1 WHERE id=1",
                (need_cpu, need_mem),
            )
            stop_decision = "停止释放"
            note = "停止成功"
        else:
            stop_decision = "停止幂等"
            note = "停止成功"

        after_row = conn.execute("SELECT * FROM quotas WHERE id=1").fetchone()
        after = quota_numbers(after_row) if after_row else {}
        order_id = _insert_order(
            conn,
            pod_id=pod_id,
            action=stored_action,
            target_image_id=img_id,
            status="已完成",
            note=note,
            now=now,
            quota_decision=stop_decision,
            snapshot={"before": before, "after": after, "was_running": was_running},
        )
        add_ledger(
            conn,
            kind=stop_decision,
            decision=stop_decision,
            order_id=order_id,
            pod_id=pod_id,
            cpu_before=before["cpu_used"],
            cpu_after=after["cpu_used"],
            mem_before_mb=before["mem_used_mb"],
            mem_after_mb=after["mem_used_mb"],
            detail=f"-{need_cpu if was_running else 0} CPU / -{need_mem if was_running else 0}MB",
            at=now,
        )
        return _receipt(
            order_id,
            "已完成",
            note,
            pod_id=pod_id,
            action=stored_action,
            code="STOPPED",
            before=before,
            after_row=after_row,
            top_hit=False,
        )

    # Unknown action: persist the failed receipt, but never touch occupancy.
    order_id = _insert_order(
        conn,
        pod_id=pod_id,
        action=stored_action,
        target_image_id=img_id,
        status="失败",
        note=f"未知动作:{action_text}",
        now=now,
        quota_decision="未知动作",
        snapshot={"before": before},
    )
    return _receipt(
        order_id,
        "失败",
        f"未知动作:{action_text}",
        pod_id=pod_id,
        action=stored_action,
        code="UNKNOWN_ACTION",
        before=before,
        after_row=q_row,
        top_hit=False,
    )
