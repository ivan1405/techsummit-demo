import { useEffect, useState } from "react";
import { api } from "../api.js";

const LABELS = ["data-source", ""];

export default function ConfluencePicker({ value, schema, onChange, optional = false }) {
  const [status, setStatus] = useState(null);
  const [query, setQuery] = useState("");
  const [label, setLabel] = useState("data-source");
  const [results, setResults] = useState(null);
  const [page, setPage] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  async function search() {
    setBusy(true);
    setError(null);
    try {
      const found = (await api.confluenceSearch(query, label)).results;
      setResults(found);
      if (!optional && !value && found.length === 1) pick(found[0]);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    api.confluenceStatus().then(setStatus).catch(() => setStatus(null));
    search();
  }, []);

  useEffect(() => {
    setPage(null);
    if (!value) return;
    api.confluencePage(value).then(setPage).catch((e) => setError(e.message));
  }, [value]);

  const pick = (p) => onChange({ doc_page: p.id, doc_schema: 0, doc_title: p.title });
  const columns = page?.schemas[schema] || [];

  return (
    <div className="confluence">
      <div className="fields conn-form" role="search" aria-label="Search Confluence">
        <label className="wide">
          Search Confluence{status && <span className="muted"> · {status.mode === "mock" ? "mock" : "Cloud"} space {status.space}</span>}
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="e.g. loyalty, support tickets"
            onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); search(); } }} />
        </label>
        <label>
          Label
          <select value={label} onChange={(e) => setLabel(e.target.value)}>
            {LABELS.map((l) => <option key={l} value={l}>{l || "any"}</option>)}
          </select>
        </label>
        <button type="button" onClick={search} disabled={busy}>{busy ? "Searching…" : "Search"}</button>
        {optional && value && <button type="button" className="link-button" onClick={() => onChange({ doc_page: "", doc_schema: 0, doc_title: "" })}>Detach</button>}
      </div>
      {error && <p className="error-text" role="alert">{error}</p>}
      {results && (
        <div className="schema-browser" role="list" aria-label="Confluence pages">
          {results.length === 0 && <p className="muted">No pages found.</p>}
          {results.map((p) => (
            <button type="button" role="listitem" key={p.id} className={p.id === value ? "active" : ""} aria-pressed={p.id === value} onClick={() => pick(p)}>
              <strong>{p.id === value && "✓ "}{p.title}</strong>
              <span className="cols">{p.space} · {p.labels.join(", ") || "no labels"}</span>
            </button>
          ))}
        </div>
      )}
      {page && (
        <div className="doc-page">
          <p className="muted">
            {page.summary} {page.url && <a href={page.url} target="_blank" rel="noreferrer">Open in Confluence</a>}
          </p>
          {page.schemas.length === 0 && <p className="error-text" role="alert">No schema table (Column + Type/Description) on this page.</p>}
          {page.schemas.length > 1 && (
            <label>
              Schema table{" "}
              <select value={schema} onChange={(e) => onChange({ doc_page: value, doc_schema: Number(e.target.value), doc_title: page.title })}>
                {page.schemas.map((s, i) => <option key={i} value={i}>Table {i + 1} ({s.length} columns)</option>)}
              </select>
            </label>
          )}
          {columns.length > 0 && (
            <div className="table-wrap">
              <table>
                <thead><tr><th>Column</th><th>Type</th><th>Description</th><th>Links to</th></tr></thead>
                <tbody>
                  {columns.map((c) => (
                    <tr key={c.name}>
                      <td><code>{c.name}</code></td>
                      <td className="muted">{c.type}</td>
                      <td>{c.description}</td>
                      <td className="muted">{c.links_to}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
