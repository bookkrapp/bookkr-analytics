// One issue from the register: what it is, how often, every affected chat, and its status.
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { getJSON } from "../api";
import { Bars, Card, ErrorBox, Info, Loading, SevBadge, Table } from "../components";
import { fmtDate, fmtIST } from "../format";
import { BucketPill, LanePill, TYPE_LABEL } from "../frictionTaxonomy";
import { useReviewer } from "./Friction";
import { STATUS_LABEL } from "./FrictionReport";

export default function FrictionIssue() {
  const { id } = useParams();
  const [s, setS] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const [me, setMe] = useReviewer();
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  useEffect(() => {
    getJSON<any>(`/api/friction/issues/${id}`).then((d) => { setS(d); setNote(d.status_note ?? ""); }).catch((e) => setErr(e.message));
  }, [id]);
  if (err) return <ErrorBox error={err} />;
  if (!s) return <Loading />;
  const save = async (fields: any) => {
    setSaving(true); setMsg(null);
    try {
      const res = await fetch(`/api/friction/issues/${id}`, { method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...fields, by: me }) });
      const d = await res.json();
      if (!res.ok) throw new Error(d.detail ?? res.statusText);
      setS(d); setMsg("Saved");
    } catch (e: any) { setMsg(e.message); }
    setSaving(false);
  };
  const users = new Set(s.items.map((i: any) => i.session_id)).size;
  const booked = new Set(s.items.filter((i: any) => i.outcome === "Booked").map((i: any) => i.session_id)).size;
  return (
    <>
      <div>
        <div className="small"><Link to="/friction?tab=priorities">← Priorities</Link></div>
        <h1 style={{ marginTop: 6 }}>{s.title}</h1>
        <div className="page-sub" style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <span className="muted">Issue #{s.id}</span>
          <BucketPill b={s.bucket} /> <span>{s.theme ?? "Other"}</span> · <span>Caused by {s.owner_label}</span> · <LanePill lane={s.fix_lane} />
        </div>
      </div>

      <div className="grid g2">
        <Card title="What goes wrong">
          <p style={{ marginTop: 0 }}>{s.description}</p>
          {s.recommended_fix && <><h3>Recommended fix</h3><p style={{ marginTop: 0 }}>{s.recommended_fix}</p></>}
          {s.lesson && <><h3>Rule for the prompt <Info k="f_issue_lesson" /></h3><p style={{ marginTop: 0 }}>{s.lesson}</p></>}
          <div className="muted small">First grouped {fmtIST(s.created_at)} · {s.chats} chats · {booked} of them still booked</div>
        </Card>
        <Card title="Status" info="f_issue_status">
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
            {Object.entries(STATUS_LABEL).map(([k, l]) => (
              <button key={k} className={`btn${s.status === k ? " primary" : ""}`} disabled={saving}
                      onClick={() => save({ status: k, status_note: note })}>{l}</button>
            ))}
          </div>
          <div className="field" style={{ marginTop: 10 }}>
            <label className="small muted">Note (ticket link, who's on it, why it's not an issue)</label>
            <textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)} style={{ width: "100%" }} />
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 6 }}>
            <input type="text" placeholder="Your name" value={me} onChange={(e) => setMe(e.target.value)} />
            <button className="btn" disabled={saving} onClick={() => save({ status_note: note })}>Save note</button>
            {msg && <span className="small muted">{msg}</span>}
          </div>
          {s.status_at && <div className="muted small" style={{ marginTop: 6 }}>
            {STATUS_LABEL[s.status]} since {fmtIST(s.status_at)}{s.status_by ? ` by ${s.status_by}` : ""}.
            {s.status === "fixed" && " If it shows up again after this date, it returns to the priority list marked “Back after fix”."}
          </div>}
        </Card>
      </div>

      <Card title="Chats per day">
        <Bars data={s.daily} x="day" xFmt={fmtDate} height={180} series={[{ key: "chats", label: "Chats", color: "var(--series-1)" }]} />
      </Card>

      <Card title={`Affected chats (${users})`} info="f_issue_chats">
        <Table rows={s.items} cols={[
          { key: "day", label: "Day", render: (r) => fmtDate(r.day) },
          { key: "severity", label: "Severity", render: (r) => <SevBadge sev={r.severity} /> },
          { key: "type", label: "Type", render: (r) => TYPE_LABEL[r.type] ?? r.type },
          { key: "what_went_wrong", label: "What went wrong", render: (r) => <span className="small">{r.what_went_wrong}</span> },
          { key: "outcome", label: "Chat ended", render: (r) => <span className="small">{r.outcome}</span> },
          { key: "open", label: "", render: (r) => <Link to={`/friction/${r.finding_id}`}>Open chat</Link> },
        ]} />
      </Card>
    </>
  );
}
