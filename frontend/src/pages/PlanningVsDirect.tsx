// Trip planning vs direct search (Funnel page): how many users asked Bookkr to plan a trip,
// how far those plans got, and how planners convert compared with users who searched directly.
import { Link } from "react-router-dom";
import { useApi } from "../api";
import { Card, ErrorBox, Info, Kpi, Loading, Table, TimeLines } from "../components";
import { fmtINR, fmtNum } from "../format";

const SIDE: Record<string, string> = { planned: "🗺️ Planned a trip", direct: "🔎 Searched directly", unclear: "❔ Not clear yet" };
const pct = (a: number, b: number) => (b ? `${((100 * a) / b).toFixed(1)}%` : "—");

export default function PlanningVsDirect() {
  const { data, error, loading } = useApi("/api/funnel/planning", {});
  if (error) return <ErrorBox error={error} />;
  if (loading && !data) return <Loading />;
  const by = Object.fromEntries((data.compare as any[]).map((r) => [r.side, r]));
  const planned = by.planned ?? { sessions: 0, users: 0, searched: 0, booked: 0 };
  const direct = by.direct ?? { sessions: 0, users: 0, searched: 0, booked: 0 };
  const total = (data.compare as any[]).reduce((s, r) => s + r.sessions, 0);
  const steps: any[] = data.steps;
  const top = steps[0]?.sessions || 1;
  const u = data.users ?? {};
  const rows = ["planned", "direct", "unclear"].filter((k) => by[k]).map((k) => by[k]);
  return (
    <>
      <div style={{ marginTop: 8 }}>
        <h2 style={{ fontSize: 17, margin: 0 }}>🗺️ Trip planning vs 🔎 direct search<Info k="planning_vs_direct" /></h2>
        <div className="page-sub">Did users ask Bookkr to plan a trip, or go straight to a flight or hotel search? Same date range as above, all journeys.</div>
      </div>

      <div className="kpis">
        <Kpi label="Chats that planned a trip" value={fmtNum(planned.sessions)} info="planned_chats"
             sub={<span>{pct(planned.sessions, total)} of chats · {fmtNum(planned.users)} users</span>} />
        <Kpi label="Chats that searched directly" value={fmtNum(direct.sessions)} info="direct_chats"
             sub={<span>{pct(direct.sessions, total)} of chats · {fmtNum(direct.users)} users</span>} />
        <Kpi label="Plans that led to a search" value={pct(planned.searched, planned.sessions)} info="plan_to_search"
             sub={<span>{fmtNum(planned.searched)} of {fmtNum(planned.sessions)} planning chats</span>} />
        <Kpi label="Booked: planned vs direct" value={<span>{pct(planned.booked, planned.sessions)} <span className="muted" style={{ fontSize: 16 }}>vs</span> {pct(direct.booked, direct.sessions)}</span>}
             info="plan_booked" sub={<span>{fmtNum(planned.booked)} vs {fmtNum(direct.booked)} chats booked</span>} />
        <Kpi label="Users who did both" value={fmtNum(u.both ?? 0)} info="plan_both_users"
             sub={<span>{fmtNum(u.planned_only ?? 0)} only planned · {fmtNum(u.direct_only ?? 0)} only searched</span>} />
      </div>

      <div className="grid g2">
        <Card title="Trip-planning funnel" info="plan_funnel" right={<Link to="/conversations?journey=trip_plan" className="small">Open planning chats →</Link>}>
          {steps.map((s, i) => (
            <div className="funnel-row" key={s.key}>
              <div>{s.name}</div>
              <div className="funnel-bar" title={`${s.sessions} chats`}>
                <div style={{ width: `${(100 * s.sessions) / top}%`, background: "var(--series-3)" }} />
              </div>
              <div className="funnel-num">
                {fmtNum(s.sessions)}
                <span className="muted">{i ? pct(s.sessions, steps[i - 1].sessions) : ""}</span>
              </div>
            </div>
          ))}
        </Card>
        <Card title="Planning vs direct chats per week" info="plan_weekly">
          <TimeLines data={data.weekly} x="week" series={[
            { key: "direct", label: "Searched directly", color: "var(--series-1)" },
            { key: "planned", label: "Planned a trip", color: "var(--series-3)" },
          ]} />
        </Card>
      </div>

      <Card title="Side by side" info="plan_compare">
        <Table rows={rows} cols={[
          { key: "side", label: "", render: (r) => <b>{SIDE[r.side] ?? r.side}</b> },
          { key: "sessions", label: "Chats", num: true },
          { key: "users", label: "Users", num: true },
          { key: "flight_searched", label: "Searched flights", num: true },
          { key: "hotel_searched", label: "Searched hotels", num: true },
          { key: "results_shown", label: "Results shown", num: true },
          { key: "booking_attempted", label: "Booking attempted", num: true },
          { key: "booked", label: "Booked", num: true },
          { key: "conv", label: "Booked %", num: true, render: (r) => pct(r.booked, r.sessions) },
          { key: "gmv", label: "GMV", num: true, render: (r) => (r.gmv ? fmtINR(r.gmv) : "—") },
          { key: "avg_user_msgs", label: "Avg user msgs", num: true },
        ]} />
      </Card>
    </>
  );
}
