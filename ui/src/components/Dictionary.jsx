import { useEffect, useMemo, useState } from "react";
import { api } from "../api.js";
import { columnQuestion } from "./Chat.jsx";
import AddSource, { RegisteredSources } from "./Sources.jsx";
import { ContractBadge } from "./Contract.jsx";

const LAYERS = ["all", "bronze", "silver", "gold"];

function matches(text, needle) {
  return (text || "").toLowerCase().includes(needle);
}

export default function Dictionary({ onAsk }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [search, setSearch] = useState("");
  const [layer, setLayer] = useState("all");
  const [showMeta, setShowMeta] = useState(false);
  const [piiOnly, setPiiOnly] = useState(false);
  const [adding, setAdding] = useState(false);

  const [version, setVersion] = useState(0);
  const reload = () => setVersion((v) => v + 1);

  const [contracts, setContracts] = useState({});

  useEffect(() => {
    api.dictionary().then(setData).catch((e) => setError(e.message));
    api
      .contracts()
      .then((c) => setContracts(Object.fromEntries(c.datasets.map((d) => [d.dataset, d]))))
      .catch(() => setContracts({}));
  }, [version]);

  const datasets = useMemo(() => {
    if (!data) return [];
    const needle = search.trim().toLowerCase();
    return data.datasets
      .filter((d) => layer === "all" || d.layer === layer)
      .map((d) => {
        const tableHit = needle && (matches(d.dataset, needle) || matches(d.description, needle));
        const columns = d.columns.filter(
          (c) =>
            (showMeta || !c.column_name.startsWith("_")) &&
            (!piiOnly || c.pii) &&
            (!needle || tableHit || [c.column_name, c.description, c.upstream].some((t) => matches(t, needle))),
        );
        return { ...d, columns };
      })
      .filter((d) => d.columns.length);
  }, [data, search, layer, showMeta, piiOnly]);

  if (error) return <p className="error-text">{error}</p>;
  if (!data) return <p className="muted">Loading…</p>;

  const total = datasets.reduce((n, d) => n + d.columns.length, 0);

  return (
    <section className="panel dictionary">
      <h2>Data dictionary</h2>
      <p className="muted">
        Every column in the lake, profiled by pipeline run <code>{data.run_id.slice(0, 8)}</code>. Descriptions live in{" "}
        <code>pipeline/src/pipeline/dictionary.py</code>.
      </p>
      <div className="toolbar">
        <input
          type="search"
          name="search"
          aria-label="Search the data dictionary"
          placeholder="Search tables, columns, descriptions or upstream (e.g. email, revenue)"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <div className="segmented" role="group" aria-label="Layer">
          {LAYERS.map((l) => (
            <button key={l} aria-pressed={layer === l} onClick={() => setLayer(l)}>
              {l}
            </button>
          ))}
        </div>
        <label className="check">
          <input type="checkbox" checked={showMeta} onChange={(e) => setShowMeta(e.target.checked)} />
          Metadata columns
        </label>
        <label className="check">
          <input type="checkbox" checked={piiOnly} onChange={(e) => setPiiOnly(e.target.checked)} />
          Personal data only
        </label>
        <button className="primary" aria-expanded={adding} onClick={() => setAdding(!adding)}>
          + Add data source
        </button>
      </div>
      <RegisteredSources version={version} onChange={reload} />
      {adding && <AddSource onClose={() => setAdding(false)} onImplemented={reload} />}
      <p className="muted" aria-live="polite">
        {datasets.length} tables · {total} columns
      </p>
      {datasets.map((d) => (
        <article key={d.dataset} className="dict-table">
          <header>
            <span className={`chip ${d.layer}`}>{d.layer}</span>
            <h3>{d.dataset}</h3>
            {d.rows != null && <span className="muted">{d.rows} rows</span>}
            <ContractBadge contract={contracts[d.dataset]} />
          </header>
          {d.description && <p className="muted">{d.description}</p>}
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Column</th>
                  <th>Type</th>
                  <th>Description</th>
                  <th>Nulls</th>
                  <th>Distinct</th>
                  <th>Samples</th>
                  <th>Upstream</th>
                </tr>
              </thead>
              <tbody>
                {d.columns.map((c) => (
                  <tr key={c.column_name}>
                    <td className="col-name">
                      <code>{c.column_name}</code>
                      {c.pii && (
                        <span className="pii-tag" title={`Personal data (${c.pii}), from ${c.pii_origin}. Masked for readers.`}>
                          PII · {c.pii}
                        </span>
                      )}
                      <button className="ask" title="Ask the assistant about this column"
                              aria-label={`Ask about ${d.dataset}.${c.column_name}`}
                              onClick={() => onAsk(columnQuestion(d.dataset, c.column_name))}>?</button>
                    </td>
                    <td className="muted">{c.data_type}</td>
                    <td>{c.description || <span className="muted">Undocumented</span>}</td>
                    <td className="num">{c.null_pct}%</td>
                    <td className="num">{c.distinct_count}</td>
                    <td className="muted samples">{c.sample_values}</td>
                    <td>
                      {c.upstream && <code>{c.upstream}</code>}
                      {c.transformation && <div className="muted">{c.transformation}</div>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </article>
      ))}
    </section>
  );
}
