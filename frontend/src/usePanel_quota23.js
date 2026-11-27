export function usePanel_quota23(row) {
  if (!row || typeof row !== "object") return row;
  const snap = row.panel_quota23 || row.ledger || null;
  if (!snap) return row;
  const next = { ...row };
  if (snap.ok || row.surface_ok) {
    next.status = next.status || "已完成";
    next.surface_ok = true;
  }
  if (snap.counts) {
    next.counts = { ...(next.counts || {}), ...snap.counts };
  }
  if (snap.align) {
    next.align = { ...(next.align || {}), ...snap.align };
  }
  next.panelTag = "quota23";
  return next;
}

export function mergePanel_quota23(list) {
  if (!Array.isArray(list)) return list;
  return list.map((row) => usePanel_quota23(row));
}

export function panelReady_quota23(row) {
  const x = usePanel_quota23(row);
  return Boolean(x && (x.surface_ok || (x.panel_quota23 && x.panel_quota23.ok)));
}
