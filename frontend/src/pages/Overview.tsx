import { Link } from "react-router-dom";
import { useApi } from "../api";
import { Card, ErrorBox, Info, Kpi, Loading, Table, TimeLines } from "../components";
import { fmtINR, fmtNum, fmtUSD } from "../format";
import { PageHeader, useSub } from "../nav";
import { MoneyPage, TopBooked } from "./Money";
import { NeedsActionStrip } from "./NeedsAction";

export default function Overview() {
  const sub = useSub("/overview");
  const { data, error, loading } = useApi("/api/overview");
  const head = <PageHeader path="/overview" sub={sub} />;
  if (error) return <>{head}<ErrorBox error={error} /></>;
  if (loading && !data) return <>{head}<Loading /></>;
  const k = data.kpis;
  const p = data.previous ?? {};
  const ops = data.ops;
  return (
    <>
      {head}

      {sub.key === "summary" && (
        <>
          <NeedsActionStrip />
          {ops.critical > 0 && (
            <div className="alert">
              <span className="badge sev-critical"><span className="dot" />Critical</span>
              <div>
                <strong>{ops.critical} bookings with money at risk ({fmtINR(ops.money_at_risk)})<Info k="money_at_risk_banner" /></strong>
                <div className="muted small">Customers were charged (or a refund is pending) but have no confirmed ticket. All-time, not limited to the selected period.</div>
              </div>
              <Link to="/ops/attention" className="btn" style={{ marginLeft: "auto" }}>Review</Link>
            </div>
          )}
          <div className="kpis">
            <Kpi label="Chat sessions" info="sessions" value={fmtNum(k.sessions)} cur={k.sessions} prev={p.sessions} />
            <Kpi label="Active users" info="active_users" value={fmtNum(k.active_users)} cur={k.active_users} prev={p.active_users} />
            <Kpi label="Flight searches" info="flight_searches" value={fmtNum(k.flight_searches)} cur={k.flight_searches} prev={p.flight_searches} />
            <Kpi label="Hotel searches" info="hotel_searches" value={fmtNum(k.hotel_searches)} cur={k.hotel_searches} prev={p.hotel_searches} />
            <Kpi label="Bookings (flights)" info="bookings" value={fmtNum(k.bookings)} cur={k.bookings} prev={p.bookings}
                 sub={`${fmtNum(k.booking_attempts)} attempts`} />
            <Kpi label="GMV" info="gmv" value={fmtINR(k.gmv)} cur={k.gmv} prev={p.gmv} sub={`Avg ${fmtINR(k.avg_booking_value)} / booking`} />
            <Kpi label="Chat → booking" info="conversion" value={`${k.conversion_pct}%`} cur={k.conversion_pct} prev={p.conversion_pct}
                 sub={`${k.sessions_booked} of ${k.sessions} sessions`} />
            <Kpi label="LLM cost" info="llm_cost" value={fmtUSD(k.llm_cost_usd)} cur={k.llm_cost_usd} prev={p.llm_cost_usd} invert
                 sub={k.bookings ? `${fmtUSD(k.llm_cost_usd / k.bookings)} per booking` : undefined} />
          </div>
          <Card title="Daily activity" info="daily_activity">
            <TimeLines
              data={data.daily}
              series={[
                { key: "sessions", label: "Chat sessions", color: "var(--series-1)" },
                { key: "flight_searches", label: "Flight searches", color: "var(--series-2)" },
                { key: "hotel_searches", label: "Hotel searches", color: "var(--series-3)" },
              ]}
            />
          </Card>
          <div className="next-links">
            Go deeper: <Link to="/overview/bookings">💰 Bookings &amp; GMV</Link> · <Link to="/overview/demand">📍 Demand</Link> ·{" "}
            <Link to="/funnel/conversion">🔻 Funnel</Link> · <Link to="/users/activity">👥 Users</Link>
          </div>
        </>
      )}

      {sub.key === "bookings" && <MoneyPage />}

      {sub.key === "demand" && (
        <>
          <div className="kpis">
            <Kpi label="Flight searches" info="flight_searches" value={fmtNum(k.flight_searches)} cur={k.flight_searches} prev={p.flight_searches} />
            <Kpi label="Hotel searches" info="hotel_searches" value={fmtNum(k.hotel_searches)} cur={k.hotel_searches} prev={p.hotel_searches} />
          </div>
          <h3 className="sec-h">What people booked</h3>
          <TopBooked />
          <h3 className="sec-h">What people searched for</h3>
          <div className="grid g2">
            <Card title="Top flight routes searched" info="top_routes">
              <Table rows={data.top_routes} cols={[
                { key: "route", label: "Route" },
                { key: "searches", label: "Searches", num: true },
                { key: "users", label: "Users", num: true },
              ]} />
            </Card>
            <Card title="Top hotel destinations searched" info="top_hotels">
              <Table rows={data.top_hotel_destinations} empty="No hotel searches in this period." cols={[
                { key: "destination", label: "Destination" },
                { key: "searches", label: "Searches", num: true },
                { key: "avg_nights", label: "Avg nights", num: true },
                { key: "avg_adults", label: "Avg adults", num: true },
              ]} />
            </Card>
          </div>
        </>
      )}
    </>
  );
}
