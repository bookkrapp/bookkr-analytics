import { Fragment, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { getJSON } from "../api";
import { Card, ErrorBox, Loading, SevBadge } from "../components";
import { fmtDate, fmtDateTime, fmtINR, fmtTime, fmtUSD, shortId } from "../format";

/** Render an intent slot value (strings, {name, iata}, {date}, numbers…) compactly. */
function val(v: any): string | null {
  if (v == null || v === "" || (Array.isArray(v) && !v.length)) return null;
  if (typeof v === "string" || typeof v === "number" || typeof v === "boolean") return String(v);
  if (Array.isArray(v)) return v.map(val).filter(Boolean).join(", ");
  if (typeof v === "object") {
    if (v.name || v.iata || v.city) return [v.name ?? v.city, v.iata ? `(${v.iata})` : null].filter(Boolean).join(" ");
    const exact = v.exact_date ?? v.exactDate ?? v.date;
    if (exact) return String(exact);
    const start = v.start_date ?? v.startDate, end = v.end_date ?? v.endDate;
    if (start || end) return `${start ?? "?"} – ${end ?? "?"}`;
    const month = v.month ?? v.monthOfTravel;
    if (month) return String(month);
    if (Object.values(v).every((x) => x == null || x === "" || (typeof x === "object" && !Array.isArray(x)))) {
      const nested = Object.values(v).map(val).filter(Boolean);
      return nested.length ? nested.join(" · ") : null;
    }
    if (v.adults != null) return `${v.adults} adult${v.adults > 1 ? "s" : ""}${v.children ? `, ${v.children} child` : ""}`;
    const s = JSON.stringify(v);
    return s === "{}" ? null : s.length > 60 ? s.slice(0, 60) + "…" : s;
  }
  return null;
}
const KEY_SLOTS = ["origin", "destination", "departure", "return", "passengers", "tripType", "cabin", "budget", "checkInDate", "travelWindow", "groupType"];

function Json({ value, label = "Raw JSON" }: { value: any; label?: string }) {
  if (value == null) return null;
  return (
    <details>
      <summary>{label}</summary>
      <pre className="json">{JSON.stringify(value, null, 2)}</pre>
    </details>
  );
}

function CardInfo({ m }: { m: any }) {
  if (!m.card_type) return null;
  const d = m.card_data;
  let body: any = null;
  if (m.card_type === "intent_confirmation" && d?.summary) {
    body = (
      <dl className="kv" style={{ marginTop: 6 }}>
        {d.summary.map((s: any, i: number) => (
          <Fragment key={i}><dt>{s.label}</dt><dd>{s.value}</dd></Fragment>
        ))}
      </dl>
    );
  } else if (m.card_type === "payment_result" && d) {
    body = <div className="small" style={{ marginTop: 4 }}>Outcome: <b>{d.outcome}</b> · {fmtINR(d.amount)} · {d.message}</div>;
  } else if (m.card_shape) {
    const counts = Object.entries(m.card_shape).filter(([, v]) => typeof v === "number").slice(0, 4);
    if (counts.length) body = <div className="small muted" style={{ marginTop: 4 }}>{counts.map(([k, v]) => `${k}: ${v}`).join(" · ")}</div>;
  }
  return (
    <div style={{ marginTop: 6 }}>
      <span className="chip">card: {m.card_type}{m.card_superseded ? " (superseded)" : ""}</span>
      {body}
      <Json value={m.card_data ?? m.card_shape} label="Card data" />
    </div>
  );
}

function Event({ e, prevHashes }: { e: any; prevHashes: Set<string> }) {
  if (e.type === "message") {
    return (
      <div className={`bubble ${e.role}`}>
        <div className="who">
          {e.role === "USER" ? "User" : e.role === "ASSISTANT" ? "Assistant" : "System"}
          {e.kind && <span className="chip">{e.kind}</span>}
          {e.bookkr_invoked && <span className="chip">@bookkr</span>}
        </div>
        {e.content}
        <CardInfo m={e} />
      </div>
    );
  }
  if (e.type === "intent") {
    const params = e.parameters ?? {};
    const slots = KEY_SLOTS.map((k) => [k, val(params[k])]).filter(([, v]) => v);
    return (
      <div className="sys-ev">
        <b>Intent extracted:</b> {e.intent_type} <span className="muted">· confidence {Number(e.confidence).toFixed(2)}</span>
        {slots.length > 0 && <div className="small" style={{ marginTop: 4 }}>{slots.map(([k, v]) => <span key={k} style={{ marginRight: 12 }}><span className="muted">{k}:</span> {v}</span>)}</div>}
        <Json value={params} label="All parameters" />
      </div>
    );
  }
  if (e.type === "flight_search") {
    const repeat = prevHashes.has(e.search_hash);
    return (
      <div className="sys-ev">
        <b>Flight search:</b> {e.origin} → {e.destination} · {fmtDate(e.departure_date)}
        {e.return_date ? ` – ${fmtDate(e.return_date)}` : " · one-way"} · {e.adults} adult{e.adults > 1 ? "s" : ""}
        {e.children ? `, ${e.children} child` : ""} · {e.cabin_class}
        <span className="muted"> · {e.result_sets ? `${e.options} options` : "no result stored"}</span>
        {repeat && <> <span className="chip">repeat of an earlier search</span></>}
      </div>
    );
  }
  if (e.type === "hotel_search") {
    return (
      <div className="sys-ev">
        <b>Hotel search:</b> {e.destination_name} · {fmtDate(e.check_in_date)} – {fmtDate(e.check_out_date)} · {e.adults} adult{e.adults > 1 ? "s" : ""}
        <span className="muted"> · {e.hotels ?? 0} hotels returned</span>
      </div>
    );
  }
  if (e.type === "booking") {
    return (
      <div className="sys-ev">
        <b>Flight booking:</b> {e.origin ? `${e.origin} → ${e.destination}` : ""} {e.airline ?? ""} · {fmtINR(e.amount_payable)} ·{" "}
        {e.status} / {e.payment_status ?? "no payment"} <SevBadge sev={e.severity} />
        <div className="small muted" style={{ marginTop: 4 }}>{e.issue}{e.failure_reason ? ` — ${e.failure_reason}` : ""}</div>
      </div>
    );
  }
  return null;
}

export default function SessionDetail() {
  const { id } = useParams();
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setData(null);
    getJSON(`/api/sessions/${id}`).then(setData).catch((e) => setError(e.message));
  }, [id]);
  if (error) return <ErrorBox error={error} />;
  if (!data) return <Loading />;
  const f = data.facts;
  const fs = data.flow_state;
  const seen = new Set<string>();
  return (
    <>
      <div>
        <Link to="/conversations" className="small">← All conversations</Link>
        <h1 style={{ marginTop: 6 }}>{f.title || "Untitled chat"}</h1>
        <div className="page-sub">
          Started {fmtDateTime(f.created_local)} IST · <span className="chip">{f.stage}</span> · {f.n_user_msgs} user messages ·
          session <code className="small">{f.session_id}</code>
        </div>
      </div>
      <div className="split">
        <Card title="Timeline">
          <div className="timeline">
            {data.timeline.map((e: any, i: number) => {
              const hashes = new Set(seen);
              if (e.type === "flight_search") seen.add(e.search_hash);
              return (
                <div className="ev" key={e.type + (e.id ?? i)}>
                  <div className="when">{fmtTime(e.created_local)}</div>
                  <Event e={e} prevHashes={hashes} />
                </div>
              );
            })}
          </div>
        </Card>
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <Card title="Session">
            <dl className="kv">
              <dt>User</dt><dd><code className="small">{shortId(f.user_id)}</code>{f.is_guest ? " (guest)" : ""}</dd>
              <dt>Chat type</dt><dd>{f.chat_type}{f.is_group_chat ? " · group" : ""}</dd>
              <dt>Flow</dt><dd>{f.current_flow}</dd>
              <dt>Messages</dt><dd>{f.n_messages} ({f.n_user_msgs} user)</dd>
              <dt>Intents</dt><dd>{f.n_intents}{f.avg_intent_confidence != null ? ` · avg conf ${Number(f.avg_intent_confidence).toFixed(2)}` : ""}</dd>
              <dt>Searches</dt><dd>{f.n_flight_searches} flight · {f.n_hotel_searches} hotel</dd>
              <dt>User msgs before 1st search</dt><dd>{f.first_search_at ? f.user_msgs_before_search : "no search"}</dd>
              <dt>Bookings</dt><dd>{f.n_booking_attempts} attempts · {f.n_booked} booked</dd>
              <dt>GMV</dt><dd>{f.gmv ? fmtINR(f.gmv) : "—"}</dd>
              <dt>LLM cost</dt><dd>{fmtUSD(f.llm_cost_usd)} ({f.n_llm_calls} calls)</dd>
            </dl>
          </Card>
          <Card title="Current flow state">
            {fs ? (
              <>
                <dl className="kv">
                  <dt>Flow</dt><dd>{fs.current_flow}</dd>
                  <dt>Flight state</dt><dd>{fs.flight_state?.status ?? "—"}</dd>
                  <dt>Multi-city phase</dt><dd>{fs.multi_city_state?.phase ?? "—"}</dd>
                  {KEY_SLOTS.map((k) => {
                    const v = val(fs.travel_intent?.[k]);
                    return v ? <Fragment key={k}><dt>{k}</dt><dd>{v}</dd></Fragment> : null;
                  })}
                </dl>
                <Json value={fs.travel_intent} label="Travel intent JSON" />
                <Json value={fs.flight_state} label="Flight state JSON" />
                <Json value={fs.multi_city_state} label="Multi-city state JSON" />
              </>
            ) : <span className="muted">No flow state.</span>}
          </Card>
          {data.llm?.length > 0 && (
            <Card title="LLM calls">
              <table>
                <thead><tr><th>Task</th><th className="num">Calls</th><th className="num">Cost</th><th className="num">Avg ms</th></tr></thead>
                <tbody>
                  {data.llm.map((l: any) => (
                    <tr key={l.task}><td>{l.task}{l.failures ? <span className="down"> ({l.failures} failed)</span> : null}</td>
                      <td className="num">{l.calls}</td><td className="num">{fmtUSD(l.cost_usd ?? 0)}</td><td className="num">{l.avg_latency_ms}</td></tr>
                  ))}
                </tbody>
              </table>
            </Card>
          )}
        </div>
      </div>
    </>
  );
}
