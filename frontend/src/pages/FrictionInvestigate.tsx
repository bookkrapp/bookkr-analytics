// Friction Finder › Code-agent export: the friction log and the investigation brief for the Claude that has the
// Bookkr codebase. Built from the issue register without any LLM call.
import { useEffect, useState } from "react";
import { getJSON } from "../api";
import { Card, ErrorBox, Kpi, Loading, Table } from "../components";
import { fmtIST, fmtNum } from "../format";
import { JOURNEY_LABEL } from "../journeys";

const BUCKETS = [["technical", "⚙️ Technical"], ["intent", "🧠 Intent"], ["suggestion", "💬 Suggestion"]];

const HANDOFF = `You have the Bookkr codebase. Attached are two files from Bookr Analyst:
1. bookkr_investigation_brief_*.md: friction issues found in real user chats, grouped as Bucket > Category > Issue, with counts, the component the judge blamed, and chat evidence.
2. bookkr_friction_log_*.jsonl: every occurrence, one per line, keyed by issue_id.

Work through the brief, highest-impact issues first (technical bucket first). For each issue:
- Find where it happens in the code and explain the actual cause.
- Say whether the judge's component and bucket are right; correct them if not.
- Use the friction log for more examples of the same issue.
Report your analysis per issue. Don't change code yet.`;

export default function Investigate({ journey }: { journey: string }) {
  const [pv, setPv] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const [buckets, setBuckets] = useState<string[]>([]);
  const [closed, setClosed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [made, setMade] = useState<any>(null);
  const [copied, setCopied] = useState(false);
  const load = () => getJSON<any>("/api/friction/investigation/preview", { journey: journey || undefined }).then(setPv).catch((e) => setErr(e.message));
  useEffect(() => { setPv(null); load(); }, [journey]); // eslint-disable-line react-hooks/exhaustive-deps

  const build = async () => {
    setBusy(true); setErr(null);
    try {
      const res = await fetch("/api/friction/investigation", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ journey: journey || undefined, buckets, include_closed: closed }) });
      const d = await res.json();
      if (!res.ok) throw new Error(d.detail ?? res.statusText);
      setMade(d); load();
    } catch (e: any) { setErr(e.message); }
    setBusy(false);
  };

  if (err && !pv) return <ErrorBox error={err} />;
  if (!pv) return <Loading />;
  const toggle = (b: string) => setBuckets((l) => (l.includes(b) ? l.filter((x) => x !== b) : [...l, b]));
  return (
    <>
      <div className="kpis">
        <Kpi label="Friction occurrences" value={fmtNum(pv.occurrences)} sub={`${fmtNum(pv.new_occurrences)} new since the last export`} />
        <Kpi label="Open issues" value={fmtNum(pv.open_issues)} sub={`in ${pv.categories} categories`} />
        <Kpi label="⚙️ Technical" value={fmtNum(pv.by_bucket.technical ?? 0)} sub="occurrences" />
        <Kpi label="🧠 Intent" value={fmtNum(pv.by_bucket.intent ?? 0)} sub="occurrences" />
        <Kpi label="💬 Suggestion" value={fmtNum(pv.by_bucket.suggestion ?? 0)} sub="occurrences" />
      </div>

      <div className="grid g2">
        <Card title="📤 Build the export">
          <div className="small" style={{ lineHeight: 1.6 }}>
            Two files for the Claude that has the Bookkr code{journey ? <> · only <b>{JOURNEY_LABEL[journey]}</b> chats</> : ""}:
            <ul style={{ margin: "6px 0", paddingLeft: 18 }}>
              <li><b>Investigation brief</b> (Markdown): Bucket › Category › Issue, with counts, the component the judge blamed, where it lives in the code and chat evidence. <b>Analysis only, no suggested fixes.</b></li>
              <li><b>Friction log</b> (JSONL): every occurrence ever found, one per line, marked new if found since the last export.</li>
            </ul>
            No Claude call: building it uses no tokens.
          </div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", margin: "10px 0" }}>
            <span className="muted small">Buckets:</span>
            {BUCKETS.map(([k, l]) => (
              <label key={k} className="toggle"><input type="checkbox" checked={buckets.includes(k)} onChange={() => toggle(k)} />{l}</label>
            ))}
            <span className="muted small">(none ticked = all)</span>
          </div>
          <div className="muted small" style={{ marginBottom: 8 }}>
            Tip: export one bucket at a time. A single bucket is a few thousand words the agent can read in one go; all three together can be over 50,000 words.
          </div>
          <label className="toggle small"><input type="checkbox" checked={closed} onChange={(e) => setClosed(e.target.checked)} />
            Include issues marked Fixed or Won't fix (to check they're really gone)</label>
          <div style={{ marginTop: 12, display: "flex", gap: 10, alignItems: "center" }}>
            <button className="btn primary" disabled={busy || !pv.occurrences} onClick={build}>{busy ? "Building…" : "Build export"}</button>
            {!pv.architecture_brief && <span className="muted small">Code locations will appear once config/bookkr_architecture.md exists.</span>}
          </div>
          {err && <div className="down small">{err}</div>}
          {made && (
            <div className="notice ok" style={{ marginTop: 12 }}>
              Built: {made.issues} issues, {made.occurrences} occurrences ({made.new_occurrences} new).{" "}
              <a href={`/api/friction/exports/${made.brief}`}>⬇ Investigation brief</a> · <a href={`/api/friction/exports/${made.log}`}>⬇ Friction log</a>
            </div>
          )}
        </Card>
        <Card title="🤝 Hand it to the code agent">
          <div className="small muted" style={{ marginBottom: 6 }}>Attach both files and paste this in the Claude session that has the Bookkr repo:</div>
          <pre className="json" style={{ maxHeight: 240 }}>{HANDOFF}</pre>
          <button className="btn" style={{ marginTop: 8 }} onClick={() => { navigator.clipboard.writeText(HANDOFF); setCopied(true); setTimeout(() => setCopied(false), 1500); }}>
            {copied ? "Copied" : "Copy message"}
          </button>
        </Card>
      </div>

      <Card title="Previous exports">
        <Table rows={pv.past} empty="No exports yet." cols={[
          { key: "created_at", label: "Built", render: (r) => fmtIST(r.created_at) },
          { key: "scope", label: "Covers", render: (r) => {
            const [j, b] = String(r.scope || "all|all").split("|");
            return <span className="small">{j === "all" ? "All journeys" : JOURNEY_LABEL[j] ?? j} · {b === "all" ? "all buckets" : b.replace(/,/g, " + ")}</span>;
          } },
          { key: "issues", label: "Issues", num: true },
          { key: "occurrences", label: "Occurrences", num: true },
          { key: "files", label: "Files", render: (r) => (
            <span className="small"><a href={`/api/friction/exports/${r.files.brief}`}>Brief</a> · <a href={`/api/friction/exports/${r.files.log}`}>Log</a></span>) },
        ]} />
      </Card>
    </>
  );
}
