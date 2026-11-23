import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useApi } from "../api";
import { Card, ErrorBox, Loading, Table } from "../components";
import { fmtDateTime, fmtINR, shortId } from "../format";

const OUTCOMES = [
  { v: "", label: "All sessions" },
  { v: "booked", label: "Booked" },
  { v: "booking_failed", label: "Booking failed" },
  { v: "no_search", label: "Chatted, no search" },
  { v: "unresolved", label: "Flow unresolved" },
  { v: "failure_msgs", label: "Has failure messages" },
  { v: "group", label: "Group chats" },
];
const FLOW_LABEL: Record<string, string> = { single_flight: "Single flight", multi_city: "Multi-city", unresolved: "Unresolved", none: "—" };

export default function Sessions() {
  const nav = useNavigate();
  const [sp, setSp] = useSearchParams();
  const [text, setText] = useState(sp.get("q") ?? "");
  const outcome = sp.get("outcome") ?? "";
  const flow = sp.get("flow") ?? "";
  const sort = sp.get("sort") ?? "newest";
  const page = Number(sp.get("page") ?? 1);
  const set = (k: string, v: string) => {
    const n = new URLSearchParams(sp);
    v ? n.set(k, v) : n.delete(k);
    if (k !== "page") n.delete("page");
    setSp(n);
  };
  const user = sp.get("user") ?? "";
  const { data, error, loading } = useApi("/api/sessions", {
    q: sp.get("q") || undefined, outcome: outcome || undefined, flow: flow || undefined, user: user || undefined,
    sort, page, page_size: 50,
  });
  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;

  return (
    <>
      <div>
        <h1>Conversations</h1>
        <div className="page-sub">Every chat session. Open one to replay it step by step: messages, extracted intents, searches and bookings.</div>
      </div>
      <Card>
        <form
          style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 12 }}
          onSubmit={(e) => {
            e.preventDefault();
            set("q", text.trim());
          }}
        >
          <input type="search" placeholder="Search titles & messages, or paste a session ID" value={text}
                 onChange={(e) => setText(e.target.value)} />
          <select value={outcome} onChange={(e) => set("outcome", e.target.value)} aria-label="Outcome">
            {OUTCOMES.map((o) => <option key={o.v} value={o.v}>{o.label}</option>)}
          </select>
          <select value={flow} onChange={(e) => set("flow", e.target.value)} aria-label="Flow">
            <option value="">All flows</option>
            <option value="single_flight">Single flight</option>
            <option value="multi_city">Multi-city</option>
            <option value="unresolved">Unresolved</option>
          </select>
          <select value={sort} onChange={(e) => set("sort", e.target.value)} aria-label="Sort">
            <option value="newest">Newest first</option>
            <option value="oldest">Oldest first</option>
            <option value="most_messages">Most user messages</option>
            <option value="stage">Furthest stage</option>
          </select>
          <button className="btn" type="submit">Search</button>
          {user && (
            <button type="button" className="chip" style={{ alignSelf: "center", cursor: "pointer" }} onClick={() => set("user", "")}
                    title="Clear user filter">User {shortId(user)} ✕</button>
          )}
          {data && <span className="muted small" style={{ alignSelf: "center" }}>{data.total} sessions</span>}
        </form>
        {error ? <ErrorBox error={error} /> : loading && !data ? <Loading /> : (
          <>
            <Table
              rows={data.items}
              empty="No sessions match these filters."
              onRow={(r) => nav(`/conversations/${r.session_id}`)}
              cols={[
                { key: "created_local", label: "Started (IST)", render: (r) => fmtDateTime(r.created_local) },
                { key: "title", label: "Title", render: (r) => r.title || <span className="muted">Untitled</span> },
                { key: "current_flow", label: "Flow", render: (r) => FLOW_LABEL[r.current_flow] ?? r.current_flow },
                { key: "stage", label: "Furthest stage", render: (r) => <span className="chip">{r.stage}</span> },
                { key: "n_user_msgs", label: "User msgs", num: true },
                { key: "searches", label: "Searches", num: true, render: (r) => r.n_flight_searches + r.n_hotel_searches },
                { key: "n_booking_attempts", label: "Booking attempts", num: true },
                { key: "gmv", label: "GMV", num: true, render: (r) => (r.gmv ? fmtINR(r.gmv) : "—") },
                { key: "flags", label: "", render: (r) => (
                  <span style={{ display: "inline-flex", gap: 4 }}>
                    {r.is_group_chat && <span className="chip">group</span>}
                    {r.n_failed_bookings > 0 && <span className="chip">booking failed</span>}
                    {r.n_failure_msgs > 0 && <span className="chip">failure msg</span>}
                  </span>
                ) },
                { key: "user_id", label: "User", render: (r) => <code className="small">{shortId(r.user_id)}</code> },
              ]}
            />
            <div className="pager">
              <button className="btn" disabled={page <= 1} onClick={() => set("page", String(page - 1))}>← Prev</button>
              <span className="muted small">Page {page} of {pages}</span>
              <button className="btn" disabled={page >= pages} onClick={() => set("page", String(page + 1))}>Next →</button>
            </div>
          </>
        )}
      </Card>
    </>
  );
}
