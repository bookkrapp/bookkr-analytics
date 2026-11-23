import { useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { getJSON } from "./api";
import { useFilters } from "./filters";
import Overview from "./pages/Overview";
import Funnel from "./pages/Funnel";
import Ops from "./pages/Ops";
import Sessions from "./pages/Sessions";
import SessionDetail from "./pages/SessionDetail";
import ComingSoon from "./pages/ComingSoon";
import Users from "./pages/Users";
import Friction from "./pages/Friction";
import FrictionReview from "./pages/FrictionReview";
import FrictionIssue from "./pages/FrictionIssue";

const RANGES = [
  { label: "7d", days: 7 },
  { label: "30d", days: 30 },
  { label: "90d", days: 90 },
  { label: "All", days: 0 },
];

function SyncStatus() {
  const { bumpRefresh } = useFilters();
  const [meta, setMeta] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const load = () => getJSON("/api/meta").then(setMeta).catch(() => setMeta(null));
  useEffect(() => {
    load();
  }, []);
  useEffect(() => {
    if (!busy) return;
    const t = setInterval(async () => {
      const m = await getJSON("/api/meta");
      setMeta(m);
      if (!m.sync_running) {
        setBusy(false);
        bumpRefresh();
      }
    }, 1500);
    return () => clearInterval(t);
  }, [busy]);
  const synced = meta?.sync?.synced_at;
  // synced_at is UTC; shift to IST for display
  const local = synced ? new Date(new Date(synced + "Z").getTime()).toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false }) : null;
  return (
    <span className="muted small" style={{ display: "inline-flex", gap: 8, alignItems: "center" }}>
      {meta?.last_sync_error ? <span className="down">Sync failed: {meta.last_sync_error}</span> : null}
      {local ? <>Data as of {local}</> : "No data synced yet"}
      <button
        className="btn"
        disabled={busy}
        onClick={async () => {
          setBusy(true);
          await fetch("/api/sync", { method: "POST" }).catch(() => setBusy(false));
        }}
      >
        {busy ? "Syncing…" : "Refresh data"}
      </button>
    </span>
  );
}

export default function App() {
  const { days, setDays, excludeInternal, setExcludeInternal } = useFilters();
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          Bookr Analyst<small>Internal analytics</small>
        </div>
        <nav className="nav">
          <NavLink to="/overview">Overview</NavLink>
          <NavLink to="/funnel">Funnel</NavLink>
          <NavLink to="/ops">Booking &amp; payment ops</NavLink>
          <NavLink to="/users">Users &amp; retention</NavLink>
          <NavLink to="/conversations">Conversations</NavLink>
          <div className="section">Model improvement</div>
          <NavLink to="/friction">Friction Finder</NavLink>
          <div className="section">Coming next</div>
          <NavLink to="/ask">
            Ask <span className="soon">Phase 2</span>
          </NavLink>
        </nav>
      </aside>
      <div className="main">
        <header className="topbar">
          <div className="controls">
            <div className="seg" role="group" aria-label="Date range">
              {RANGES.map((r) => (
                <button key={r.label} className={days === r.days ? "on" : ""} onClick={() => setDays(r.days)}>
                  {r.label}
                </button>
              ))}
            </div>
            <label className="toggle" title="Hide users listed in INTERNAL_USER_IDS / INTERNAL_EMAIL_DOMAINS (.env)">
              <input type="checkbox" checked={excludeInternal} onChange={(e) => setExcludeInternal(e.target.checked)} />
              Exclude internal users
            </label>
          </div>
          <SyncStatus />
        </header>
        <main className="content">
          <Routes>
            <Route path="/" element={<Navigate to="/overview" replace />} />
            <Route path="/overview" element={<Overview />} />
            <Route path="/funnel" element={<Funnel />} />
            <Route path="/ops" element={<Ops />} />
            <Route path="/conversations" element={<Sessions />} />
            <Route path="/conversations/:id" element={<SessionDetail />} />
            <Route path="/ask" element={<ComingSoon title="Ask" text="Ask questions in plain English; an agent writes and runs the SQL, then explains the result. Planned for Phase 2." />} />
            <Route path="/users" element={<Users />} />
            <Route path="/friction" element={<Friction />} />
            <Route path="/friction/issue/:id" element={<FrictionIssue />} />
            <Route path="/friction/:id" element={<FrictionReview />} />
            <Route path="*" element={<Navigate to="/overview" replace />} />
          </Routes>
        </main>
      </div>
    </div>
  );
}
