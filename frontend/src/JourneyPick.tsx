// The journey filter buttons. The choice lives in the URL (?journey=) so it carries between sub-pages that use it.
import { useSearchParams } from "react-router-dom";
import { Info } from "./components";
import { JOURNEYS } from "./journeys";

export function useJourney(enabled = true): [string, (j: string) => void] {
  const [sp, setSp] = useSearchParams();
  const journey = enabled ? sp.get("journey") ?? "" : "";
  const set = (j: string) => { const n = new URLSearchParams(sp); j ? n.set("journey", j) : n.delete("journey"); setSp(n, { replace: true }); };
  return [journey, set];
}

export default function JourneyPick({ journey, set }: { journey: string; set: (j: string) => void }) {
  return (
    <div className="journey-pick" role="group" aria-label="Journey">
      <span className="muted small">Journey:</span>
      {[{ v: "", label: "All journeys", icon: "🌐" }, ...JOURNEYS].map((j) => (
        <button key={j.v} className={journey === j.v ? "on" : ""} onClick={() => set(j.v)}>
          <span aria-hidden>{j.icon}</span> {j.label}
        </button>
      ))}
      <Info k="journey" />
    </div>
  );
}
