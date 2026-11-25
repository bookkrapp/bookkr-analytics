// Quality & reliability (old ops board parity): reply ratings with comments, server errors by endpoint,
// service status checks and the TripJack hotel catalogue sync.
import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { getJSON, useApi } from "../api";
import { Bars, Card, ErrorBox, KpiC, Loading, Table } from "../components";
import { fmtIST, fmtNum } from "../format";
import { PageHeader, useSub } from "../nav";

const dayFmt = (d: string) => new Date(d + "T00:00:00").toLocaleDateString("en-IN", { day: "2-digit", month: "short" });

function Services() {
  const [d, setD] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const load = (force = false) => {
    setBusy(true);
    getJSON<any>("/api/services", force ? { force: true } : {}).then(setD).catch((e) => setErr(e.message)).finally(() => setBusy(false));
  };
  useEffect(() => { load(); }, []);
  if (err) return <ErrorBox error={err} />;
  if (!d) return <Loading />;
  const label: Record<string, string> = { up: "UP", slow: "SLOW", down: "DOWN", unset: "NOT SET UP" };
  return (
    <Card title="🩺 Services" right={<button className="btn" disabled={busy} onClick={() => load(true)}>{busy ? "Checking…" : "Check again"}</button>}>
      <div className={`overall ${d.overall.level}`} style={{ marginBottom: 10 }}>● {d.overall.label}</div>
      <Table rows={d.rows} cols={[
        { key: "name", label: "Service", render: (r) => <>{r.name}{r.critical && <span className="tag">critical</span>}</> },
        { key: "status", label: "Status", render: (r) => <span className={`svc ${r.status}`}><i />{label[r.status] ?? r.status}</span> },
        { key: "latency_ms", label: "Response", num: true, render: (r) => (r.latency_ms != null ? `${fmtNum(r.latency_ms)} ms` : "—") },
        { key: "credits", label: "Credits left", num: true, render: (r) => (r.left != null ? `${fmtNum(r.left)}${r.limit ? ` / ${fmtNum(r.limit)}` : ""}` : "—") },
        { key: "detail", label: "Detail", render: (r) => <span className="small wrap">{r.detail ?? ""}</span> },
        { key: "checked_at", label: "Checked", render: (r) => fmtIST(r.checked_at) },
      ]} />
      {d.not_possible.map((x: any) => <div key={x.id} className="muted small" style={{ marginTop: 6 }}>{x.name}: not checked. {x.detail}.</div>)}
      <div className="muted small" style={{ marginTop: 6 }}>Each row reads NOT SET UP until its URL or key is added to the server's environment (Render → Environment).</div>
    </Card>
  );
}

export default function Quality() {
  const sub = useSub("/quality");
  const nav = useNavigate();
  const { data, error, loading } = useApi("/api/quality");
  const head = <PageHeader path="/quality" sub={sub} />;
  if (sub.key === "operations") {
    return (
      <>
        {head}
        <Services />
        {error ? <ErrorBox error={error} /> : !data ? <Loading /> : (
          <Card title="🏨 TripJack hotel catalogue sync" right={data.hotel_sync.stale
            ? <span className="badge sev-critical"><span className="dot" />No successful run in 26 h</span>
            : <span className="badge sev-ok"><span className="dot" />Up to date</span>}>
            <Table rows={data.hotel_sync.runs} empty="No sync runs recorded." cols={[
              { key: "started_local", label: "Started (IST)", render: (r) => fmtIST(r.started_local) },
              { key: "finished_local", label: "Finished (IST)", render: (r) => fmtIST(r.finished_local) },
              { key: "status", label: "Status", render: (r) => <span className={`svc ${r.status === "SUCCEEDED" ? "up" : r.status === "FAILED" ? "down" : "slow"}`}><i />{r.status}</span> },
              { key: "trigger", label: "Trigger" },
              { key: "total_hotels", label: "Hotels", num: true, render: (r) => fmtNum(r.total_hotels) },
              { key: "indexed_cities", label: "Cities", num: true, render: (r) => fmtNum(r.indexed_cities) },
              { key: "content_missing_count", label: "Missing content", num: true, render: (r) => fmtNum(r.content_missing_count) },
              { key: "error", label: "Error", render: (r) => <span className="small wrap" style={{ minWidth: 200 }}>{r.error ?? ""}</span> },
            ]} />
          </Card>
        )}
      </>
    );
  }
  if (error) return <>{head}<ErrorBox error={error} /></>;
  if (loading && !data) return <>{head}<Loading /></>;
  const n = data.numbers;
  return (
    <>
      {head}
      {sub.key === "ratings" && (
        <>
          <div className="kpis">
            <KpiC label="👍 Thumbs up" n={n.thumbs_up} fmt={fmtNum} />
            <KpiC label="👎 Thumbs down" n={n.thumbs_down} fmt={fmtNum} tone={n.thumbs_down.value ? "warn" : undefined} />
            <KpiC label="Thumbs-down share" n={n.down_pct} fmt={(v) => `${v}%`} />
          </div>
          <Card title="Ratings and comments" right={<span className="muted small">Click a rating to open the chat at that reply</span>}>
            <Table rows={data.comments} empty="No ratings in this period."
                   onRow={(r) => r.chat_session_id && nav(`/conversations/${r.chat_session_id}#${r.message_id}`)} cols={[
              { key: "created_local", label: "When (IST)", render: (r) => fmtIST(r.created_local) },
              { key: "is_positive", label: "Rating", render: (r) => (r.is_positive ? "👍" : "👎") },
              { key: "comment", label: "Comment (masked)", render: (r) => <span className="wrap" style={{ minWidth: 300 }}>{r.comment || <span className="muted">no comment</span>}</span> },
              { key: "chat", label: "Chat", render: (r) => (r.chat_session_id ? <Link to={`/conversations/${r.chat_session_id}`} onClick={(e) => e.stopPropagation()}>Open chat →</Link> : "—") },
            ]} />
          </Card>
          <div className="muted small">Thumbs-down chats are also read by the Friction Finder: <Link to="/friction/issues">see the issues</Link>.</div>
        </>
      )}
      {sub.key === "errors" && (
        <>
          <div className="kpis">
            <KpiC label="Server errors (5xx)" n={n.server_errors} fmt={fmtNum} tone={n.server_errors.value ? "bad" : undefined} info="server_errors" />
            <KpiC label="Rejected requests (4xx)" n={n.rejected} fmt={fmtNum} />
          </div>
          <Card title="Errors per day">
            <Bars data={data.daily} x="date" xFmt={dayFmt} series={[
              { key: "server_errors", label: "Server errors", color: "var(--critical)" }, { key: "rejected", label: "Rejected", color: "var(--warning)" }]} />
          </Card>
          <Card title="By endpoint" right={<span className="muted small">Ids in paths are shown as :id</span>}>
            <Table rows={data.endpoints} empty="No errors in this period." cols={[
              { key: "event_name", label: "Kind", render: (r) => (r.event_name === "server_error" ? "5xx server error" : "4xx rejected") },
              { key: "endpoint", label: "Endpoint", render: (r) => <code className="small">{r.endpoint}</code> },
              { key: "status", label: "Status" },
              { key: "error_type", label: "Error type", render: (r) => r.error_type ?? "—" },
              { key: "errors", label: "Errors", num: true },
              { key: "users", label: "Users hit", num: true },
            ]} />
          </Card>
        </>
      )}
    </>
  );
}
