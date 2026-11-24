import { Link, useNavigate } from "react-router-dom";
import { useApi } from "../api";
import { Bars, Card, ErrorBox, Kpi, Loading, SevBadge, Table } from "../components";
import { fmtDateTime, fmtINR, fmtNum, shortId } from "../format";
import { PageHeader, useSub } from "../nav";

export default function Ops() {
  const sub = useSub("/ops");
  const nav = useNavigate();
  // Ops ignores the date-range filter: a stuck payment matters no matter how old it is.
  const { data, error, loading } = useApi("/api/ops", {}, { useDays: false });
  const head = <PageHeader path="/ops" sub={sub} />;
  if (error) return <>{head}<ErrorBox error={error} /></>;
  if (loading && !data) return <>{head}<Loading /></>;
  const s = data.summary;
  return (
    <>
      {head}

      {sub.key === "attention" && (
        <>
          <div className="kpis">
            <Kpi label="Money at risk" info="money_at_risk_banner" value={fmtINR(s.money_at_risk)} sub={`${s.critical} critical bookings`} />
            <Kpi label="Needs attention" info="needs_attention" value={fmtNum(s.critical + s.warning)} sub={`${s.critical} critical · ${s.warning} warning`} />
          </div>
          <Card title="🚨 Bookings needing attention" info="needs_attention" right={<span className="muted small">Click a row to open the conversation</span>}>
            <Table
              rows={data.issues}
              empty="Nothing needs attention."
              onRow={(r) => r.chat_session_id && nav(`/conversations/${r.chat_session_id}`)}
              cols={[
                { key: "severity", label: "Severity", render: (r) => <SevBadge sev={r.severity} /> },
                { key: "issue", label: "Issue", render: (r) => <span className="wrap" style={{ minWidth: 200 }}>{r.issue}</span> },
                { key: "created_local", label: "Created (IST)", render: (r) => fmtDateTime(r.created_local) },
                { key: "route", label: "Route", render: (r) => (r.origin ? `${r.origin} → ${r.destination}` : "—") },
                { key: "airline", label: "Airline" },
                { key: "amount_payable", label: "Amount", num: true, render: (r) => fmtINR(r.amount_payable) },
                { key: "status", label: "Booking status" },
                { key: "payment_status", label: "Payment status" },
                { key: "hours_since_update", label: "Hrs since update", num: true, info: "hours_since_update" },
                { key: "provider_booking_id", label: "Tripjack ref" },
                { key: "failure_reason", label: "Failure reason", render: (r) => <span className="small wrap" style={{ minWidth: 260 }}>{r.failure_reason ?? "—"}</span> },
                { key: "user_id", label: "User", render: (r) => <code className="small">{shortId(r.user_id)}</code> },
              ]}
            />
          </Card>
        </>
      )}

      {sub.key === "outcomes" && (
        <>
          <div className="kpis">
            <Kpi label="Booking attempts" value={fmtNum(s.attempts)} sub={`${s.booked} booked · ${s.failed} failed · ${s.pending} pending`} />
            <Kpi label="Booking success rate" info="success_rate" value={`${s.success_rate ?? 0}%`} />
            <Kpi label="GMV (booked)" info="gmv" value={fmtINR(s.gmv)} />
          </div>
          <div className="grid g2">
            <Card title="Daily booking outcomes">
              <Bars data={data.daily} x="date" stacked
                    xFmt={(d) => new Date(d + "T00:00:00").toLocaleDateString("en-IN", { day: "2-digit", month: "short" })}
                    series={[
                      { key: "booked", label: "Booked", color: "var(--good)" },
                      { key: "pending", label: "Pending", color: "var(--warning)" },
                      { key: "failed", label: "Failed", color: "var(--critical)" },
                    ]} />
            </Card>
            <Card title="Why bookings failed">
              <Bars data={data.failures} x="category" layout="vertical" height={200}
                    series={[{ key: "bookings", label: "Failed bookings", color: "var(--series-1)" }]} />
            </Card>
          </div>
          <Card title="Booking status × payment status">
            <Table rows={data.matrix} cols={[
              { key: "status", label: "Booking status" },
              { key: "payment_status", label: "Payment status" },
              { key: "bookings", label: "Bookings", num: true },
              { key: "amount", label: "Amount", num: true, render: (r) => fmtINR(r.amount) },
            ]} />
          </Card>
          <div className="muted small">The ones that need action are on <Link to="/ops/attention">🚨 Needs attention</Link>.</div>
        </>
      )}
    </>
  );
}
