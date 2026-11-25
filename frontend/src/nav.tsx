// Dashboards and their sub-pages. One place drives the sidebar, the tabs on each dashboard, the question each
// sub-page answers, and which filters apply to it.
import { ReactNode } from "react";
import { NavLink, useParams, useSearchParams } from "react-router-dom";
import { useFilters } from "./filters";
import { JOURNEY_LABEL } from "./journeys";

// date: the date range at the top applies · all_time: it doesn't (always all-time) · internal: the internal-user toggle
// applies · journey: the journey filter on the page applies · period: the page has its own period picker
export type Applies = { date?: boolean; all_time?: boolean; internal?: boolean; journey?: boolean; period?: boolean };
export type Sub = { key: string; label: string; icon: string; question: string; applies: Applies };
export type Section = { path: string; label: string; icon: string; subs: Sub[] };

const STD: Applies = { date: true, internal: true };

export const NAV: Section[] = [
  {
    path: "/overview", label: "Overview", icon: "🏠", subs: [
      { key: "summary", label: "Summary", icon: "📊", question: "How is Bookkr doing in this period, what changed, and does anything need action now?", applies: STD },
      { key: "bookings", label: "Bookings & money", icon: "💰", question: "How many flights and hotels were booked, for how much, and how much of it was paid from the wallet?", applies: STD },
      { key: "demand", label: "Demand", icon: "📍", question: "Where do users want to fly and stay, and what do they actually book?", applies: STD },
    ],
  },
  {
    path: "/funnel", label: "Funnel", icon: "🔻", subs: [
      { key: "conversion", label: "Conversion", icon: "🔻", question: "How far do chats get, from the first message to a booking, and where do they stop?", applies: { ...STD, journey: true } },
      { key: "effort", label: "Speed & effort", icon: "⏱️", question: "How much effort does it take a user to reach a search?", applies: { ...STD, journey: true } },
      { key: "journeys", label: "Journeys compared", icon: "🧭", question: "Which kinds of trips do users come for, and which ones convert?", applies: STD },
      { key: "planning", label: "Trip planning", icon: "🗺️", question: "Do users ask Bookkr to plan a trip or search directly, and how far do plans get?", applies: STD },
      { key: "checkout", label: "Checkout steps", icon: "🧾", question: "Once a user starts booking, at which checkout step do they drop off?", applies: STD },
    ],
  },
  {
    path: "/ops", label: "Booking & payment ops", icon: "💳", subs: [
      { key: "attention", label: "Needs action", icon: "🚨", question: "What needs someone now: money taken with no booking, bookings stuck at TripJack, customers waiting on support?", applies: { all_time: true, internal: true } },
      { key: "payments", label: "Payments", icon: "💳", question: "How many Razorpay payments succeed, by method, why do the rest fail, and what was refunded?", applies: STD },
      { key: "outcomes", label: "Outcomes", icon: "📈", question: "How do booking attempts end, and why do they fail?", applies: { all_time: true, internal: true } },
      { key: "support", label: "Support", icon: "🎧", question: "How many tickets came in, how fast were they closed, and who is waiting on us?", applies: STD },
    ],
  },
  {
    path: "/growth", label: "Growth", icon: "📈", subs: [
      { key: "signups", label: "Signups & vouchers", icon: "✍️", question: "How many people signed up, and how many launch vouchers went out?", applies: STD },
      { key: "wallet", label: "Wallet", icon: "👛", question: "How much wallet credit is outstanding, and are vouchers being used?", applies: STD },
      { key: "sources", label: "Traffic sources", icon: "🧲", question: "Where do visitors come from, and which campaigns bring people who book?", applies: STD },
    ],
  },
  {
    path: "/users", label: "Users & retention", icon: "👥", subs: [
      { key: "activity", label: "Activity", icon: "🙋", question: "How many people use Bookkr: accounts vs guests, web vs app, new vs returning?", applies: STD },
      { key: "retention", label: "Retention", icon: "🔁", question: "Do users come back, and how soon?", applies: STD },
      { key: "behaviour", label: "Behaviour", icon: "🧪", question: "Do returning users behave differently, and who uses Bookkr the most?", applies: STD },
    ],
  },
  {
    path: "/quality", label: "Quality & reliability", icon: "✅", subs: [
      { key: "ratings", label: "Reply ratings", icon: "👍", question: "How do users rate Bookkr's replies, and what do the thumbs-down say?", applies: STD },
      { key: "errors", label: "Server errors", icon: "🧯", question: "Which endpoints fail, how often, and for how many users?", applies: STD },
      { key: "operations", label: "Operations", icon: "🩺", question: "Are Bookkr's services up, and did the hotel catalogue sync run?", applies: {} },
    ],
  },
  { path: "/conversations", label: "Conversations", icon: "💬", subs: [] },
  { path: "/ask", label: "Ask", icon: "❓", subs: [] },
  {
    path: "/friction", label: "Friction Finder", icon: "🔍", subs: [
      { key: "brief", label: "Brief", icon: "📋", question: "What hurt users most in this period, and what should we fix first?", applies: { period: true } },
      { key: "issues", label: "Issues", icon: "🎯", question: "Which named issues hit the most users, ranked P1 to P3?", applies: { period: true, journey: true } },
      { key: "patterns", label: "Patterns", icon: "🧩", question: "Which product areas and kinds of friction are growing or shrinking?", applies: { period: true, journey: true } },
      { key: "queue", label: "Review queue", icon: "✅", question: "Check individual chats the finder flagged on a day: agree, not friction, or skip.", applies: { journey: true } },
      { key: "investigate", label: "Code-agent export", icon: "📤", question: "What should the Claude with the Bookkr code investigate, and with which evidence?", applies: { journey: true } },
      { key: "dataset", label: "Training data", icon: "📦", question: "Which files go to RAG, prompts, fine-tuning and engineering?", applies: {} },
      { key: "rules", label: "Rules", icon: "📐", question: "How the finder decides what counts as friction.", applies: {} },
    ],
  },
  { path: "/tracker", label: "Feature tracker", icon: "📒", subs: [] },
];

export const section = (path: string) => NAV.find((s) => s.path === path)!;

/** The sub-page from the URL (/funnel/:sub), falling back to the first one. */
export function useSub(path: string): Sub {
  const { sub } = useParams();
  const s = section(path);
  return s.subs.find((x) => x.key === sub) ?? s.subs[0];
}

function FilterChips({ applies }: { applies: Applies }) {
  const { rangeLabel, excludeInternal } = useFilters();
  const [sp] = useSearchParams();
  const j = sp.get("journey");
  const chips: ReactNode[] = [];
  if (applies.date) chips.push(<span key="d" className="fchip">📅 {rangeLabel}</span>);
  if (applies.all_time) chips.push(<span key="a" className="fchip muted-chip" title="The date range at the top doesn't apply here">📅 All time (date range not used)</span>);
  if (applies.period) chips.push(<span key="p" className="fchip" title="Pick the period below; the date range at the top doesn't apply here">📅 Period picked below</span>);
  if (applies.internal) chips.push(<span key="i" className="fchip">👤 Internal users {excludeInternal ? "excluded" : "included"}</span>);
  if (applies.journey) chips.push(<span key="j" className="fchip">🧭 {j ? JOURNEY_LABEL[j] : "All journeys"}</span>);
  if (!chips.length) return null;
  return <div className="fchips"><span className="muted small">Filters on this page:</span>{chips}</div>;
}

/** Dashboard title, its sub-page tabs, and what the current sub-page answers. */
export function PageHeader({ path, sub, right, children }: { path: string; sub?: Sub; right?: ReactNode; children?: ReactNode }) {
  const s = section(path);
  const keep = new URLSearchParams(useSearchParams()[0]);
  ["page", "tab"].forEach((k) => keep.delete(k));
  const qs = keep.toString() ? `?${keep}` : "";
  return (
    <div className="page-head">
      <div className="page-title">
        <h1>{s.icon} {s.label}</h1>
        {right}
      </div>
      {s.subs.length > 0 && (
        <nav className="subtabs" aria-label={`${s.label} pages`}>
          {s.subs.map((x) => (
            <NavLink key={x.key} to={`${s.path}/${x.key}${qs}`} className={({ isActive }) => (isActive || x === sub ? "on" : "")}>
              <span aria-hidden>{x.icon}</span> {x.label}
            </NavLink>
          ))}
        </nav>
      )}
      {sub && (
        <div className="page-q">
          <div><b>{sub.label}:</b> {sub.question}</div>
          <FilterChips applies={sub.applies} />
        </div>
      )}
      {children}
    </div>
  );
}
