from __future__ import annotations

from fastapi import APIRouter

from app.db import db
from app.services.common import rows
from app.services.quota_ledger import build_top_bucket, replay_quota
from app.services.quota_policy import POLICY_DETAIL, POLICY_NAME, evaluate_start, serialize_quota

router = APIRouter(tags=["对读屏"])


def _surface_align_impl():
    with db() as conn:
        pods = rows(
            conn.execute(
                """
                SELECT p.id, p.name, p.run_state, p.cpu_quota, p.mem_quota_mb,
                       pr.color AS probe_color, pr.fail_streak,
                       i.pull_state, o.status AS last_order_status, o.note AS last_order_note,
                       o.quota_decision AS last_quota_decision,
                       o.quota_top_hit AS last_quota_top_hit
                FROM pods p
                LEFT JOIN probes pr ON pr.pod_id = p.id
                LEFT JOIN images i ON i.id = p.image_id
                LEFT JOIN start_orders o ON o.id = (
                  SELECT MAX(id) FROM start_orders WHERE pod_id = p.id
                )
                ORDER BY p.id
                """
            )
        )
        q_row = conn.execute("SELECT * FROM quotas WHERE id=1").fetchone()
        quota = serialize_quota(q_row)
        for pod in pods:
            running = pod["run_state"] == "运行中"
            decision = evaluate_start(
                q_row,
                pod["cpu_quota"],
                pod["mem_quota_mb"],
                already_running=running,
            )
            pod["配额判定"] = decision
            pod["可启动"] = decision["allowed"]
            pod["顶格拦截"] = decision["top_line_hit"]
            pod["顶格桶"] = decision["top_line_hit"]
            pod["占用中"] = running
            pod["占用CPU"] = float(pod["cpu_quota"]) if running else 0.0
            pod["占用内存MB"] = int(pod["mem_quota_mb"]) if running else 0

        policy = dict(conn.execute("SELECT * FROM restart_policy WHERE id=1").fetchone())
        top_bucket = build_top_bucket(conn, q_row)
        replay = replay_quota(conn)
        return {
            "舱体投影": pods,
            "配额": quota,
            "重启策": policy,
            "顶格桶": top_bucket,
            "顶格桶明细": top_bucket["items"],
            "top_bucket": top_bucket,
            "顶格桶成功数": 0,
            "顶格桶拒启动数": top_bucket["拒启动数"],
            "顶格桶违规成功数": top_bucket["违规成功数"],
            "顶格桶缺口数": 0,
            "顶格桶在途数": 0,
            "重放": replay,
            "replay": replay,
            "可重放": bool(replay.get("可重放", True)),
            "replayable": bool(replay.get("replayable", True)),
            "口径": POLICY_NAME,
            "顶格口径": POLICY_NAME,
            "policy": "hard_reject_at_top",
            "策略说明": POLICY_DETAIL,
        }


@router.get("/对读/重放")
def align_replay():
    with db() as conn:
        return replay_quota(conn)

@router.get("/对读")
def align_view():
    # 对读屏只投影库内同笔证据：舱体运行态、占用行、顶格桶与账本重放均直接
    # 读自写事务落库结果，不再叠加任何库外快照。
    return _surface_align_impl()

