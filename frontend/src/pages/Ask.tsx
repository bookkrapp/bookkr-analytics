// Ask (phase 2): plain-English questions -> one read-only SQL query on the analytics copy -> answer, table, chart.
import { useEffect, useRef, useState } from "react";
import { getJSON } from "../api";
import { Bars, Card, ErrorBox, Table, TimeLines } from "../components";
import { fmtIST, fmtNum } from "../format";

const EXAMPLES = [
  "How many users planned a trip vs searched flights directly in the last 7 days, and how many booked?",
  "Top 10 flight routes searched this week and how many of those searches ended in a booking",
  "Daily new chats and bookings for the last 14 days",
  "Which airlines had the most failed bookings this month?",
  "How many users came back on 2 or more different days in the last 30 days?",
  "Average LLM cost per chat by journey type",
];

const STEPS = ["Reading the tables…", "Writing the SQL…", "Running the query…", "Explaining the result…"];

async function post(path: string, body: any) {
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail ?? res.statusText);
  return data;
}

const isNum = (v: any) => typeof v === "number";
const cell = (v: any) => (v === null || v === undefined ? "—" : isNum(v) ? fmtNum(v) : typeof v === "object" ? JSON.stringify(v) : String(v));
const COLORS = ["var(--series-1)", "var(--series-2)", "var(--series-3)", "var(--series-4)"];

function Chart({ r }: { r: any }) {
  const c = r.chart ?? {};
  if (!r.rows?.length || r.rows.length < 2 || !c.x || !r.columns.includes(c.x)) return null;
  const ys: string[] = (Array.isArray(c.y) ? c.y : [c.y]).filter((k: string) => r.columns.includes(k) && r.rows.some((x: any) => isNum(x[k])));
  if (!ys.length || !["bar", "line"].includes(c.type)) return null;
  const series = ys.slice(0, 4).map((k, i) => ({ key: k, label: k.replace(/_/g, " "), color: COLORS[i] }));
  const data = r.rows.slice(0, 60);
  return c.type === "line"
    ? <TimeLines data={data} x={c.x} series={series} height={260} />
    : <Bars data={data} x={c.x} series={series} height={Math.max(220, Math.min(data.length, 20) * 28)}
            layout={data.length > 6 ? "vertical" : "horizontal"} xFmt={(v) => String(v).slice(0, 28)} />;
}

function Result({ r, onFollow }: { r: any; onFollow: (q: string) => void }) {
  const [follow, setFollow] = useState("");
  if (r.clarify) {
    return (
      <Card title="🤔 Bookr Analyst needs a bit more detail">
        <div className="ask-q">“{r.question}”</div>
        <p>{r.clarify}</p>
      </Card>
    );
  }
  return (
    <section className="card ask-result">
      <div className="ask-q">“{r.question}”</div>
      <div className="ask-answer">{r.answer}</div>
      {r.highlights?.length > 0 && <ul className="ask-highlights">{r.highlights.map((h: string, i: number) => <li key={i}>{h}</li>)}</ul>}
      {(r.assumptions || r.caveats) && (
        <div className="ask-note">
          {r.assumptions && <div>📌 {r.assumptions}</div>}
          {r.caveats && <div>⚠️ {r.caveats}</div>}
        </div>
      )}
      <Chart r={r} />
      <h3 style={{ marginTop: 14 }}>{r.title || "Result"} <span className="muted small">· {fmtNum(r.row_count)} rows{r.truncated ? " (first 500)" : ""}</span></h3>
      <Table rows={r.rows} empty="The query returned no rows."
             cols={r.columns.map((c: string) => ({ key: c, label: c.replace(/_/g, " "), num: r.rows.some((x: any) => isNum(x[c])),
                                                    render: (x: any) => cell(x[c]) }))} />
      <details>
        <summary>Show the SQL</summary>
        <pre className="json">{r.sql}</pre>
      </details>
      <form className="ask-follow" onSubmit={(e) => { e.preventDefault(); if (follow.trim()) { onFollow(follow.trim()); setFollow(""); } }}>
        <input type="text" value={follow} onChange={(e) => setFollow(e.target.value)} placeholder="Follow up, e.g. “now split it by week” or “only round trips”" />
        <button className="btn" type="submit" disabled={!follow.trim()}>Ask follow-up</button>
      </form>
      {r.synced_at && <div className="muted small" style={{ marginTop: 8 }}>Data as of {fmtIST(r.synced_at)} · read-only query on the masked analytics copy</div>}
    </section>
  );
}

export default function Ask() {
  const [status, setStatus] = useState<any>(null);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [step, setStep] = useState(0);
  const [err, setErr] = useState<string | null>(null);
  const [result, setResult] = useState<any>(null);
  const [hist, setHist] = useState<any[]>([]);
  const box = useRef<HTMLTextAreaElement>(null);
  const loadHist = () => getJSON<any>("/api/ask/history").then((d) => setHist(d.items)).catch(() => {});
  useEffect(() => { getJSON("/api/ask/status").then(setStatus).catch(() => setStatus({ llm_enabled: false })); loadHist(); }, []);
  useEffect(() => {
    if (!busy) return;
    setStep(0);
    const t = setInterval(() => setStep((s) => Math.min(s + 1, STEPS.length - 1)), 5000);
    return () => clearInterval(t);
  }, [busy]);

  const run = async (question: string, previous?: any) => {
    setBusy(true); setErr(null);
    try {
      const r = await post("/api/ask", { question, previous: previous ? { question: previous.question, sql: previous.sql } : undefined });
      setResult(r); loadHist();
    } catch (e: any) { setErr(e.message); }
    setBusy(false);
  };

  return (
    <>
      <div>
        <h1>💬 Ask</h1>
        <div className="page-sub">Ask a question about Bookkr in plain English. Claude writes one read-only SQL query on the masked analytics copy, runs it and explains the result. Check the SQL before you quote a number.</div>
      </div>
      {status && !status.llm_enabled && (
        <div className="notice">Ask needs Claude. Set <code>ANTHROPIC_API_KEY</code> on the server (Render → Environment), or log in to Claude Code on the machine that runs Bookr Analyst.</div>
      )}
      <div className="ask-layout">
        <div className="ask-main">
          <section className="card">
            <form onSubmit={(e) => { e.preventDefault(); if (q.trim()) run(q.trim()); }}>
              <textarea ref={box} className="ask-box" value={q} onChange={(e) => setQ(e.target.value)} rows={3}
                        placeholder="e.g. How many round-trip searches ended in a booking last week?"
                        onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); if (q.trim() && !busy) run(q.trim()); } }} />
              <div className="ask-actions">
                <button className="btn primary" type="submit" disabled={busy || !q.trim() || (status && !status.llm_enabled)}>
                  {busy ? STEPS[step] : "Ask"}
                </button>
                <span className="muted small">Enter to ask · Shift+Enter for a new line · usually 15–40 s</span>
              </div>
            </form>
            {!result && !busy && (
              <div className="ask-examples">
                <div className="muted small">Try one:</div>
                {EXAMPLES.map((x) => <button key={x} className="chip-btn" onClick={() => { setQ(x); box.current?.focus(); }}>{x}</button>)}
              </div>
            )}
          </section>
          {err && <ErrorBox error={err} />}
          {result && <Result r={result} onFollow={(f) => { setQ(f); run(f, result); }} />}
        </div>
        <aside className="card ask-history">
          <h2>🕘 Recent questions</h2>
          {!hist.length ? <div className="muted small">Nothing asked yet.</div> : (
            <ul>
              {hist.map((h) => (
                <li key={h.id}>
                  <button onClick={async () => { setErr(null); try { setResult(await getJSON(`/api/ask/${h.id}`)); } catch (e: any) { setErr(e.message); } }}>
                    <div>{h.question}</div>
                    <div className="muted small">{fmtIST(h.created_at)}{h.row_count != null ? ` · ${h.row_count} rows` : ""}</div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </aside>
      </div>
    </>
  );
}
