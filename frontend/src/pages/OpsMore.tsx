// Ops › Payments and Ops › Support (old ops board parity), with drill-downs to the attempts and the chats.
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useApi } from "../api";
import { Bars, Card, DrillDrawer, DrillSpec, ErrorBox, KpiC, Loading, Table } from "../components";
import { fmtDateTime, fmtINR, fmtIST, fmtNum, shortId } from "../format";
import { age } from "./NeedsAction";

const dayFmt = (d: string) => new Date(d + "T00:00:00").toLocaleDateString("en-IN", { day: "2-digit", month: "short" });

const paymentCols = [
  { key: "created_local", label: "When (IST)", render: (r: any) => fmtDateTime(r.created_local) },
  { key: "status", label: "Status" },
  { key: "method", label: "Method" },
  { key: "inr", label: "Amount", num: true, render: (r: any) => fmtINR(r.inr) },
  { key: "error_description", label: "Razorpay's reason", render: (r: any) => <span className="small wrap" style={{ minWidth: 220 }}>{r.error_description ?? "—"}</span> },
  { key: "product", label: "For" },
  { key: "booking_status", label: "Booking", render: (r: any) => r.booking_status ?? "—" },
  { key: "chat", label: "Chat", render: (r: any) => (r.chat_session_id ? <Link to={`/conversations/${r.chat_session_id}`} onClick={(e) => e.stopPropagation()}>Open chat →</Link> : "—") },
  { key: "razorpay_payment_id", label: "Razorpay id", render: (r: any) => <code className="small">{r.razorpay_payment_id ?? "—"}</code> },
];

function paymentDrill(title: string, params: Record<string, any>): DrillSpec {
  return { title, path: "/api/list/payments", params, cols: paymentCols,
           rowLink: (r) => (r.chat_session_id ? `/conversations/${r.chat_session_id}` : null) };
}

export function PaymentsPage() {
  const { data, error, loading } = useApi("/api/payments");
  const [drill, setDrill] = useState<DrillSpec | null>(null);
  if (error) return <ErrorBox error={error} />;
  if (loading && !data) return <Loading />;
  const n = data.numbers;
  return (
    <>
      <div className="kpis">
        <KpiC label="Payment success rate" n={n.success_pct} fmt={(v) => `${v}%`} info="payment_success"
              sub={`${fmtNum(n.attempts.value)} attempts`} />
        <KpiC label="Succeeded" n={n.succeeded} fmt={fmtNum} onClick={() => setDrill(paymentDrill("Successful payments", { status: "succeeded" }))}
              sub={fmtINR(n.succeeded_inr.value)} />
        <KpiC label="Failed" n={n.failed} fmt={fmtNum} tone={n.failed.value ? "bad" : undefined}
              onClick={() => setDrill(paymentDrill("Failed payments", { status: "failed" }))} />
        <KpiC label="Pending" n={n.pending} fmt={fmtNum} onClick={() => setDrill(paymentDrill("Pending payments", { status: "pending" }))} />
        <KpiC label="Refunds" n={n.refunds} fmt={fmtNum} sub={`${fmtINR(n.refund_inr.value)} · ${n.refunds_failed.value} failed`} />
      </div>
      <div className="grid g2">
        <Card title="By method" right={<span className="muted small">Click a method for its attempts</span>}>
          <Table rows={data.by_method} empty="No payment attempts." onRow={(r) => setDrill(paymentDrill(`Payments by ${r.method}`, { method: r.method }))} cols={[
            { key: "method", label: "Method" },
            { key: "success_pct", label: "Success", num: true, render: (r) => (r.success_pct == null ? "—" : `${r.success_pct}%`) },
            { key: "succeeded", label: "Succeeded", num: true },
            { key: "failed", label: "Failed", num: true },
            { key: "pending", label: "Pending", num: true },
          ]} />
        </Card>
        <Card title="Why payments failed" right={<span className="muted small">Click a reason for its attempts</span>}>
          <Table rows={data.reasons} empty="No failed payments." onRow={(r) => setDrill(paymentDrill(`Failed: ${r.reason}`, { status: "failed", reason: r.reason }))} cols={[
            { key: "reason", label: "Razorpay's reason", render: (r) => <span className="wrap" style={{ minWidth: 240 }}>{r.reason}</span> },
            { key: "source", label: "Source", render: (r) => r.source ?? "—" },
            { key: "attempts", label: "Attempts", num: true },
          ]} />
        </Card>
      </div>
      <div className="grid g2">
        <Card title="Payment attempts per day">
          <Bars data={data.daily} x="date" stacked xFmt={dayFmt} series={[
            { key: "succeeded", label: "Succeeded", color: "var(--good)" }, { key: "failed", label: "Failed", color: "var(--critical)" }]} />
        </Card>
        <Card title="By product">
          <Table rows={data.by_product} cols={[
            { key: "product", label: "Product" }, { key: "succeeded", label: "Succeeded", num: true }, { key: "failed", label: "Failed", num: true }]} />
        </Card>
      </div>
      <DrillDrawer spec={drill} onClose={() => setDrill(null)} />
    </>
  );
}

export function SupportPage() {
  const nav = useNavigate();
  const { data, error, loading } = useApi("/api/support");
  if (error) return <ErrorBox error={error} />;
  if (loading && !data) return <Loading />;
  const a = data.awaiting ?? {};
  const o = data.opened ?? {};
  return (
    <>
      <div className="kpis">
        <KpiC label="Awaiting our reply (now)" n={{ value: a.total ?? 0, prev: null, change: null }} fmt={fmtNum}
              tone={a.over_24h ? "bad" : a.total ? "warn" : undefined} sub={a.total ? `oldest ${age(a.oldest_wait_min)}` : "as of the last sync"}
              onClick={() => nav("/ops/attention")} />
        <KpiC label="Tickets opened" n={{ value: o.opened ?? 0, prev: null, change: null }} fmt={fmtNum} />
        <KpiC label="Closed since" n={{ value: o.closed_since ?? 0, prev: null, change: null }} fmt={fmtNum} />
        <KpiC label="Median time to close" n={{ value: o.median_hours_to_close, prev: null, change: null }} fmt={(v) => `${v} h`} />
      </div>
      <div className="grid g2">
        <Card title="By priority and status">
          <Table rows={data.by} empty="No tickets in this period." cols={[
            { key: "priority", label: "Priority" }, { key: "status", label: "Status" }, { key: "tickets", label: "Tickets", num: true }]} />
        </Card>
        <Card title="Tickets" right={<span className="muted small">Click a ticket for the user's chats</span>}>
          <Table rows={data.tickets} empty="No tickets in this period." onRow={(r) => nav(`/conversations?user=${encodeURIComponent(r.user_id)}`)} cols={[
            { key: "number", label: "Ticket", render: (r) => `#${r.number}` },
            { key: "priority", label: "Priority" }, { key: "status", label: "Status" }, { key: "source", label: "Source" },
            { key: "created_local", label: "Opened (IST)", render: (r) => fmtIST(r.created_local) },
            { key: "hours_open", label: "Hours open", num: true },
            { key: "user_id", label: "User", render: (r) => <code className="small">{shortId(r.user_id)}</code> },
          ]} />
        </Card>
      </div>
      <div className="muted small">Ticket subjects and messages aren't copied into this tool (they can hold personal details).</div>
    </>
  );
}
