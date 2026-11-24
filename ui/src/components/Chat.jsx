import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api } from "../api.js";

const SUGGESTIONS = [
  "Revenue by country",
  "Top products",
  "What are customers complaining about?",
  "Where does customer revenue come from?",
  "What data quality issues were found?",
];

export default function Chat() {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const endRef = useRef(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  async function send(text) {
    const content = text.trim();
    if (!content || loading) return;
    const history = [...messages, { role: "user", content }];
    setMessages(history);
    setInput("");
    setLoading(true);
    try {
      const res = await api.chat(history.map(({ role, content }) => ({ role, content })));
      setMessages([...history, { role: "assistant", content: res.reply, steps: res.steps }]);
    } catch (err) {
      setMessages([...history, { role: "assistant", content: `**Error:** ${err.message}`, error: true }]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="chat">
      <div className="messages">
        {messages.length === 0 && (
          <div className="empty">
            <img className="mark" src="/assets/parser-mark.svg" alt="" />
            <h2>Ask your data lake</h2>
            <p>Answers come from the gold layer, with full lineage back to the raw sources.</p>
            <div className="suggestions">
              {SUGGESTIONS.map((s) => (
                <button key={s} onClick={() => send(s)}>
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`msg ${m.role} ${m.error ? "error" : ""}`}>
            <div className="bubble">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.content}</ReactMarkdown>
              {m.steps?.length > 0 && (
                <details className="steps">
                  <summary>{m.steps.length} MCP tool call(s)</summary>
                  {m.steps.map((s, j) => (
                    <div key={j} className="step">
                      <code className="tool">{s.tool}</code>
                      <pre>{s.args.sql || JSON.stringify(s.args, null, 2)}</pre>
                      <pre className="result">{s.result}</pre>
                    </div>
                  ))}
                </details>
              )}
            </div>
          </div>
        ))}
        {loading && (
          <div className="msg assistant">
            <div className="bubble typing">Thinking…</div>
          </div>
        )}
        <div ref={endRef} />
      </div>
      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault();
          send(input);
        }}
      >
        <input
          name="message"
          aria-label="Message"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask about sales, customers, products, lineage…"
        />
        <button type="submit" disabled={loading || !input.trim()}>
          Send
        </button>
      </form>
    </section>
  );
}
