import { Fragment, useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { getJSON } from "../api";
import { Bars, Card, ErrorBox, Info, Kpi, Loading, SevBadge, Table } from "../components";
import { fmtDate, fmtDateTime, fmtNum, fmtUSD } from "../format";
import {
  BUCKETS, BUCKET_LABEL, BUCKET_SHORT, BucketPill, LANE_LABEL, LanePill, OWNER_LABEL, TYPE_BUCKET, TYPE_LABEL, shortModel,
} from "../frictionTaxonomy";
import Priorities, { RagPack } from "./FrictionReport";
import { PageHeader, useSub } from "../nav";
import JourneyPick, { useJourney } from "../JourneyPick";
import Investigate from "./FrictionInvestigate";

export { TYPE_LABEL };

export function useReviewer(): [string, (v: string) => void] {
  const [name, setName] = useState(() => {
    try { return localStorage.getItem("ba.reviewer") ?? ""; } catch { return ""; }
  });
  const set = (v: string) => {
    setName(v);
    try { localStorage.setItem("ba.reviewer", v); } catch { /* ignore */ }
  };
  return [name, set];
}

async function post(path: string, body: any) {
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail ?? res.statusText);
  return data;
}

function useLoad<T = any>(path: string | null, deps: any[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!path) return;
    let alive = true;
    getJSON<T>(path).then((d) => alive && (setData(d), setError(null))).catch((e) => alive && setError(e.message));
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, tick, ...deps]);
  return { data, error, reload: () => setTick((t) => t + 1) };
}

function RunPanel({ status, day, setDay, onDone }: any) {
  const [useLlm, setUseLlm] = useState<boolean>(status.llm_enabled);
  const [err, setErr] = useState<string | null>(null);
  const [running, setRunning] = useState<boolean>(status.running);
  const [progress, setProgress] = useState<string | null>(null);
  useEffect(() => {
    if (!running) return;
    const t = setInterval(async () => {
      const s = await getJSON("/api/friction/status").catch(() => null);
      if (!s) return;
      setProgress(s.latest_run?.progress ?? null);
      if (!s.running) {
        setRunning(false);
        if (s.latest_run?.status === "failed") setErr(s.latest_run.error);
        onDone();
      }
    }, 1500);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [running]);
  const info = status.days.find((d: any) => d.day === day);
  return (
    <Card title="Analyse a day" info="f_scanned">
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
        <select value={day} onChange={(e) => setDay(e.target.value)} aria-label="Day">
          {status.days.map((d: any) => (
            <option key={d.day} value={d.day}>
              {fmtDate(d.day)} · {d.chats} chats · {d.run ? (d.review?.pending ? `${d.review.pending} to review` : "analysed") : "not analysed"}
            </option>
          ))}
        </select>
        <label className="toggle" title={status.llm_enabled ? status.provider_label : "Log in to Claude Code or add ANTHROPIC_API_KEY to enable"}>
          <input type="checkbox" checked={useLlm && status.llm_enabled} disabled={!status.llm_enabled}
                 onChange={(e) => setUseLlm(e.target.checked)} />
          Use LLM judge ({status.model})
        </label>
        <button className="btn primary" disabled={running || !day} onClick={async () => {
          setErr(null);
          try {
            await post("/api/friction/runs", { day, use_llm: useLlm && status.llm_enabled });
            setProgress("Starting…");
            setRunning(true);
          } catch (e: any) { setErr(e.message); }
        }}>{running ? "Analysing…" : info?.run ? "Re-run analysis" : "Run analysis"}</button>
        {running && <span className="muted small">{progress}</span>}
        {err && <span className="down small">{err}</span>}
      </div>
      {info?.run && !running && (
        <div className="muted small" style={{ marginTop: 8 }}>
          Last run {fmtDateTime(info.run.finished_at?.slice(0, 19))} UTC · {info.run.mode === "llm" ? `LLM judge (${info.run.model})` : "rules only"}
          {" · "}{info.run.sessions_scanned} chats scanned · {info.run.sessions_flagged} flagged
          {info.run.sessions_skipped_reviewed ? ` · ${info.run.sessions_skipped_reviewed} already reviewed (kept)` : ""}
          {info.run.cost_usd ? ` · ${fmtUSD(info.run.cost_usd)}` : ""}
          {" · "}Re-running keeps every chat that has already been reviewed.
        </div>
      )}
    </Card>
  );
}

function Queue({ status, onStatus }: { status: any; onStatus: () => void }) {
  const nav = useNavigate();
  const [sp, setSp] = useSearchParams();
  const firstDay = status.days.find((d: any) => d.review?.pending)?.day ?? status.days[0]?.day ?? "";
  const day = sp.get("day") ?? firstDay;
  const st = sp.get("status") ?? "";
  const type = sp.get("type") ?? "";
  const bucket = sp.get("bucket") ?? "";
  const lane = sp.get("lane") ?? "";
  const journey = sp.get("journey") ?? "";
  const set = (k: string, v: string) => {
    const n = new URLSearchParams(sp);
    v ? n.set(k, v) : n.delete(k);
    if (k === "bucket" && v && type && TYPE_BUCKET[type] && TYPE_BUCKET[type] !== v) n.delete("type");
    setSp(n, { replace: true });
  };
  const qs = new URLSearchParams({
    day, ...(st ? { status: st } : {}), ...(type ? { type } : {}), ...(bucket ? { bucket } : {}), ...(lane ? { lane } : {}),
    ...(journey ? { journey } : {}),
  }).toString();
  const list = useLoad<any[]>(day ? `/api/friction/findings?${qs}` : null);
  const sum = useLoad<any>(day ? `/api/friction/summary?day=${day}` : null);
  const reload = () => { list.reload(); sum.reload(); onStatus(); };
  const t = sum.data?.totals;
  const run = sum.data?.run;
  const byBucket: any[] = sum.data?.by_bucket ?? [];
  const typesShown = (sum.data?.by_type ?? []).filter((b: any) => !bucket || b.bucket === bucket || b.bucket == null);
  return (
    <>
      <RunPanel status={status} day={day} setDay={(d: string) => set("day", d)} onDone={reload} />
      {t && (
        <div className="kpis">
          <Kpi label="Chats analysed" info="f_scanned" value={fmtNum(run?.sessions_scanned ?? 0)} />
          <Kpi label="Chats with friction" info="f_flagged" value={fmtNum(t.findings)}
               sub={run?.sessions_scanned ? `${Math.round((100 * t.findings) / run.sessions_scanned)}% of chats` : undefined} />
          <Kpi label="Friction items" info="f_items" value={fmtNum(t.items)} />
          <Kpi label="Waiting for review" info="f_pending" value={fmtNum(t.pending)} />
          <Kpi label="Agreed" info="f_agreed" value={fmtNum(t.agreed)} sub={`${t.not_friction} not friction · ${t.skipped} skipped`} />
          <Kpi label="Finder precision" info="f_precision" value={t.precision_pct != null ? `${t.precision_pct}%` : "—"} />
        </div>
      )}
      {t && (
        <div className="buckets four" role="group" aria-label="Filter by bucket">
          <button type="button" className={`bucket-card all${!bucket ? " on" : ""}`} aria-pressed={!bucket} onClick={() => set("bucket", "")}>
            <span className="lbl">All friction<Info k="f_bucket" /></span>
            <span className="big">{fmtNum(t.items)}</span>
            <span className="small muted">{fmtNum(t.findings)} chats · {fmtNum(t.pending)} to review</span>
          </button>
          {byBucket.map((b) => (
            <button key={b.bucket} type="button" className={`bucket-card ${b.bucket}${bucket === b.bucket ? " on" : ""}`}
                    aria-pressed={bucket === b.bucket} onClick={() => set("bucket", bucket === b.bucket ? "" : b.bucket)}>
              <span className="lbl">{BUCKET_LABEL[b.bucket]}<Info k={`f_bucket_${b.bucket}`} /></span>
              <span className="big">{fmtNum(b.items)}</span>
              <span className="small muted">{BUCKET_SHORT[b.bucket]}</span>
              <span className="small muted">{fmtNum(b.chats)} chats · {fmtNum(b.pending)} to review
                {b.precision_pct != null ? ` · ${b.precision_pct}% precision` : ""}</span>
            </button>
          ))}
        </div>
      )}
      <div className="grid">
        <Card title={bucket ? `Chats to review · ${BUCKET_LABEL[bucket]}` : "Chats to review"} right={
          <span style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <select value={st} onChange={(e) => set("status", e.target.value)} aria-label="Status">
              <option value="">All</option>
              <option value="pending">Waiting for review</option>
              <option value="reviewed">Reviewed, not submitted</option>
              <option value="submitted">Submitted</option>
            </select>
            <select value={type} onChange={(e) => set("type", e.target.value)} aria-label="Type">
              <option value="">All types</option>
              {BUCKETS.filter((b) => !bucket || b === bucket).map((b) => (
                <optgroup key={b} label={BUCKET_LABEL[b]}>
                  {Object.entries(TYPE_BUCKET).filter(([, tb]) => tb === b).map(([k]) => <option key={k} value={k}>{TYPE_LABEL[k]}</option>)}
                </optgroup>
              ))}
              <optgroup label="Any bucket">
                {Object.entries(TYPE_BUCKET).filter(([, tb]) => tb == null).map(([k]) => <option key={k} value={k}>{TYPE_LABEL[k]}</option>)}
              </optgroup>
            </select>
            <select value={lane} onChange={(e) => set("lane", e.target.value)} aria-label="Fix lane">
              <option value="">All fix lanes</option>
              {Object.entries(LANE_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </span>}>
          {list.error ? <ErrorBox error={list.error} /> : !list.data ? <Loading /> : (
            <Table
              rows={list.data}
              empty={run ? "No friction found for this day with these filters." : "This day hasn't been analysed yet. Click Run analysis above."}
              onRow={(r) => nav(`/friction/${r.id}`)}
              cols={[
                { key: "severity", label: "Severity", render: (r) => <SevBadge sev={r.severity} /> },
                { key: "title", label: "Chat", render: (r) => (
                  <span className="wrap" style={{ minWidth: 220 }}>
                    <b>{r.title || "Untitled chat"}</b>
                    <span className="small muted" style={{ display: "block" }}>{r.summary}</span>
                  </span>) },
                { key: "buckets", label: "Bucket", info: "f_bucket", render: (r) => (
                  <span style={{ display: "flex", gap: 4, flexDirection: "column", alignItems: "flex-start" }}>
                    {r.buckets.map((b: string) => <BucketPill key={b} b={b} />)}
                  </span>) },
                { key: "types", label: "Friction", render: (r) => (
                  <span className="wrap" style={{ display: "flex", gap: 4, flexWrap: "wrap", minWidth: 140 }}>
                    {r.types.map((x: string) => <span key={x} className="chip">{TYPE_LABEL[x] ?? x}</span>)}
                  </span>) },
                { key: "lanes", label: "Fix goes to", info: "f_lane", render: (r) => (
                  <span style={{ display: "flex", gap: 4, flexWrap: "wrap", minWidth: 110 }}>
                    {r.lanes.map((l: string) => <LanePill key={l} lane={l} />)}
                  </span>) },
                { key: "n_items", label: "Items", num: true },
                { key: "score", label: "Score", num: true, info: "f_score" },
                { key: "status", label: "Review", render: (r) => r.status === "submitted"
                    ? <span className="badge sev-ok"><span className="dot" />Submitted</span>
                    : r.status === "reviewed" ? <span className="badge sev-info"><span className="dot" />Reviewed</span>
                    : <span className="badge sev-warning"><span className="dot" />{r.n_pending} pending</span> },
                { key: "chat", label: "", render: (r) => (
                  <Link to={`/conversations/${r.session_id}`} onClick={(e) => e.stopPropagation()} className="small">Open chat ↗</Link>) },
              ]}
            />
          )}
        </Card>
        <div style={{ display: "flex", flexDirection: "column", gap: 16, minWidth: 0 }}>
          <Card title={bucket ? `Friction by type · ${BUCKET_LABEL[bucket]}` : "Friction by type"}>
            {typesShown.length ? (
              <Bars data={typesShown.map((b: any) => ({ ...b, label: TYPE_LABEL[b.type] ?? b.type }))} x="label" layout="vertical"
                    height={Math.max(140, 36 * typesShown.length)}
                    series={[{ key: "items", label: "Items", color: bucket ? `var(--b-${bucket})` : "var(--series-1)" }]} />
            ) : <span className="muted">Nothing yet.</span>}
          </Card>
          <Card title="Where the fixes go" info="f_lane">
            {sum.data?.by_lane?.length ? (
              <Bars data={sum.data.by_lane.map((b: any) => ({ ...b, label: LANE_LABEL[b.fix_lane] ?? b.fix_lane }))} x="label" layout="vertical"
                    height={Math.max(120, 36 * sum.data.by_lane.length)}
                    series={[{ key: "items", label: "Items", color: "var(--series-1)" }, { key: "agreed", label: "Agreed", color: "var(--series-3)" }]} />
            ) : <span className="muted">Nothing yet.</span>}
          </Card>
        </div>
      </div>
    </>
  );
}

function Dataset() {
  const ds = useLoad<any>("/api/friction/dataset");
  const [reviewer] = useReviewer();
  const [busy, setBusy] = useState(false);
  const [last, setLast] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const [group, setGroup] = useState("");
  if (ds.error) return <ErrorBox error={ds.error} />;
  if (!ds.data) return <Loading />;
  const d = ds.data;
  const groups: Record<string, number> = Object.fromEntries((d.groups ?? []).map((g: any) => [g.group, g.count]));
  const preview = d.examples.filter((e: any) => !group || e.group === group);
  const GROUP_HELP: Record<string, string> = {
    intent_rag: "Intent items an LLM owns (extractor, router, hotel turn scope). Few-shot / RAG examples, plus rules for the prompt.",
    response_quality: "Agent-suggestion items an LLM owns (replies, plans, web answers, chips). Examples and rules for those prompts.",
    finetune: "Turns the fine-tuned (LoRA) extractor got wrong, or structural misses. Input for the next training round.",
    engineering: "Technical items and anything a fixed rule, a service or product owns. Never used as training data.",
  };
  const fileRow = (f: any) => (
    <span style={{ display: "inline-flex", gap: 10 }}>
      <a href={`/api/friction/exports/${f.jsonl}`}>JSONL</a><a href={`/api/friction/exports/${f.md}`}>Markdown</a>
    </span>
  );
  return (
    <>
      <div className="kpis">
        <Kpi label="Agreed items" info="f_examples" value={fmtNum(d.examples.length)} />
        <Kpi label="Intent RAG" info="f_group_intent_rag" value={fmtNum(groups.intent_rag ?? 0)} />
        <Kpi label="Response quality" info="f_group_response_quality" value={fmtNum(groups.response_quality ?? 0)} />
        <Kpi label="Fine-tune queue" info="f_group_finetune" value={fmtNum(groups.finetune ?? 0)} />
        <Kpi label="Engineering list" info="f_group_engineering" value={fmtNum(groups.engineering ?? 0)} />
        <Kpi label="New since last files" info="f_new_since" value={fmtNum(d.new_since_last_export)}
             sub={`${fmtNum(d.pending_items)} still waiting for review`} />
      </div>
      <Card title="Generate the files">
        <p style={{ marginTop: 0 }}>
          Every <b>agreed</b> item goes into exactly one file, decided by its fix lane. Each file comes as <b>.jsonl</b> (one item per line,
          for a vector store or few-shot retrieval) and <b>.md</b> (rules and readable examples, grouped by the component that owns them).
        </p>
        <ul className="small" style={{ marginTop: 0 }}>
          {(d.groups ?? []).map((g: any) => <li key={g.group}><b>{g.label}</b> ({g.count}): {GROUP_HELP[g.group]}</li>)}
        </ul>
        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <button className="btn primary" disabled={busy || !d.examples.length} onClick={async () => {
            setBusy(true); setErr(null);
            try { setLast(await post("/api/friction/export", { reviewer })); ds.reload(); } catch (e: any) { setErr(e.message); }
            setBusy(false);
          }}>{busy ? "Generating…" : `Generate files (${d.examples.length} items)`}</button>
          {!d.examples.length && <span className="muted small">Agree with at least one item in the review queue first.</span>}
          {err && <span className="down small">{err}</span>}
        </div>
        {last && (
          <div className="up small" style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 2 }}>
            {last.files.map((f: any) => <span key={f.group}>Created {f.label} ({f.count}): {fileRow(f)}</span>)}
          </div>
        )}
      </Card>
      {(d.finetune_candidates?.length > 0 || d.lora_signals?.models?.length > 0) && (
        <div className="grid g2">
          <Card title="Fine-tune candidates" info="f_ft_candidates">
            <Table rows={d.finetune_candidates ?? []} empty="No RAG clusters yet." cols={[
              { key: "owner", label: "Owner", render: (r) => OWNER_LABEL[r.owner] ?? r.owner },
              { key: "type", label: "Type", render: (r) => TYPE_LABEL[r.type] ?? r.type },
              { key: "items", label: "Agreed", num: true },
              { key: "ready", label: "Status", render: (r) => r.ready
                  ? <span className="badge sev-warning"><span className="dot" />Consider fine-tuning</span>
                  : <span className="muted small">{r.items} of {r.threshold}</span> },
            ]} />
          </Card>
          <Card title={`Intent extractor, last ${d.lora_signals?.days ?? 30} days of data`} info="f_lora_signals">
            <Table rows={d.lora_signals?.models ?? []} empty="No extractor calls in the data." cols={[
              { key: "model", label: "Model", render: (r) => <span title={r.model}>{shortModel(r.model)}{r.is_lora ? " (fine-tuned)" : ""}</span> },
              { key: "calls", label: "Calls", num: true },
              { key: "failed", label: "Failed", num: true },
              { key: "structural", label: "Schema / parse", num: true, info: "f_structural" },
            ]} />
          </Card>
        </div>
      )}
      <div className="grid g2">
        <Card title="Previous files">
          <Table rows={d.exports} empty="No files generated yet." cols={[
            { key: "created_at", label: "Created (UTC)", render: (r) => fmtDateTime(r.created_at.slice(0, 19)) },
            { key: "created_by", label: "By", render: (r) => r.created_by || "—" },
            { key: "examples", label: "Items", num: true },
            { key: "dl", label: "Download", render: (r) => r.files ? (
              <span style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                {r.files.map((f: any) => <span key={f.group} className="small">{f.label} ({f.count}): {fileRow(f)}</span>)}
              </span>
            ) : r.jsonl_file ? fileRow({ jsonl: r.jsonl_file, md: r.md_file }) : "—" },
          ]} />
        </Card>
        <Card title="Reviews by person">
          <Table rows={d.by_reviewer} empty="No reviews yet." cols={[
            { key: "reviewer", label: "Reviewer" },
            { key: "reviewed", label: "Reviewed", num: true },
            { key: "agreed", label: "Agreed", num: true },
          ]} />
        </Card>
      </div>
      {d.rules.length > 0 && (
        <Card title="Guidance rules in the training files" info="f_rules">
          <ul style={{ margin: 0, paddingLeft: 18 }}>{d.rules.map((r: string) => <li key={r}>{r}</li>)}</ul>
        </Card>
      )}
      <Card title="Items preview" right={
        <select value={group} onChange={(e) => setGroup(e.target.value)} aria-label="File">
          <option value="">All files</option>
          {(d.groups ?? []).map((g: any) => <option key={g.group} value={g.group}>{g.label} ({g.count})</option>)}
        </select>}>
        {preview.length ? preview.slice(0, 30).map((e: any) => (
          <div key={e.id} className="item-card" style={{ marginBottom: 10 }}>
            <div className="item-head">
              <BucketPill b={e.bucket} />
              <span className="chip">{TYPE_LABEL[e.friction_type] ?? e.friction_type}</span>
              <LanePill lane={e.fix_lane} />
              <span className="muted small">{OWNER_LABEL[e.owner] ?? e.owner}</span>
              <SevBadge sev={e.severity} />
              <span className="muted small">{e.flow} · {fmtDate(e.source.day)} · reviewed by {e.source.reviewed_by}</span>
              <Link className="small" to={`/conversations/${e.source.session_id}`} style={{ marginLeft: "auto" }}>Open chat ↗</Link>
            </div>
            <div style={{ marginTop: 6 }}><span className="muted small">User said:</span> “{e.user_message}”</div>
            <div className="small" style={{ marginTop: 4 }}><span className="muted">What went wrong:</span> {e.what_went_wrong}</div>
            {e.expected_interpretation && <pre className="json">{typeof e.expected_interpretation === "string" ? e.expected_interpretation : JSON.stringify(e.expected_interpretation, null, 2)}</pre>}
            {e.guidance && <div className="small" style={{ marginTop: 4 }}><span className="muted">Rule:</span> {e.guidance}</div>}
          </div>
        )) : <span className="muted">No agreed items {group ? "in this file " : ""}yet.</span>}
      </Card>
    </>
  );
}

function Rules({ status }: { status: any }) {
  return (
    <>
      <Card title="Mandatory fields (rule R1)">
        <p style={{ marginTop: 0 }}>Bookkr has to collect these before it can move on. When the assistant <b>asks</b> for one of them, even a second time, the
          finder <b>does not</b> count it as friction. When the <b>user has to repeat</b> a value they had already given, that <b>is</b> flagged as
          “Repeated info”, because intent extraction missed it.</p>
        <dl className="kv">
          {Object.entries(status.mandatory_fields).map(([flow, f]: any) => (
            <Fragment key={flow}><dt>{flow.replace("_", " ")}</dt><dd>{f.join(", ")}</dd></Fragment>
          ))}
        </dl>
        <div className="muted small" style={{ marginTop: 8 }}>Edit in <code>config/friction.json</code>. This list was inferred from the assistant's questions in the data and still needs the team's confirmation (TRACKER.md, Q1).</div>
      </Card>
      <Card title="Three buckets (rule R8)">
        <p style={{ marginTop: 0 }}>Each item is sorted by <b>what the user ran into</b>, then by <b>which component made the first wrong decision</b>.
          The owner decides where the fix goes. Only items an LLM owns reach the training files. The full map is in <code>SYSTEM_MAP.html</code>.</p>
        <div className="buckets">
          {(status.taxonomy?.buckets ?? []).map((b: any) => (
            <div key={b.key} className={`bucket-card ${b.key}`} style={{ cursor: "default" }}>
              <span className="lbl">{b.label}</span>
              <span className="small">{b.description}</span>
              <ul className="small" style={{ margin: "6px 0 0", paddingLeft: 16, display: "flex", flexDirection: "column", gap: 4 }}>
                {(status.taxonomy?.types ?? []).filter((t: any) => t.bucket === b.key).map((t: any) => (
                  <li key={t.key}><b>{TYPE_LABEL[t.key] ?? t.key}</b>: <span className="muted">{t.description}</span></li>
                ))}
              </ul>
            </div>
          ))}
        </div>
        <dl className="kv" style={{ marginTop: 10 }}>
          {(status.taxonomy?.types ?? []).filter((t: any) => !t.bucket).map((t: any) => (
            <Fragment key={t.key}><dt>{TYPE_LABEL[t.key] ?? t.key} (any bucket)</dt><dd>{t.description}</dd></Fragment>
          ))}
        </dl>
      </Card>
      <div className="grid g2">
        <Card title="Where the fix goes" info="f_lane">
          <dl className="kv">
            {(status.taxonomy?.fix_lanes ?? []).map((l: any) => (
              <Fragment key={l.key}><dt><LanePill lane={l.key} /></dt><dd>{l.description}</dd></Fragment>
            ))}
          </dl>
          <ol className="small" style={{ margin: "10px 0 0", paddingLeft: 18 }}>
            <li>Technical, or a fixed rule / service owns it → Engineering.</li>
            <li>Product owns it → Product. Owner unclear → Needs triage.</li>
            <li>An LLM owns it and the lesson must always hold → Prompt change.</li>
            <li>The intent extractor owns it and the fine-tuned LoRA model served that turn (or the miss is structural) → Fine-tune queue.</li>
            <li>Any other LLM-owned item → RAG example. A RAG cluster with {status.finetune_cluster_min ?? 20}+ agreed items is flagged as a fine-tune candidate.</li>
          </ol>
        </Card>
        <Card title="Owners">
          <dl className="kv">
            {(status.taxonomy?.owners ?? []).map((o: any) => (
              <Fragment key={o.key}><dt>{o.label}</dt><dd className="muted">{o.kind === "llm" ? "LLM task" : o.kind === "rule" ? "Fixed rule" : o.kind === "service" ? "Service" : o.kind === "product" ? "Product" : "Unknown"}</dd></Fragment>
            ))}
          </dl>
        </Card>
      </div>
      <Card title="How a day is analysed">
        <ol style={{ margin: 0, paddingLeft: 18 }}>
          <li>Every chat whose last user message falls on that day (IST) is loaded with its full history. Data is already masked: no emails, phones or ID numbers.</li>
          <li><b>Rule signals</b> score each chat. Intent: user repeated info, corrections, Edit on the confirmation card, repeated searches, a search that differs from the extracted intent, long slot-filling. Technical: error replies, failed LLM calls the user saw, failed payments and bookings, expired fares, slow replies. Agent suggestion: "coming soon" replies, unsupported-feature requests, filters changed right after results. Asking for a mandatory field scores 0 (R1).</li>
          <li>{status.llm_enabled
            ? <>Chats with a score of at least 1 are read by the <b>LLM judge</b> ({status.model}). For each friction item it writes the bucket, the owner, an explanation, the correct interpretation (or what a good response would have been) and a lesson. It is told about R1 and about every pattern a reviewer marked “Not friction”.</>
            : <>The <b>LLM judge is off</b> (no Claude Code login and no <code>ANTHROPIC_API_KEY</code>), so chats with a score of 2 or more become findings straight from the rule signals, with each signal's default bucket and owner. Explanations are templates; reviewers can correct the routing and type the interpretation and lesson.</>}</li>
          <li>The fix lane follows from the bucket, the owner and the model that served the turn (from <code>LlmCallLog</code>).</li>
          <li>You review each item and can correct its bucket, owner and lane. Agreed items go into the file for their lane.</li>
        </ol>
      </Card>
    </>
  );
}

export default function Friction() {
  const sub = useSub("/friction");
  const status = useLoad<any>("/api/friction/status");
  const judge = status.data && (
    <span className={`judge-pill ${status.data.llm_enabled ? "on" : ""}`}
          title={status.data.llm_enabled ? `${status.data.model} via ${status.data.provider_label}` : "Findings come from rule signals only"}>
      {status.data.llm_enabled ? `🤖 Claude judge on (${status.data.provider_label})` : "📏 Rules-only on this server"}
      <Info text={status.data.llm_enabled
        ? "Chats are read by Claude, which writes the explanation, owner and lesson for each friction item."
        : "No Claude Code login or ANTHROPIC_API_KEY on this server, so new findings come from rule signals only. Days already judged by Claude (from a laptop with the Claude subscription) keep their Claude findings and show here as usual."} />
    </span>
  );
  const [journey, setJourney] = useJourney(!!sub.applies.journey);
  return (
    <>
      <PageHeader path="/friction" sub={sub} right={judge}>
        {sub.applies.journey && <JourneyPick journey={journey} set={setJourney} />}
      </PageHeader>
      {status.error ? <ErrorBox error={status.error} /> : !status.data ? <Loading /> : (
        <>
          {(sub.key === "brief" || sub.key === "issues" || sub.key === "patterns") && <Priorities view={sub.key} />}
          {sub.key === "queue" && <Queue status={status.data} onStatus={status.reload} />}
          {sub.key === "investigate" && <Investigate journey={journey} />}
          {sub.key === "dataset" && <><RagPack /><Dataset /></>}
          {sub.key === "rules" && <Rules status={status.data} />}
        </>
      )}
    </>
  );
}
