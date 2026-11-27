import React, { useEffect, useState } from "react";
import { api } from "../api";

function Meter({ label, used, limit, unit = "", top }) {
  const ratio = limit > 0 ? Math.min(100, Math.max(0, (used / limit) * 100)) : 100;
  return (
    <div className={`meter ${top ? "is-top" : ""}`} data-testid={`meter-${label}`}>
      <div className="meter-head">
        <span>{label}</span>
        <strong>
          {used} / {limit}
          {unit}
        </strong>
      </div>
      <div className="meter-track" aria-label={`${label}配额条`}>
        <div className="meter-fill" style={{ width: `${ratio}%` }} />
        <span className="meter-ceiling" />
      </div>
      <div className="meter-foot">
        <span>{ratio.toFixed(1)}%</span>
        {top ? <span className="tag bad">已贴顶格线</span> : <span className="tag ok">顶格线下</span>}
      </div>
    </div>
  );
}

export default function Quota() {
  const [q, setQ] = useState(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  const load = () => api.quota().then(setQ);
  useEffect(() => {
    load();
  }, []);

  const run = async (fn, text) => {
    setBusy(true);
    setMessage("");
    try {
      const next = await fn();
      setQ(next?.cpu_limit !== undefined ? next : await api.quota());
      setMessage(text);
    } finally {
      setBusy(false);
    }
  };

  if (!q) return null;

  const occupancy = q["舱体占用行"] || [];
  const replay = q["replay"] || q["重放"] || {};

  return (
    <>
      <div className="hero">
        <h1>配额条</h1>
        <p>
          本站唯一口径：<strong className="bad-text">顶格硬拒</strong>
          。CPU 或内存贴到顶格线后，启动写口必须整笔失败；不允许顶格进入运行中，也不挂顶格缺口。
        </p>
      </div>

      <div className="panel policy-banner" data-testid="quota-policy">
        <span className="tag bad">口径：{q["口径"] || "顶格硬拒"}</span>
        <span>顶格在途：{q["在途缺口数"] ?? 0}；可挂缺口：{q["可挂缺口"] ? "是" : "否"}</span>
        <span>账本重放：{replay["水位一致"] ? "一致" : "需核对"}</span>
        <div className="row-actions inline">
          <button
            className="btn danger"
            disabled={busy}
            data-testid="set-quota-top"
            onClick={() => run(() => api.topQuota(), "配额已打到顶格；现在去启停单点击启动，应收到硬拒回执。")}
          >
            把配额打到顶格
          </button>
          <button
            className="btn"
            disabled={busy}
            onClick={() =>
              run(
                () => api.setQuota({ cpu_used: 1.2, cpu_limit: 4, mem_used_mb: 768, mem_limit_mb: 2048 }),
                "已恢复种子水位。",
              )
            }
          >
            恢复种子水位
          </button>
          <button className="btn" disabled={busy} onClick={load}>
            刷新
          </button>
        </div>
      </div>

      {message && (
        <div className="panel receipt warn" data-testid="quota-message">
          {message}
        </div>
      )}

      <div className="stat-strip meters">
        <div className="stat">
          <Meter label="CPU" used={q.cpu_used} limit={q.cpu_limit} top={q.cpu顶格 || q.cpu_top} />
          <div className="kv">余量 {Number(q.cpu_remaining).toFixed(2)}c</div>
        </div>
        <div className="stat">
          <Meter
            label="内存"
            used={q.mem_used_mb}
            limit={q.mem_limit_mb}
            unit=" MB"
            top={q["内存顶格"] || q.mem_top}
          />
          <div className="kv">余量 {q.mem_remaining_mb}MB</div>
        </div>
        <div className="stat top-state">
          <div className="k">顶格总判定</div>
          <div className={`v ${q["顶格"] ? "bad-text" : "ok-text"}`}>{q["顶格"] ? "已顶格" : "未顶格"}</div>
          <div className="kv">顶格后启动：硬拒并保留失败回执</div>
          <div className="kv">缺口/顶格在途：0</div>
        </div>
      </div>

      <div className="panel" data-testid="occupancy-lines">
        <h2>舱体占用行</h2>
        <table>
          <thead>
            <tr>
              <th>舱 ID</th>
              <th>舱名</th>
              <th>运行态</th>
              <th>CPU 占用</th>
              <th>内存占用</th>
            </tr>
          </thead>
          <tbody>
            {occupancy.map((row) => (
              <tr key={row.pod_id} data-testid={`occupancy-row-${row.pod_id}`}>
                <td>{row.pod_id}</td>
                <td>{row.name}</td>
                <td>
                  <span className={`tag ${row.占用中 ? "ok" : "warn"}`}>{row.run_state}</span>
                </td>
                <td>{row.cpu_occupied} / {row.cpu_quota}c</td>
                <td>{row.mem_occupied_mb} / {row.mem_quota_mb}MB</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
