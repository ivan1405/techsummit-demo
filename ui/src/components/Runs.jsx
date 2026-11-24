import { useCallback, useEffect, useState } from "react";
import { api } from "../api.js";

const short = (id) => (id ? id.slice(0, 8) : "");
const when = (iso) => (iso ? new Date(iso).toLocaleString() : "");
const signed = (n) => (n == null ? "" : n > 0 ? `+${n.toLocaleString()}` : n.toLocaleString());

function ChangeSummary({ s }) {
  if (!s) return <span className="muted">first run</span>;
  const parts = [
    [s.datasets_added, "added", "added"],
    [s.datasets_removed, "removed", "removed"],
    [s.row_changes, "row changes", "changed"],
    [s.schema_changes, "schema", "changed"],
    [s.quality_changes, "quality", "changed"],
    [s.contract_changes, "contract", "removed"],
  ].filter(([n]) => n);
  if (!parts.length) return <span className="muted">no changes</span>;
  return parts.map(([n, label, cls]) => (
    <span key={label} className={`diff-chip ${cls}`}>{n} {label}</span>
  ));
}

function Schema({ d }) {
  return (
    <>
      {d.columns_added.map((c) => <span key={`+${c}`} className="diff-chip added">+{c}</span>)}
      {d.columns_removed.map((c) => <span key={`-${c}`} className="diff-chip removed">−{c}</span>)}
      {d.type_changes.map((t) => (
        <span key={`~${t.column}`} className="diff-chip changed">{t.column}: {t.from} → {t.to}</span>
      ))}
    </>
  );
}

function Diff({ diff, onlyChanges }) {
  const rows = diff.datasets.filter((d) => !onlyChanges || d.status !== "unchanged");
  return (
    <>
      <p className="muted">
        Run <code>{short(diff.run.run_id)}</code> ({when(diff.run.started_at)}) compared with{" "}
        <code>{short(diff.base.run_id)}</code> ({when(diff.base.started_at)}). <ChangeSummary s={diff.summary} />
      </p>
      <table className="diff-table">
        <thead>
          <tr><th>Dataset</th><th>Change</th><th>Rows before</th><th>Rows after</th><th>Δ rows</th><th>Schema</th></tr>
        </thead>
        <tbody>
          {rows.map((d) => (
            <tr key={d.dataset} className={`diff-${d.status}`}>
              <td><span className={`chip ${d.layer}`}>{d.dataset}</span></td>
              <td><span className={`diff-chip ${d.status}`}>{d.status}</span></td>
              <td className="num">{d.rows_base?.toLocaleString() ?? "–"}</td>
              <td className="num">{d.rows?.toLocaleString() ?? "–"}</td>
              <td className={`num ${d.rows_delta > 0 ? "up" : d.rows_delta < 0 ? "down" : ""}`}>
                {d.rows_delta ? `${signed(d.rows_delta)}${d.rows_pct != null ? ` (${signed(d.rows_pct)}%)` : ""}` : ""}
              </td>
              <td><Schema d={d} /></td>
            </tr>
          ))}
          {!rows.length && <tr><td colSpan={6} className="muted">No dataset changed between these runs.</td></tr>}
        </tbody>
      </table>
      {diff.quality.length > 0 && (
        <>
          <h3>Data quality changes</h3>
          <table>
            <thead><tr><th>Job</th><th>Rule</th><th>Dropped before</th><th>Dropped now</th><th>Δ</th></tr></thead>
            <tbody>
              {diff.quality.map((q) => (
                <tr key={`${q.job_name}/${q.rule}`}>
                  <td>{q.job_name}</td><td>{q.rule}</td>
                  <td className="num">{q.base}</td><td className="num">{q.current}</td>
                  <td className={`num ${q.delta > 0 ? "down" : "up"}`}>{signed(q.delta)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      {diff.contracts.length > 0 && (
        <>
          <h3>Contract changes</h3>
          <table>
            <thead><tr><th>Dataset</th><th>Check</th><th>Before</th><th>Now</th></tr></thead>
            <tbody>
              {diff.contracts.map((c) => (
                <tr key={`${c.dataset}/${c.check}`}>
                  <td><code>{c.dataset}</code></td><td>{c.check}</td>
                  <td>{c.base ? <span className={`check-chip ${c.base}`}>{c.base}</span> : "–"}</td>
                  <td>{c.current ? <span className={`check-chip ${c.current}`}>{c.current}</span> : "–"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </>
  );
}

export default function Runs() {
  const [history, setHistory] = useState(null);
  const [selected, setSelected] = useState("");
  const [base, setBase] = useState("");
  const [diff, setDiff] = useState(null);
  const [onlyChanges, setOnlyChanges] = useState(true);
  const [error, setError] = useState("");
  const [running, setRunning] = useState(false);

  const [limit, setLimit] = useState(20);
  const load = useCallback(() => api.runs(limit).then((h) => {
    setHistory(h);
    setSelected((s) => (h.runs.some((r) => r.run_id === s) ? s : h.runs[0]?.run_id || ""));
  }).catch((e) => setError(e.message)), [limit]);

  useEffect(() => { load(); }, [load]);

  const run = history?.runs.find((r) => r.run_id === selected);
  const baseId = base || run?.base_run_id || "";

  useEffect(() => {
    setDiff(null);
    if (!selected || !baseId) return undefined;
    let current = true;
    api.runDiff(selected, baseId)
      .then((d) => current && setDiff(d))
      .catch((e) => current && setError(e.message));
    return () => { current = false; };
  }, [selected, baseId]);

  const trigger = async () => {
    setRunning(true);
    setError("");
    try {
      const res = await api.runPipeline();
      setBase("");
      await load();
      setSelected(res.run_id);
    } catch (e) {
      setError(e.message);
    } finally {
      setRunning(false);
    }
  };

  if (!history) return error ? <p className="error-text">{error}</p> : <p className="muted">Loading…</p>;

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>Run history</h2>
        <button className="primary" onClick={trigger} disabled={running}>
          {running ? "Running pipeline…" : "Run pipeline now"}
        </button>
      </div>
      <p className="muted">
        {history.total_runs} pipeline runs recorded in the lineage tables. Select a run to see what changed compared with
        the run before it, or pick another run to compare with.
      </p>
      {error && <p className="error-text" role="alert">{error}</p>}
      <table className="runs-table">
        <thead>
          <tr>
            <th>Run</th><th>Started</th><th>Status</th><th>Trigger</th><th>Duration</th><th>Datasets</th>
            <th>Gold rows</th><th>Rows dropped</th><th>Contract fails</th><th>Changes vs previous</th>
          </tr>
        </thead>
        <tbody>
          {history.runs.map((r) => (
            <tr key={r.run_id} className={r.run_id === selected ? "selected" : ""} aria-selected={r.run_id === selected}
                onClick={() => { setSelected(r.run_id); setBase(""); }}>
              <td><button className="run-link" onClick={() => { setSelected(r.run_id); setBase(""); }}><code>{short(r.run_id)}</code></button></td>
              <td>{when(r.started_at)}</td>
              <td><span className={`status ${r.status}`}>{r.status}</span></td>
              <td>{r.trigger}</td>
              <td className="num">{r.duration_s != null ? `${r.duration_s}s` : ""}</td>
              <td className="num">{r.datasets}</td>
              <td className="num">{r.gold_rows.toLocaleString()}</td>
              <td className="num">{r.rows_dropped}</td>
              <td className={`num ${r.contract_failures ? "down" : ""}`}>{r.contract_failures}</td>
              <td><ChangeSummary s={r.changes} /></td>
            </tr>
          ))}
        </tbody>
      </table>
      {history.total_runs > history.runs.length && (
        <button className="link-button" onClick={() => setLimit((n) => n + 20)}>
          Show older runs ({history.total_runs - history.runs.length} more)
        </button>
      )}

      {run && (
        <div className="run-diff">
          <div className="toolbar">
            <h3>Run <code>{short(run.run_id)}</code> compared with</h3>
            <select aria-label="Compare with run" value={baseId} onChange={(e) => setBase(e.target.value)}>
              {!run.base_run_id && !base && <option value="">(first run)</option>}
              {history.runs.filter((r) => r.run_id !== run.run_id).map((r) => (
                <option key={r.run_id} value={r.run_id}>
                  {short(r.run_id)} · {when(r.started_at)}{r.run_id === run.base_run_id ? " (previous)" : ""}
                </option>
              ))}
            </select>
            <label className="inline">
              <input type="checkbox" checked={onlyChanges} onChange={(e) => setOnlyChanges(e.target.checked)} /> Only changes
            </label>
          </div>
          {!baseId && <p className="muted">This is the first recorded run: there is nothing to compare it with.</p>}
          {baseId && !diff && <p className="muted">Comparing…</p>}
          {diff && <Diff diff={diff} onlyChanges={onlyChanges} />}
        </div>
      )}
    </section>
  );
}
