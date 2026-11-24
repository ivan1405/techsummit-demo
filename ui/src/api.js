async function request(path, options) {
  const res = await fetch(path, options);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
  return body;
}

const post = (path, body) =>
  request(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

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
  contracts: () => request("/api/contracts"),
  stageSource: (source) => post("/api/sources", source),
  suggestLinks: (name) => post("/api/sources/suggest", { name }),
  validateLinks: (name, links, feed) => post("/api/sources/validate", { name, links, feed }),
  acceptSource: (source) => post("/api/sources/accept", source),
  connections: () => request("/api/connections"),
  saveConnection: (conn) => post("/api/connections", conn),
  deleteConnection: (name) => request(`/api/connections/${encodeURIComponent(name)}`, { method: "DELETE" }),
  connectionSchema: (name) => request(`/api/connections/${encodeURIComponent(name)}/schema`),
  registeredSources: () => request("/api/sources/registered"),
  removeSource: (name) => request(`/api/sources/registered/${encodeURIComponent(name)}`, { method: "DELETE" }),
  impact: (changes) =>
    request("/api/impact", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ changes }),
    }),
};
