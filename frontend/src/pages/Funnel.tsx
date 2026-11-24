import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useApi } from "../api";
import { Bars, Card, ErrorBox, Info, Kpi, Loading, Table, TimeLines } from "../components";
import { fmtNum } from "../format";
import { JOURNEYS, JOURNEY_LABEL } from "../journeys";
import { PageHeader, useSub } from "../nav";
import PlanningVsDirect from "./PlanningVsDirect";

const pct = (a: number, b: number) => (b ? `${((100 * a) / b).toFixed(1)}%` : "—");

/** The journey filter: lives in the URL (?journey=) so it stays set when moving between Conversion and Speed & effort. */
function JourneyPick({ journey, set }: { journey: string; set: (j: string) => void }) {
  return (
    <div className="journey-pick" role="group" aria-label="Journey">
      <span className="muted small">Journey:</span>
      {[{ v: "", label: "All journeys", icon: "🌐" }, ...JOURNEYS].map((j) => (
        <button key={j.v} className={journey === j.v ? "on" : ""} onClick={() => set(j.v)}>
          <span aria-hidden>{j.icon}</span> {j.label}
        </button>
      ))}
      <Info k="journey" />
    </div>
  );
}

export default function Funnel() {
  const sub = useSub("/funnel");
  const nav = useNavigate();
  const [sp, setSp] = useSearchParams();
  const filtered = sub.applies.journey;
  const journey = filtered ? sp.get("journey") ?? "" : "";
  const setJourney = (j: string) => { const n = new URLSearchParams(sp); j ? n.set("journey", j) : n.delete("journey"); setSp(n, { replace: true }); };
  const head = <PageHeader path="/funnel" sub={sub}>{filtered && <JourneyPick journey={journey} set={setJourney} />}</PageHeader>;
  const { data, error, loading } = useApi("/api/funnel", { journey: journey || undefined });
  if (sub.key === "planning") return <>{head}<PlanningVsDirect /></>;
  if (error) return <>{head}<ErrorBox error={error} /></>;
  if (loading && !data) return <>{head}<Loading /></>;
  const max = data.stages[0]?.sessions || 1;
  const ux = data.ux ?? {};
  const ended = data.stages.filter((s: any) => s.level < 7).map((s: any) => ({ name: s.name, sessions: s.ended_here, level: s.level }));
  const chats = (j: string, extra = "") => `/conversations?${[j && `journey=${j}`, extra].filter(Boolean).join("&")}`;
  const label = journey ? JOURNEY_LABEL[journey] : "all journeys";
  return (
    <>
      {head}

      {sub.key === "conversion" && (
        <>
          <Card title={`Funnel · ${label}`} info="funnel" right={<span className="muted small">% of previous stage</span>}>
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
              <div className="muted small" style={{ marginTop: 8 }}>Booking attempted and Booked count flight bookings only. Hotel bookings aren't synced yet.</div>
            )}
          </Card>
          <div className="grid g2">
            <Card title={`Where chats stopped · ${label}`} info="where_stopped"
                  right={<Link to={chats(journey, "outcome=no_search")} className="small">Open stalled chats →</Link>}>
              <Bars data={ended} x="name" layout="vertical" height={250}
                    series={[{ key: "sessions", label: "Chats that ended here", color: "var(--series-1)" }]} />
            </Card>
            <Card title={`Weekly funnel · ${label}`} info="funnel">
              <TimeLines data={data.weekly} x="week" series={[
                { key: "sessions", label: "Chats", color: "var(--series-1)" },
                { key: "searched", label: "Searched", color: "var(--series-2)" },
                { key: "booked", label: "Booked", color: "var(--series-3)" },
              ]} />
            </Card>
          </div>
        </>
      )}

      {sub.key === "effort" && (
        <>
          <div className="kpis">
            <Kpi label="Avg user messages / chat" info="avg_user_msgs" value={ux.avg_user_msgs_per_session ?? "—"} />
            <Kpi label="Median user msgs before 1st search" info="msgs_to_search" value={ux.median_user_msgs_to_search ?? "—"} />
            <Kpi label="Median minutes to 1st search" info="minutes_to_search" value={ux.median_minutes_to_search ?? "—"} />
          </div>
          <h3 className="sec-h">Where effort is wasted · {label}</h3>
          <div className="kpis">
            <Kpi label="Chats with no search" info="no_search" value={`${ux.pct_no_search ?? 0}%`}
                 sub={<Link to={chats(journey, "outcome=no_search")}>User chatted but no search ran →</Link>} />
            <Kpi label="Flow never resolved" info="unresolved" value={`${ux.pct_unresolved_flow ?? 0}%`}
                 sub={<Link to={chats(journey, "outcome=unresolved")}>Bookkr never settled on a flow →</Link>} />
            <Kpi label="Chats with failure messages" info="failure_msgs" value={fmtNum(ux.sessions_with_failures)}
                 sub={<Link to={chats(journey, "outcome=failure_msgs")}>Open them →</Link>} />
          </div>
          <div className="muted small">Why chats get stuck, chat by chat, is in the <Link to="/friction/issues">Friction Finder</Link>.</div>
        </>
      )}

      {sub.key === "journeys" && (
        <>
          <div className="grid g2">
            <Card title="Chats by journey" info="journey">
              <Bars data={data.by_journey.map((r: any) => ({ ...r, name: JOURNEY_LABEL[r.journey] ?? r.journey }))} x="name" layout="vertical"
                    height={Math.max(200, data.by_journey.length * 40)}
                    series={[{ key: "sessions", label: "Chats", color: "var(--series-1)" }, { key: "searched", label: "Searched", color: "var(--series-2)" }]} />
            </Card>
            <Card title="Booked % by journey" info="by_journey">
              <Bars data={data.by_journey.map((r: any) => ({ name: JOURNEY_LABEL[r.journey] ?? r.journey,
                                                            booked_pct: r.sessions ? Math.round((1000 * r.booked) / r.sessions) / 10 : 0 }))}
                    x="name" layout="vertical" height={Math.max(200, data.by_journey.length * 40)} fmt={(v) => `${v}%`}
                    series={[{ key: "booked_pct", label: "Booked %", color: "var(--series-3)" }]} />
            </Card>
          </div>
          <Card title="Side by side" info="by_journey" right={<span className="muted small">Click a row to see its funnel</span>}>
            <Table rows={data.by_journey} onRow={(r) => nav(`/funnel/conversion?journey=${r.journey}`)} cols={[
              { key: "journey", label: "Journey", render: (r) => JOURNEY_LABEL[r.journey] ?? r.journey },
              { key: "sessions", label: "Chats", num: true },
              { key: "users", label: "Users", num: true },
              { key: "searched", label: "Searched", num: true },
              { key: "results_shown", label: "Results shown", num: true },
              { key: "booking_attempted", label: "Booking attempted", num: true },
              { key: "booked", label: "Booked", num: true },
              { key: "conv", label: "Booked %", num: true, render: (r) => pct(r.booked, r.sessions) },
              { key: "avg_user_msgs", label: "Avg user msgs", num: true },
              { key: "open", label: "", render: (r) => <Link to={chats(r.journey)} onClick={(e) => e.stopPropagation()} className="small">Chats →</Link> },
            ]} />
          </Card>
        </>
      )}
    </>
  );
}
