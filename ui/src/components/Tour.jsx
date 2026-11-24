import { useState } from "react";

const STEPS = [
  { tab: "chat", title: "Ask the data", ask: "Revenue by country",
    text: "The chatbot answers from the gold tables through the MCP server. Expand the SQL and MCP tool calls under the answer to see exactly what ran." },
  { tab: "lineage", title: "Trace a number",
    text: "Click gold.customer_360, then its revenue column: the path lights up back through silver and bronze to orders.csv." },
  { tab: "dictionary", title: "Data dictionary and PII",
    text: "Every column with type, profile and upstream. Personal data is tagged PII and masked for readers: switch to Analyst in the header to see raw values. Click ? to ask about a column." },
  { tab: "dictionary", title: "Onboard a new source",
    text: "+ Add data source > Saved connection 'erp' > support_tickets (or upload data/samples/loyalty.json). Review the AI-suggested links, validate, approve the plan, then Accept & implement." },
  { tab: "impact", title: "What-if impact",
    text: "Propose a change, e.g. drop silver.orders.amount, and see which downstream datasets and columns break before anything is built." },
  { tab: "quality", title: "Quality and contracts",
    text: "Rows dropped per rule in the last run, and the data contract of each gold table: schema, row counts and freshness." },
  { tab: "runs", title: "Schedules and alerts",
    text: "Click Simulate late delivery for orders, then Run pipeline now: a critical alert appears and the header badge turns red. Turn on the schedule to watch runs arrive by themselves; Deliver now clears it." },
  { tab: "chat", title: "Ask what happened", ask: "Is any data late?",
    text: "The chatbot reads the same alerts. Try also: What changed since the last run?" },
];

export default function Tour({ onClose, onTab, onAsk }) {
  const [i, setI] = useState(0);
  const step = STEPS[i];
  const go = (n) => {
    setI(n);
    onTab(STEPS[n].tab);
  };

  return (
    <aside className="tour" role="dialog" aria-label="Guided tour">
      <div className="tour-head">
        <span className="muted">Guided tour · {i + 1}/{STEPS.length}</span>
        <button className="link-button" onClick={onClose} aria-label="Close tour">Close</button>
      </div>
      <h3>{step.title}</h3>
      <p>{step.text}</p>
      <div className="tour-actions">
        <button className="ghost" disabled={i === 0} onClick={() => go(i - 1)}>Back</button>
        {step.ask && <button className="ghost" onClick={() => onAsk(step.ask)}>Ask "{step.ask}"</button>}
        {i < STEPS.length - 1
          ? <button className="primary" onClick={() => go(i + 1)}>Next</button>
          : <button className="primary" onClick={onClose}>Finish</button>}
      </div>
    </aside>
  );
}
