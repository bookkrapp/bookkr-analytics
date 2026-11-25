// Users › Activity and Retention additions (old ops board parity): accounts vs guests, web vs app, DAU / WAU,
// people who came back after their first day, and people from the previous period who stayed.
import { useApi } from "../api";
import { Bars, Card, ErrorBox, KpiC, Loading } from "../components";
import { fmtNum } from "../format";

const dayFmt = (d: string) => new Date(d + "T00:00:00").toLocaleDateString("en-IN", { day: "2-digit", month: "short" });
const cmp = (value: any, prev?: any) => {
  if (typeof value !== "number" || typeof prev !== "number") return { value, prev: prev ?? null, change: null };
  if (!prev) return { value, prev, change: value ? "new" : null };
  const p = Math.round((100 * (value - prev)) / prev);
  return { value, prev, change: p === 0 ? "0%" : p > 0 ? `+${p}%` : `−${-p}%` };
};

export function AudienceActivity() {
  const { data, error, loading } = useApi("/api/audience");
  if (error) return <ErrorBox error={error} />;
  if (loading && !data) return <Loading />;
  const n = data.numbers;
  const l = data.live;
  return (
    <>
      <h3 className="sec-h">Who was active (any app event a person caused)</h3>
      <div className="kpis">
        <KpiC label="Active people" n={n.active} fmt={fmtNum} info="active_people" />
        <KpiC label="Accounts" n={n.accounts} fmt={fmtNum} sub="signed in" />
        <KpiC label="Guests" n={n.guests} fmt={fmtNum} sub="one browser or device each" />
        <KpiC label="On the web" n={n.web} fmt={fmtNum} />
        <KpiC label="In the app" n={n.app} fmt={fmtNum} />
      </div>
      <div className="kpis">
        <KpiC label={`Active on ${dayFmt(l.as_of)} (last synced day)`} n={cmp(l.dau_today, l.dau_yesterday)} fmt={fmtNum} sub="vs the day before" />
        <KpiC label="Active in the last 7 days (WAU)" n={cmp(l.wau, l.wau_before)} fmt={fmtNum} sub="vs the 7 days before" />
      </div>
      <div className="grid g2">
        <Card title="Daily active: accounts vs guests">
          <Bars data={data.daily} x="date" stacked xFmt={dayFmt} series={[
            { key: "accounts", label: "Accounts", color: "var(--series-1)" }, { key: "guests", label: "Guests", color: "var(--axis)" }]} />
        </Card>
        <Card title="Daily active: web vs app">
          <Bars data={data.daily} x="date" stacked xFmt={dayFmt} series={[
            { key: "web", label: "Web", color: "var(--series-2)" }, { key: "app", label: "App", color: "var(--series-3)" }]} />
        </Card>
      </div>
    </>
  );
}

export function AudienceRetention() {
  const { data, error, loading } = useApi("/api/audience");
  if (error) return <ErrorBox error={error} />;
  if (loading && !data) return <Loading />;
  const n = data.numbers;
  const r = data.retained;
  return (
    <div className="kpis">
      <KpiC label="Came back after their first day" n={n.came_back_pct} fmt={(v) => `${v}%`} info="came_back"
            sub={`${fmtNum(n.came_back.value)} of ${fmtNum(n.engaged.value)} who used Bookkr`} />
      <KpiC label="Stayed from the previous period" n={{ value: r?.stayed_pct ?? null, prev: null, change: null }} fmt={(v) => `${v}%`}
            info="retained" sub={r ? `${fmtNum(r.stayed)} of ${fmtNum(r.cohort)} active before were active again` : "needs a range with a previous period"} />
    </div>
  );
}
