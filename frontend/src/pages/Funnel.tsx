import { useState } from "react";
import { Link } from "react-router-dom";
import { useApi } from "../api";
import { Bars, Card, ErrorBox, Info, Kpi, Loading, Table, TimeLines } from "../components";
import { fmtNum } from "../format";

const FLOWS = [
  { v: "", label: "All flows" },
  { v: "single_flight", label: "Single flight" },
  { v: "multi_city", label: "Multi-city" },
  { v: "unresolved", label: "Unresolved" },
];
const FLOW_LABEL: Record<string, string> = { single_flight: "Single flight", multi_city: "Multi-city", unresolved: "Unresolved", none: "No flow state" };

export default function Funnel() {
  const [flow, setFlow] = useState("");
  const { data, error, loading } = useApi("/api/funnel", { flow: flow || undefined });
  if (error) return <ErrorBox error={error} />;
  if (loading && !data) return <Loading />;
  const max = data.stages[0]?.sessions || 1;
  const ux = data.ux ?? {};
  const ended = data.stages.filter((s: any) => s.level < 7).map((s: any) => ({ name: s.name, sessions: s.ended_here, level: s.level }));
  return (
    <>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end", gap: 12, flexWrap: "wrap" }}>
        <div>
          <h1>Conversion funnel</h1>
          <div className="page-sub">Each chat session counted at the furthest stage it reached.</div>
        </div>
        <select value={flow} onChange={(e) => setFlow(e.target.value)} aria-label="Flow">
          {FLOWS.map((f) => <option key={f.v} value={f.v}>{f.label}</option>)}
        </select>
      </div>

      <Card title="Funnel" info="funnel" right={<span className="muted small">% of previous stage</span>}>
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
        <Card title="Where sessions stopped" info="where_stopped" right={<Link to="/conversations?outcome=no_search" className="small">Open stalled chats →</Link>}>
          <Bars data={ended} x="name" layout="vertical" height={250}
                series={[{ key: "sessions", label: "Sessions that ended here", color: "var(--series-1)" }]} />
        </Card>
      </div>

      <Card title="By conversation flow" info="by_flow">
        <Table
          rows={data.by_flow}
          cols={[
            { key: "flow", label: "Flow", render: (r) => FLOW_LABEL[r.flow] ?? r.flow },
            { key: "sessions", label: "Sessions", num: true },
            { key: "searched", label: "Searched", num: true },
            { key: "results_shown", label: "Results shown", num: true },
            { key: "booking_attempted", label: "Booking attempted", num: true },
            { key: "booked", label: "Booked", num: true },
            { key: "conv", label: "Booked %", num: true, render: (r) => (r.sessions ? `${((100 * r.booked) / r.sessions).toFixed(1)}%` : "—") },
            { key: "avg_user_msgs", label: "Avg user msgs", num: true },
          ]}
        />
      </Card>
    </>
  );
}
