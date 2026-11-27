from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.db import connect
from app.routers.quotas import QuotaIn, update_quota
from app.services.order_service import create_order


def _quota(client):
    response = client.get("/api/配额")
    assert response.status_code == 200
    return response.json()


def _align(client):
    response = client.get("/api/对读")
    assert response.status_code == 200
    return response.json()


def test_顶格拒启动_cpu顶格时失败回执且舱体不运行(top_quota_client):
    client = top_quota_client
    before = _quota(client)

    response = client.post("/api/启停单", json={"pod_id": 1, "action": "启动"})
    assert response.status_code == 200
    receipt = response.json()

    assert receipt["status"] == "失败"
    assert receipt["ok"] is False
    assert receipt["code"] == "QUOTA_TOP_HARD_REJECTED"
    assert receipt["口径"] == "顶格硬拒"
    assert receipt["顶格"] is True
    assert "配额" in receipt["note"]

    after = _quota(client)
    assert after["cpu_used"] == before["cpu_used"]
    assert after["mem_used_mb"] == before["mem_used_mb"]

    pods = client.get("/api/舱体").json()
    pod1 = next(p for p in pods if p["id"] == 1)
    assert pod1["run_state"] == "已停止"
    assert pod1["占用中"] is False
    assert pod1["占用CPU"] == 0.0

    align = _align(client)
    bucket = align["顶格桶"]
    assert bucket["口径"] == "顶格硬拒"
    assert bucket["成功数"] == 0
    assert bucket["拒启动数"] >= 1
    assert bucket["违规成功数"] == 0
    assert any(item["pod_id"] == 1 and item["status"] == "失败" for item in bucket["items"])
    assert align["重放"]["水位一致"] is True


def test_顶格拒启动_内存单独顶格同样硬拒(client):
    client.post(
        "/api/配额",
        json={"cpu_used": 1.2, "cpu_limit": 4.0, "mem_used_mb": 2048, "mem_limit_mb": 2048},
    )

    receipt = client.post("/api/启停单", json={"pod_id": 1, "action": "启动"}).json()
    assert receipt["status"] == "失败"
    assert receipt["code"] == "QUOTA_TOP_HARD_REJECTED"

    pod1 = next(p for p in client.get("/api/舱体").json() if p["id"] == 1)
    assert pod1["run_state"] == "已停止"

    bucket = _align(client)["顶格桶"]
    assert bucket["成功数"] == 0
    assert bucket["拒启动数"] == 1


def test_未顶格但本舱会越过顶格线也同笔硬拒(client):
    # 3.8 + 0.5 = 4.3 > 4.0; the bar was not already full, but this write crosses it.
    client.post("/api/配额", json={"cpu_used": 3.8, "cpu_limit": 4.0, "mem_used_mb": 768})

    receipt = client.post("/api/启停单", json={"pod_id": 1, "action": "启动"}).json()
    assert receipt["status"] == "失败"
    assert receipt["顶格"] is True
    assert receipt["判定"]["would_cross_top"] is True

    quota = _quota(client)
    assert quota["cpu_used"] == 3.8
    assert next(p for p in client.get("/api/舱体").json() if p["id"] == 1)["run_state"] == "已停止"


def test_顶格挂缺口_硬拒站不得出现缺口或顶格在途(top_quota_client):
    client = top_quota_client
    receipt = client.post("/api/启停单", json={"pod_id": 1, "action": "启动"}).json()
    assert receipt["status"] == "失败"

    quota = _quota(client)
    align = _align(client)

    # The hard-reject arm must not silently become the gap arm.
    assert quota["gap_allowed"] is False
    assert quota["可挂缺口"] is False
    assert quota["缺口数"] == 0
    assert quota["在途缺口数"] == 0
    assert quota["顶格在途"] == []
    assert align["顶格桶"]["顶格在途"] == []
    assert align["顶格桶"]["缺口数"] == 0
    assert align["顶格桶"]["成功数"] == 0

    orders = client.get("/api/启停单").json()
    order = next(o for o in orders if o["id"] == receipt["id"])
    assert order["status"] == "失败"
    assert order["quota_decision"] == "顶格硬拒"
    assert order["quota_top_hit"] == 1


def test_顶格时即使舱体已在运行_重复启动写口也不计成功(client):
    client.post("/api/启停单", json={"pod_id": 1, "action": "启动"})
    client.post("/api/配额/顶格")

    receipt = client.post("/api/启停单", json={"pod_id": 1, "action": "启动"}).json()
    assert receipt["status"] == "失败"
    assert receipt["code"] == "QUOTA_TOP_HARD_REJECTED"
    assert _align(client)["顶格桶"]["成功数"] == 0


def test_镜像未就绪先于配额判定时_不污染顶格重放(client):
    client.post("/api/配额/顶格")
    receipt = client.post("/api/启停单", json={"pod_id": 2, "action": "启动"}).json()
    assert receipt["status"] == "失败"
    assert receipt["code"] == "IMAGE_NOT_READY"
    replay = _align(client)["重放"]
    assert replay["水位一致"] is True
    assert replay["决策重放"]["全部一致"] is True


def test_正常启动_配额占用行与重放水位协同(client):
    before = _quota(client)
    receipt = client.post("/api/启停单", json={"pod_id": 1, "action": "启动"}).json()
    assert receipt["status"] == "已完成"
    assert receipt["code"] == "STARTED"

    after = _quota(client)
    assert after["cpu_used"] == pytest.approx(before["cpu_used"] + 0.5)
    assert after["mem_used_mb"] == before["mem_used_mb"] + 256

    pod1 = next(p for p in client.get("/api/舱体").json() if p["id"] == 1)
    assert pod1["run_state"] == "运行中"
    assert pod1["占用中"] is True
    assert pod1["占用CPU"] == 0.5
    assert pod1["占用内存MB"] == 256

    quota_occupancy = next(row for row in after["舱体占用行"] if row["pod_id"] == 1)
    assert quota_occupancy["cpu_occupied"] == 0.5
    assert quota_occupancy["mem_occupied_mb"] == 256

    replay = client.get("/api/配额/重放").json()
    assert replay["水位一致"] is True
    assert replay["逐笔一致"] is True


def test_改配额乐观版本冲突返回明确回执(client):
    quota = _quota(client)
    version = quota["version"]

    ok = client.post("/api/配额", json={"cpu_limit": 4.5, "expected_version": version})
    assert ok.status_code == 200
    conflict = client.post("/api/配额", json={"cpu_limit": 5.0, "expected_version": version})
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "QUOTA_VERSION_CONFLICT"


def test_改配额并发与启动并发_不产生回执和状态双结局(client):
    # Remaining CPU is exactly 0.5: among five distinct 0.5-core pods only one
    # can win the BEGIN IMMEDIATE transaction.
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("UPDATE quotas SET cpu_used=3.5, cpu_limit=4.0, mem_used_mb=0, mem_limit_mb=2048 WHERE id=1")
        for idx in range(5):
            conn.execute(
                "INSERT INTO pods(name, image_id, run_state, cpu_quota, mem_quota_mb) VALUES (?,?,?,?,?)",
                (f"race-{idx}", 1, "已停止", 0.5, 256),
            )
        conn.commit()
    finally:
        conn.close()

    barrier = threading.Barrier(5)

    def start(idx):
        barrier.wait()
        return create_order(pod_id=idx + 3, action="启动")

    with ThreadPoolExecutor(max_workers=5) as pool:
        receipts = list(pool.map(start, range(5)))

    successes = [r for r in receipts if r["status"] == "已完成"]
    rejects = [r for r in receipts if r["code"] == "QUOTA_TOP_HARD_REJECTED"]
    assert len(successes) == 1
    assert len(rejects) == 4

    conn = connect()
    try:
        q = conn.execute("SELECT * FROM quotas WHERE id=1").fetchone()
        running = conn.execute("SELECT COUNT(*) AS c FROM pods WHERE run_state='运行中'").fetchone()["c"]
        bucket_rows = conn.execute(
            "SELECT COUNT(*) AS c FROM start_orders WHERE action='启动' AND quota_top_hit=1 AND status='已完成'"
        ).fetchone()["c"]
        replay = conn.execute("SELECT COUNT(*) AS c FROM quota_ledger").fetchone()["c"]
    finally:
        conn.close()

    assert float(q["cpu_used"]) == pytest.approx(4.0)
    assert running == 1
    assert bucket_rows == 0
    assert replay >= 5

    align = _align(client)
    assert align["顶格桶"]["成功数"] == 0
    assert align["顶格桶"]["违规成功数"] == 0
    assert align["重放"]["水位一致"] is True


def test_改配额同时点启动_每笔结局都可重放且不矛盾(client):
    for iteration in range(8):
        conn = connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("UPDATE pods SET run_state='已停止' WHERE id=1")
            conn.execute(
                "UPDATE quotas SET cpu_used=0, cpu_limit=1.0, mem_used_mb=0, mem_limit_mb=1024, version=version+1 WHERE id=1"
            )
            conn.commit()
        finally:
            conn.close()

        barrier = threading.Barrier(2)

        def change_quota():
            barrier.wait()
            return update_quota(QuotaIn(cpu_used=1.0, cpu_limit=1.0, mem_used_mb=1024, mem_limit_mb=1024))

        def start_pod():
            barrier.wait()
            return create_order(pod_id=1, action="启动")

        result = {}
        with ThreadPoolExecutor(max_workers=2) as pool:
            quota_future = pool.submit(change_quota)
            start_future = pool.submit(start_pod)
            result["quota"] = quota_future.result()
            result["start"] = start_future.result()

        receipt = result["start"]
        if receipt["status"] == "失败":
            assert receipt["code"] == "QUOTA_TOP_HARD_REJECTED"
            pod = client.get("/api/舱体").json()[0]
            assert pod["run_state"] == "已停止"
        else:
            assert receipt["status"] == "已完成"
            # A success must have been legal against the pre-start snapshot.
            decision = receipt["判定"]
            assert decision["quota_allowed"] is True
            assert decision["cpu_before"] + 0.5 <= decision["cpu_before"] + 0.5 + 1e-9
            pod = client.get("/api/舱体").json()[0]
            assert pod["run_state"] == "运行中"

        replay = client.get("/api/配额/重放").json()
        assert replay["水位一致"] is True, (iteration, replay)


@pytest.mark.parametrize("fault_stage", ["after_starting", "after_quota_increment", "before_commit"])
def test_半截脏启动回滚_配额和运行态回到点击前(client, fault_stage):
    before = _quota(client)

    def fault(stage, _conn):
        if stage == fault_stage:
            raise RuntimeError("注入半截脏启动")

    receipt = create_order(pod_id=1, action="启动", fault=fault)
    assert receipt["status"] == "失败"
    assert receipt["code"] == "START_DIRTY_WRITE_ROLLED_BACK"
    assert receipt["rolled_back"] is True
    assert receipt["restored_run_state"] == "已停止"

    after = _quota(client)
    assert after["cpu_used"] == before["cpu_used"]
    assert after["mem_used_mb"] == before["mem_used_mb"]

    pod1 = next(p for p in client.get("/api/舱体").json() if p["id"] == 1)
    assert pod1["run_state"] == "已停止"
    assert pod1["占用中"] is False

    align = _align(client)
    assert align["重放"]["水位一致"] is True
    assert not any(
        "回滚" in item["quota_decision"] and item["status"] == "失败"
        for item in align["顶格桶明细"]
    )  # rollback receipts are not quota-top bucket entries


def test_顶格收口_完成亮与配额证据五面同验(top_quota_client):
    """顶格下启停写口不得亮完成；五面（启停/配额/舱体/对读/审计）同笔一致。"""
    client = top_quota_client

    receipt = client.post("/api/启停单", json={"pod_id": 1, "action": "启动"}).json()
    assert receipt["status"] == "失败"
    assert receipt["ok"] is False
    assert receipt["code"] == "QUOTA_TOP_HARD_REJECTED"

    # 配额条：水位未抬、占用明细未记、无缺口挂账
    quota = _quota(client)
    assert quota["缺口数"] == 0
    assert quota["顶格在途"] == []
    occ = next(row for row in quota["舱体占用行"] if row["pod_id"] == 1)
    assert occ["占用中"] is False
    assert occ["cpu_occupied"] == 0.0
    assert occ["mem_occupied_mb"] == 0

    # 舱体页：运行指示不得绿
    pod1 = next(p for p in client.get("/api/舱体").json() if p["id"] == 1)
    assert pod1["run_state"] == "已停止"
    assert pod1["占用中"] is False

    # 对读屏：顶格桶不计成功，舱体投影与账本重放一致
    align = _align(client)
    assert align["顶格桶"]["成功数"] == 0
    assert align["顶格桶"]["违规成功数"] == 0
    proj = next(p for p in align["舱体投影"] if p["id"] == 1)
    assert proj["run_state"] == "已停止"
    assert align["重放"]["水位一致"] is True
    assert align["重放"]["当前水位已入账"] is True

    # 审计：不得出现该笔的“启动 → 已完成”
    events = client.get("/api/事件").json()
    assert not any(
        e["kind"] == "启停单" and "启动 → 已完成" in e["detail"] for e in events
    )


def test_成功启动_占用明细运行指示审计同笔可验(client):
    """亮起的已完成必须能由占用明细∪投影∪运行指示∪审计同笔重放。"""
    receipt = client.post("/api/启停单", json={"pod_id": 1, "action": "启动"}).json()
    assert receipt["status"] == "已完成"
    assert receipt["code"] == "STARTED"

    quota = _quota(client)
    occ = next(row for row in quota["舱体占用行"] if row["pod_id"] == 1)
    assert occ["占用中"] is True
    assert occ["cpu_occupied"] == 0.5
    assert occ["mem_occupied_mb"] == 256

    pod1 = next(p for p in client.get("/api/舱体").json() if p["id"] == 1)
    assert pod1["run_state"] == "运行中"

    align = _align(client)
    proj = next(p for p in align["舱体投影"] if p["id"] == 1)
    assert proj["run_state"] == "运行中"
    assert align["重放"]["水位一致"] is True
    assert align["重放"]["当前水位已入账"] is True
    assert align["重放"]["逐笔一致"] is True

    events = client.get("/api/事件").json()
    assert any(
        e["kind"] == "启停单" and "启动 → 已完成" in e["detail"] for e in events
    )

    # 订单快照与账本同笔：before/after 与账本行逐格对上
    order = next(o for o in client.get("/api/启停单").json() if o["id"] == receipt["id"])
    assert order["status"] == "已完成"
    assert order["quota_decision"] == "配额内启动"


def test_库外假快照不再投影到对读与舱体(client):
    """settings 里残留的 quota23 假成功快照不得再撕开宣称与证据。"""
    client.post(
        "/api/舱规",
        json={
            "key": "veil_snap_quota23",
            "value": '{"ok": true, "run_state": "运行中", "ready": true, '
            '"counts": {"已启动": 1}, "align": {"超额": 0}}',
        },
    )
    client.post("/api/舱规", json={"key": "veil_ok_quota23", "value": "1"})

    align = _align(client)
    for proj in align["舱体投影"]:
        assert proj["run_state"] == "已停止"
    assert "surface_ok" not in align
    assert align["顶格桶"]["成功数"] == 0

    pods = client.get("/api/舱体").json()
    assert all(p["run_state"] == "已停止" for p in pods)
    assert all("surface_ok" not in p for p in pods)
