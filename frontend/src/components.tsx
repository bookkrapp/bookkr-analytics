import { ReactNode, useEffect, useRef, useState } from "react";
import {
  Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { delta, fmtDate } from "./format";
import { G } from "./glossary";

/** ⓘ button: hover, focus or tap to read what a metric means. `k` is a glossary key, or pass `text`. */
export function Info({ k, text }: { k?: string; text?: string }) {
  const body = text ?? (k ? G[k] : undefined);
  const [pos, setPos] = useState<{ left: number; top: number; above: boolean } | null>(null);
  const [pinned, setPinned] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);
  const show = () => {
    const r = ref.current?.getBoundingClientRect();
    if (!r) return;
    const W = 280;
    const left = Math.max(8, Math.min(r.left + r.width / 2 - W / 2, window.innerWidth - W - 8));
    const above = r.bottom + 140 > window.innerHeight;
    setPos({ left, top: above ? r.top - 6 : r.bottom + 6, above });
  };
  useEffect(() => {
    if (!pos) return;
    const close = (e: Event) => {
      if (e.type === "mousedown" && ref.current?.contains(e.target as Node)) return;
      setPos(null);
      setPinned(false);
    };
    document.addEventListener("mousedown", close);
    window.addEventListener("scroll", close, true);
    return () => {
      document.removeEventListener("mousedown", close);
      window.removeEventListener("scroll", close, true);
    };
  }, [pos]);
  if (!body) return null;
  return (
    <span className="info" ref={ref}
          onMouseEnter={show} onMouseLeave={() => !pinned && setPos(null)}>
      <button type="button" className="info-btn" aria-label="What does this mean?" aria-expanded={!!pos}
              onFocus={show} onBlur={() => !pinned && setPos(null)}
              onClick={(e) => { e.stopPropagation(); e.preventDefault(); setPinned(true); show(); }}>i</button>
      {pos && (
        <span className={`info-pop${pos.above ? " above" : ""}`} role="tooltip"
              style={{ left: pos.left, top: pos.top }}>{body}</span>
      )}
    </span>
  );
}

export function Card({ title, right, info, children }: { title?: ReactNode; right?: ReactNode; info?: string; children: ReactNode }) {
  return (
    <section className="card">
      {(title || right) && (
        <div className="card-head">
          {title ? <h2>{title}{info && <Info k={info} />}</h2> : <span />}
          {right}
        </div>
      )}
      {children}
    </section>
  );
}

export function Loading() {
  return <div className="loading">Loading…</div>;
}
export function ErrorBox({ error }: { error: string }) {
  return <div className="error">Couldn't load data: {error}</div>;
}
export function Empty({ text = "No data for this period." }: { text?: string }) {
  return <div className="empty">{text}</div>;
}

export function Kpi({ label, value, cur, prev, sub, invert, info }: {
  label: string; value: ReactNode; cur?: number; prev?: number; sub?: ReactNode; invert?: boolean; info?: string;
}) {
  const d = delta(cur, prev);
  const good = d == null ? null : invert ? d < 0 : d > 0;
  return (
    <div className="kpi">
      <div className="label">{label}{info && <Info k={info} />}</div>
      <div className="value">{value}</div>
      <div className="sub">
        {d != null ? (
          <span className={d === 0 ? "" : good ? "up" : "down"}>
            {d > 0 ? "▲" : d < 0 ? "▼" : "■"} {Math.abs(d)}% vs previous period
          </span>
        ) : (
          sub ?? <span>&nbsp;</span>
        )}
      </div>
    </div>
  );
}

export const SEV_LABEL: Record<string, string> = {
  critical: "Critical", warning: "Warning", info: "Info", ok: "OK", high: "High", medium: "Medium", low: "Low",
};
export function SevBadge({ sev }: { sev: string }) {
  return (
    <span className={`badge sev-${sev}`}>
      <span className="dot" />
      {SEV_LABEL[sev] ?? sev}
    </span>
  );
}

export type Series = { key: string; label: string; color: string };

function TT({ active, payload, label, fmt, labelFmt }: any) {
  if (!active || !payload?.length) return null;
  return (
    <div className="tt">
      <div className="tt-title">{labelFmt ? labelFmt(label) : label}</div>
      {payload.map((p: any) => (
        <div className="tt-row" key={p.dataKey}>
          <span>
            <i style={{ background: p.color }} />
            {p.name}
          </span>
          <b>{fmt ? fmt(p.value) : p.value}</b>
        </div>
      ))}
    </div>
  );
}

function Legend({ series }: { series: Series[] }) {
  if (series.length < 2) return null;
  return (
    <div className="chart-legend">
      {series.map((s) => (
        <span key={s.key}>
          <i style={{ background: s.color }} />
          {s.label}
        </span>
      ))}
    </div>
  );
}

const axisProps = {
  tick: { fill: "var(--text-muted)", fontSize: 11 },
  axisLine: { stroke: "var(--axis)" },
  tickLine: false,
} as const;

export function TimeLines({ data, x = "date", series, height = 240, fmt, yFmt }: {
  data: any[]; x?: string; series: Series[]; height?: number; fmt?: (v: number) => string; yFmt?: (v: number) => string;
}) {
  return (
    <>
      <Legend series={series} />
      <ResponsiveContainer width="100%" height={height}>
        <LineChart data={data} margin={{ top: 6, right: 12, left: 0, bottom: 0 }}>
          <CartesianGrid stroke="var(--grid)" vertical={false} />
          <XAxis dataKey={x} tickFormatter={fmtDate} {...axisProps} minTickGap={24} />
          <YAxis {...axisProps} axisLine={false} width={48} allowDecimals={false} tickFormatter={yFmt} />
          <Tooltip content={<TT fmt={fmt} labelFmt={fmtDate} />} cursor={{ stroke: "var(--axis)", strokeWidth: 1 }} />
          {series.map((s) => (
            <Line key={s.key} type="linear" dataKey={s.key} name={s.label} stroke={s.color}
                  strokeWidth={2} dot={data.length <= 12 ? { r: 3, strokeWidth: 0, fill: s.color } : false}
                  isAnimationActive={false} activeDot={{ r: 4, strokeWidth: 2, stroke: "var(--surface-1)" }} />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </>
  );
}

export function Bars({ data, x, series, height = 240, stacked, fmt, xFmt, yFmt, layout = "horizontal" }: {
  data: any[]; x: string; series: Series[]; height?: number; stacked?: boolean;
  fmt?: (v: number) => string; xFmt?: (v: any) => string; yFmt?: (v: number) => string; layout?: "horizontal" | "vertical";
}) {
  const vertical = layout === "vertical";
  return (
    <>
      <Legend series={series} />
      <ResponsiveContainer width="100%" height={height}>
        <BarChart data={data} layout={layout} margin={{ top: 6, right: 12, left: 0, bottom: 0 }} barCategoryGap="25%">
          <CartesianGrid stroke="var(--grid)" vertical={vertical} horizontal={!vertical} />
          {vertical ? (
            <>
              <XAxis type="number" {...axisProps} allowDecimals={false} tickFormatter={yFmt} />
              <YAxis type="category" dataKey={x} {...axisProps} width={130} tickFormatter={xFmt} />
            </>
          ) : (
            <>
              <XAxis dataKey={x} {...axisProps} tickFormatter={xFmt} minTickGap={16} />
              <YAxis {...axisProps} axisLine={false} width={48} allowDecimals={false} tickFormatter={yFmt} />
            </>
          )}
          <Tooltip content={<TT fmt={fmt} labelFmt={xFmt} />} cursor={{ fill: "var(--surface-2)" }} />
          {series.map((s, i) => {
            const last = i === series.length - 1;
            const r: [number, number, number, number] = vertical ? [0, 4, 4, 0] : [4, 4, 0, 0];
            return (
              <Bar key={s.key} dataKey={s.key} name={s.label} fill={s.color} stackId={stacked ? "a" : undefined}
                   radius={!stacked || last ? r : [0, 0, 0, 0]} stroke="var(--surface-1)" strokeWidth={stacked ? 2 : 0}
                   maxBarSize={36} isAnimationActive={false} />
            );
          })}
        </BarChart>
      </ResponsiveContainer>
    </>
  );
}

export function Table({ cols, rows, onRow, empty }: {
  cols: { key: string; label: string; num?: boolean; info?: string; render?: (r: any) => ReactNode }[];
  rows: any[]; onRow?: (r: any) => void; empty?: string;
}) {
  if (!rows?.length) return <Empty text={empty} />;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>{cols.map((c) => <th key={c.key} className={c.num ? "num" : ""}>{c.label}{c.info && <Info k={c.info} />}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={r.id ?? r.session_id ?? i} className={onRow ? "click" : ""} onClick={() => onRow?.(r)}>
              {cols.map((c) => (
                <td key={c.key} className={c.num ? "num" : ""}>{c.render ? c.render(r) : r[c.key] ?? "—"}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
