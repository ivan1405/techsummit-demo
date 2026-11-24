const LABEL = { green: "Contract OK", amber: "Stale", red: "Contract broken" };

function age(minutes) {
  if (minutes == null) return "never";
  if (minutes < 60) return `${Math.round(minutes)} min ago`;
  if (minutes < 48 * 60) return `${Math.round(minutes / 60)} h ago`;
  return `${Math.round(minutes / 1440)} d ago`;
}

export function ContractBadge({ contract }) {
  if (!contract) return null;
  const failed = contract.checks.filter((c) => c.status === "fail").map((c) => c.check);
  const title = [
    `Owner: ${contract.owner}`,
    `Updated ${age(contract.age_minutes)} (max ${contract.max_age_minutes} min): ${contract.freshness}`,
    failed.length ? `Failed: ${failed.join(", ")}` : `${contract.checks.length} checks passed`,
  ].join("\n");
  return (
    <span className={`contract-badge contract-${contract.status}`} title={title} aria-label={`${LABEL[contract.status]}. ${title}`}>
      {LABEL[contract.status]} · {age(contract.age_minutes)}
    </span>
  );
}

export default function Contracts({ data }) {
  return (
    <>
      <h3>Data contracts</h3>
      <p className="muted">Checked on every pipeline run; freshness is measured now against each contract&apos;s max age.</p>
      <table>
        <thead>
          <tr><th>Dataset</th><th>Owner</th><th>Status</th><th>Updated</th><th>Max age</th><th>Checks</th></tr>
        </thead>
        <tbody>
          {data.datasets.map((d) => (
            <tr key={d.dataset}>
              <td><code>{d.dataset}</code></td>
              <td>{d.owner}</td>
              <td><ContractBadge contract={d} /></td>
              <td className="muted">{age(d.age_minutes)}</td>
              <td className="num">{d.max_age_minutes} min</td>
              <td>
                {d.checks.map((c) => (
                  <span key={c.check} className={`check-chip ${c.status}`} title={`expected ${c.expectation}; observed ${c.observed}`}>
                    {c.check}
                  </span>
                ))}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
