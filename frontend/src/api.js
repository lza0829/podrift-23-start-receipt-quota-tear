const j = async (url, opt) => {
  const r = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...opt,
  });
  const text = await r.text();
  let body = {};
  try {
    body = text ? JSON.parse(text) : {};
  } catch {
    body = { raw: text };
  }
  if (!r.ok && typeof body === "object" && body !== null) {
    body.http_status = r.status;
  }
  return body;
};

export const api = {
  health: () => j("/api/健康"),
  images: () => j("/api/镜像"),
  advancePull: (id) => j(`/api/镜像/${id}/推进拉取`, { method: "POST" }),
  pods: () => j("/api/舱体"),
  orders: () => j("/api/启停单"),
  createOrder: (body) => j("/api/启停单", { method: "POST", body: JSON.stringify(body) }),
  probes: () => j("/api/探针"),
  setProbe: (podId, body) => j(`/api/探针/${podId}`, { method: "POST", body: JSON.stringify(body) }),
  quota: () => j("/api/配额"),
  setQuota: (body) => j("/api/配额", { method: "POST", body: JSON.stringify(body) }),
  topQuota: () => j("/api/配额/顶格", { method: "POST" }),
  quotaReplay: () => j("/api/配额/重放"),
  policy: () => j("/api/重启策"),
  setPolicy: (body) => j("/api/重启策", { method: "POST", body: JSON.stringify(body) }),
  align: () => j("/api/对读"),
  alignReplay: () => j("/api/对读/重放"),
  settings: () => j("/api/舱规"),
  events: () => j("/api/事件"),
};
