import { useEffect, useState } from "react";
import { api } from "./api.js";
import Chat from "./components/Chat.jsx";
import Dictionary from "./components/Dictionary.jsx";
import Impact from "./components/Impact.jsx";
import Lineage from "./components/Lineage.jsx";
import Quality from "./components/Quality.jsx";

const TABS = { chat: "Chat", lineage: "Lineage", dictionary: "Data dictionary", impact: "Impact analysis", quality: "Data quality" };

export default function App() {
  const [tab, setTabState] = useState(() => (location.hash.slice(1) in TABS ? location.hash.slice(1) : "chat"));
  const setTab = (id) => {
    setTabState(id);
    history.replaceState(null, "", `#${id}`);
  };
  const [health, setHealth] = useState(null);
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
    api.health().then(setHealth).catch(() => setHealth({ status: "down" }));
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
          <button className="theme-toggle" onClick={toggleTheme} aria-pressed={dark}>
            {dark ? "Light" : "Dark"} mode
          </button>
        </div>
      </header>
      <main>
        {tab === "chat" && <Chat />}
        {tab === "lineage" && <Lineage />}
        {tab === "dictionary" && <Dictionary />}
        {tab === "impact" && <Impact />}
        {tab === "quality" && <Quality />}
      </main>
    </div>
  );
}
