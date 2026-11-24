import { useEffect, useMemo, useState } from "react";
import { api } from "../api.js";
import { columnQuestion } from "./Chat.jsx";

export const NODE_W = 165;
export const NODE_H = 44;
const COL_GAP = 40;
const ROW_GAP = 18;

export function layout(nodes, edges) {
  const depth = {};
  const incoming = (id) => edges.filter((e) => e.target === id).map((e) => e.source);
  const visit = (id, seen = new Set()) => {
    if (depth[id] !== undefined) return depth[id];
    if (seen.has(id)) return 0;
    seen.add(id);
    const parents = incoming(id);
    depth[id] = parents.length ? Math.max(...parents.map((p) => visit(p, seen))) + 1 : 0;
    return depth[id];
  };
  nodes.forEach((n) => visit(n.id));
  const columns = {};
  nodes.forEach((n) => (columns[depth[n.id]] ||= []).push(n));
  const positions = {};
  let height = 0;
  Object.entries(columns).forEach(([d, col]) => {
    col.sort((a, b) => a.id.localeCompare(b.id));
    col.forEach((n, i) => {
      positions[n.id] = { x: 20 + d * (NODE_W + COL_GAP), y: 20 + i * (NODE_H + ROW_GAP) };
    });
    height = Math.max(height, 20 + col.length * (NODE_H + ROW_GAP));
  });
  const width = 40 + Object.keys(columns).length * (NODE_W + COL_GAP) - COL_GAP;
  return { positions, width, height: height + 20 };
}

export default function Lineage({ onAsk }) {
  const [graph, setGraph] = useState(null);
  const [error, setError] = useState(null);
  const [selected, setSelected] = useState(null);
  const [trace, setTrace] = useState(null);

  useEffect(() => {
    api.lineage().then(setGraph).catch((e) => setError(e.message));
  }, []);

  const view = useMemo(() => (graph ? layout(graph.nodes, graph.edges) : null), [graph]);
  const highlighted = useMemo(() => {
    if (!trace) return new Set();
    return new Set(trace.steps.flatMap((s) => [s.source_dataset, s.target_dataset]));
  }, [trace]);

  const select = (id) => {
    setSelected(id);
    setTrace(null);
  };

  if (error) return <p className="error-text">{error}</p>;
  if (!graph) return <p className="muted">Loading…</p>;

  const node = graph.nodes.find((n) => n.id === selected);
  const jobs = graph.jobs.filter((j) => j.outputs.split(", ").includes(selected));

  async function traceColumn(column) {
    setTrace(await api.columnLineage(selected, column));
  }

  return (
    <section className="lineage">
      <div className="graph">
        <div className="legend">
          {["source", "bronze", "silver", "gold"].map((l) => (
            <span key={l} className={`chip ${l}`}>
              {l}
            </span>
          ))}
          <span className="muted">Run {graph.run_id.slice(0, 8)} · click a dataset</span>
        </div>
        <svg width={view.width} height={view.height}>
          <defs>
            <marker id="arrow" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="6" markerHeight="6" orient="auto">
              <path d="M0,0 L10,5 L0,10 z" className="arrow" />
            </marker>
          </defs>
          {graph.edges.map((e, i) => {
            const a = view.positions[e.source];
            const b = view.positions[e.target];
            const x1 = a.x + NODE_W, y1 = a.y + NODE_H / 2, x2 = b.x, y2 = b.y + NODE_H / 2;
            const active = highlighted.has(e.source) && highlighted.has(e.target);
            return (
              <path
                key={i}
                d={`M${x1},${y1} C${x1 + 40},${y1} ${x2 - 40},${y2} ${x2},${y2}`}
                className={`edge ${active ? "active" : ""}`}
                markerEnd="url(#arrow)"
              />
            );
          })}
          {graph.nodes.map((n) => {
            const p = view.positions[n.id];
            return (
              <g
                key={n.id}
                transform={`translate(${p.x},${p.y})`}
                className={`node ${n.layer} ${selected === n.id ? "selected" : ""} ${highlighted.has(n.id) ? "traced" : ""}`}
                role="button"
                tabIndex={0}
                aria-pressed={selected === n.id}
                aria-label={`${n.layer} ${n.name}`}
                onClick={() => select(n.id)}
                onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), select(n.id))}
              >
                <rect width={NODE_W} height={NODE_H} rx="8" />
                <rect className="bar" x="0" y="8" width="3" height={NODE_H - 16} rx="1.5" />
                <text x="12" y="18" className="layer">{n.layer}</text>
                <text x="12" y="35" className="name">{n.name}</text>
                {n.rows != null && <text x={NODE_W - 10} y="18" textAnchor="end" className="rows">{n.rows} rows</text>}
              </g>
            );
          })}
        </svg>
      </div>
      <aside className="details">
        {!node && <p className="muted">Select a dataset to see the jobs, row counts and column lineage.</p>}
        {node && (
          <>
            <h3>{node.id}</h3>
            {jobs.map((j) => (
              <div key={j.job_name} className="job">
                <strong>{j.job_name}</strong> <span className={`status ${j.status}`}>{j.status}</span>
                <p>{j.description}</p>
                <p className="muted">
                  in: {j.inputs}
                  <br />
                  rows in {j.rows_in} → out {j.rows_out} · dropped {j.rows_dropped}
                </p>
              </div>
            ))}
            {node.columns.length > 0 && (
              <>
                <h4>Columns: click one to trace it</h4>
                <div className="columns">
                  {node.columns.map((c) => (
                    <button key={c} className={trace?.column === c ? "active" : ""} onClick={() => traceColumn(c)}>
                      {c}
                    </button>
                  ))}
                </div>
              </>
            )}
            {trace && (
              <div className="trace">
                <h4>
                  {trace.dataset}.{trace.column}
                </h4>
                <ol>
                  {trace.steps.map((s, i) => (
                    <li key={i}>
                      <code>{s.target_dataset}.{s.target_column}</code> ← <code>{s.source_dataset}.{s.source_column}</code>
                      <div className="muted">{s.transformation}</div>
                    </li>
                  ))}
                </ol>
                <p>
                  <strong>Origin:</strong> {trace.origins.join(", ")}
                </p>
                <button className="primary" onClick={() => onAsk(columnQuestion(trace.dataset, trace.column))}>
                  Ask about this column
                </button>
              </div>
            )}
          </>
        )}
      </aside>
    </section>
  );
}
