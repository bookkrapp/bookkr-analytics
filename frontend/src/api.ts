import { useEffect, useState } from "react";
import { useFilters } from "./filters";

export async function getJSON<T = any>(path: string, params: Record<string, any> = {}): Promise<T> {
  const qs = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") qs.set(k, String(v));
  });
  const url = qs.toString() ? `${path}?${qs}` : path;
  const res = await fetch(url);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* not json */
    }
    throw new Error(`${res.status}: ${detail}`);
  }
  return res.json();
}

/** Fetch an endpoint with the global filters (days, exclude_internal) applied. */
export function useApi<T = any>(path: string, extra: Record<string, any> = {}, opts: { useDays?: boolean } = {}) {
  const { days, excludeInternal, refreshKey } = useFilters();
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const params = { ...(opts.useDays === false ? {} : { days }), exclude_internal: excludeInternal, ...extra };
  const key = path + JSON.stringify(params) + refreshKey;
  useEffect(() => {
    let alive = true;
    setLoading(true);
    getJSON<T>(path, params)
      .then((d) => alive && (setData(d), setError(null)))
      .catch((e) => alive && setError(e.message))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return { data, error, loading };
}
