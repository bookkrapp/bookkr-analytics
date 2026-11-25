// Funnel › Checkout steps: once a user starts booking (the fare screen), which step do they stop at?
// Steps come from the app's booking_step events; each step drills into the attempts that stopped there, with chats.
import { useState } from "react";
import { Link } from "react-router-dom";
import { useApi } from "../api";
import { Card, DrillDrawer, DrillSpec, ErrorBox, KpiC, Loading, Table, TimeLines } from "../components";
import { fmtDateTime, fmtNum } from "../format";

const STEP_LABEL: Record<string, string> = {
  fare: "Fare chosen", travellers: "Traveller details", review: "Review", extras: "Extras (baggage, meals)",
  seats: "Seats", rules: "Fare rules", payment: "Payment",
};
const pct = (a: number, b: number) => (b ? `${((100 * a) / b).toFixed(1)}%` : "—");

export default function Checkout() {
  const [product, setProduct] = useState("flight");
  const { data, error, loading } = useApi("/api/checkout", { product });
  const [drill, setDrill] = useState<DrillSpec | null>(null);
  if (error) return <ErrorBox error={error} />;
  if (loading && !data) return <Loading />;
  const steps: any[] = data.steps;
  const top = Math.max(data.attempts, 1);
  const stopped = (step: string | null, title: string): DrillSpec => ({
    title, path: "/api/list/checkout", params: { product, last_step: step ?? undefined },
    rowLink: (r) => (r.chat_session_id ? `/conversations/${r.chat_session_id}` : null),
    cols: [
      { key: "started_local", label: "Started (IST)", render: (r) => fmtDateTime(r.started_local) },
      { key: "last_step", label: "Last step seen", render: (r) => STEP_LABEL[r.last_step] ?? r.last_step ?? "—" },
      { key: "steps_seen", label: "Steps seen", num: true },
      { key: "payment_opened", label: "Payment opened", render: (r) => (r.payment_opened ? "yes" : "no") },
      { key: "minutes", label: "Minutes in checkout", num: true },
      { key: "chat", label: "Chat", render: (r) => (r.chat_session_id ? <Link to={`/conversations/${r.chat_session_id}`} onClick={(e) => e.stopPropagation()}>Open chat →</Link> : "—") },
    ],
  });
  return (
    <>
      <div className="journey-pick">
        <span className="muted small">Product:</span>
        {[["flight", "✈️ Flights"], ["hotel", "🏨 Hotels"]].map(([k, l]) => (
          <button key={k} className={product === k ? "on" : ""} onClick={() => setProduct(k)}>{l}</button>
        ))}
      </div>
      <div className="kpis">
        <KpiC label="Checkout attempts" n={{ value: data.attempts, prev: null, change: null }} fmt={fmtNum}
              sub={`from ${fmtNum(data.chats)} chats`} onClick={() => setDrill(stopped(null, "All checkout attempts"))} />
        <KpiC label="Reached payment" n={{ value: data.payment_opened, prev: null, change: null }} fmt={fmtNum}
              sub={`${pct(data.payment_opened, data.attempts)} of attempts`} />
        <KpiC label="Confirmed bookings" n={{ value: data.confirmed, prev: null, change: null }} fmt={fmtNum}
              sub={`${pct(data.confirmed, data.attempts)} of attempts`} />
      </div>
      <Card title="Checkout steps, in the order users see them" info="checkout_steps" right={<span className="muted small">Click a step for the attempts that stopped there</span>}>
        {!steps.length ? <div className="empty">No checkout steps recorded for this product in this period.</div> : (
          <>
            {steps.map((s, i) => (
              <div className="funnel-row clickable" key={s.step} onClick={() => setDrill(stopped(s.step, `Stopped at: ${STEP_LABEL[s.step] ?? s.step}`))}
                   title="Show the attempts whose last step was this one">
                <div>{STEP_LABEL[s.step] ?? s.step}</div>
                <div className="funnel-bar"><div style={{ width: `${(100 * s.viewed) / top}%`, background: "var(--series-2)" }} /></div>
                <div className="funnel-num">{fmtNum(s.viewed)}<span className="muted">{i ? pct(s.viewed, steps[i - 1].viewed) : ""}</span></div>
              </div>
            ))}
            <div className="funnel-row">
              <div><b>Payment opened</b></div>
              <div className="funnel-bar"><div style={{ width: `${(100 * data.payment_opened) / top}%`, background: "var(--series-3)" }} /></div>
              <div className="funnel-num">{fmtNum(data.payment_opened)}</div>
            </div>
            <div className="funnel-row">
              <div><b>Confirmed</b></div>
              <div className="funnel-bar"><div style={{ width: `${(100 * data.confirmed) / top}%`, background: "var(--good)" }} /></div>
              <div className="funnel-num">{fmtNum(data.confirmed)}</div>
            </div>
          </>
        )}
      </Card>
      <div className="grid g2">
        <Card title="Each step: viewed, completed, skipped">
          <Table rows={steps} empty="—" onRow={(r) => setDrill(stopped(r.step, `Stopped at: ${STEP_LABEL[r.step] ?? r.step}`))} cols={[
            { key: "step", label: "Step", render: (r) => STEP_LABEL[r.step] ?? r.step },
            { key: "viewed", label: "Viewed", num: true },
            { key: "completed", label: "Completed", num: true },
            { key: "done", label: "Completed %", num: true, render: (r) => pct(r.completed, r.viewed) },
            { key: "skipped", label: "Skipped", num: true },
          ]} />
        </Card>
        <Card title="Checkout attempts per day">
          <TimeLines data={data.daily} series={[{ key: "attempts", label: "Attempts", color: "var(--series-2)" },
                                               { key: "payment_opened", label: "Reached payment", color: "var(--series-3)" }]} />
        </Card>
      </div>
      <DrillDrawer spec={drill} onClose={() => setDrill(null)} />
    </>
  );
}
