import { createContext, useContext, useEffect, useState, ReactNode } from "react";

type Filters = {
  days: number; // 0 = all time
  excludeInternal: boolean;
  setDays: (d: number) => void;
  setExcludeInternal: (v: boolean) => void;
  refreshKey: number;
  bumpRefresh: () => void;
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

export function FiltersProvider({ children }: { children: ReactNode }) {
  const [days, setDays] = useState<number>(() => load("ba.days", 30));
  const [excludeInternal, setExcludeInternal] = useState<boolean>(() => load("ba.excl", true));
  const [refreshKey, setRefreshKey] = useState(0);
  useEffect(() => {
    try {
      localStorage.setItem("ba.days", JSON.stringify(days));
      localStorage.setItem("ba.excl", JSON.stringify(excludeInternal));
    } catch {
      /* storage unavailable */
    }
  }, [days, excludeInternal]);
  return (
    <Ctx.Provider
      value={{ days, excludeInternal, setDays, setExcludeInternal, refreshKey, bumpRefresh: () => setRefreshKey((k) => k + 1) }}
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
