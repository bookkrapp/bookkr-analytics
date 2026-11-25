import { createContext, useContext, useEffect, useState, ReactNode } from "react";

/** The date range at the top. Day counts as before, plus IST calendar presets and a custom from/to (IST days). */
export type Preset = "today" | "yesterday" | "7d" | "30d" | "90d" | "month" | "all" | "custom";
export const PRESETS: { key: Preset; label: string; days?: number }[] = [
  { key: "today", label: "Today" },
  { key: "yesterday", label: "Yesterday" },
  { key: "7d", label: "7d", days: 7 },
  { key: "30d", label: "30d", days: 30 },
  { key: "90d", label: "90d", days: 90 },
  { key: "month", label: "This month" },
  { key: "all", label: "All", days: 0 },
  { key: "custom", label: "Custom" },
];

type Filters = {
  preset: Preset;
  setPreset: (p: Preset) => void;
  custom: { from: string; to: string };
  setCustom: (c: { from: string; to: string }) => void;
  /** Kept for pages that still think in days: the day count of the preset (30 when it isn't one). */
  days: number;
  setDays: (d: number) => void;
  excludeInternal: boolean;
  setExcludeInternal: (v: boolean) => void;
  refreshKey: number;
  bumpRefresh: () => void;
  /** Query parameters that send the range to the API. */
  rangeParams: () => Record<string, string | number>;
  rangeLabel: string;
};

const Ctx = createContext<Filters | null>(null);

function load<T>(key: string, fallback: T): T {
  try {
    const v = localStorage.getItem(key);
    return v === null ? fallback : (JSON.parse(v) as T);
  } catch {
    return fallback;
  }
}

const fmtDay = (d: string) => (d ? new Date(d + "T00:00:00").toLocaleDateString("en-IN", { day: "2-digit", month: "short" }) : "?");

export function FiltersProvider({ children }: { children: ReactNode }) {
  const legacy = load<number | null>("ba.days", null);
  const [preset, setPreset] = useState<Preset>(() =>
    load<Preset | null>("ba.range", null) ?? (PRESETS.find((p) => p.days === legacy)?.key ?? "30d"));
  const [custom, setCustom] = useState(() => load("ba.custom", { from: "", to: "" }));
  const [excludeInternal, setExcludeInternal] = useState<boolean>(() => load("ba.excl", true));
  const [refreshKey, setRefreshKey] = useState(0);
  useEffect(() => {
    try {
      localStorage.setItem("ba.range", JSON.stringify(preset));
      localStorage.setItem("ba.custom", JSON.stringify(custom));
      localStorage.setItem("ba.excl", JSON.stringify(excludeInternal));
    } catch {
      /* storage unavailable */
    }
  }, [preset, custom, excludeInternal]);
  const p = PRESETS.find((x) => x.key === preset)!;
  const days = p.days ?? 30;
  const rangeParams = (): Record<string, string | number> => {
    if (preset === "custom" && custom.from && custom.to) return { from: custom.from, to: custom.to };
    if (p.days !== undefined) return { days: p.days };
    return { range: preset, days: 30 };
  };
  const rangeLabel = preset === "custom" ? `${fmtDay(custom.from)} – ${fmtDay(custom.to)}`
    : preset === "all" ? "All time" : p.days ? `Last ${p.days} days` : p.label;
  return (
    <Ctx.Provider
      value={{
        preset, setPreset, custom, setCustom, days,
        setDays: (d) => setPreset(PRESETS.find((x) => x.days === d)?.key ?? "30d"),
        excludeInternal, setExcludeInternal, refreshKey, bumpRefresh: () => setRefreshKey((k) => k + 1),
        rangeParams, rangeLabel,
      }}
    >
      {children}
    </Ctx.Provider>
  );
}

export function useFilters() {
  const c = useContext(Ctx);
  if (!c) throw new Error("FiltersProvider missing");
  return c;
}
