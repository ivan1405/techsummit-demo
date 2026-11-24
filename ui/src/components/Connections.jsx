import { useEffect, useState } from "react";
import { api } from "../api.js";

const URL_HINT = "postgresql://reader:reader@source-db:5432/erp · mysql://user:pass@host:3306/db · sqlite:///path.db · https://…/file.csv";

function ConnectionForm({ onSaved }) {
  const [form, setForm] = useState({ name: "", url: "", description: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });

  async function save() {
    if (!/^[a-z][a-z0-9_-]{0,40}$/.test(form.name) || !form.url.trim()) {
      setError("Enter a lowercase name (letters, digits, - or _) and a connection string.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const saved = await api.saveConnection(form);
      setForm({ name: "", url: "", description: "" });
      onSaved(saved);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fields conn-form" role="group" aria-label="New connection">
      <label>
        Name
        <input name="conn_name" value={form.name} onChange={set("name")} placeholder="erp-replica" />
      </label>
      <label className="wide">
        Connection string
        <input name="conn_url" value={form.url} onChange={set("url")} placeholder={URL_HINT} />
      </label>
      <label>
        Description
        <input name="conn_description" value={form.description} onChange={set("description")} placeholder="Read replica of the ERP" />
      </label>
      <button type="button" className="primary" disabled={busy} onClick={save}>{busy ? "Testing…" : "Test & save"}</button>
      {error && <p className="error-text" role="alert">{error}</p>}
    </div>
  );
}

export default function ConnectionPicker({ value, table, onChange }) {
  const [items, setItems] = useState(null);
  const [schema, setSchema] = useState(null);
  const [managing, setManaging] = useState(false);
  const [error, setError] = useState(null);

  const load = () => api.connections().then(setItems).catch((e) => setError(e.message));
  useEffect(() => {
    load();
  }, []);

  useEffect(() => {
    setSchema(null);
    if (!value) return;
    api.connectionSchema(value).then(setSchema).catch((e) => setError(e.message));
  }, [value]);

  async function remove(name) {
    if (!window.confirm(`Delete connection ${name}?`)) return;
    try {
      await api.deleteConnection(name);
      if (value === name) onChange({ connection: "", table: "" });
      load();
    } catch (e) {
      setError(e.message);
    }
  }

  const current = items?.find((c) => c.name === value);

  return (
    <div className="connections">
      <div className="fields">
        <label>
          Connection
          <select name="connection" value={value} onChange={(e) => onChange({ connection: e.target.value, table: "" })} required>
            <option value="">{items?.length ? "Choose a saved connection" : "No connections yet"}</option>
            {items?.map((c) => (
              <option key={c.name} value={c.name}>{c.name} ({c.type})</option>
            ))}
          </select>
        </label>
        <button type="button" className="link-button" aria-expanded={managing} onClick={() => setManaging(!managing)}>
          {managing ? "Done" : "Manage connections"}
        </button>
      </div>
      {current && <p className="muted"><code>{current.url}</code> {current.description && `· ${current.description}`}</p>}
      {managing && (
        <div className="conn-manage">
          <ConnectionForm onSaved={(c) => { load(); onChange({ connection: c.name, table: "" }); }} />
          {items?.length > 0 && (
            <table>
              <thead><tr><th>Name</th><th>Type</th><th>Connection string</th><th>Description</th><th /></tr></thead>
              <tbody>
                {items.map((c) => (
                  <tr key={c.name}>
                    <td>{c.name}</td>
                    <td className="muted">{c.type}</td>
                    <td><code>{c.url}</code></td>
                    <td className="muted">{c.description}</td>
                    <td><button type="button" className="link-button" onClick={() => remove(c.name)} aria-label={`Delete ${c.name}`}>Delete</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
      {error && <p className="error-text" role="alert">{error}</p>}
      {schema && (
        <div className="schema-browser" role="listbox" aria-label="Tables">
          {schema.map((t) => (
            <button type="button" key={t.table} role="option" aria-selected={table === t.table}
              className={table === t.table ? "active" : ""} onClick={() => onChange({ connection: value, table: t.table })}
              title={t.columns.map((c) => `${c.name}: ${c.type}`).join("\n")}>
              <strong>{t.table}</strong> <span className="muted">{t.columns.length} cols</span>
              <span className="cols">{t.columns.slice(0, 6).map((c) => c.name).join(", ")}{t.columns.length > 6 ? "…" : ""}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
