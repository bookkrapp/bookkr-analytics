// Needs action (old ops board parity): money taken or held with no booking, paid bookings stuck at TripJack, and
// support tickets waiting on our reply. As of the last sync; every row opens its chat or the user's chats.
import { Link, useNavigate } from "react-router-dom";
import { useApi } from "../api";
import { Card, ErrorBox, Info, Loading, Table } from "../components";
import { fmtINR, fmtIST, fmtNum, shortId } from "../format";

export const age = (min?: number | null) =>
  min == null ? "—" : min < 60 ? `${min} min` : min < 48 * 60 ? `${Math.round(min / 6) / 10} h` : `${Math.round(min / 144) / 10} days`;

const chatLink = (r: any) => (r.chat_session_id
  ? <Link to={`/conversations/${r.chat_session_id}`} onClick={(e) => e.stopPropagation()}>Open chat →</Link>
  : <Link to={`/conversations?user=${encodeURIComponent(r.user_id)}`} onClick={(e) => e.stopPropagation()}>User's chats →</Link>);

/** Three cards for the top of Overview › Summary; each opens Ops › Needs action. */
export function NeedsActionStrip() {
  const nav = useNavigate();
  const { data } = useApi("/api/needs-action", {}, { useDays: false });
  if (!data) return null;
  const s = data.support ?? {};
  const cards = [
    { n: data.paid_not_booked.length, lbl: "Paid, not booked", sub: data.paid_not_booked.length ? `${fmtINR(data.paid_not_booked_inr)} held or taken` : "Nothing waiting", tone: data.paid_not_booked.length ? "bad" : "" },
    { n: data.stuck.length, lbl: "Paid, stuck at TripJack > 30 min", sub: data.stuck.length ? `oldest ${age(data.stuck[0]?.age_min)}` : "Nothing stuck", tone: data.stuck.length ? "bad" : "" },
    { n: s.total ?? 0, lbl: "Support awaiting our reply", sub: s.total ? `High ${s.high} · oldest ${age(s.oldest_wait_min)} · ${s.over_24h} over 24 h` : "Inbox clear",
      tone: s.over_24h ? "bad" : s.total ? "warn" : "" },
  ];
  return (
    <div>
      <div className="muted small" style={{ marginBottom: 6 }}>🚨 Needs action · as of the last sync ({fmtIST(data.as_of)}) <Info k="needs_action" /></div>
      <div className="action-strip">
        {cards.map((c) => (
          <button key={c.lbl} className={`action-card ${c.tone}`} onClick={() => nav("/ops/attention")}>
            <div className="lbl">{c.lbl}</div>
            <div className="big">{fmtNum(c.n)}</div>
            <div className="muted small">{c.sub}</div>
          </button>
        ))}
      </div>
    </div>
  );
}

/** The full Needs action lists for Ops › Needs action. */
export function NeedsActionLists() {
  const nav = useNavigate();
  const { data, error, loading } = useApi("/api/needs-action", {}, { useDays: false });
  if (error) return <ErrorBox error={error} />;
  if (loading && !data) return <Loading />;
  const s = data.support ?? {};
  const open = (r: any) => nav(r.chat_session_id ? `/conversations/${r.chat_session_id}` : `/conversations?user=${encodeURIComponent(r.user_id)}`);
  return (
    <>
      <NeedsActionStrip />
      <Card title="💸 Paid, not booked" info="paid_not_booked" right={<span className="muted small">Failed bookings of the last 30 days whose money is held or taken and not given back</span>}>
        <Table rows={data.paid_not_booked} empty="Nothing: every failed booking's money was voided or refunded." onRow={open} cols={[
          { key: "product", label: "Product", render: (r) => (r.product === "hotel" ? "🏨 Hotel" : "✈️ Flight") },
          { key: "booking_id", label: "Booking", render: (r) => <code className="small">{shortId(r.booking_id)}</code> },
          { key: "payment_id", label: "Razorpay payment", render: (r) => (r.payment_id ? <code className="small">{r.payment_id}</code> : "wallet") },
          { key: "inr", label: "Amount", num: true, render: (r) => fmtINR(r.inr) },
          { key: "state", label: "Money is", render: (r) => <span className={`badge sev-${r.state === "captured" ? "critical" : "warning"}`}><span className="dot" />{r.state}</span> },
          { key: "age_min", label: "Since last change", num: true, render: (r) => age(r.age_min) },
          { key: "chat", label: "", render: chatLink },
        ]} />
      </Card>
      <Card title="⏳ Paid, stuck at TripJack" info="stuck" right={<span className="muted small">Money taken, booking not settled for over 30 minutes</span>}>
        <Table rows={data.stuck} empty="Nothing stuck." onRow={open} cols={[
          { key: "product", label: "Product", render: (r) => (r.product === "hotel" ? "🏨 Hotel" : "✈️ Flight") },
          { key: "booking_id", label: "Booking", render: (r) => <code className="small">{shortId(r.booking_id)}</code> },
          { key: "status", label: "Status" },
          { key: "value_inr", label: "Value", num: true, render: (r) => fmtINR(r.value_inr) },
          { key: "age_min", label: "Stuck for", num: true, render: (r) => age(r.age_min) },
          { key: "chat", label: "", render: chatLink },
        ]} />
      </Card>
      <Card title="🎧 Support awaiting our reply" right={<span className="muted small">Open tickets: the customer wrote last</span>}>
        <div className="small" style={{ marginBottom: 8 }}>
          {fmtNum(s.total ?? 0)} waiting · High {s.high ?? 0} · Medium {s.medium ?? 0} · Low {s.low ?? 0} · oldest {age(s.oldest_wait_min)} · {s.over_24h ?? 0} over 24 h
        </div>
        <Table rows={s.waiting ?? []} empty="Nobody is waiting on a reply." onRow={(r) => nav(`/conversations?user=${encodeURIComponent(r.user_id)}`)} cols={[
          { key: "number", label: "Ticket", render: (r) => `#${r.number}` },
          { key: "priority", label: "Priority" },
          { key: "source", label: "Source" },
          { key: "waiting_min", label: "Waiting", num: true, render: (r) => <span className={r.waiting_min > 1440 ? "down" : r.waiting_min > 240 ? "" : "muted"}>{age(r.waiting_min)}</span> },
          { key: "created_local", label: "Opened (IST)", render: (r) => fmtIST(r.created_local) },
          { key: "user_id", label: "User", render: (r) => <Link to={`/conversations?user=${encodeURIComponent(r.user_id)}`} onClick={(e) => e.stopPropagation()}>{shortId(r.user_id)} · chats →</Link> },
        ]} />
      </Card>
    </>
  );
}
