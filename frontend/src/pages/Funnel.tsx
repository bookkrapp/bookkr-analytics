import { useState } from "react";
import { Link } from "react-router-dom";
import { useApi } from "../api";
import { Bars, Card, ErrorBox, Info, Kpi, Loading, Table, TimeLines } from "../components";
import { fmtNum } from "../format";
import { JOURNEYS, JOURNEY_LABEL } from "../journeys";
import PlanningVsDirect from "./PlanningVsDirect";

export default function Funnel() {
  const [journey, setJourney] = useState("");
  const { data, error, loading } = useApi("/api/funnel", { journey: journey || undefined });
  if (error) return <ErrorBox error={error} />;
  if (loading && !data) return <Loading />;
  const max = data.stages[0]?.sessions || 1;
  const ux = data.ux ?? {};
  const ended = data.stages.filter((s: any) => s.level < 7).map((s: any) => ({ name: s.name, sessions: s.ended_here, level: s.level }));
  const chats = (j: string) => (j ? `/conversations?journey=${j}` : "/conversations");
  return (
    <>
      <div>
        <h1>Conversion funnel</h1>
        <div className="page-sub">Each chat session counted at the furthest stage it reached. Pick a journey to see its own funnel.</div>
      </div>
      <div className="journey-pick" role="group" aria-label="Journey">
        {[{ v: "", label: "All journeys", icon: "🌐" }, ...JOURNEYS].map((j) => (
          <button key={j.v} className={journey === j.v ? "on" : ""} onClick={() => setJourney(j.v)}>
            <span aria-hidden>{j.icon}</span> {j.label}
          </button>
        ))}
        <Info k="journey" />
      </div>

      <Card title={journey ? `Funnel · ${JOURNEY_LABEL[journey]}` : "Funnel"} info="funnel"
            right={<span className="muted small">% of previous stage</span>}>
        {data.stages.map((s: any) => (
          <div className="funnel-row" key={s.level}>
            <div>{s.name}{s.level === 2 && <Info k="stage_engaged" />}{s.level === 3 && <Info k="stage_intent" />}</div>
            <div className="funnel-bar" title={`${s.sessions} sessions`}>
              <div style={{ width: `${(100 * s.sessions) / max}%` }} />
            </div>
            <div className="funnel-num">
              {fmtNum(s.sessions)}
              <span className="muted">{s.level > 1 ? `${s.pct_of_prev}%` : ""}</span>
            </div>
          </div>
        ))}
        {(journey === "hotel" || journey === "flight_hotel") && (
          <div className="muted small" style={{ marginTop: 8 }}>
            Booking attempted and Booked count flight bookings only. Hotel bookings aren't synced yet.
          </div>
        )}
      </Card>

      <div className="kpis">
        <Kpi label="Avg user messages / session" info="avg_user_msgs" value={ux.avg_user_msgs_per_session ?? "—"} />
        <Kpi label="Median user msgs before 1st search" info="msgs_to_search" value={ux.median_user_msgs_to_search ?? "—"} />
        <Kpi label="Median minutes to 1st search" info="minutes_to_search" value={ux.median_minutes_to_search ?? "—"} />
        <Kpi label="Sessions with no search" info="no_search" value={`${ux.pct_no_search ?? 0}%`} sub="User chatted but no search ran" />
        <Kpi label="Flow never resolved" info="unresolved" value={`${ux.pct_unresolved_flow ?? 0}%`} sub="currentFlow = unresolved" />
        <Kpi label="Sessions with failure msgs" info="failure_msgs" value={fmtNum(ux.sessions_with_failures)} />
      </div>

      <div className="grid g2">
        <Card title="Weekly funnel" info="funnel">
          <TimeLines
            data={data.weekly}
            x="week"
            series={[
              { key: "sessions", label: "Sessions", color: "var(--series-1)" },
              { key: "searched", label: "Searched", color: "var(--series-2)" },
              { key: "booked", label: "Booked", color: "var(--series-3)" },
            ]}
          />
        </Card>
        <Card title="Where sessions stopped" info="where_stopped"
              right={<Link to={`${chats(journey)}${journey ? "&" : "?"}outcome=no_search`} className="small">Open stalled chats →</Link>}>
          <Bars data={ended} x="name" layout="vertical" height={250}
                series={[{ key: "sessions", label: "Sessions that ended here", color: "var(--series-1)" }]} />
        </Card>
      </div>

      <Card title="By journey (all journeys)" info="by_journey">
        <Table
          rows={data.by_journey}
          onRow={(r) => setJourney(r.journey)}
          cols={[
            { key: "journey", label: "Journey", render: (r) => JOURNEY_LABEL[r.journey] ?? r.journey },
            { key: "sessions", label: "Sessions", num: true },
            { key: "users", label: "Users", num: true },
            { key: "searched", label: "Searched", num: true },
            { key: "results_shown", label: "Results shown", num: true },
            { key: "booking_attempted", label: "Booking attempted", num: true },
            { key: "booked", label: "Booked", num: true },
            { key: "conv", label: "Booked %", num: true, render: (r) => (r.sessions ? `${((100 * r.booked) / r.sessions).toFixed(1)}%` : "—") },
            { key: "avg_user_msgs", label: "Avg user msgs", num: true },
            { key: "open", label: "", render: (r) => <Link to={chats(r.journey)} onClick={(e) => e.stopPropagation()} className="small">Chats →</Link> },
          ]}
        />
      </Card>

      <PlanningVsDirect />
    </>
  );
}
