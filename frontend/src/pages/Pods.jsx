import React, { useEffect, useState } from "react";
import { api } from "../api";

export default function Pods() {
  const [rows, setRows] = useState([]);
  useEffect(() => { api.pods().then(setRows); }, []);
  return (
    <>
      <div className="hero">
        <h1>舱体</h1>
        <p>主舱登记、旁挂 sidecar、运行态与探针旁读。</p>
      </div>
      <div className="panel">
        <table>
          <thead>
            <tr><th>舱</th><th>镜像</th><th>拉取</th><th>运行</th><th>占用行</th><th>sidecar</th><th>配额</th><th>顶格判定</th><th>探针</th></tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <td>{r.name}</td>
                <td>{r.image_name}:{r.image_tag}</td>
                <td>{r.pull_state}</td>
                <td><span className={`tag ${r.run_state === "运行中" ? "ok" : "warn"}`}>{r.run_state}</span></td>
                <td><span className={`tag ${r["占用中"] ? "ok" : "warn"}`}>{r["占用状态"] || (r.run_state === "运行中" ? "占用中" : "未占用")}</span></td>
                <td>{r.sidecar || "—"}</td>
                <td>{r.cpu_quota}c / {r.mem_quota_mb}MB</td>
                <td>{r["顶格拦截"] ? <span className="tag bad">顶格硬拒</span> : <span className="tag ok">配额内</span>}</td>
                <td><span className={`tag ${r.probe_color === "绿" ? "ok" : "bad"}`}>{r.probe_color} · {r.fail_streak}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
