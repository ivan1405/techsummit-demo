import { useEffect, useMemo, useState } from "react";
import { api } from "../api.js";
import ImpactGraph, { Legend } from "./ImpactGraph.jsx";
import ConnectionPicker from "./Connections.jsx";
import ConfluencePicker from "./Confluence.jsx";

const KINDS = { file: "File upload", json: "JSON object", connection: "Saved connection", database: "Database URL", confluence: "Confluence page" };
const RELATIONS = { join_key: "Join key", maps_to: "Maps to", new: "New attribute" };
const CHECK_CLASS = { ok: "status-added", warn: "status-impacted", fail: "status-broken" };
const DB_HINT = "postgresql://reader:reader@source-db:5432/erp";
const JSON_HINT = '{"members": [{"customer_id": "C0001", "tier": "gold", "points": 120}]}';

function snake(text) {
  return text.toLowerCase().replace(/\.[^.]+$/, "").replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").replace(/^(\d)/, "s_$1");
}

function download(payload, filename = `${payload.source.name}-onboarding.json`, type = "application/json") {
  const blob = new Blob([typeof payload === "string" ? payload : JSON.stringify(payload, null, 2)], { type });
  const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: filename });
  a.click();
  URL.revokeObjectURL(a.href);
}

export default function AddSource({ onClose, onImplemented }) {
  const [form, setForm] = useState({ kind: "file", name: "", filename: "", content: "", url: "", table: "", query: "", connection: "", doc_page: "", doc_schema: 0, doc_title: "" });
  const [source, setSource] = useState(null);
  const [links, setLinks] = useState(null);
  const [meta, setMeta] = useState(null);
  const [feed, setFeed] = useState("");
  const [result, setResult] = useState(null);
  const [selected, setSelected] = useState(null);
  const [implemented, setImplemented] = useState(null);
  const [plan, setPlan] = useState(null);
  const [approved, setApproved] = useState(false);
  const [published, setPublished] = useState(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState(null);

  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });
  const goldDatasets = useMemo(
    () => [...new Set(meta?.targets.map((t) => t.ref.split(".").slice(0, 2).join(".")).filter((d) => d.startsWith("gold.")))],
    [meta],
  );

  async function run(label, fn) {
    setBusy(label);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy("");
    }
  }

  async function readFile(e) {
    const file = e.target.files[0];
    if (!file) return;
    const content = await file.text();
    setForm({ ...form, filename: file.name, content, name: form.name || snake(file.name) });
  }

  const profile = (e) => {
    e.preventDefault();
    run("Profiling…", async () => {
      const staged = await api.stageSource(form);
      setSource(staged);
      setLinks(null);
      clearResult();
      setFeed(`gold.${staged.name}`);
    });
  };

  const suggest = () =>
    run("Asking AI for linkages…", async () => {
      const res = await api.suggestLinks(source.name);
      setLinks(res.links);
      setMeta(res);
      clearResult();
    });

  const payloadLinks = () =>
    links.map(({ source_column, relation, target, description }) => ({ source_column, relation, target: relation === "new" ? null : target, ...(description ? { description } : {}) }));

  function clearResult() {
    setResult(null);
    setPlan(null);
    setApproved(false);
    setPublished(null);
    setImplemented(null);
  }

  const validate = () =>
    run("Validating…", async () => {
      clearResult();
      setResult(await api.validateLinks(source.name, payloadLinks(), feed));
    });

  const makePlan = () =>
    run("Building the implementation plan…", async () => {
      setApproved(false);
      setPublished(null);
      setPlan(await api.plan(source.name, payloadLinks(), result.feed));
    });

  const publish = () =>
    run("Publishing the plan to Confluence…", async () => {
      setPublished((await api.publishPlan(source.name, payloadLinks(), result.feed)).page);
    });

  const accept = () =>
    run("Implementing: landing the source and re-running the medallion pipeline…", async () => {
      const { doc_title, ...rest } = form;
      const res = await api.acceptSource({ ...rest, name: source.name, links: payloadLinks(), feed: result.feed, plan_id: plan.id });
      setImplemented(res);
      onImplemented?.();
    });

  const edit = (i, patch) => {
    setLinks(links.map((l, j) => (j === i ? { ...l, ...patch, origin: "edited" } : l)));
    clearResult();
  };

  const hasDocs = source && Object.keys(source.docs || {}).length > 0;

  return (
    <section className="add-source" aria-label="Add data source">
      <header className="wizard-head">
        <h3>Add data source</h3>
        <span className="muted">Profile it, let AI propose linkages, fix them, validate, approve the plan, then implement.</span>
        <button className="link-button" onClick={onClose}>Close</button>
      </header>

      <form className="wizard-step" onSubmit={profile}>
        <h4>1. Source</h4>
        <div className="segmented" role="group" aria-label="Source type">
          {Object.entries(KINDS).map(([k, label]) => (
            <button type="button" key={k} aria-pressed={form.kind === k} onClick={() => setForm({ ...form, kind: k })}>{label}</button>
          ))}
        </div>
        <div className="fields">
          <label>
            Dataset name
            <input name="name" value={form.name} onChange={set("name")} placeholder="e.g. loyalty" required pattern="[a-z][a-z0-9_]*" />
          </label>
          {form.kind === "file" && (
            <label>
              CSV, JSON or JSONL file
              <input type="file" name="file" accept=".csv,.json,.jsonl" onChange={readFile} required={!form.content} />
            </label>
          )}
          {form.kind === "connection" && form.table && (
            <label>
              Table
              <input name="table" value={form.table} onChange={set("table")} />
            </label>
          )}
          {form.kind === "database" && (
            <>
              <label className="wide">
                Connection string (read-only)
                <input name="url" value={form.url} onChange={set("url")} placeholder={DB_HINT} required />
              </label>
              <label>
                Table
                <input name="table" value={form.table} onChange={set("table")} placeholder="support_tickets" />
              </label>
            </>
          )}
        </div>
        {form.kind === "connection" && (
          <ConnectionPicker value={form.connection} table={form.table}
            onChange={(patch) => setForm({ ...form, ...patch, name: form.name || (patch.table ? patch.table.split(".").pop().toLowerCase().replace(/[^a-z0-9_]/g, "_") : "") })} />
        )}
        {form.kind === "json" && (
          <label className="block">
            JSON object or array
            <textarea name="content" rows={6} value={form.content} onChange={set("content")} placeholder={JSON_HINT} required />
          </label>
        )}
        {(form.kind === "database" || form.kind === "connection") && (
          <label className="block">
            …or a SELECT query
            <textarea name="query" rows={2} value={form.query} onChange={set("query")} placeholder="SELECT * FROM support_tickets WHERE opened_on > '2026-07-01'" />
          </label>
        )}
        <button type="submit" className="primary" disabled={!!busy}>Profile source</button>
        {form.kind === "confluence" && (
          <>
            <p className="muted">Documented-only source: links are proposed and checked from the page's schema table, before any data exists.</p>
            <ConfluencePicker value={form.doc_page} schema={form.doc_schema}
              onChange={(patch) => setForm({ ...form, ...patch, name: form.name || snake(patch.doc_title.replace(/\(.*\)/, "")).slice(0, 40) })} />
          </>
        )}
        {form.kind !== "confluence" && (
          <details className="doc-attach" open={!!form.doc_page}>
            <summary>Attach Confluence documentation {form.doc_title ? `· ${form.doc_title}` : "(optional: descriptions and documented links)"}</summary>
            <ConfluencePicker optional value={form.doc_page} schema={form.doc_schema} onChange={(patch) => setForm({ ...form, ...patch })} />
          </details>
        )}
        {form.kind === "file" && form.filename && <span className="muted"> {form.filename}</span>}
      </form>

      {error && <p className="error-text" role="alert">{error}</p>}
      {busy && <p className="muted" aria-live="polite">{busy}</p>}

      {source && (
        <div className="wizard-step">
          <h4>2. Profile: <code>{source.dataset}</code> · {source.schema_only ? "documented schema, no data yet" : `${source.rows} rows`}</h4>
          {source.doc && <p className="muted">Documented in {source.doc.url ? <a href={source.doc.url} target="_blank" rel="noreferrer">{source.doc.title}</a> : source.doc.title}.</p>}
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th>Column</th><th>Type</th>{hasDocs && <th>Description</th>}<th>Nulls</th><th>Distinct</th><th>Samples</th></tr>
              </thead>
              <tbody>
                {source.columns.map((c) => (
                  <tr key={c.column_name}>
                    <td><code>{c.column_name}</code></td>
                    <td className="muted">{c.data_type}</td>
                    {hasDocs && <td>{source.docs[c.column_name]?.description || <span className="muted">undocumented</span>}</td>}
                    <td className="num">{c.null_pct}%</td>
                    <td className="num">{c.distinct_count}</td>
                    <td className="muted samples">{c.sample_values}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <button className="primary" onClick={suggest} disabled={!!busy}>Suggest linkages with AI</button>
        </div>
      )}

      {links && (
        <div className="wizard-step">
          <h4>
            3. Linkages{" "}
            <span className="badge-inline">{meta.mode === "llm" ? "AI reviewed" : "Heuristic (demo mode, no LLM)"}</span>
          </h4>
          <p className="muted">Correct anything wrong: change the relation or point it at another column.</p>
          {meta.docs?.documented > 0 && (
            <p className="muted" role="note">
              {meta.docs.documented} column(s) documented in {meta.docs.title || "Confluence"}
              {meta.docs.undocumented.length > 0 && `; undocumented: ${meta.docs.undocumented.join(", ")}`}
              {meta.docs.missing_in_data.length > 0 && `; documented but not in the data: ${meta.docs.missing_in_data.join(", ")}`}.
            </p>
          )}
          <datalist id="link-targets">{meta.targets.map((t) => <option key={t.ref} value={t.ref}>{t.description}</option>)}</datalist>
          <datalist id="feed-datasets">{goldDatasets.map((d) => <option key={d} value={d} />)}</datalist>
          <div className="table-wrap">
            <table className="links">
              <thead>
                <tr><th>Source column</th><th>Relation</th><th>Existing column</th><th>Confidence</th><th>Why</th></tr>
              </thead>
              <tbody>
                {links.map((l, i) => (
                  <tr key={l.source_column}>
                    <td><code>{l.source_column}</code>{l.description && <span className="doc-hint">{l.description}</span>}</td>
                    <td>
                      <select aria-label={`Relation for ${l.source_column}`} value={l.relation} onChange={(e) => edit(i, { relation: e.target.value })}>
                        {Object.entries(RELATIONS).map(([k, label]) => <option key={k} value={k}>{label}</option>)}
                      </select>
                    </td>
                    <td>
                      <input aria-label={`Target for ${l.source_column}`} list="link-targets" value={l.relation === "new" ? "" : l.target || ""}
                        disabled={l.relation === "new"} placeholder={l.relation === "new" ? "—" : "layer.dataset.column"}
                        onChange={(e) => edit(i, { target: e.target.value })} />
                    </td>
                    <td className="num">{l.origin === "edited" ? "—" : `${Math.round(l.confidence * 100)}%`}</td>
                    <td>
                      <span className={`origin ${l.origin}`}>{l.origin === "ai" ? "AI" : l.origin}</span> <span className="muted">{l.origin === "edited" ? "Changed by you; validate to check it against the data." : l.reason}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="fields">
            <label>
              Feeds dataset
              <input name="feed" list="feed-datasets" value={feed} onChange={(e) => { setFeed(e.target.value); clearResult(); }} />
            </label>
          </div>
          <button className="primary" onClick={validate} disabled={!!busy}>Validate</button>
        </div>
      )}

      {result && (
        <div className="wizard-step">
          <h4>
            4. Validation <span className={`status-pill ${CHECK_CLASS[result.status]}`}>{result.status}</span>
          </h4>
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th>Column</th><th>Relation</th><th>Existing column</th><th>Check</th><th>Value match</th><th>Detail</th></tr>
              </thead>
              <tbody>
                {result.checks.map((c) => (
                  <tr key={`${c.relation}-${c.source_column}`}>
                    <td><code>{c.source_column}</code></td>
                    <td className="muted">{RELATIONS[c.relation] || c.relation}</td>
                    <td>{c.target ? <code>{c.target}</code> : <span className="muted">—</span>}</td>
                    <td><span className={`status-pill ${CHECK_CLASS[c.status]}`}>{c.status}</span></td>
                    <td className="num">{c.overlap == null ? "—" : `${Math.round(c.overlap * 100)}%`}</td>
                    <td>{c.message}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {result.impact.warnings.map((w) => <p key={w} className="warning" role="note">{w}</p>)}
          <div className="graph source-graph">
            <Legend><span className="muted">Proposed lineage for {source.dataset} → {result.feed}</span></Legend>
            <ImpactGraph report={result.impact} selected={selected} onSelect={setSelected} />
          </div>
          <div className="actions">
            <button onClick={() => download({ source: { ...source, sample: undefined }, links, validation: result })}>Export JSON</button>
          </div>
        </div>
      )}

      {result && (
        <div className="wizard-step">
          <h4>5. Implementation plan {plan && <span className={`status-pill ${plan.implementable ? CHECK_CLASS[plan.status] : "status-broken"}`}>{plan.implementable ? plan.status : "blocked"}</span>}</h4>
          {!plan && (
            <>
              <p className="muted">Review exactly what will be built (layers, joins, quality rules, contract and impact) before anything is implemented.</p>
              <button className="primary" onClick={makePlan} disabled={!!busy}>Generate plan</button>
            </>
          )}
          {plan && <PlanView plan={plan} />}
          {plan && (
            <div className="actions">
              <button onClick={() => download(plan.markdown, `${source.name}-plan-${plan.id}.md`, "text/markdown")}>Download Markdown</button>
              <button onClick={() => download({ ...plan, markdown: undefined }, `${source.name}-plan-${plan.id}.json`)}>Download JSON</button>
              <button onClick={publish} disabled={!!busy || !!published}>{published ? (published.existing ? "Already published" : "Published") : "Publish to Confluence"}</button>
              {published && <a className="button-link" href={published.url} target="_blank" rel="noreferrer">Open “{published.title}”</a>}
            </div>
          )}
          {plan?.implementable && (
            <label className="approve">
              <input type="checkbox" checked={approved} onChange={(e) => setApproved(e.target.checked)} />
              I reviewed plan <code>{plan.id}</code> and approve it for implementation.
            </label>
          )}
        </div>
      )}

      {plan && (
        <div className="wizard-step">
          <h4>6. Implement</h4>
          {!implemented && (
            <>
              <p className="muted">
                Saves the source and confirmed links to the lake registry and re-runs the pipeline: <code>bronze.{source.name}</code> →{" "}
                <code>silver.{source.name}</code> → <code>{result.feed}</code>, with lineage, dictionary and data quality.
              </p>
              {plan.blockers.map((b) => <p key={b} className="error-text" role="alert">{b}</p>)}
              {plan.implementable && !approved && <p className="muted">Approve the plan above to enable implementation.</p>}
              <button className="primary" onClick={accept} disabled={!!busy || !plan.implementable || !approved}>
                {plan.status === "warn" ? "Accept with warnings & implement" : "Accept & implement"}
              </button>
            </>
          )}
          {implemented && (
            <div className="implemented" role="status">
              <p>
                <span className="status-pill status-added">implemented</span> Pipeline run <code>{implemented.run.run_id.slice(0, 8)}</code> built{" "}
                <code>bronze.{source.name}</code>, <code>silver.{source.name}</code> ({implemented.run.silver[source.name]} rows) and{" "}
                <code>{implemented.source.feed}</code> ({implemented.run.gold[implemented.source.feed.split(".")[1]]} rows) from plan <code>{plan.id}</code>.
              </p>
              <div className="actions">
                <a className="button-link" href="#lineage">Open lineage</a>
                <button onClick={onClose}>Close and view dictionary</button>
              </div>
            </div>
          )}
        </div>
      )}
    </section>
  );
}

function PlanView({ plan }) {
  const pct = (v) => (v == null ? "—" : `${Math.round(v * 100)}%`);
  const c = plan.contract;
  return (
    <div className="plan-section" aria-label="Implementation plan">
      <p><strong>{plan.title}</strong> <span className="muted">· plan {plan.id}</span></p>
      <h5>Layers</h5>
      <div className="table-wrap">
        <table>
          <thead><tr><th>Dataset</th><th>Action</th><th>How</th><th>Expected rows</th></tr></thead>
          <tbody>
            {plan.layers.map((l) => (
              <tr key={l.dataset}><td><code>{l.dataset}</code></td><td>{l.action}</td><td>{l.how}</td><td className="num">{l.expected_rows}</td></tr>
            ))}
          </tbody>
        </table>
      </div>
      <h5>Joins</h5>
      {plan.joins.length === 0 ? <p className="muted">No joins: the source stands alone.</p> : (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Source column</th><th>Target</th><th>Value match</th><th>Expected matches</th><th>Check</th></tr></thead>
            <tbody>
              {plan.joins.map((j) => (
                <tr key={j.source_column}>
                  <td><code>{j.source_column}</code></td><td><code>{j.target}</code></td><td className="num">{pct(j.overlap)}</td>
                  <td className="num">{j.expected_matches ?? "—"}</td><td><span className={`status-pill ${CHECK_CLASS[j.status]}`}>{j.status}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <h5>Data quality rules</h5>
      <ul>{plan.quality.map((q) => <li key={q.rule}><code>{q.rule}</code> on <code>{q.layer}</code>: {q.action}</li>)}</ul>
      <h5>Data contract</h5>
      <ul>
        <li><code>{c.dataset}</code>: {c.action === "create" ? "new contract" : "existing contract extended"}, owner {c.owner || "n/a"}{c.max_age_minutes ? `, max age ${c.max_age_minutes} min` : ""}</li>
        {c.rows && <li>Row count between {c.rows[0].toLocaleString()} and {c.rows[1].toLocaleString()}</li>}
        {c.add_columns?.length > 0 && <li>Adds columns: {c.add_columns.join(", ")}</li>}
        {c.rules.map((r) => <li key={r}>{r}</li>)}
      </ul>
      <h5>Impact</h5>
      <p>{Object.entries(plan.impact.summary).filter(([, v]) => v).map(([k, v]) => `${v} ${k.replace("_", " ")}`).join(", ") || "No changes"} · datasets: {plan.impact.datasets.join(", ") || "none"}</p>
      {plan.warnings.map((w) => <p key={w} className="warning" role="note">{w}</p>)}
    </div>
  );
}

export function RegisteredSources({ version, onChange }) {
  const [items, setItems] = useState([]);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState("");

  useEffect(() => {
    api.registeredSources().then(setItems).catch((e) => setError(e.message));
  }, [version]);

  async function remove(name) {
    if (!window.confirm(`Remove ${name} from the pipeline? Its bronze, silver and gold data is dropped on the next run.`)) return;
    setBusy(name);
    try {
      await api.removeSource(name);
      onChange?.();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy("");
    }
  }

  if (!items.length && !error) return null;
  return (
    <div className="registered" aria-label="Onboarded sources">
      <span className="muted">Onboarded sources:</span>
      {items.map((s) => (
        <span key={s.name} className="chip source">
          {s.name} → {s.feed}
          <button aria-label={`Remove ${s.name}`} onClick={() => remove(s.name)} disabled={!!busy}>
            {busy === s.name ? "…" : "×"}
          </button>
        </span>
      ))}
      {error && <span className="error-text">{error}</span>}
    </div>
  );
}
