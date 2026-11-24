import { useMemo } from "react";
import { layout, NODE_H, NODE_W } from "./Lineage.jsx";

export const STATUSES = ["added", "removed", "renamed", "modified", "impacted", "broken"];

export function Legend({ children }) {
  return (
    <div className="legend">
      {STATUSES.map((s) => (
        <span key={s} className={`chip status-${s}`}>{s}</span>
      ))}
      {children}
    </div>
  );
}

export default function ImpactGraph({ report, selected, onSelect }) {
  const view = useMemo(() => layout(report.nodes, report.edges), [report]);
  const changed = useMemo(() => {
    const by = {};
    report.columns.forEach((c) => (by[c.dataset] = (by[c.dataset] || 0) + 1));
    return by;
  }, [report]);

  return (
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
        const count = changed[n.id];
        const pick = () => onSelect?.(selected === n.id ? null : n.id);
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
  );
}
