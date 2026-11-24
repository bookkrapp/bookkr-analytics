import { useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes, useLocation, useParams } from "react-router-dom";
import { getJSON } from "./api";
import { useFilters } from "./filters";
import { NAV } from "./nav";
import Overview from "./pages/Overview";
import Funnel from "./pages/Funnel";
import Ops from "./pages/Ops";
import Sessions from "./pages/Sessions";
import SessionDetail from "./pages/SessionDetail";
import Ask from "./pages/Ask";
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

const OLD_TABS: Record<string, string> = { priorities: "brief" };

/** /funnel → /funnel/conversion, keeping the query string (and mapping old ?tab= links). */
function FirstSub({ path }: { path: string }) {
  const loc = useLocation();
  const sp = new URLSearchParams(loc.search);
  const s = NAV.find((x) => x.path === path)!;
  const tab = sp.get("tab") ?? (path === "/friction" && sp.get("day") ? "queue" : null);
  sp.delete("tab");
  const want = tab && s.subs.some((x) => x.key === (OLD_TABS[tab] ?? tab)) ? (OLD_TABS[tab] ?? tab) : s.subs[0].key;
  const qs = sp.toString();
  return <Navigate to={`${path}/${want}${qs ? `?${qs}` : ""}`} replace />;
}

/** /friction/brief etc. are sub-pages; /friction/123 is a chat in the review queue. */
function FrictionOrReview() {
  const { sub } = useParams();
  return sub && /^\d+$/.test(sub) ? <FrictionReview /> : <Friction />;
}

export default function App() {
  const { days, setDays, excludeInternal, setExcludeInternal } = useFilters();
  const loc = useLocation();
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          Bookr Analyst<small>Internal analytics</small>
        </div>
        <nav className="nav">
          {NAV.map((s) => {
            const open = loc.pathname === s.path || loc.pathname.startsWith(s.path + "/");
            return (
              <div key={s.path}>
                {s.path === "/friction" && <div className="section">Model improvement</div>}
                <NavLink to={s.path} className={open ? "active" : ""}><span><span aria-hidden className="nav-ico">{s.icon}</span>{s.label}</span></NavLink>
                {open && s.subs.length > 0 && (
                  <div className="nav-subs">
                    {s.subs.map((x, i) => (
                      <NavLink key={x.key} to={`${s.path}/${x.key}`}
                               className={({ isActive }) => (isActive || (i === 0 && loc.pathname === s.path) ? "active" : "")}>
                        {x.label}
                      </NavLink>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
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
            {/* each dashboard opens on its first sub-page; old links like /friction?tab=queue keep working */}
            {NAV.filter((s) => s.subs.length).map((s) => (
              <Route key={s.path} path={s.path} element={<FirstSub path={s.path} />} />
            ))}
            <Route path="/overview/:sub" element={<Overview />} />
            <Route path="/funnel/:sub" element={<Funnel />} />
            <Route path="/ops/:sub" element={<Ops />} />
            <Route path="/users/:sub" element={<Users />} />
            <Route path="/conversations" element={<Sessions />} />
            <Route path="/conversations/:id" element={<SessionDetail />} />
            <Route path="/ask" element={<Ask />} />
            <Route path="/friction/issue/:id" element={<FrictionIssue />} />
            <Route path="/friction/:sub" element={<FrictionOrReview />} />
            <Route path="*" element={<Navigate to="/overview" replace />} />
          </Routes>
        </main>
      </div>
    </div>
  );
}
