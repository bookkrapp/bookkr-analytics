// Growth (old ops board parity + traffic sources): signups and launch vouchers, wallet credit, where visitors come from.
import { useNavigate } from "react-router-dom";
import { useApi } from "../api";
import { Bars, Card, ErrorBox, KpiC, Loading, Table } from "../components";
import { fmtINR, fmtNum } from "../format";
import { PageHeader, useSub } from "../nav";

const dayFmt = (d: string) => new Date(d + "T00:00:00").toLocaleDateString("en-IN", { day: "2-digit", month: "short" });
const pct = (a: number, b: number) => (b ? `${((100 * a) / b).toFixed(1)}%` : "—");

export default function Growth() {
  const sub = useSub("/growth");
  const nav = useNavigate();
  const { data, error, loading } = useApi("/api/growth");
  const head = <PageHeader path="/growth" sub={sub} />;
  if (error) return <>{head}<ErrorBox error={error} /></>;
  if (loading && !data) return <>{head}<Loading /></>;
  const n = data.numbers;
  const w = data.wallet ?? {};
  return (
    <>
      {head}
      {sub.key === "signups" && (
        <>
          <div className="kpis">
            <KpiC label="Signups" n={n.signups} fmt={fmtNum} info="signups" sub="accounts, not guests" />
            <KpiC label="Guest sessions started" n={n.guest_accounts} fmt={fmtNum} />
            <KpiC label="Launch vouchers granted" n={n.vouchers} fmt={fmtNum} sub={fmtINR(n.voucher_inr.value)} />
            <KpiC label="Wallet money spent" n={n.wallet_spent_inr} fmt={fmtINR} sub="on confirmed bookings" />
          </div>
          <div className="grid g2">
            <Card title="Signups and guests per day">
              <Bars data={data.daily} x="date" xFmt={dayFmt} series={[
                { key: "signups", label: "Signups", color: "var(--series-1)" }, { key: "guests", label: "Guests", color: "var(--axis)" }]} />
            </Card>
            <Card title="Signups by how they signed up">
              <Table rows={data.by_signup_source} empty="No signups in this period." cols={[
                { key: "source", label: "Signup source" }, { key: "signups", label: "Signups", num: true }]} />
            </Card>
          </div>
          <div className="small"><a onClick={() => nav("/users/activity")} style={{ cursor: "pointer" }}>How many of them came back →</a></div>
        </>
      )}

      {sub.key === "wallet" && (
        <>
          <div className="kpis">
            <KpiC label="Wallet credit outstanding (now)" n={{ value: w.outstanding_inr, prev: null, change: null }} fmt={fmtINR}
                  sub={`across ${fmtNum(w.wallets_with_balance)} wallets`} info="wallet_outstanding" />
            <KpiC label="Usable on anything" n={{ value: w.any_inr, prev: null, change: null }} fmt={fmtINR} />
            <KpiC label="Flights only" n={{ value: w.flight_inr, prev: null, change: null }} fmt={fmtINR} />
            <KpiC label="Hotels only" n={{ value: w.hotel_inr, prev: null, change: null }} fmt={fmtINR} />
            <KpiC label="Voucher holders who spent" n={{ value: w.vouchers_used, prev: null, change: null }} fmt={fmtNum}
                  sub={`${pct(w.vouchers_used, (w.vouchers_used ?? 0) + (w.vouchers_unused ?? 0))} · ${fmtNum(w.vouchers_unused)} haven't`} />
          </div>
          <Card title="Wallet movements in this period" right={<span className="muted small">Credits (vouchers, admin, referral) and debits (held, spent, released)</span>}>
            <Table rows={data.wallet_entries} empty="No wallet movements in this period." cols={[
              { key: "kind", label: "Kind" }, { key: "reference_type", label: "For" }, { key: "status", label: "Status" },
              { key: "entries", label: "Entries", num: true }, { key: "inr", label: "Amount", num: true, render: (r) => fmtINR(r.inr) }]} />
          </Card>
          <div className="small"><a onClick={() => nav("/overview/bookings")} style={{ cursor: "pointer" }}>Bookings paid partly from the wallet →</a></div>
        </>
      )}

      {sub.key === "sources" && (
        <>
          <div className="kpis">
            <KpiC label="Visits" n={n.visits} fmt={fmtNum} info="visits" />
            <KpiC label="Distinct visitors" n={n.visitors} fmt={fmtNum} />
          </div>
          <div className="grid g2">
            <Card title="Visits by source">
              <Bars data={data.sources} x="source" layout="vertical" height={Math.max(180, data.sources.length * 34)}
                    series={[{ key: "visits", label: "Visits", color: "var(--series-1)" }, { key: "visitors", label: "Visitors", color: "var(--series-2)" }]} />
            </Card>
            <Card title="Campaigns that bring bookers" info="campaigns" right={<span className="muted small">Signed-in users by the tags of their first visit</span>}>
              <Table rows={data.campaigns} empty="No tagged visits in this period." cols={[
                { key: "utm_source", label: "Source" }, { key: "utm_medium", label: "Medium" }, { key: "utm_campaign", label: "Campaign" },
                { key: "users", label: "Users", num: true }, { key: "booked", label: "Booked", num: true },
                { key: "rate", label: "Booked %", num: true, render: (r) => pct(r.booked, r.users) }]} />
            </Card>
          </div>
          <div className="grid g2">
            <Card title="Top referrers">
              <Table rows={data.referrers} cols={[{ key: "referrer", label: "Referrer" }, { key: "visits", label: "Visits", num: true }]} />
            </Card>
            <Card title="Top landing pages">
              <Table rows={data.landings} cols={[{ key: "landing", label: "Landing page" }, { key: "visits", label: "Visits", num: true }]} />
            </Card>
          </div>
        </>
      )}
      {!data.has_previous && <div className="muted small">"All time" has no previous period, so there are no changes to show.</div>}
    </>
  );
}
