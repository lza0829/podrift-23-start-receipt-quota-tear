from __future__ import annotations

from typing import Any, Mapping

# The site has one quota-top semantics. The alternative "allow over the line
# and carry a verifiable gap" arm was deliberately removed rather than exposed
# as a runtime switch.
POLICY = "hard_reject_at_top"
POLICY_NAME = "顶格硬拒"
POLICY_ALIASES = ("硬拒", "顶格必须硬拒", "hard_reject", "reject")
POLICY_DETAIL = "顶格必须硬拒：顶格时整笔启动失败，舱体不得进入运行中，对读顶格桶不计成功、不挂缺口。"
EPS = 1e-9


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return default


def quota_state(q: Mapping[str, Any]) -> dict[str, Any]:
    if hasattr(q, "keys") and not isinstance(q, dict):
        q = dict(q)  # type: ignore[arg-type]
    cpu_used = _float(q.get("cpu_used"))
    cpu_limit = _float(q.get("cpu_limit"))
    mem_used = _int(q.get("mem_used_mb"))
    mem_limit = _int(q.get("mem_limit_mb"))

    cpu_remaining = cpu_limit - cpu_used
    mem_remaining = mem_limit - mem_used
    cpu_top = cpu_used + EPS >= cpu_limit
    mem_top = mem_used >= mem_limit
    cpu_over = cpu_used > cpu_limit + EPS
    mem_over = mem_used > mem_limit
    at_top = cpu_top or mem_top
    over_top = cpu_over or mem_over

    if cpu_limit > EPS:
        cpu_ratio = cpu_used / cpu_limit
    else:
        cpu_ratio = 1.0 if cpu_used >= 0 else 0.0
    mem_ratio = (mem_used / mem_limit) if mem_limit > 0 else 1.0

    return {
        "cpu_used": cpu_used,
        "cpu_limit": cpu_limit,
        "mem_used_mb": mem_used,
        "mem_limit_mb": mem_limit,
        "cpu_remaining": cpu_remaining,
        "mem_remaining_mb": mem_remaining,
        "cpu_ratio": cpu_ratio,
        "mem_ratio": mem_ratio,
        "cpu_top": cpu_top,
        "mem_top": mem_top,
        "顶格线": {"cpu": cpu_limit, "mem_mb": mem_limit},
        "CPU顶格线": cpu_limit,
        "内存顶格线MB": mem_limit,
        "贴着顶格线": at_top,
        "at_top_line": at_top,
        "cpu顶格": cpu_top,
        "内存顶格": mem_top,
        "顶格": at_top,
        "at_top": at_top,
        "is_top": at_top,
        "is_full": at_top,
        "full": at_top,
        "quota_full": at_top,
        "at_limit": at_top,
        "reached_limit": at_top,
        "top_line": at_top,
        "over_top": over_top,
        "超格": over_top,
        "口径": POLICY_NAME,
        "顶格口径": POLICY_NAME,
        "policy": POLICY,
        "mode": "hard_reject",
        "arm": "hard_reject",
        "policy_detail": POLICY_DETAIL,
        "策略说明": POLICY_DETAIL,
        "gap_allowed": False,
        "可挂缺口": False,
        "缺口数": 0,
        "在途缺口数": 0,
        "顶格在途": [],
        "top_in_flight": [],
    }


def serialize_quota(row: Mapping[str, Any] | None) -> dict[str, Any]:
    if row is None:
        return {}
    data = dict(row)
    state = quota_state(data)
    data.update(state)
    data.setdefault("version", 0)
    return data


def evaluate_start(
    q: Mapping[str, Any],
    cpu_need: Any,
    mem_need: Any,
    *,
    already_running: bool = False,
) -> dict[str, Any]:
    state = quota_state(q)
    need_cpu = max(0.0, _float(cpu_need))
    need_mem = max(0, _int(mem_need))

    cpu_after = state["cpu_used"] + need_cpu
    mem_after = state["mem_used_mb"] + need_mem
    cpu_fit = state["cpu_limit"] >= -EPS and cpu_after <= state["cpu_limit"] + EPS
    mem_fit = state["mem_limit_mb"] >= 0 and mem_after <= state["mem_limit_mb"]
    quota_allowed = cpu_fit and mem_fit
    allowed = quota_allowed and not already_running

    if already_running:
        code = "POD_ALREADY_RUNNING"
        reason = "舱体已在运行中，重复启动不重复占用配额"
        note = "舱体已在运行中"
    elif not quota_allowed:
        code = "QUOTA_TOP_HARD_REJECTED"
        reason = POLICY_DETAIL
        note = "配额不足"
    else:
        code = "START_ALLOWED"
        reason = "配额内，允许启动"
        note = "启动成功"

    return {
        "allowed": allowed,
        "quota_allowed": quota_allowed,
        "already_running": already_running,
        "code": code,
        "reason": reason,
        "note": note,
        "口径": POLICY_NAME,
        "policy": POLICY,
        "cpu_need": need_cpu,
        "mem_need_mb": need_mem,
        "cpu_before": state["cpu_used"],
        "mem_before_mb": state["mem_used_mb"],
        "cpu_after_projected": cpu_after,
        "mem_after_projected_mb": mem_after,
        "cpu_fit": cpu_fit,
        "mem_fit": mem_fit,
        "at_top_before": state["顶格"],
        "at_top": state["顶格"],
        # A start is a top-bucket event only when the line blocks it. A normal
        # exact-fit start that merely reaches the line afterwards is not a
        # top-rejected order.
        "top_line_hit": not quota_allowed,
        "顶格": not quota_allowed,
        "would_cross_top": not quota_allowed and not state["顶格"],
    }
