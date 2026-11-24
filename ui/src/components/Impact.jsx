import { useEffect, useMemo, useState } from "react";
import { api } from "../api.js";
import ImpactGraph, { Legend } from "./ImpactGraph.jsx";

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
        <Legend>{report && <span className="muted">Run {report.run_id.slice(0, 8)} · click a dataset</span>}</Legend>
        {report && <ImpactGraph report={report} selected={selected} onSelect={setSelected} />}
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
