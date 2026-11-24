import { useEffect, useMemo, useState } from "react";
import { api } from "../api.js";
import { layout, NODE_H, NODE_W } from "./Lineage.jsx";

const TYPES = {
  add_column: { label: "Add column", fields: ["dataset", "column", "sources", "transformation"] },
  rename_column: { label: "Rename column", fields: ["dataset", "column", "new_name"] },
  remove_column: { label: "Drop column", fields: ["dataset", "column"] },
  change_logic: { label: "Change logic", fields: ["dataset", "column", "transformation"] },
  add_dataset: { label: "Add dataset / source", fields: ["dataset", "inputs"] },
  remove_dataset: { label: "Drop dataset", fields: ["dataset"] },
};
const PLACEHOLDER = {
  dataset: "gold.returns",
  column: "column name",
  new_name: "new column name",
  sources: "gold.sales.amount, gold.sales.order_id",
  inputs: "silver.orders, source.returns.csv",
  transformation: "SUM(amount) WHERE status = 'returned'",
};
const LISTS = ["sources", "inputs"];
const EXAMPLES = {
  "Returns reporting": [
    { type: "add_dataset", dataset: "bronze.returns", inputs: ["source.returns.csv"] },
    { type: "add_dataset", dataset: "gold.returns", inputs: ["bronze.returns", "gold.sales"] },
    { type: "add_column", dataset: "gold.returns", column: "returned_amount", sources: ["gold.sales.amount"],
      transformation: "SUM(amount) of returned order lines" },
  ],
  "GDPR: drop customer email": [{ type: "remove_column", dataset: "silver.customers", column: "email" }],
  "Rename unit_price": [{ type: "rename_column", dataset: "silver.orders", column: "unit_price", new_name: "price_eur" }],
};
const STATUS_ORDER = ["broken", "removed", "impacted", "renamed", "modified", "added"];

function describe(c) {
  const target = c.column ? `${c.dataset}.${c.column}` : c.dataset;
  const extra = c.new_name ? ` → ${c.new_name}` : c.sources?.length ? ` ← ${c.sources.join(", ")}` : c.inputs?.length ? ` ← ${c.inputs.join(", ")}` : "";
  return `${TYPES[c.type].label}: ${target}${extra}`;
}

function download(changes, report) {
  const blob = new Blob([JSON.stringify({ proposal: changes, impact: report }, null, 2)], { type: "application/json" });
  const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: "lineage-proposal.json" });
  a.click();
  URL.revokeObjectURL(a.href);
}

export default function Impact() {
  const [dictionary, setDictionary] = useState(null);
  const [changes, setChanges] = useState([]);
  const [draft, setDraft] = useState({ type: "add_column" });
  const [report, setReport] = useState(null);
  const [error, setError] = useState(null);
  const [selected, setSelected] = useState(null);

  useEffect(() => {
    api.dictionary().then(setDictionary).catch((e) => setError(e.message));
  }, []);

  useEffect(() => {
    let live = true;
    api
      .impact(changes)
      .then((r) => live && (setReport(r), setError(null)))
      .catch((e) => live && setError(e.message));
    return () => {
      live = false;
    };
  }, [changes]);

  const datasetIds = useMemo(() => dictionary?.datasets.map((d) => d.dataset) ?? [], [dictionary]);
  const columnRefs = useMemo(
    () => dictionary?.datasets.flatMap((d) => d.columns.filter((c) => !c.column_name.startsWith("_")).map((c) => `${d.dataset}.${c.column_name}`)) ?? [],
    [dictionary],
  );
  const draftColumns = useMemo(
    () => dictionary?.datasets.find((d) => d.dataset === draft.dataset)?.columns.map((c) => c.column_name) ?? [],
    [dictionary, draft.dataset],
  );
  const view = useMemo(() => (report ? layout(report.nodes, report.edges) : null), [report]);
  const changed = useMemo(() => {
    const by = {};
    report?.columns.forEach((c) => (by[c.dataset] ||= []).push(c));
    return by;
  }, [report]);

  const fields = TYPES[draft.type].fields;
  const set = (k) => (e) => setDraft({ ...draft, [k]: e.target.value });

  function addChange(e) {
    e.preventDefault();
    const change = { type: draft.type };
    fields.forEach((f) => {
      const v = (draft[f] || "").trim();
      if (v) change[f] = LISTS.includes(f) ? v.split(",").map((s) => s.trim()).filter(Boolean) : v;
    });
    setChanges([...changes, change]);
    setDraft({ type: draft.type, dataset: draft.dataset });
  }

  const node = report?.nodes.find((n) => n.id === selected);
  const visible = report?.columns.filter((c) => !selected || c.dataset === selected) ?? [];

  return (
    <section className="impact">
      <aside className="designer">
        <h2>Lineage impact</h2>
        <p className="muted">
          Describe a new requirement as changes. Nothing runs: the preview applies them to the latest run&apos;s lineage.
        </p>
        <form onSubmit={addChange}>
          <label>
            Change
            <select name="type" value={draft.type} onChange={set("type")}>
              {Object.entries(TYPES).map(([id, t]) => (
                <option key={id} value={id}>{t.label}</option>
              ))}
            </select>
          </label>
          {fields.map((f) => (
            <label key={f}>
              {f.replace("_", " ")}
              <input
                name={f}
                list={f === "dataset" ? "impact-datasets" : f === "column" ? "impact-columns" : f === "sources" ? "impact-refs" : undefined}
                value={draft[f] || ""}
                onChange={set(f)}
                placeholder={PLACEHOLDER[f]}
                required={["dataset", "column", "new_name"].includes(f) || (f === "transformation" && draft.type === "change_logic")}
              />
            </label>
          ))}
          <datalist id="impact-datasets">{datasetIds.map((d) => <option key={d} value={d} />)}</datalist>
          <datalist id="impact-columns">{draftColumns.map((c) => <option key={c} value={c} />)}</datalist>
          <datalist id="impact-refs">{columnRefs.map((c) => <option key={c} value={c} />)}</datalist>
          <button type="submit" className="primary">Add to proposal</button>
        </form>
        <div className="examples">
          <span className="muted">Examples:</span>
          {Object.entries(EXAMPLES).map(([name, ex]) => (
            <button key={name} onClick={() => setChanges(ex)}>{name}</button>
          ))}
        </div>
        <h4>Proposal ({changes.length})</h4>
        {changes.length === 0 && <p className="muted">No changes yet: the graph shows today&apos;s lineage.</p>}
        <ol className="proposal">
          {changes.map((c, i) => (
            <li key={i}>
              <span>{describe(c)}</span>
              <button aria-label={`Remove change ${i + 1}`} onClick={() => setChanges(changes.filter((_, j) => j !== i))}>×</button>
            </li>
          ))}
        </ol>
        <div className="actions">
          <button onClick={() => download(changes, report)} disabled={!changes.length || !report}>Export JSON</button>
          <button onClick={() => setChanges([])} disabled={!changes.length}>Clear</button>
        </div>
        {error && <p className="error-text" role="alert">{error}</p>}
      </aside>
      <div className="graph">
        <div className="legend">
          {["added", "removed", "renamed", "modified", "impacted", "broken"].map((s) => (
            <span key={s} className={`chip status-${s}`}>{s}</span>
          ))}
          {report && <span className="muted">Run {report.run_id.slice(0, 8)} · click a dataset</span>}
        </div>
        {view && (
          <svg className="fit" viewBox={`0 0 ${view.width} ${view.height}`} width={view.width} role="img" aria-label="Proposed lineage graph">
            <defs>
              <marker id="impact-arrow" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="6" markerHeight="6" orient="auto">
                <path d="M0,0 L10,5 L0,10 z" className="arrow" />
              </marker>
            </defs>
            {report.edges.map((e, i) => {
              const a = view.positions[e.source];
              const b = view.positions[e.target];
              const x1 = a.x + NODE_W, y1 = a.y + NODE_H / 2, x2 = b.x, y2 = b.y + NODE_H / 2;
              return (
                <path key={i} d={`M${x1},${y1} C${x1 + 40},${y1} ${x2 - 40},${y2} ${x2},${y2}`}
                  className={`edge status-${e.status}`} markerEnd="url(#impact-arrow)" />
              );
            })}
            {report.nodes.map((n) => {
              const p = view.positions[n.id];
              const count = changed[n.id]?.length;
              const pick = () => setSelected(selected === n.id ? null : n.id);
              return (
                <g key={n.id} transform={`translate(${p.x},${p.y})`}
                  className={`node ${n.layer} status-${n.status} ${selected === n.id ? "selected" : ""}`}
                  role="button" tabIndex={0} aria-pressed={selected === n.id}
                  aria-label={`${n.id}: ${n.status}${count ? `, ${count} column changes` : ""}`}
                  onClick={pick}
                  onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), pick())}>
                  <rect width={NODE_W} height={NODE_H} rx="8" />
                  <rect className="bar" x="0" y="8" width="3" height={NODE_H - 16} rx="1.5" />
                  <text x="12" y="18" className="layer">{n.layer}</text>
                  <text x="12" y="35" className="name">{n.name}</text>
                  {n.status !== "unchanged" && <text x={NODE_W - 10} y="18" textAnchor="end" className="rows">{n.status}</text>}
                  {count > 0 && <text x={NODE_W - 10} y="35" textAnchor="end" className="rows">{count} col{count > 1 ? "s" : ""}</text>}
                </g>
              );
            })}
          </svg>
        )}
      </div>
      <aside className="details">
        {report && (
          <>
            <h3>{node ? node.id : "What would change"}</h3>
            <div className="summary">
              {STATUS_ORDER.filter((s) => report.summary[s]).map((s) => (
                <span key={s} className={`chip status-${s}`}>{report.summary[s]} {s}</span>
              ))}
            </div>
            {report.warnings.map((w) => (
              <p key={w} className="warning" role="note">{w}</p>
            ))}
            {visible.length === 0 && <p className="muted">{changes.length ? "No column changes here." : "Add a change to see its impact."}</p>}
            <ul className="impact-list">
              {visible.map((c) => (
                <li key={`${c.dataset}.${c.column}`}>
                  <span className={`status-pill status-${c.status}`}>{c.status}</span>
                  <code>{c.dataset}.{c.column}</code>
                  {c.reason && <div className="muted">{c.reason}</div>}
                </li>
              ))}
            </ul>
          </>
        )}
      </aside>
    </section>
  );
}
