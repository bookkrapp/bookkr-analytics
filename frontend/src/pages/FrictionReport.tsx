// Priorities tab: the automated issue report for a day, week or month (phase 3c).
// Judged friction items are grouped into named issues on the server; this page ranks them and shows the analyst brief.
import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { getJSON } from "../api";
import { Bars, Card, ErrorBox, Info, Kpi, Loading, Table } from "../components";
import { fmtDate, fmtIST, fmtNum } from "../format";
import { BucketPill, LanePill, TYPE_LABEL } from "../frictionTaxonomy";

async function send(method: string, path: string, body?: any) {
  const res = await fetch(path, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail ?? res.statusText);
  return data;
}

export const STATUS_LABEL: Record<string, string> = {
  open: "Open", in_progress: "Being fixed", fixed: "Fixed", wont_fix: "Won't fix", not_issue: "Not an issue",
};

export function PriorityBadge({ p }: { p?: string }) {
  if (!p) return null;
  return <span className={`prio ${p.toLowerCase()}`}>{p}</span>;
}

function Trend({ cur, prev, trend }: { cur: number; prev: number; trend?: string }) {
  if (trend === "new" || (!prev && cur)) return <span className="trend new">New</span>;
  const d = cur - prev;
  if (!d) return <span className="muted small">= {prev}</span>;
  return <span className={`small ${d > 0 ? "down" : "up"}`}>{d > 0 ? "▲" : "▼"} {Math.abs(d)} <span className="muted">(was {prev})</span></span>;
}

const periodLabel = (period: string, p: any) =>
  period === "day" ? fmtDate(p.start)
    : period === "week" || period === "last7" ? `${fmtDate(p.start)} – ${fmtDate(p.end)}`
      : new Date(p.start + "T00:00:00").toLocaleDateString("en-IN", { month: "long", year: "numeric" });

function Brief({ rep, onMade }: { rep: any; onMade: () => void }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const b = rep.brief?.content;
  useEffect(() => {
    if (!busy) return;
    const t = setInterval(async () => {
      const s = await getJSON<any>(`/api/friction/briefs/status?period=${rep.period}&start=${rep.start}`).catch(() => null);
      if (!s || s.status === "running") return;
      setBusy(false);
      if (s.status?.startsWith("failed")) setErr(s.status);
      onMade();
    }, 3000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [busy]);
  const make = async () => {
    setErr(null);
    try { await send("POST", "/api/friction/briefs", { period: rep.period, start: rep.start }); setBusy(true); }
    catch (e: any) { setErr(e.message); }
  };
  const name = (id: number) => rep.issues.find((i: any) => i.id === id)?.title;
  const IssueRef = ({ id, title }: { id: number; title?: string }) =>
    <Link to={`/friction/issue/${id}`}>{title || name(id) || `Issue #${id}`}</Link>;
  return (
    <Card title="Analyst brief" info="f_brief" right={
      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        {rep.brief && <span className="muted small">Written {fmtIST(rep.brief.created_at)} · {rep.brief.triggered_by === "schedule" ? "automatic" : "on request"}</span>}
        <button className="btn" disabled={busy || !rep.llm_enabled} onClick={make}>
          {busy ? "Writing… (about a minute)" : rep.brief ? "Rewrite" : "Write brief"}
        </button>
      </div>}>
      {err && <div className="down small">{err}</div>}
      {!b ? (
        <div className="muted">No brief for this period yet. {rep.llm_enabled
          ? "Briefs are written automatically every morning (daily), after Sunday (weekly) and at month end (monthly), or press Write brief."
          : "Turn on the LLM judge to get briefs."}</div>
      ) : (
        <div className="brief">
          <div className="headline">{b.headline}</div>
          {b.summary && <p>{b.summary}</p>}
          {b.top_actions?.length > 0 && (
            <>
              <h3>Fix first</h3>
              <ol className="actions">
                {b.top_actions.map((a: any, n: number) => (
                  <li key={n}>
                    <div><b>{a.issue_id ? <IssueRef id={a.issue_id} title={a.title} /> : a.title}</b>
                      <span className="tag">{a.team}</span>{a.effort && <span className={`tag effort-${a.effort}`}>{a.effort} effort</span>}</div>
                    <div className="muted">{a.why}</div>
                    <div>→ {a.action}</div>
                  </li>
                ))}
              </ol>
            </>
          )}
          <div className="brief-cols">
            {[["Quick wins", b.quick_wins], ["Getting worse", b.worsening], ["Getting better", b.improving]].map(([t, l]: any) =>
              l?.length ? (
                <div key={t}>
                  <h3>{t}</h3>
                  <ul>{l.map((x: any, n: number) => (
                    <li key={n}>{x.issue_id ? <><IssueRef id={x.issue_id} />: <span className="muted">{x.note}</span></> : x.note}</li>
                  ))}</ul>
                </div>
              ) : null)}
            {b.watch?.length > 0 && (
              <div><h3>Keep an eye on</h3><ul>{b.watch.map((w: string, n: number) => <li key={n}>{w}</li>)}</ul></div>
            )}
          </div>
          {b.data_note && <div className="muted small" style={{ marginTop: 8 }}>Note: {b.data_note}</div>}
        </div>
      )}
    </Card>
  );
}

function RagPack() {
  const [pv, setPv] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [made, setMade] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const load = () => getJSON<any>("/api/friction/rag-pack/preview").then(setPv).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, []);
  const files: any[] | null = made?.files ?? pv?.last?.files ?? null;
  return (
    <Card title="Intent RAG pack" info="f_rag_pack" right={
      <button className="btn primary" disabled={busy || !pv?.examples} onClick={async () => {
        setBusy(true); setErr(null);
        try { setMade(await send("POST", "/api/friction/rag-pack", {})); load(); } catch (e: any) { setErr(e.message); }
        setBusy(false);
      }}>{busy ? "Building…" : "Build RAG pack"}</button>}>
      {!pv ? <Loading /> : (
        <>
          <div className="small">
            <b>{pv.examples}</b> examples from <b>{pv.issues}</b> intent issues, ready for the intent prompts
            ({pv.human_agreed} human-agreed, the rest judged with confidence ≥ 0.7). One rule per issue plus its 2–3 clearest examples, so repeated bugs
            don't flood the file. Built automatically every morning too.
          </div>
          <div className="muted small" style={{ marginTop: 6 }}>
            {pv.by_prompt.map((p: any) => `${p.label}: ${p.examples}`).join(" · ")}
          </div>
          {files && (
            <div className="small" style={{ marginTop: 8 }}>
              Latest{pv.last && !made ? ` (${fmtIST(pv.last.created_at)})` : ""}:{" "}
              {files.map((f: any) => (
                <span key={f.jsonl}>
                  <a href={`/api/friction/exports/${f.jsonl}`}>JSONL for retrieval</a>{" · "}
                  <a href={`/api/friction/exports/${f.md}`}>Markdown (rules + examples)</a>
                </span>
              ))}
            </div>
          )}
          {err && <div className="down small">{err}</div>}
        </>
      )}
    </Card>
  );
}

export default function Priorities() {
  const nav = useNavigate();
  const [sp, setSp] = useSearchParams();
  const period = sp.get("period") ?? "last7";
  const [periods, setPeriods] = useState<any>(null);
  const [rep, setRep] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const [bucket, setBucket] = useState("");
  useEffect(() => { getJSON<any>("/api/friction/periods").then(setPeriods).catch((e) => setErr(e.message)); }, [tick]);
  const list: any[] = periods?.[period] ?? [];
  const start = sp.get("start") ?? list[0]?.start;
  const [shown, setShown] = useState("");
  useEffect(() => {
    if (!start) return;
    const key = `${period}:${start}`;
    if (key !== shown) setRep(null);   // new period: show the spinner; same period: refresh in place
    getJSON<any>(`/api/friction/report?period=${period}&start=${start}`)
      .then((d) => { setRep(d); setShown(key); }).catch((e) => setErr(e.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [period, start, tick]);
  // keep the page current while new days are judged and grouped: every minute, and when the tab comes back into view
  useEffect(() => {
    const bump = () => setTick((x) => x + 1);
    const t = setInterval(bump, 60000);
    const vis = () => document.visibilityState === "visible" && bump();
    document.addEventListener("visibilitychange", vis);
    return () => { clearInterval(t); document.removeEventListener("visibilitychange", vis); };
  }, []);
  // poll while issues are being grouped
  useEffect(() => {
    if (!rep?.grouping?.running) return;
    const t = setTimeout(() => setTick((x) => x + 1), 8000);
    return () => clearTimeout(t);
  }, [rep]);
  const set = (k: string, v: string) => { const n = new URLSearchParams(sp); v ? n.set(k, v) : n.delete(k); if (k === "period") n.delete("start"); setSp(n, { replace: true }); };

  if (err) return <ErrorBox error={err} />;
  if (!periods) return <Loading />;
  const cur = list.find((p) => p.start === start);
  const issues = (rep?.issues ?? []).filter((i: any) => !bucket || i.bucket === bucket);
  const p1 = rep?.issues.filter((i: any) => i.priority === "P1") ?? [];
  const affectedUsers = rep ? rep.issues.reduce((s: number, i: any) => s + i.users, 0) : 0;
  return (
    <>
      <div className="toolbar">
        <div className="seg" role="tablist">
          {[["day", "Day"], ["last7", "Last 7 days"], ["week", "Week"], ["month", "Month"]].map(([k, l]) => (
            <button key={k} className={period === k ? "on" : ""} onClick={() => set("period", k)}>{l}</button>
          ))}
        </div>
        <select value={start ?? ""} onChange={(e) => set("start", e.target.value)} aria-label="Period">
          {list.map((p) => (
            <option key={p.start} value={p.start}>
              {periodLabel(period, p)}{p.complete ? "" : " (so far)"} · {p.judged_days}/{p.days} days judged
            </option>
          ))}
        </select>
        {rep?.grouping?.running
          ? <span className="muted small">Grouping new items into issues… {rep.grouping.progress}</span>
          : rep?.coverage?.unassigned_items > 0 && rep.llm_enabled && (
            <button className="btn" onClick={async () => { await send("POST", "/api/friction/issues/group"); setTick((x) => x + 1); }}>
              Group {rep.coverage.unassigned_items} new items into issues
            </button>)}
      </div>

      {!rep ? <Loading /> : (
        <>
          {(rep.coverage.rules_only_days.length > 0) && (
            <div className="notice">
              <b>Partial coverage.</b> {rep.coverage.rules_only_days.length} of {rep.coverage.days} days in this period were scanned by rules only
              ({rep.coverage.rules_only_days.map((d: string) => fmtDate(d)).join(", ")}), so their chats aren't in the issue list yet.
              The LLM judge fills them in; the type counts below already include them.
            </div>
          )}

          <Brief rep={rep} onMade={() => setTick((x) => x + 1)} />

          <div className="kpis">
            <Kpi label="Chats in period" value={fmtNum(rep.baseline.chats)} info="f_period_chats"
                 sub={<span>{fmtNum(rep.baseline.users)} users · {rep.baseline.booking_rate_pct ?? "—"}% booked</span>} />
            <Kpi label="Chats read by the judge" value={fmtNum(rep.read_by_judge)} info="f_judged_chats"
                 sub={<span>of {fmtNum(rep.judged_chats)} scanned · {rep.coverage.judged_days}/{rep.coverage.days} days judged</span>} />
            <Kpi label="Open issues seen" value={fmtNum(rep.issues.length)} info="f_issues_open"
                 sub={<span>{rep.issues.filter((i: any) => i.trend === "new").length} new this period</span>} />
            <Kpi label="P1 issues" value={fmtNum(p1.length)} info="f_priority"
                 sub={<span>{p1.reduce((s: number, i: any) => s + i.users, 0)} users hit</span>} />
            <Kpi label="User–issue hits" value={fmtNum(affectedUsers)} info="f_user_hits"
                 sub={<span>a user hit by 2 issues counts twice</span>} />
          </div>

          <Card title="Issues by priority" info="f_priority" right={
            <div className="seg">
              {[["", "All"], ["technical", "Technical"], ["intent", "Intent"], ["suggestion", "Suggestion"]].map(([k, l]) => (
                <button key={k} className={bucket === k ? "on" : ""} onClick={() => setBucket(k)}>{l}</button>
              ))}
            </div>}>
            <Table rows={issues} onRow={(r) => nav(`/friction/issue/${r.id}`)}
                   empty={rep.coverage.judged_days ? "No grouped issues yet. Group the new items into issues." : "No LLM-judged days in this period yet."}
                   cols={[
                     { key: "priority", label: "Priority", render: (r) => <PriorityBadge p={r.priority} /> },
                     { key: "title", label: "Issue", render: (r) => (
                       <div className="issue-cell">
                         <div>{r.title}{r.regression && <span className="tag bad">Back after fix</span>}{r.status === "in_progress" && <span className="tag">Being fixed</span>}</div>
                         <div className="muted small">{r.theme} · {r.owner_label}</div>
                       </div>) },
                     { key: "users", label: "Users", num: true, info: "f_issue_users" },
                     { key: "chats", label: "Chats", num: true },
                     { key: "trend", label: "vs previous", render: (r) => <Trend cur={r.chats} prev={r.prev_chats} trend={r.trend} /> },
                     { key: "avg_severity", label: "Severity", num: true, info: "f_issue_severity",
                       render: (r) => <span title={`${r.high} high-severity`}>{r.avg_severity >= 2.5 ? "High" : r.avg_severity >= 1.75 ? "Medium" : "Low"}</span> },
                     { key: "bucket", label: "Bucket", render: (r) => <BucketPill b={r.bucket} /> },
                     { key: "fix_lane", label: "Fix goes to", render: (r) => <LanePill lane={r.fix_lane} /> },
                   ]} />
          </Card>

          <div className="grid g2">
            <Card title="Where it hurts: product areas" info="f_themes">
              {rep.by_theme.length ? (
                <Bars data={rep.by_theme.slice(0, 10)} x="theme" layout="vertical" height={Math.max(160, rep.by_theme.slice(0, 10).length * 34)}
                      series={[{ key: "score", label: "Priority score", color: "var(--series-1)" }]} />
              ) : <div className="muted">No grouped issues yet.</div>}
            </Card>
            <Card title="What kind of friction (all chats, rules + judge)" info="f_types_period">
              <Table rows={rep.by_type.slice(0, 12)} cols={[
                { key: "type", label: "Type", render: (r) => <span title={r.description}>{TYPE_LABEL[r.type] ?? r.type}</span> },
                { key: "bucket", label: "Bucket", render: (r) => <BucketPill b={r.bucket} /> },
                { key: "chats", label: "Chats", num: true },
                { key: "prev", label: "vs previous", render: (r) => <Trend cur={r.chats} prev={r.prev_chats} /> },
              ]} />
            </Card>
          </div>

          {(rep.gone_quiet.length > 0 || rep.closed_but_seen.length > 0) && (
            <div className="grid g2">
              <Card title="Gone quiet this period" info="f_gone_quiet">
                <Table rows={rep.gone_quiet} onRow={(r) => nav(`/friction/issue/${r.id}`)} empty="—" cols={[
                  { key: "title", label: "Issue" },
                  { key: "prev_chats", label: "Chats last period", num: true },
                  { key: "status", label: "Status", render: (r) => STATUS_LABEL[r.status] ?? r.status },
                ]} />
              </Card>
              <Card title="Closed, but still seen" info="f_closed_seen">
                <Table rows={rep.closed_but_seen} onRow={(r) => nav(`/friction/issue/${r.id}`)} empty="—" cols={[
                  { key: "title", label: "Issue" },
                  { key: "chats", label: "Chats", num: true },
                  { key: "status", label: "Status", render: (r) => STATUS_LABEL[r.status] ?? r.status },
                ]} />
              </Card>
            </div>
          )}

          <RagPack />
          <div className="muted small">
            {cur && !cur.complete && "This period isn't over yet; numbers grow as days are added. "}
            Priority = users hit × average severity (1–3), ranked: top 3 are P1, next 7 are P2, the rest P3. Issues marked Fixed, Won't fix or Not an issue drop out of the ranking unless they come back after the fix date.
            <Info k="f_priority" />
          </div>
        </>
      )}
    </>
  );
}
