import { useEffect, useState } from "react";
import { api } from "../api.js";

export default function Quality() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    api.quality().then(setData).catch((e) => setError(e.message));
  }, []);

  if (error) return <p className="error-text">{error}</p>;
  if (!data) return <p className="muted">Loading…</p>;

  return (
    <section className="panel">
      <h2>Data quality</h2>
      <p className="muted">
        Run <code>{data.run_id}</code>: <strong>{data.total_rows_dropped}</strong> rows dropped while building the
        silver layer.
      </p>
      <table>
        <thead>
          <tr>
            <th>Job</th>
            <th>Input dataset</th>
            <th>Rule</th>
            <th>Rows dropped</th>
          </tr>
        </thead>
        <tbody>
          {data.rules.map((r, i) => (
            <tr key={i}>
              <td>{r.job_name}</td>
              <td>{r.dataset}</td>
              <td>{r.rule}</td>
              <td className="num">{r.rows_affected}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
