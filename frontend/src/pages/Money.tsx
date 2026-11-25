// Overview › Bookings & money and the "top booked" lists of Overview › Demand (old ops board parity).
// Flights and hotels side by side, GBV with its card / wallet split, and every number drills into its bookings.
import { useState } from "react";
import { Link } from "react-router-dom";
import { useApi } from "../api";
import { Bars, Card, DrillDrawer, DrillSpec, ErrorBox, KpiC, Loading, Table, TimeLines } from "../components";
import { fmtDateTime, fmtINR, fmtINRShort, fmtNum, shortId } from "../format";

const PRODUCT = { flight: "✈️ Flights", hotel: "🏨 Hotels" } as const;
const OUTCOME_LABEL: Record<string, string> = {
  confirmed: "confirmed", wallet: "confirmed with wallet", failed: "failed", cancelled: "cancelled", abandoned: "abandoned at payment",
};

export const bookingCols = [
  { key: "created_local", label: "Started (IST)", render: (r: any) => fmtDateTime(r.created_local) },
  { key: "product", label: "Product", render: (r: any) => (r.product === "hotel" ? "🏨 Hotel" : "✈️ Flight") },
  { key: "status", label: "Status" },
  { key: "what", label: "Route / city", render: (r: any) => r.route ?? r.city ?? "—" },
  { key: "airline", label: "Airline", render: (r: any) => r.airline ?? "—" },
  { key: "value_inr", label: "Value", num: true, render: (r: any) => fmtINR(r.value_inr) },
  { key: "wallet_inr", label: "From wallet", num: true, render: (r: any) => (r.wallet_inr ? fmtINR(r.wallet_inr) : "—") },
  { key: "failure_reason", label: "Failure reason", render: (r: any) => <span className="small wrap" style={{ minWidth: 200 }}>{r.failure_reason ?? "—"}</span> },
  { key: "chat", label: "Chat", render: (r: any) => (r.chat_session_id ? <Link to={`/conversations/${r.chat_session_id}`} onClick={(e) => e.stopPropagation()}>Open chat →</Link> : <span className="muted">no chat</span>) },
  { key: "user_id", label: "User", render: (r: any) => <code className="small">{shortId(r.user_id)}</code> },
];

export function bookingDrill(title: string, params: Record<string, any>): DrillSpec {
  return { title, path: "/api/list/bookings", params, cols: bookingCols,
           rowLink: (r) => (r.chat_session_id ? `/conversations/${r.chat_session_id}` : null) };
}

export function MoneyPage() {
  const { data, error, loading } = useApi("/api/money");
  const [drill, setDrill] = useState<DrillSpec | null>(null);
  if (error) return <ErrorBox error={error} />;
  if (loading && !data) return <Loading />;
  const n = data.numbers;
  const open = (product: string, outcome: string) =>
    setDrill(bookingDrill(`${PRODUCT[product as keyof typeof PRODUCT]}: ${OUTCOME_LABEL[outcome]}`, { product, outcome }));
  const per100 = (b: number, s: number) => (s ? `${((100 * b) / s).toFixed(1)} per 100 searches` : "—");
  return (
    <>
      <div className="kpis">
        <KpiC label="Total GBV (flights + hotels)" n={n.total_gbv} fmt={fmtINR} info="gbv" sub="incl. the wallet's share" />
        <KpiC label="Confirmed flights" n={n.flight.confirmed} fmt={fmtNum} onClick={() => open("flight", "confirmed")}
              sub={per100(n.flight.confirmed.value, data.searches.flight_searches)} />
        <KpiC label="Confirmed hotels" n={n.hotel.confirmed} fmt={fmtNum} onClick={() => open("hotel", "confirmed")}
              sub={per100(n.hotel.confirmed.value, data.searches.hotel_searches)} />
      </div>

      <div className="grid g2">
        {(["flight", "hotel"] as const).map((p) => {
          const m = n[p];
          return (
            <Card key={p} title={PRODUCT[p]} info="money_product">
              <div className="kpis">
                <KpiC label="Confirmed" n={m.confirmed} fmt={fmtNum} onClick={() => open(p, "confirmed")} />
                <KpiC label="With wallet" n={m.with_wallet} fmt={fmtNum} onClick={() => open(p, "wallet")}
                      sub={`${fmtNum(m.without_wallet.value)} without`} />
                <KpiC label="GBV" n={m.gbv} fmt={fmtINR} />
                <KpiC label="Paid by card / UPI" n={m.card} fmt={fmtINR} />
                <KpiC label="Paid from wallet" n={m.wallet} fmt={fmtINR} />
                <KpiC label="Average booking" n={m.avg} fmt={fmtINR} />
                <KpiC label="Failed" n={m.failed} fmt={fmtNum} onClick={() => open(p, "failed")} tone={m.failed.value ? "bad" : undefined} />
                <KpiC label="Cancelled" n={m.cancelled} fmt={fmtNum} onClick={() => open(p, "cancelled")} />
                {p === "flight" && <KpiC label="Abandoned at payment" n={m.abandoned} fmt={fmtNum} onClick={() => open(p, "abandoned")}
                                         info="abandoned" />}
              </div>
              {m.not_inr.value > 0 && <div className="muted small">{m.not_inr.value} confirmed bookings not in INR are left out of the money totals.</div>}
            </Card>
          );
        })}
      </div>

      <div className="grid g2">
        <Card title="Confirmed bookings per day">
          <Bars data={data.daily} x="date" stacked xFmt={(d) => new Date(d + "T00:00:00").toLocaleDateString("en-IN", { day: "2-digit", month: "short" })}
                series={[{ key: "flights", label: "Flights", color: "var(--series-1)" }, { key: "hotels", label: "Hotels", color: "var(--series-3)" }]} />
        </Card>
        <Card title="GBV per day, and the wallet's share">
          <TimeLines data={data.daily} fmt={fmtINR} yFmt={fmtINRShort}
                     series={[{ key: "gbv", label: "GBV", color: "var(--series-1)" }, { key: "wallet", label: "From wallet", color: "var(--series-2)" }]} />
        </Card>
      </div>
      {!data.has_previous && <div className="muted small">"All time" has no previous period, so there are no changes to show.</div>}
      <DrillDrawer spec={drill} onClose={() => setDrill(null)} />
    </>
  );
}

/** Top booked airlines, routes and hotel cities, each drilling into its bookings. */
export function TopBooked() {
  const { data, error, loading } = useApi("/api/money");
  const [drill, setDrill] = useState<DrillSpec | null>(null);
  if (error) return <ErrorBox error={error} />;
  if (loading && !data) return <Loading />;
  const cols = (label: string) => [
    { key: "label", label }, { key: "bookings", label: "Bookings", num: true },
    { key: "gbv", label: "GBV", num: true, render: (r: any) => fmtINR(r.gbv) },
  ];
  return (
    <>
      <div className="grid g3">
        <Card title="Top booked airlines" right={<span className="muted small">Click a row for its bookings</span>}>
          <Table rows={data.top.airlines} empty="No confirmed flights." cols={cols("Airline")}
                 onRow={(r) => setDrill(bookingDrill(`Bookings on ${r.label}`, { product: "flight", outcome: "confirmed", airline: r.label }))} />
        </Card>
        <Card title="Top booked routes">
          <Table rows={data.top.routes} empty="No confirmed flights." cols={cols("Route")}
                 onRow={(r) => setDrill(bookingDrill(`Bookings ${r.label}`, { product: "flight", outcome: "confirmed", route: r.label }))} />
        </Card>
        <Card title="Top booked hotel cities">
          <Table rows={data.top.cities} empty="No confirmed hotels." cols={cols("City")}
                 onRow={(r) => setDrill(bookingDrill(`Hotel bookings in ${r.label}`, { product: "hotel", outcome: "confirmed", city: r.label }))} />
        </Card>
      </div>
      <DrillDrawer spec={drill} onClose={() => setDrill(null)} />
    </>
  );
}
