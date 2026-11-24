import { useRef } from "react";

// Fixed hex colours (not CSS variables) so the exported PNG looks the same as on screen.
const PALETTE = ["#e44144", "#012542", "#8092a0", "#1f6b4a", "#e8a33d", "#516c83", "#a3bbce", "#961e22", "#9fd0b6", "#5d6271"];
const INK = "#012542";
const MUTED = "#4b657b";
const GRID = "#dce4e9";
const W = 600;

const fmt = (v) => v.toLocaleString(undefined, { maximumFractionDigits: 2 });
const clip = (s, n) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);

function Pie({ spec }) {
  const n = spec.values.length;
  const total = spec.values.reduce((a, b) => a + b, 0) || 1;
  const height = Math.max(300, 60 + n * 22);
  const [cx, cy, r] = [160, 40 + (height - 40) / 2, 115];
  const inner = spec.kind === "donut" ? 62 : 0;
  const at = (angle, radius) => `${cx + radius * Math.cos(angle)} ${cy + radius * Math.sin(angle)}`;
  let angle = -Math.PI / 2;
  const slices = spec.values.map((v, i) => {
    const a0 = angle;
    angle += (v / total) * 2 * Math.PI;
    const full = angle - a0 >= 2 * Math.PI - 1e-6;
    const large = angle - a0 > Math.PI ? 1 : 0;
    const d = full
      ? null
      : `M ${at(a0, r)} A ${r} ${r} 0 ${large} 1 ${at(angle, r)} ` +
        (inner ? `L ${at(angle, inner)} A ${inner} ${inner} 0 ${large} 0 ${at(a0, inner)} Z` : `L ${cx} ${cy} Z`);
    return { i, v, d, full };
  });
  return (
    <g>
      {slices.map(({ i, v, d, full }) =>
        full ? (
          <circle key={i} cx={cx} cy={cy} r={inner ? (r + inner) / 2 : r} fill={inner ? "none" : PALETTE[i % PALETTE.length]}
            stroke={inner ? PALETTE[i % PALETTE.length] : "none"} strokeWidth={inner ? r - inner : 0}>
            <title>{`${spec.labels[i]}: ${fmt(v)}`}</title>
          </circle>
        ) : (
          v > 0 && (
            <path key={i} d={d} fill={PALETTE[i % PALETTE.length]} stroke="#ffffff" strokeWidth="1.5">
              <title>{`${spec.labels[i]}: ${fmt(v)}`}</title>
            </path>
          )
        ),
      )}
      {inner > 0 && (
        <text x={cx} y={cy + 5} textAnchor="middle" fontSize="14" fontWeight="600" fill={INK}>{fmt(total)}</text>
      )}
      {spec.labels.map((label, i) => (
        <g key={i} transform={`translate(320 ${50 + i * 22})`}>
          <rect width="12" height="12" rx="2" fill={PALETTE[i % PALETTE.length]} />
          <text x="18" y="11" fontSize="12" fill={INK}>
            {clip(label, 22)} <tspan fill={MUTED}>{fmt(spec.values[i])} ({((spec.values[i] / total) * 100).toFixed(1)}%)</tspan>
          </text>
        </g>
      ))}
    </g>
  );
}

function Bars({ spec }) {
  const [labelW, top, row] = [170, 44, 26];
  const plotW = W - labelW - 100;
  const lo = Math.min(0, ...spec.values);
  const hi = Math.max(0, ...spec.values) || 1;
  const x = (v) => labelW + ((v - lo) / (hi - lo)) * plotW;
  return (
    <g>
      <line x1={x(0)} x2={x(0)} y1={top - 4} y2={top + spec.values.length * row} stroke={GRID} />
      {spec.values.map((v, i) => (
        <g key={i} transform={`translate(0 ${top + i * row})`}>
          <text x={labelW - 8} y="15" fontSize="12" textAnchor="end" fill={INK}>{clip(spec.labels[i], 24)}</text>
          <rect x={Math.min(x(0), x(v))} y="3" width={Math.max(1, Math.abs(x(v) - x(0)))} height={row - 8} rx="3"
            fill={PALETTE[0]}>
            <title>{`${spec.labels[i]}: ${fmt(v)}`}</title>
          </rect>
          <text x={Math.max(x(0), x(v)) + 6} y="15" fontSize="11" fill={MUTED}>{fmt(v)}</text>
        </g>
      ))}
    </g>
  );
}

function Line({ spec, height }) {
  const [left, right, top, bottom] = [70, W - 44, 44, height - 44];
  const n = spec.values.length;
  const lo = Math.min(0, ...spec.values);
  const hi = Math.max(...spec.values, lo + 1);
  const x = (i) => (n === 1 ? (left + right) / 2 : left + (i / (n - 1)) * (right - left));
  const y = (v) => bottom - ((v - lo) / (hi - lo)) * (bottom - top);
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((t) => lo + t * (hi - lo));
  const every = Math.ceil(n / 6);
  return (
    <g>
      {ticks.map((t, i) => (
        <g key={i}>
          <line x1={left} x2={right} y1={y(t)} y2={y(t)} stroke={GRID} />
          <text x={left - 8} y={y(t) + 4} fontSize="11" textAnchor="end" fill={MUTED}>{fmt(Math.round(t))}</text>
        </g>
      ))}
      <polyline fill="none" stroke={PALETTE[0]} strokeWidth="2.5" points={spec.values.map((v, i) => `${x(i)},${y(v)}`).join(" ")} />
      {spec.values.map((v, i) => (
        <circle key={i} cx={x(i)} cy={y(v)} r="3.5" fill={PALETTE[0]}>
          <title>{`${spec.labels[i]}: ${fmt(v)}`}</title>
        </circle>
      ))}
      {spec.labels.map((label, i) => ((i % every === 0 && n - 1 - i >= every / 2) || i === n - 1) && (
        <text key={i} x={x(i)} y={bottom + 18} fontSize="11" textAnchor="middle" fill={MUTED}>{clip(label, 12)}</text>
      ))}
    </g>
  );
}

function chartHeight(spec) {
  const n = spec.values.length;
  if (spec.kind === "bar") return 44 + n * 26 + 16;
  if (spec.kind === "line") return 300;
  return Math.max(300, 60 + n * 22);
}

export default function Chart({ spec }) {
  const ref = useRef(null);
  const height = chartHeight(spec);

  function download() {
    const svg = ref.current.cloneNode(true);
    svg.setAttribute("width", W);
    svg.setAttribute("height", height);
    const xml = new XMLSerializer().serializeToString(svg);
    const img = new Image();
    img.onload = () => {
      const canvas = document.createElement("canvas");
      [canvas.width, canvas.height] = [W * 2, height * 2];
      const ctx = canvas.getContext("2d");
      ctx.scale(2, 2);
      ctx.drawImage(img, 0, 0);
      const a = document.createElement("a");
      a.download = `${spec.title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "chart"}.png`;
      a.href = canvas.toDataURL("image/png");
      a.click();
    };
    img.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(xml)}`;
  }

  return (
    <figure className="chart">
      <svg ref={ref} xmlns="http://www.w3.org/2000/svg" viewBox={`0 0 ${W} ${height}`} width="100%" role="img"
        aria-label={spec.title} fontFamily="Inter, Arial, sans-serif">
        <rect width={W} height={height} fill="#ffffff" />
        <text x="16" y="26" fontSize="15" fontWeight="600" fill={INK}>{spec.title}</text>
        {spec.value_label && (
          <text x={W - 16} y="26" fontSize="11" textAnchor="end" fill={MUTED}>{spec.value_label}</text>
        )}
        {spec.kind === "bar" ? <Bars spec={spec} /> : spec.kind === "line" ? <Line spec={spec} height={height} /> : <Pie spec={spec} />}
      </svg>
      <figcaption>
        <button type="button" className="link-button" onClick={download}>Download PNG</button>
      </figcaption>
    </figure>
  );
}
