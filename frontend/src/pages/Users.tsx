import { useNavigate } from "react-router-dom";
import { useApi } from "../api";
import { Bars, Card, ErrorBox, Info, Kpi, Loading, Table } from "../components";
import { fmtDate, fmtINR, fmtNum, shortId } from "../format";
import { PageHeader, useSub } from "../nav";

const WEEKS = [0, 1, 2, 3, 4, 5, 6, 7];

function HeatCell({ v }: { v: number | null | undefined }) {
  if (v == null) return <td className="cell muted">·</td>;
  // sequential single-hue ramp: one hue, opacity carries magnitude
  const a = Math.round(12 + (v / 100) * 78);
  return (
    <td className="cell" title={`${v}% of the cohort active`}
        style={{ background: `color-mix(in srgb, var(--series-1) ${a}%, transparent)`, color: a > 55 ? "#fff" : "var(--text-primary)" }}>
      {Math.round(v)}%
    </td>
  );
}

export default function Users() {
  const sub = useSub("/users");
  const nav = useNavigate();
  const { data, error, loading } = useApi("/api/users");
  const head = <PageHeader path="/users" sub={sub} />;
  if (error) return <>{head}<ErrorBox error={error} /></>;
  if (loading && !data) return <>{head}<Loading /></>;
  const k = data.kpis, p = data.previous ?? {}, x = data.extra ?? {};
  const heavy = data.top_users.filter((u: any) => u.sessions_all_time >= data.heavy_user_threshold);
  const dayFmt = (d: string) => new Date(d + "T00:00:00").toLocaleDateString("en-IN", { day: "2-digit", month: "short" });
  return (
    <>
      {head}

      {heavy.length > 0 && sub.key !== "retention" && (
        <div className="notice">
          <b>{heavy.length} user{heavy.length > 1 ? "s have" : " has"} {data.heavy_user_threshold}+ chats each</b>{" "}
          ({heavy.map((u: any) => shortId(u.user_id)).join(", ")}). These are probably team or test accounts and they inflate the returning-user numbers.
          Add their IDs to <code>INTERNAL_USER_IDS</code> in <code>.env</code> and refresh to exclude them.
        </div>
      )}

      {sub.key === "activity" && (<>
      <div className="kpis">
        <Kpi label="Active users" info="u_active" value={fmtNum(k.active_users)} cur={k.active_users} prev={p.active_users} />
        <Kpi label="New users" info="u_new" value={fmtNum(k.new_users)} cur={k.new_users} prev={p.new_users} />
        <Kpi label="Returning users" info="u_returning" value={fmtNum(k.returning_users)} cur={k.returning_users} prev={p.returning_users} />
        <Kpi label="Returning share" info="u_returning_pct" value={`${k.returning_pct}%`} cur={k.returning_pct} prev={p.returning_pct} />
        <Kpi label="Used on 2+ days" info="u_repeat" value={fmtNum(k.repeat_in_period)}
             sub={k.active_users ? `${Math.round((100 * k.repeat_in_period) / k.active_users)}% of active users` : undefined} />
        <Kpi label="Sessions per user" info="u_sessions_per_user" value={x.avg_sessions_per_user ?? "—"} />
      </div>

      <div className="grid g2">
        <Card title="Daily active users: new vs returning" info="u_daily">
          <Bars data={data.daily} x="date" stacked xFmt={dayFmt}
                series={[
                  { key: "new_users", label: "New (first day)", color: "var(--series-1)" },
                  { key: "returning_users", label: "Returning", color: "var(--series-2)" },
                ]} />
        </Card>
        <Card title="How many days users were active" info="u_frequency">
          <Bars data={data.frequency} x="bucket" series={[{ key: "users", label: "Users", color: "var(--series-1)" }]} />
        </Card>
      </div>
      </>)}

      {sub.key === "retention" && (<>
      <div className="kpis">
        <Kpi label="Came back within 7 days" info="u_r7" value={x.returned_7d_pct != null ? `${x.returned_7d_pct}%` : "—"}
             sub={`n = ${x.returned_7d_base ?? 0} users`} />
        <Kpi label="Came back within 30 days" info="u_r30" value={x.returned_30d_pct != null ? `${x.returned_30d_pct}%` : "—"}
             sub={`n = ${x.returned_30d_base ?? 0} users`} />
        <Kpi label="Median days between visits" info="u_gap" value={x.median_days_between_visits ?? "—"} />
        <Kpi label="Repeat bookers" info="u_repeat_bookers" value={fmtNum(x.repeat_bookers)}
             sub={`of ${fmtNum(x.booking_users)} users who booked`} />
      </div>

      <Card title="Weekly cohorts" info="u_cohorts" right={<span className="muted small">All-time · % of cohort active N weeks after their first week</span>}>
        {data.cohorts.length ? (
          <div className="table-wrap">
            <table className="heat">
              <thead>
                <tr><th>First week</th><th className="num">Users</th>{WEEKS.map((w) => <th key={w} className="num">W{w}</th>)}</tr>
              </thead>
              <tbody>
                {data.cohorts.map((c: any) => (
                  <tr key={c.cohort}>
                    <td>{fmtDate(c.cohort)}</td>
                    <td className="num">{c.size}</td>
                    {WEEKS.map((w) => <HeatCell key={w} v={c.weeks[w]} />)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <span className="muted">No cohorts yet.</span>}
      </Card>
      </>)}

      {sub.key === "behaviour" && (<>
      <Card title="Behaviour by chat number" info="u_by_session">
        <Table rows={data.by_session_number} cols={[
          { key: "bucket", label: "User's…" },
          { key: "sessions", label: "Chats", num: true },
          { key: "searched_pct", label: "Reached a search", num: true, render: (r) => `${r.searched_pct}%` },
          { key: "booked_pct", label: "Booked", num: true, render: (r) => `${r.booked_pct}%` },
          { key: "msgs_to_search", label: "Msgs before 1st search", num: true, info: "msgs_to_search", render: (r) => r.msgs_to_search ?? "—" },
        ]} />
      </Card>

      <Card title="Most active users" info="u_top" right={<span className="muted small">Click a user to see their chats</span>}>
        <Table
          rows={data.top_users}
          onRow={(r) => nav(`/conversations?user=${encodeURIComponent(r.user_id)}`)}
          cols={[
            { key: "user_id", label: "User", render: (r) => (
              <span style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
                <code className="small">{shortId(r.user_id)}</code>
                {r.is_guest && <span className="chip">guest</span>}
                {r.sessions_all_time >= data.heavy_user_threshold && <span className="chip flag">likely team/test</span>}
              </span>) },
            { key: "first_day", label: "First seen", render: (r) => fmtDate(r.first_day) },
            { key: "last_day", label: "Last seen", render: (r) => fmtDate(r.last_day) },
            { key: "active_days", label: "Active days (period)", num: true },
            { key: "total_active_days", label: "Active days (all)", num: true },
            { key: "sessions", label: "Chats (period)", num: true },
            { key: "searches_all_time", label: "Searches (all)", num: true },
            { key: "bookings_all_time", label: "Bookings (all)", num: true },
            { key: "gmv_all_time", label: "GMV (all)", num: true, render: (r) => (r.gmv_all_time ? fmtINR(r.gmv_all_time) : "—") },
          ]}
        />
        <div className="muted small" style={{ marginTop: 8 }}>
          Personal details aren't copied into this tool; users are shown by the first 8 characters of their ID.<Info text="The sync keeps only the user ID, whether it's a guest account, and the email domain. Names, emails and phone numbers never leave Neon." />
        </div>
      </Card>
      </>)}
    </>
  );
}
