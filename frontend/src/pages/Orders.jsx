import React, { useEffect, useState } from "react";
import { api } from "../api";

export default function Orders() {
  const [pods, setPods] = useState([]);
  const [orders, setOrders] = useState([]);
  const [receipt, setReceipt] = useState(null);
  const [busyPod, setBusyPod] = useState(null);

  const load = () => Promise.all([api.pods(), api.orders()]).then(([p, o]) => {
    setPods(Array.isArray(p) ? p : []);
    setOrders(Array.isArray(o) ? o : []);
  });

  useEffect(() => {
    load();
  }, []);

  const act = async (podId, action) => {
    setBusyPod(`${podId}-${action}`);
    try {
      const body = await api.createOrder({ pod_id: podId, action });
      setReceipt(body);
      await load();
    } finally {
      setBusyPod(null);
    }
  };

  const receiptFailed = receipt && receipt.status !== "已完成";

  return (
    <>
      <div className="hero">
        <h1>启停单</h1>
        <p>
          本站执行<strong className="bad-text">顶格硬拒</strong>
          ：镜像未就绪、配额不足或 CPU/内存已顶格时，启停写口只给失败回执；资源写入与成败在同一事务，失败退回点击前水位。
        </p>
      </div>

      {receipt && (
        <div
          className={`panel receipt ${receiptFailed ? "bad" : "ok"}`}
          data-testid="order-receipt"
          data-status={receipt.status}
          data-code={receipt.code}
        >
          <div className="receipt-title">
            <span className={`tag ${receiptFailed ? "bad" : "ok"}`}>{receipt.status}</span>
            <strong>回执 #{receipt.id ?? receipt.order_id}</strong>
            <span>{receipt.code}</span>
            <span className="tag bad">{receipt["口径"] || "顶格硬拒"}</span>
          </div>
          <div>{receipt.note}</div>
          {receiptFailed && receipt["顶格"] && (
            <div className="receipt-detail">顶格桶不得计成功；舱体不得进入运行中，也不得留下顶格缺口。</div>
          )}
        </div>
      )}

      <div className="panel">
        <div className="row-actions">
          {pods.map((p) => (
            <span key={p.id} className="pod-action" data-testid={`pod-action-${p.id}`}>
              <strong>{p.name}</strong>
              <span className={`tag ${p.run_state === "运行中" ? "ok" : "warn"}`}>{p.run_state}</span>
              <button
                className="btn danger"
                disabled={busyPod === `${p.id}-启动`}
                data-testid={`start-${p.id}`}
                onClick={() => act(p.id, "启动")}
              >
                启动
              </button>
              <button
                className="btn"
                disabled={busyPod === `${p.id}-停止`}
                data-testid={`stop-${p.id}`}
                onClick={() => act(p.id, "停止")}
              >
                停止
              </button>
            </span>
          ))}
        </div>
      </div>

      <div className="panel">
        <table data-testid="orders-table">
          <thead>
            <tr>
              <th>ID</th>
              <th>舱</th>
              <th>动作</th>
              <th>状态</th>
              <th>说明</th>
              <th>配额口径</th>
              <th>时间</th>
            </tr>
          </thead>
          <tbody>
            {orders.map((o) => (
              <tr key={o.id} data-testid={`order-row-${o.id}`} data-top={o.quota_top_hit ? "1" : "0"}>
                <td>{o.id}</td>
                <td>{o.pod_id}</td>
                <td>{o.action}</td>
                <td>
                  <span className={`tag ${o.status === "已完成" ? "ok" : o.status === "失败" ? "bad" : "warn"}`}>
                    {o.status}
                  </span>
                </td>
                <td>{o.note}</td>
                <td>
                  {o.quota_decision || "—"}
                  {o.quota_top_hit ? <span className="tag bad">顶格</span> : null}
                </td>
                <td>{o.created_at}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
