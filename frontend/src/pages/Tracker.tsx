// Feature tracker: every feature of the old Bookkr ops dashboard (ops-dashboard/ in the worktree-ops-dashboard branch)
// and where it lives here now. Status: had = Bookr Analyst already had it; added = built for parity (2026-11-25);
// partial = here, with a difference; not = not carried over, and why. Update this list when a row changes.
import { useState } from "react";
import { Link } from "react-router-dom";
import { Card } from "../components";

type Status = "had" | "added" | "partial" | "not";
type Row = { feature: string; old: string; status: Status; where?: string; to?: string; note?: string };
const LABEL: Record<Status, string> = { had: "Already had", added: "Added", partial: "Added, partly", not: "Not carried over" };

const GROUPS: { group: string; rows: Row[] }[] = [
  { group: "🗓️ Time range and comparison", rows: [
    { feature: "Presets Today, Yesterday, Last 7 days, This month", old: "Range buttons", status: "added", where: "Top bar on every page", to: "/overview" },
    { feature: "Custom from / to", old: "Date and time, IST", status: "partial", where: "Top bar › Custom", note: "Whole IST days, not minutes; up to 366 days (old board: 92)." },
    { feature: "Change vs the previous period on every number", old: "+20% / −8% / new", status: "added", where: "KPI tiles on Overview, Bookings & money, Payments, Growth, Users, Quality", to: "/overview/bookings" },
    { feature: "Range kept in the page URL (shareable link)", old: "?range= / ?from=&to=", status: "not", note: "The range is kept in your browser instead. Can add if you share links often." },
  ] },
  { group: "🚨 Needs action", rows: [
    { feature: "Paid, not booked (Razorpay payments and webhooks are the truth; late authorizations; refunds and voids excluded)", old: "Red strip + rows", status: "added", where: "Ops › Needs action; strip on Overview › Summary", to: "/ops/attention" },
    { feature: "Wallet money still held on a failed booking", old: "Part of paid-not-booked", status: "added", where: "Ops › Needs action", to: "/ops/attention" },
    { feature: "Paid, stuck at TripJack over 30 min (flights and hotels)", old: "Rows with age", status: "added", where: "Ops › Needs action", to: "/ops/attention" },
    { feature: "Support awaiting our reply: by priority, oldest wait, over 24 h", old: "Strip line", status: "added", where: "Ops › Needs action and Ops › Support", to: "/ops/support" },
    { feature: "Always live (20-second cache)", old: "Live queries on production", status: "partial", note: "As of the last sync (your choice, 25 Nov). Refresh data brings it up to date." },
  ] },
  { group: "💰 Bookings and money", rows: [
    { feature: "Confirmed bookings per product: flights and hotels", old: "Flights / Hotels columns", status: "added", where: "Overview › Bookings & money", to: "/overview/bookings", note: "Hotels were not in Bookr Analyst before." },
    { feature: "Confirmed with / without wallet", old: "Row", status: "added", where: "Overview › Bookings & money", to: "/overview/bookings" },
    { feature: "GBV incl. the wallet's share, split card / wallet", old: "Row", status: "added", where: "Overview › Bookings & money", to: "/overview/bookings" },
    { feature: "Average booking value", old: "Row", status: "added", where: "Overview › Bookings & money", to: "/overview/bookings", note: "Flights only before; hotels added." },
    { feature: "Cancelled · failed (abandoned checkouts left out of failed)", old: "Row", status: "added", where: "Overview › Bookings & money", to: "/overview/bookings", note: "Abandoned is shown as its own number too." },
    { feature: "Total GBV, flights + hotels", old: "Row", status: "added", where: "Overview › Bookings & money", to: "/overview/bookings" },
    { feature: "Bookings not in INR left out of money totals", old: "Note", status: "added", where: "Overview › Bookings & money" },
  ] },
  { group: "📈 Growth", rows: [
    { feature: "Search → booking (bookings per 100 searches), per product", old: "Line", status: "added", where: "Overview › Bookings & money", to: "/overview/bookings" },
    { feature: "Signups (accounts, not guests, not deleted)", old: "Number", status: "added", where: "Growth › Signups & vouchers", to: "/growth/signups" },
    { feature: "Launch vouchers granted, count and ₹", old: "Number", status: "added", where: "Growth › Signups & vouchers", to: "/growth/signups" },
    { feature: "Wallet credit outstanding; voucher holders who spent / didn't", old: "Live line", status: "added", where: "Growth › Wallet", to: "/growth/wallet" },
    { feature: "Chat sessions in range", old: "Number", status: "had", where: "Overview › Summary", to: "/overview/summary" },
    { feature: "Top booked airlines and hotel cities", old: "Top 5 lists", status: "added", where: "Overview › Demand (+ top booked routes)", to: "/overview/demand" },
    { feature: "Top searched routes and cities", old: "Top 5 lists", status: "had", where: "Overview › Demand", to: "/overview/demand" },
  ] },
  { group: "👥 Users", rows: [
    { feature: "Active people, web vs app", old: "Number", status: "added", where: "Users › Activity", to: "/users/activity" },
    { feature: "Accounts vs guests", old: "Split everywhere", status: "added", where: "Users › Activity", to: "/users/activity" },
    { feature: "DAU today / yesterday, WAU vs the week before", old: "Live numbers", status: "added", where: "Users › Activity", to: "/users/activity", note: "For the last synced day." },
    { feature: "Active now (last 5 minutes)", old: "Live number", status: "not", note: "Needs live queries on production; the synced copy can't say who is online now." },
    { feature: "Returning: used Bookkr on a later day than their first", old: "Rate", status: "added", where: "Users › Retention", to: "/users/retention" },
    { feature: "Retained: active in the previous period and again in this one", old: "Rate", status: "added", where: "Users › Retention", to: "/users/retention" },
  ] },
  { group: "💳 Payments", rows: [
    { feature: "Razorpay success rate, by method (UPI, card, netbanking, other)", old: "Line", status: "added", where: "Ops › Payments", to: "/ops/payments" },
    { feature: "Pending attempts", old: "Count", status: "added", where: "Ops › Payments", to: "/ops/payments" },
    { feature: "Top failure reasons", old: "Top 3", status: "added", where: "Ops › Payments (top 10)", to: "/ops/payments" },
    { feature: "Refunds", old: "Declined by the owner", status: "added", where: "Ops › Payments", to: "/ops/payments", note: "Added anyway: count, ₹ and failed refunds." },
  ] },
  { group: "✅ Quality and operations", rows: [
    { feature: "Reply thumbs up / down and thumbs-down share", old: "Line", status: "added", where: "Quality › Reply ratings", to: "/quality/ratings" },
    { feature: "Latest thumbs-down comments", old: "Latest 5", status: "added", where: "Quality › Reply ratings", to: "/quality/ratings", note: "All ratings in range, each opening its chat." },
    { feature: "500s by endpoint", old: "Top 5 from ErrorLog", status: "partial", where: "Quality › Server errors", to: "/quality/errors", note: "From the app's server_error events: ErrorLog doesn't exist in production. Latest-five list not shown." },
    { feature: "TripJack hotel catalogue sync: last run, stale after 26 h", old: "Live line", status: "added", where: "Quality › Operations", to: "/quality/operations", note: "Last 15 runs." },
    { feature: "Service status table and overall status line", old: "Live probes", status: "added", where: "Quality › Operations", to: "/quality/operations", note: "Each service reads NOT SET UP until its URL / key is added on Render." },
    { feature: "Provider traffic rows (success share, avg ms from ProviderCallLog)", old: "Per provider", status: "not", note: "ProviderCallLog doesn't exist in production yet." },
    { feature: "TripJack flights row (judged from real calls only)", old: "Passive row", status: "not", note: "Same: needs ProviderCallLog. Never probed, to protect the look-to-book ratio." },
    { feature: "Excluded accounts (OPS_EXCLUDED_EMAILS)", old: "Exclusion line", status: "had", where: "Top bar › Exclude internal users (INTERNAL_USER_IDS / INTERNAL_EMAIL_DOMAINS)" },
    { feature: "Login", old: "Bookkr admin login", status: "had", where: "Browser login (BASIC_AUTH_PASSWORD on Render)" },
  ] },
];

const BEYOND = [
  { feature: "Checkout steps funnel (fare → travellers → … → payment → confirmed), drilling to each stalled attempt's chat", to: "/funnel/checkout" },
  { feature: "Traffic sources, referrers, landing pages and campaigns that bring bookers", to: "/growth/sources" },
  { feature: "Drill-downs: bookings, payments, checkout attempts, tickets and ratings open their chat", to: "/overview/bookings" },
  { feature: "Funnel by journey, trip planning vs direct, Friction Finder, Ask, Conversations explorer (Bookr Analyst's own)", to: "/funnel/conversion" },
];

export default function Tracker() {
  const [only, setOnly] = useState<Status | "">("");
  const all = GROUPS.flatMap((g) => g.rows);
  const count = (s: Status) => all.filter((r) => r.status === s).length;
  return (
    <>
      <div className="page-head">
        <div className="page-title"><h1>📒 Feature tracker</h1></div>
        <div className="page-q">
          <div>Every feature of the old Bookkr ops dashboard and where it is now. Click a status to filter, a row to open the page.</div>
        </div>
      </div>
      <div className="journey-pick">
        <button className={only === "" ? "on" : ""} onClick={() => setOnly("")}>All {all.length}</button>
        {(["added", "partial", "had", "not"] as Status[]).map((s) => (
          <button key={s} className={only === s ? "on" : ""} onClick={() => setOnly(s)}><span className={`tracker-status ${s}`}>{LABEL[s]}</span> {count(s)}</button>
        ))}
      </div>
      {GROUPS.map((g) => {
        const rows = g.rows.filter((r) => !only || r.status === only);
        if (!rows.length) return null;
        return (
          <Card key={g.group} title={g.group}>
            <div className="table-wrap">
              <table>
                <thead><tr><th>Feature</th><th>On the old board</th><th>Status</th><th>Where it is now</th><th>Note</th></tr></thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.feature}>
                      <td className="wrap-cell" style={{ whiteSpace: "normal", minWidth: 260 }}>{r.feature}</td>
                      <td className="muted small" style={{ whiteSpace: "normal" }}>{r.old}</td>
                      <td><span className={`tracker-status ${r.status}`}>{LABEL[r.status]}</span></td>
                      <td style={{ whiteSpace: "normal" }}>{r.to ? <Link to={r.to}>{r.where} →</Link> : r.where ?? "—"}</td>
                      <td className="small muted" style={{ whiteSpace: "normal", minWidth: 220 }}>{r.note ?? ""}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        );
      })}
      {!only && (
        <Card title="✨ Here, but not on the old board">
          <ul style={{ margin: 0, paddingLeft: 18, display: "flex", flexDirection: "column", gap: 6 }}>
            {BEYOND.map((b) => <li key={b.feature}><Link to={b.to}>{b.feature} →</Link></li>)}
          </ul>
        </Card>
      )}
    </>
  );
}
