import { useEffect, useState } from "react";
import { api, getRole, setRole as storeRole } from "./api.js";
import Chat from "./components/Chat.jsx";
import Dictionary from "./components/Dictionary.jsx";
import Impact from "./components/Impact.jsx";
import Lineage from "./components/Lineage.jsx";
import Quality from "./components/Quality.jsx";
import Runs from "./components/Runs.jsx";

const TABS = { chat: "Chat", lineage: "Lineage", dictionary: "Data dictionary", impact: "Impact analysis", quality: "Data quality", runs: "Run history" };

export default function App() {
  const [tab, setTabState] = useState(() => (location.hash.slice(1) in TABS ? location.hash.slice(1) : "chat"));
  const setTab = (id) => {
    setTabState(id);
    history.replaceState(null, "", `#${id}`);
  };
  const [health, setHealth] = useState(null);
  const [role, setRoleState] = useState(getRole);
  const setRole = (next) => {
    storeRole(next);
    setRoleState(next);
  };
  const [question, setQuestion] = useState(null);
  const ask = (text) => {
    setQuestion({ text, id: Date.now() });
    setTab("chat");
  };
  const [dark, setDark] = useState(() => document.documentElement.classList.contains("dark"));
  const toggleTheme = () => {
    const next = !dark;
    document.documentElement.classList.toggle("dark", next);
    localStorage.setItem("theme", next ? "dark" : "light");
    setDark(next);
  };

  useEffect(() => {
    const onHash = () => location.hash.slice(1) in TABS && setTabState(location.hash.slice(1));
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    api.health().then((h) => {
      setHealth(h);
      if (!h.roles?.includes(getRole())) setRole("reader");
    }).catch(() => setHealth({ status: "down" }));
  }, []);

  return (
    <div className="app">
      <header>
        <div className="brand">
          <img src="/assets/logo-dark.svg" alt="Parser" />
          <span className="divider" />
          <div>
            <h1>Data Assistant</h1>
            <p>Medallion lakehouse · MCP · LangGraph</p>
          </div>
        </div>
        <nav role="tablist">
          {Object.entries(TABS).map(([id, label]) => (
            <button key={id} role="tab" aria-selected={tab === id} onClick={() => setTab(id)}>
              {label}
            </button>
          ))}
        </nav>
        <div className="header-end">
          <span className={`badge ${health?.status === "ok" ? "ok" : "down"}`}>
            {health?.status === "ok" ? (health.model ? `LLM: ${health.model}` : "Demo mode (no LLM)") : "Agent offline"}
          </span>
          <label className="role-select" title="Data role for chat and dictionary: readers see personal data masked">
            <select aria-label="Data role" className={role === "analyst" ? "pii-on" : ""} value={role}
                    onChange={(e) => setRole(e.target.value)}>
              {(health?.roles || ["reader"]).map((r) => (
                <option key={r} value={r}>{r === "analyst" ? "Analyst · PII visible" : "Reader · PII masked"}</option>
              ))}
            </select>
          </label>
          <button className="theme-toggle" onClick={toggleTheme} aria-pressed={dark}>
            {dark ? "Light" : "Dark"} mode
          </button>
        </div>
      </header>
      <main>
        <Chat question={question} hidden={tab !== "chat"} />
        {tab === "lineage" && <Lineage onAsk={ask} key={role} />}
        {tab === "dictionary" && <Dictionary onAsk={ask} key={role} />}
        {tab === "impact" && <Impact key={role} />}
        {tab === "quality" && <Quality key={role} />}
        {tab === "runs" && <Runs key={role} />}
      </main>
    </div>
  );
}
