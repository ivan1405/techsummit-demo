import { useEffect, useState } from "react";
import { api } from "./api.js";
import Chat from "./components/Chat.jsx";
import Lineage from "./components/Lineage.jsx";
import Quality from "./components/Quality.jsx";

const TABS = { chat: "Chat", lineage: "Lineage", quality: "Data quality" };

export default function App() {
  const [tab, setTabState] = useState(() => (location.hash.slice(1) in TABS ? location.hash.slice(1) : "chat"));
  const setTab = (id) => {
    setTabState(id);
    history.replaceState(null, "", `#${id}`);
  };
  const [health, setHealth] = useState(null);

  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth({ status: "down" }));
  }, []);

  return (
    <div className="app">
      <header>
        <div className="brand">
          <span className="logo">P</span>
          <div>
            <h1>Parser AI · Data Assistant</h1>
            <p>Medallion lakehouse · MCP · LangGraph</p>
          </div>
        </div>
        <nav>
          {Object.entries(TABS).map(([id, label]) => (
            <button key={id} className={tab === id ? "active" : ""} onClick={() => setTab(id)}>
              {label}
            </button>
          ))}
        </nav>
        <span className={`badge ${health?.status === "ok" ? "ok" : "down"}`}>
          {health?.status === "ok" ? (health.model ? `LLM: ${health.model}` : "Demo mode (no LLM)") : "Agent offline"}
        </span>
      </header>
      <main>
        {tab === "chat" && <Chat />}
        {tab === "lineage" && <Lineage />}
        {tab === "quality" && <Quality />}
      </main>
    </div>
  );
}
