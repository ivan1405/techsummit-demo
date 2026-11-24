import { useCallback, useEffect, useState } from "react";
import { api } from "../api.js";

const INTERVALS = [1, 5, 15, 60];
const time = (iso) => (iso ? new Date(iso).toLocaleTimeString() : "");
const age = (m) => (m < 1 ? "just now" : m < 120 ? `${Math.round(m)} min ago` : `${(m / 60).toFixed(1)} h ago`);

export default function Ops({ version, onChange }) {
  const [schedule, setSchedule] = useState(null);
  const [arrivals, setArrivals] = useState([]);
  const [alerts, setAlerts] = useState(null);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    api.schedule().then(setSchedule).catch((e) => setError(e.message));
    api.arrivals().then(setArrivals).catch((e) => setError(e.message));
    api.alerts().then(setAlerts).catch(() => setAlerts(null));
  }, []);
  useEffect(() => { load(); }, [load, version]);

  const act = async (fn) => {
    setError("");
    try {
      await fn();
      load();
      onChange?.();
    } catch (e) {
      setError(e.message);
    }
  };
  const save = (patch) => act(async () => setSchedule(await api.setSchedule({ ...schedule, ...patch })));
  const late = Object.fromEntries((alerts?.sources || []).map((s) => [s.source, s]));

  return (
    <div className="ops">
      <div className="ops-card">
        <h3>Schedule</h3>
        {schedule && (
          <div className="toolbar">
            <label className="check">
              <input type="checkbox" checked={schedule.enabled} onChange={(e) => save({ enabled: e.target.checked })} />
              Run the pipeline every
            </label>
            <select aria-label="Schedule interval" value={schedule.interval_minutes}
                    onChange={(e) => save({ interval_minutes: Number(e.target.value) })}>
              {INTERVALS.map((m) => <option key={m} value={m}>{m} min</option>)}
            </select>
            <span className="muted">
              {schedule.enabled ? `Next run at ${time(schedule.next_run_at)}` : "Off: runs only start manually"}
            </span>
          </div>
        )}
      </div>

      <div className="ops-card">
        <h3>Source deliveries</h3>
        <table className="ops-table">
          <thead><tr><th>Source</th><th>SLA</th><th>Last run saw</th><th>Next run</th><th /></tr></thead>
          <tbody>
            {arrivals.map((a) => (
              <tr key={a.source}>
                <td><code>{a.source}</code></td>
                <td>every {a.max_age_minutes >= 120 ? `${a.max_age_minutes / 60} h` : `${a.max_age_minutes} min`}</td>
                <td>
                  {late[a.source] && (
                    <span className={`diff-chip ${late[a.source].status === "late" ? "removed" : "added"}`}>
                      {late[a.source].status === "late" ? `late · ${age(late[a.source].age_minutes)}` : "on time"}
                    </span>
                  )}
                </td>
                <td>{a.status === "late" ? <span className="diff-chip changed">delivery missing</span> : "delivered"}</td>
                <td>
                  {a.status === "late" ? (
                    <button className="link-button" onClick={() => act(() => api.deliverSource(a.source))}>Deliver now</button>
                  ) : (
                    <button className="link-button" onClick={() => act(() => api.delaySource(a.source))}>
                      Simulate late delivery
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="muted small">Delivery changes apply from the next run.</p>
      </div>

      <div className="ops-card" aria-live="polite">
        <h3>Alerts {alerts && <span className="muted">· run <code>{alerts.run_id.slice(0, 8)}</code></span>}</h3>
        {alerts?.active.length ? (
          <ul className="alerts">
            {alerts.active.map((a) => (
              <li key={`${a.kind}-${a.dataset}`} className={`alert ${a.severity}`}>
                <strong>{a.severity}</strong> <code>{a.dataset}</code> {a.message}
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted">{alerts ? "No active alerts in the latest run." : "No alerts recorded yet."}</p>
        )}
      </div>
      {error && <p className="error-text" role="alert">{error}</p>}
    </div>
  );
}
