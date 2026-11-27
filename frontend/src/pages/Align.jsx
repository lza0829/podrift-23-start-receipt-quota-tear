import React, { useEffect, useState } from "react";
import { api } from "../api";

export default function Align() {
  const [data, setData] = useState(null);
  const load = () => api.align().then(setData);
  useEffect(() => {
    load();
  }, []);
  if (!data) return null;

  const bucket = data["顶格桶"] || {};
  const replay = data["重放"] || data.replay || {};
  const decisionReplay = replay["决策重放"] || {};
  const items = bucket.items || bucket["明细"] || [];

  return (
    <>
      <div className="hero">
        <h1>对读屏</h1>
        <p>
          启停结果、探针色、镜像拉取态同屏投影。配额口径全站统一为
          <strong className="bad-text"> 顶格硬拒</strong>；顶格桶只收顶格拒启动，不计成功、不挂缺口，历史判定可重放。
        </p>
      </div>

      <div className="panel policy-banner" data-testid="top-bucket">
        <h2>顶格桶 · {bucket["口径"]}</h2>
        <div className="bucket-grid">
          <div className="bucket-stat">
            <span>当前配额</span>
            <strong className={bucket["顶格"] ? "bad-text" : "ok-text"}>{bucket["顶格"] ? "已顶格" : "未顶格"}</strong>
          </div>
          <div className="bucket-stat">
            <span>拒启动</span>
            <strong>{bucket["拒启动数"] ?? 0}</strong>
          </div>
          <div className="bucket-stat">
            <span>计成功</span>
            <strong className="bad-text">{bucket["成功数"] ?? 0}</strong>
          </div>
          <div className="bucket-stat">
            <span>违规成功</span>
            <strong className="bad-text">{bucket["违规成功数"] ?? 0}</strong>
          </div>
          <div className="bucket-stat">
            <span>顶格在途/缺口</span>
            <strong>0</strong>
          </div>
          <div className="bucket-stat">
            <span>决策重放</span>
            <strong className={decisionReplay["全部一致"] === false ? "bad-text" : "ok-text"}>
              {decisionReplay["全部一致"] === false ? "不一致" : `一致 ${decisionReplay["一致数"] ?? 0}/${decisionReplay["条数"] ?? 0}`}
            </strong>
          </div>
        </div>
        <div className="row-actions inline">
          <button className="btn" onClick={load}>刷新对读</button>
          <span className="kv">账本水位：{replay["水位一致"] ? "一致" : "需核对"}；外部改额跳点：{replay.baseline_jumps ?? 0}</span>
        </div>
      </div>

      {items.length > 0 && (
        <div className="panel">
          <h2>顶格桶流水</h2>
          <table data-testid="top-bucket-table">
            <thead>
              <tr><th>启停单</th><th>舱</th><th>状态</th><th>说明</th><th>口径</th><th>时间</th></tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.id} data-testid={`top-bucket-order-${item.id}`}>
                  <td>#{item.id}</td>
                  <td>{item.pod_name} ({item.pod_id})</td>
                  <td><span className="tag bad">{item.status}</span></td>
                  <td>{item.note}</td>
                  <td>{item.quota_decision}</td>
                  <td>{item.created_at}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="panel">
        <table data-testid="align-table">
          <thead>
            <tr><th>舱</th><th>运行</th><th>占用</th><th>配额判定</th><th>探针</th><th>镜像</th><th>最近启停</th></tr>
          </thead>
          <tbody>
            {data["舱体投影"].map((r) => (
              <tr key={r.id}>
                <td>{r.name}</td>
                <td>{r.run_state}</td>
                <td>{r["占用中"] ? `${r["占用CPU"]}c / ${r["占用内存MB"]}MB` : "未占用"}</td>
                <td>
                  {r["顶格拦截"] ? (
                    <span className="tag bad">顶格硬拒</span>
                  ) : r.run_state === "运行中" ? (
                    <span className="tag ok">已在运行</span>
                  ) : r["可启动"] ? (
                    <span className="tag ok">可启动</span>
                  ) : (
                    <span className="tag warn">{r["配额判定"]?.code || "不可启动"}</span>
                  )}
                </td>
                <td>{r.probe_color} · {r.fail_streak}</td>
                <td>{r.pull_state}</td>
                <td>{r.last_order_status || "—"} {r.last_order_note || ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
