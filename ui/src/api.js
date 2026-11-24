async function request(path, options) {
  const res = await fetch(path, options);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
  return body;
}

export const api = {
  health: () => request("/api/health"),
  chat: (messages) =>
    request("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages }),
    }),
  lineage: () => request("/api/lineage"),
  columnLineage: (dataset, column) =>
    request(`/api/lineage/column?dataset=${encodeURIComponent(dataset)}&column=${encodeURIComponent(column)}`),
  quality: () => request("/api/quality"),
  dictionary: () => request("/api/dictionary"),
  impact: (changes) =>
    request("/api/impact", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ changes }),
    }),
};
