import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { getJSON } from "../api";
import { Card, ErrorBox, Info, Loading, SevBadge } from "../components";
import { fmtDate, fmtTime } from "../format";
import {
  BUCKETS, BUCKET_LABEL, BucketPill, LANE_LABEL, LanePill, OWNER_GROUPS, OWNER_LABEL, TYPE_LABEL, laneFor, shortModel,
} from "../frictionTaxonomy";
import { useReviewer } from "./Friction";

type Draft = {
  verdict: string; note: string; corrected_interpretation: string; corrected_guidance: string;
  bucket: string; owner: string; lesson_is_rule: boolean; lane: string; // lane "" = follow bucket and owner
};

const INTERP_LABEL: Record<string, [string, string]> = {
  intent: ["Correct interpretation (JSON)", 'e.g. {"origin": {"name": "Bangalore", "iata": "BLR"}, "departure": {"exact_date": "2026-12-15"}}'],
  suggestion: ["What a good response would have offered", "e.g. Show non-stop morning flights first, since the user asked for them"],
  technical: ["Expected behaviour (optional)", "e.g. The search should have returned Tripjack fares"],
};
const GUIDANCE_LABEL: Record<string, string> = {
  intent: "Lesson for the owner",
  suggestion: "Lesson for the owner",
  technical: "Engineering note",
};

const VERDICTS: [string, string][] = [["agree", "Agree: add to dataset"], ["not_friction", "Not friction"], ["skip", "Skip"]];

const routingSame = (it: any, d: Draft) =>
  d.bucket === it.bucket && d.owner === it.owner && d.lesson_is_rule === !!it.lesson_is_rule;

/** The lane the item will land in with the draft's routing (stored lane if nothing changed, e.g. structural → fine-tune). */
const effectiveLane = (it: any, d: Draft) =>
  d.lane || (routingSame(it, d) && it.fix_lane ? it.fix_lane : laneFor(d.bucket, d.owner, it.served_model, d.lesson_is_rule));

function pretty(s?: string | null) {
  if (!s) return "";
  try { return JSON.stringify(JSON.parse(s), null, 2); } catch { return s; }
}

export default function FrictionReview() {
  const { sub: id } = useParams();   // routed as /friction/:sub (a number = a chat in the queue)
  const nav = useNavigate();
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<number, Draft>>({});
  const [focus, setFocus] = useState<number | null>(null);
  const [reviewer, setReviewer] = useReviewer();
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [saving, setSaving] = useState(false);

  const init = (f: any) => {
    setData(f);
    const d: Record<number, Draft> = {};
    for (const it of f.items) {
      d[it.id] = {
        verdict: it.verdict,
        note: it.review_note ?? "",
        corrected_interpretation: pretty(it.corrected_interpretation ?? it.expected_interpretation),
        corrected_guidance: it.corrected_guidance ?? it.guidance ?? "",
        bucket: it.bucket ?? "intent",
        owner: it.owner ?? "unknown",
        lesson_is_rule: !!it.lesson_is_rule,
        lane: it.fix_lane && it.fix_lane !== laneFor(it.bucket, it.owner, it.served_model, !!it.lesson_is_rule) ? it.fix_lane : "",
      };
    }
    setDrafts(d);
  };
  useEffect(() => {
    setData(null); setMsg(null);
    getJSON(`/api/friction/findings/${id}`).then(init).catch((e) => setError(e.message));
  }, [id]);

  const flagged = useMemo(() => {
    const m: Record<number, number[]> = {};
    data?.items.forEach((it: any, i: number) => (it.turns ?? []).forEach((t: number) => (m[t] = [...(m[t] ?? []), i + 1])));
    return m;
  }, [data]);

  if (error) return <ErrorBox error={error} />;
  if (!data) return <Loading />;
  const set = (iid: number, patch: Partial<Draft>) => setDrafts((d) => ({ ...d, [iid]: { ...d[iid], ...patch } }));
  const jump = (t: number) => {
    setFocus(t);
    document.getElementById(`t${t}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
  };
  const pending = Object.values(drafts).filter((d) => d.verdict === "pending").length;
  const signals = (data.signals ?? []) as any[];
  const ignored = signals.filter((s) => s.code === "expected_ask");
  const context = signals.filter((s) => s.code !== "expected_ask" && !s.weight);

  const save = async (submit: boolean) => {
    setSaving(true); setMsg(null);
    try {
      const body = {
        reviewer, submit,
        items: data.items.map((it: any) => {
          const d = drafts[it.id];
          const origInterp = pretty(it.expected_interpretation);
          return {
            id: it.id, verdict: d.verdict, note: d.note,
            corrected_interpretation: d.corrected_interpretation.trim() && d.corrected_interpretation !== origInterp ? d.corrected_interpretation : null,
            corrected_guidance: d.corrected_guidance.trim() && d.corrected_guidance !== (it.guidance ?? "") ? d.corrected_guidance : null,
            bucket: d.bucket, owner: d.owner, lesson_is_rule: d.lesson_is_rule,
            // an explicit lane wins; unchanged routing keeps the stored lane; otherwise the backend recomputes it
            fix_lane: d.lane ? d.lane : routingSame(it, d) ? it.fix_lane : null,
          };
        }),
      };
      const res = await fetch(`/api/friction/findings/${id}/review`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      const out = await res.json();
      if (!res.ok) throw new Error(out.detail ?? res.statusText);
      init(out);
      if (submit) {
        if (out.next_pending_id) nav(`/friction/${out.next_pending_id}`);
        else nav(`/friction/queue?day=${out.day}`);
      } else setMsg({ ok: true, text: "Saved." });
    } catch (e: any) { setMsg({ ok: false, text: e.message }); }
    setSaving(false);
  };

  return (
    <>
      <div>
        <Link to={`/friction/queue?day=${data.day}`} className="small">← Review queue · {fmtDate(data.day)}</Link>
        <h1 style={{ marginTop: 6 }}>{data.title || "Untitled chat"}</h1>
        <div className="page-sub" style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <span className="chip">{data.flow}</span> <span className="chip">{data.outcome}</span>
          {data.n_user_msgs} user messages · score {data.score}<Info k="f_score" /> · found by {data.source === "llm" ? "LLM judge" : "rule signals"}
          {data.submitted_at && <span className="badge sev-ok"><span className="dot" />Submitted by {data.submitted_by}</span>}
          <Link to={`/conversations/${data.session_id}`} className="btn" style={{ marginLeft: "auto" }}>Open full conversation ↗</Link>
          {data.next_pending_id && <Link to={`/friction/${data.next_pending_id}`} className="btn">Next to review →</Link>}
        </div>
      </div>

      <div className="alert warn"><div><strong>Why this chat was flagged</strong><div className="small">{data.summary}</div></div></div>

      <div className="split" style={{ gridTemplateColumns: "minmax(0, 1fr) minmax(360px, 440px)" }}>
        <Card title="Conversation" right={<span className="muted small">Flagged turns have a red edge · data is masked</span>}>
          <div className="timeline">
            {data.transcript.map((t: any) => (
              <div key={t.t} id={`t${t.t}`} className={`ev turn${flagged[t.t] ? " flag" : ""}${focus === t.t ? " focus" : ""}`}>
                <div className="when">t{t.t}<br />{fmtTime(t.at)}</div>
                <div>
                  <div className={`bubble ${t.role}`}>
                    <div className="who">{t.role === "USER" ? "User" : t.role === "ASSISTANT" ? "Assistant" : "System"}
                      {t.card_type && <span className="chip">{t.card_type}</span>}</div>
                    {t.text}
                    {t.card && <div className="small muted" style={{ marginTop: 4 }}>{t.card}</div>}
                  </div>
                  {t.events?.map((e: any, i: number) => <div key={i} className="sub-ev">· {e.text}</div>)}
                  {flagged[t.t] && (
                    <div className="flags">
                      {flagged[t.t].map((n) => (
                        <span key={n} className="chip flag" onClick={() => document.getElementById(`item${n}`)?.scrollIntoView({ behavior: "smooth", block: "center" })}>
                          ⚑ item {n}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        </Card>

        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {data.items.map((it: any, i: number) => {
            const d = drafts[it.id];
            if (!d) return null;
            const lane = effectiveLane(it, d);
            const [interpLabel, interpPh] = INTERP_LABEL[d.bucket] ?? INTERP_LABEL.intent;
            return (
              <div key={it.id} id={`item${i + 1}`} className={`item-card ${d.verdict}`}>
                <div className="item-head">
                  <b>Item {i + 1}</b>
                  <BucketPill b={d.bucket} />
                  <span className="chip">{TYPE_LABEL[it.type] ?? it.type}</span>
                  <SevBadge sev={it.severity} />
                  {it.confidence != null && <span className="muted small">conf {Number(it.confidence).toFixed(2)}</span>}
                </div>
                <div className="route" aria-label={`Routing for item ${i + 1}`}>
                  <label htmlFor={`b${it.id}`}><span>Bucket<Info k="f_bucket" /></span>
                    <select id={`b${it.id}`} value={d.bucket} onChange={(e) => set(it.id, { bucket: e.target.value, lane: "" })}>
                      {BUCKETS.map((b) => <option key={b} value={b}>{BUCKET_LABEL[b]}</option>)}
                    </select>
                  </label>
                  <label htmlFor={`o${it.id}`}><span>Caused by<Info k="f_owner" /></span>
                    <select id={`o${it.id}`} value={d.owner} onChange={(e) => set(it.id, { owner: e.target.value, lane: "" })}>
                      {OWNER_GROUPS.map(([g, os]) => (
                        <optgroup key={g} label={g}>{os.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</optgroup>
                      ))}
                    </select>
                  </label>
                  <label htmlFor={`l${it.id}`}><span>Fix goes to<Info k="f_lane" /></span>
                    <select id={`l${it.id}`} value={d.lane} onChange={(e) => set(it.id, { lane: e.target.value })}>
                      <option value="">Auto: {LANE_LABEL[lane]}</option>
                      {Object.entries(LANE_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                    </select>
                  </label>
                  <label htmlFor={`r${it.id}`} style={{ flexDirection: "row", alignItems: "center", gap: 6, alignSelf: "end" }}>
                    <input id={`r${it.id}`} type="checkbox" checked={d.lesson_is_rule}
                           onChange={(e) => set(it.id, { lesson_is_rule: e.target.checked, lane: "" })} />
                    Lesson is a rule<Info k="f_lesson_rule" />
                  </label>
                  <div className="hint">
                    Goes to <LanePill lane={lane} />
                    {" "}· {OWNER_LABEL[d.owner] ?? d.owner}
                    {it.served_model && <> · intent extracted by <b>{shortModel(it.served_model)}</b></>}
                  </div>
                </div>
                {it.turns?.length > 0 && (
                  <div className="small" style={{ marginTop: 6 }}>
                    <span className="muted">Where: </span>
                    {it.turns.map((t: number) => <button key={t} type="button" className="chip" style={{ cursor: "pointer", marginRight: 4 }} onClick={() => jump(t)}>t{t}</button>)}
                  </div>
                )}
                {it.user_quote && <div className="field"><label>User said</label><div className="val">“{it.user_quote}”</div></div>}
                <div className="field"><label>What went wrong</label><div className="val">{it.what_went_wrong}</div></div>
                {it.why_friction && <div className="field"><label>Why it's friction</label><div className="val">{it.why_friction}</div></div>}
                <div className="field">
                  <label>{interpLabel}<Info k="f_expected" /></label>
                  <textarea className={d.bucket === "intent" ? "mono" : ""} rows={d.bucket === "intent" ? 4 : 2} value={d.corrected_interpretation}
                            placeholder={interpPh}
                            onChange={(e) => set(it.id, { corrected_interpretation: e.target.value })} />
                </div>
                <div className="field">
                  <label>{GUIDANCE_LABEL[d.bucket] ?? "Lesson"}<Info k="f_guidance" /></label>
                  <textarea rows={2} value={d.corrected_guidance}
                            placeholder={d.bucket === "technical" ? "What engineering should look at" : "One general lesson that would have prevented this"}
                            onChange={(e) => set(it.id, { corrected_guidance: e.target.value })} />
                </div>
                <div className="verdicts" role="group" aria-label="Verdict">
                  {VERDICTS.map(([v, l]) => (
                    <button key={v} type="button" className={`${v}${d.verdict === v ? " on" : ""}`}
                            onClick={() => set(it.id, { verdict: d.verdict === v ? "pending" : v })}>{l}</button>
                  ))}
                  <Info k="f_verdict" />
                </div>
                {d.verdict !== "pending" && (
                  <div className="field">
                    <label>{d.verdict === "not_friction" ? "Why isn't this friction? (the finder learns from this)" : "Note (optional)"}</label>
                    <textarea rows={2} value={d.note} onChange={(e) => set(it.id, { note: e.target.value })} />
                  </div>
                )}
                {it.reviewer && <div className="muted small" style={{ marginTop: 6 }}>Last reviewed by {it.reviewer}</div>}
              </div>
            );
          })}

          <Card title="Rule signals">
            {signals.filter((s) => s.code !== "expected_ask" && s.weight).map((s, i) => (
              <div key={i} className="small" style={{ marginBottom: 4 }}>
                <BucketPill b={s.bucket} /> <span className="chip">{s.code}</span> +{s.weight} · {s.detail}
                {s.turns?.length ? <> · {s.turns.map((t: number) => <a key={t} href={`#t${t}`} onClick={(e) => { e.preventDefault(); jump(t); }}> t{t}</a>)}</> : null}
              </div>
            ))}
            {ignored.length > 0 && (
              <div className="small muted" style={{ marginTop: 6 }}>
                Ignored by rule R1 (mandatory field asks): {ignored.map((s) => s.detail.replace(" — ignored (rule R1)", "")).join("; ")}
              </div>
            )}
            {context.length > 0 && (
              <div className="small muted" style={{ marginTop: 6 }}>
                Context only, not friction on its own: {context.map((s) => s.detail).join("; ")}
              </div>
            )}
          </Card>
        </div>
      </div>

      <div className="review-bar">
        <label className="small muted" htmlFor="reviewer">Your name</label>
        <input id="reviewer" type="text" value={reviewer} onChange={(e) => setReviewer(e.target.value)} placeholder="Name" />
        <span className="muted small">{pending ? `${pending} of ${data.items.length} items still need a verdict` : "All items have a verdict"}</span>
        {msg && <span className={msg.ok ? "up small" : "down small"}>{msg.text}</span>}
        <span style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
          <button className="btn" disabled={saving || !reviewer.trim()} onClick={() => save(false)}>Save draft</button>
          <button className="btn primary" disabled={saving || !reviewer.trim() || pending > 0} onClick={() => save(true)}
                  title={pending ? "Give every item a verdict first" : ""}>
            Submit review{data.next_pending_id ? " & next" : ""}
          </button>
        </span>
      </div>
    </>
  );
}
