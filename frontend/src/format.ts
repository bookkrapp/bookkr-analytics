const inr0 = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 });
const num = new Intl.NumberFormat("en-IN");

export const fmtINR = (v: number | null | undefined) => (v == null ? "—" : inr0.format(v));
export const fmtNum = (v: number | null | undefined) => (v == null ? "—" : num.format(v));
export const fmtUSD = (v: number | null | undefined) => (v == null ? "—" : `$${v.toFixed(2)}`);
export const fmtPct = (v: number | null | undefined) => (v == null ? "—" : `${v}%`);

/** Compact INR for axes: ₹1.8L, ₹25K */
export function fmtINRShort(v: number) {
  if (Math.abs(v) >= 1e7) return `₹${(v / 1e7).toFixed(1)}Cr`;
  if (Math.abs(v) >= 1e5) return `₹${(v / 1e5).toFixed(1)}L`;
  if (Math.abs(v) >= 1e3) return `₹${(v / 1e3).toFixed(0)}K`;
  return `₹${v}`;
}

/** Timestamps from the API are already shifted to IST ("created_local"). */
export function fmtDateTime(s?: string | null) {
  if (!s) return "—";
  const d = new Date(s.replace(" ", "T"));
  return d.toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false });
}
/** A UTC timestamp from the server (e.g. "2026-11-19T20:45:38+00:00") shown in India time. */
export function fmtIST(s?: string | null) {
  if (!s) return "—";
  const iso = /[zZ]|[+-]\d\d:?\d\d$/.test(s) ? s : s.replace(" ", "T") + "Z";
  return new Date(iso).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short",
    hour: "2-digit", minute: "2-digit", hour12: false }) + " IST";
}
export function fmtDate(s?: string | null) {
  if (!s) return "—";
  const d = new Date(s.length <= 10 ? s + "T00:00:00" : s.replace(" ", "T"));
  return d.toLocaleDateString("en-IN", { day: "2-digit", month: "short" });
}
export function fmtTime(s?: string | null) {
  if (!s) return "";
  const d = new Date(s.replace(" ", "T"));
  return d.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
}

/** % change between current and previous; null when not comparable. */
export function delta(cur?: number | null, prev?: number | null): number | null {
  if (cur == null || prev == null || prev === 0) return null;
  return Math.round(((cur - prev) / prev) * 100);
}

export const shortId = (id?: string | null) =>
  !id ? "—" : id.startsWith("guest_") ? `g·${id.slice(6, 12)}` : id.slice(0, 8);
