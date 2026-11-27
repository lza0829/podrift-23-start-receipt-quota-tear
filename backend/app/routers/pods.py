from __future__ import annotations

from fastapi import APIRouter

from app.db import db
from app.services.common import rows
from app.services.quota_policy import evaluate_start, serialize_quota

router = APIRouter(tags=["舱体"])


@router.get("/舱体")
def list_pods():
    with db() as conn:
        pod_rows = rows(
            conn.execute(
                """
                SELECT p.*, i.name AS image_name, i.tag AS image_tag, i.pull_state,
                       pr.color AS probe_color, pr.fail_streak
                FROM pods p
                LEFT JOIN images i ON i.id = p.image_id
                LEFT JOIN probes pr ON pr.pod_id = p.id
                ORDER BY p.id
                """
            )
        )
        q_row = conn.execute("SELECT * FROM quotas WHERE id=1").fetchone()
        quota = serialize_quota(q_row) if q_row else {}
        for pod in pod_rows:
            running = pod["run_state"] == "运行中"
            decision = evaluate_start(
                q_row,
                pod["cpu_quota"],
                pod["mem_quota_mb"],
                already_running=running,
            ) if q_row else {}
            pod["占用中"] = running
            pod["is_occupying"] = running
            pod["占用状态"] = "占用中" if running else "未占用"
            pod["占用CPU"] = float(pod["cpu_quota"]) if running else 0.0
            pod["占用内存MB"] = int(pod["mem_quota_mb"]) if running else 0
            pod["cpu_occupied"] = pod["占用CPU"]
            pod["mem_occupied_mb"] = pod["占用内存MB"]
            pod["配额判定"] = decision
            pod["可启动"] = bool(decision.get("allowed"))
            pod["顶格拦截"] = bool(decision.get("top_line_hit"))
            pod["quota_policy"] = quota.get("policy")
            pod["口径"] = quota.get("口径")
        return pod_rows

