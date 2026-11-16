import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ACTIVE, api, formatDate, formatTime, Meeting, Report, ReportData, Segment } from "../api";
import { Card, Notice, StatusBadge, useAction, useFetch } from "../ui";

export default function MeetingPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const meeting = useFetch<Meeting>(`/meetings/${id}`);
  const transcript = useFetch<Segment[]>(`/meetings/${id}/transcript`);
  const report = useFetch<Report>(`/meetings/${id}/report`);
  const action = useAction();
  const [summary, setSummary] = useState("");
  const [tab, setTab] = useState<"report" | "transcript" | "video">("report");
  const bottom = useRef<HTMLDivElement>(null);

  const m = meeting.data;
  const isActive = !!m && ACTIVE.includes(m.status);

  // Flux temps réel : statut, segments de transcript, résumé glissant.
  useEffect(() => {
    const scheme = location.protocol === "https:" ? "wss" : "ws";
    let socket: WebSocket | null = null;
    let closed = false;
    const connect = () => {
      socket = new WebSocket(`${scheme}://${location.host}/ws/meetings/${id}`);
      socket.onmessage = (event) => {
        const message = JSON.parse(event.data);
        if (message.type === "segment") {
          transcript.setData((current) => [...(current ?? []), message.segment]);
        } else if (message.type === "summary") {
          setSummary(message.summary);
        } else {
          meeting.reload();
          if (message.status === "done" || message.type === "refresh") {
            report.reload();
            transcript.reload();
          }
        }
      };
      socket.onclose = () => {
        if (!closed) setTimeout(connect, 3000);
      };
    };
    connect();
    return () => {
      closed = true;
      socket?.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  useEffect(() => {
    if (isActive) bottom.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [transcript.data?.length, isActive]);

  if (meeting.error) return <Notice kind="error">{meeting.error}</Notice>;
  if (!m) return <p className="muted">Chargement…</p>;

  const post = (path: string) => action.run(() => api(`/meetings/${id}${path}`, { method: "POST" }).then(meeting.reload));
  const remove = async () => {
    if (!confirm("Supprimer définitivement cette réunion, son enregistrement et son compte rendu ?")) return;
    if (await action.run(() => api(`/meetings/${id}`, { method: "DELETE" }))) navigate("/history");
  };
  const liveSummary = summary || m.live_summary;
  const segments = transcript.data ?? [];

  return (
    <>
      <div className="page-head">
        <div>
          <h1>{m.title}</h1>
          <p className="muted">
            {formatDate(m.start_at)} ·{" "}
            <a href={m.meet_url} target="_blank" rel="noreferrer">
              {m.meet_url.replace("https://", "")}
            </a>
            {m.recording_seconds > 0 && ` · ${formatTime(m.recording_seconds)} enregistrées`}
          </p>
        </div>
        <StatusBadge status={m.status} />
      </div>
      {m.status_message && <Notice kind={m.status === "failed" ? "error" : "info"}>{m.status_message}</Notice>}
      {action.error && <Notice kind="error">{action.error}</Notice>}

      <div className="row wrap">
        {!isActive && m.status !== "processing" && (
          <button className="primary" onClick={() => post("/join")} disabled={action.busy}>
            Envoyer le bot dans la réunion
          </button>
        )}
        {(m.status === "in_meeting" || m.status === "recording") && (
          <>
            {m.status === "in_meeting" ? (
              <button className="primary" onClick={() => post("/control/record_start")} disabled={action.busy}>
                ● Démarrer l'enregistrement
              </button>
            ) : (
              <button onClick={() => post("/control/record_stop")} disabled={action.busy}>
                ■ Arrêter l'enregistrement
              </button>
            )}
          </>
        )}
        {isActive && (
          <button className="danger" onClick={() => post("/control/leave")} disabled={action.busy}>
            Faire quitter le bot
          </button>
        )}
        {!isActive && (
          <button className="link danger-text" onClick={remove}>
            Supprimer
          </button>
        )}
      </div>

      {isActive ? (
        <div className="grid-2">
          <Card title="Transcript en direct">
            <TranscriptView segments={segments} empty="Le transcript apparaît ici dès que l'enregistrement démarre." />
            <div ref={bottom} />
          </Card>
          <Card title="Résumé en direct">
            {liveSummary ? <div className="pre">{liveSummary}</div> : <p className="muted">Mis à jour toutes les quelques minutes pendant l'enregistrement.</p>}
          </Card>
        </div>
      ) : (
        <>
          <div className="tabs">
            <button className={tab === "report" ? "active" : ""} onClick={() => setTab("report")}>
              Compte rendu
            </button>
            <button className={tab === "transcript" ? "active" : ""} onClick={() => setTab("transcript")}>
              Transcript ({segments.length})
            </button>
            {m.has_recording && (
              <button className={tab === "video" ? "active" : ""} onClick={() => setTab("video")}>
                Enregistrement
              </button>
            )}
          </div>
          {tab === "report" && (
            <ReportPanel
              meeting={m}
              report={report.data}
              hasTranscript={segments.length > 0}
              onChange={() => {
                report.reload();
                meeting.reload();
              }}
            />
          )}
          {tab === "transcript" && (
            <Card
              actions={
                segments.length > 0 && (
                  <a className="button" href={`/api/meetings/${id}/export/txt`}>
                    Télécharger (.txt)
                  </a>
                )
              }
            >
              <TranscriptView segments={segments} empty="Aucun transcript pour cette réunion." />
            </Card>
          )}
          {tab === "video" && (
            <Card
              actions={
                <a className="button" href={`/api/meetings/${id}/recording?download=true`}>
                  Télécharger (.mp4)
                </a>
              }
            >
              <video controls src={`/api/meetings/${id}/recording`} />
            </Card>
          )}
        </>
      )}
    </>
  );
}

function TranscriptView({ segments, empty }: { segments: Segment[]; empty: string }) {
  if (!segments.length) return <p className="muted">{empty}</p>;
  return (
    <div className="transcript">
      {segments.map((s) => (
        <p key={`${s.source}-${s.id}`}>
          <span className="time">{formatTime(s.start_s)}</span>
          {s.speaker && <strong>{s.speaker} : </strong>}
          {s.text}
        </p>
      ))}
    </div>
  );
}

const lines = (value: string) => value.split("\n").map((l) => l.trim()).filter(Boolean);

function ReportPanel(props: { meeting: Meeting; report: Report | null; hasTranscript: boolean; onChange: () => void }) {
  const { meeting, report, hasTranscript, onChange } = props;
  const action = useAction();
  const [editing, setEditing] = useState(false);
  const [notice, setNotice] = useState("");
  const base = `/meetings/${meeting.id}`;

  const regenerate = () =>
    action.run(() => api(`${base}/report/regenerate`, { method: "POST" }).then(onChange));
  const toDrive = async () => {
    if (await action.run(() => api(`${base}/export/drive`, { method: "POST" }))) {
      setNotice("Dépôt dans Google Drive lancé : les liens apparaîtront ici dans quelques instants.");
    }
  };

  if (meeting.status === "processing") return <Card><p className="muted">Traitement en cours : transcription puis rédaction du compte rendu…</p></Card>;
  if (!report) {
    return (
      <Card>
        <p className="muted">Aucun compte rendu pour cette réunion.</p>
        {action.error && <Notice kind="error">{action.error}</Notice>}
        {hasTranscript && (
          <button className="primary" onClick={regenerate} disabled={action.busy}>
            Générer le compte rendu
          </button>
        )}
      </Card>
    );
  }
  if (editing) {
    return (
      <ReportEditor
        initial={report.data}
        onCancel={() => setEditing(false)}
        onSave={async (data) => {
          if (await action.run(() => api(`${base}/report`, { method: "PUT", body: data }))) {
            setEditing(false);
            onChange();
          }
        }}
        error={action.error}
      />
    );
  }

  const r = report.data;
  return (
    <Card
      title={r.title || meeting.title}
      actions={
        <>
          <a className="button primary" href={`/api${base}/export/pdf`}>PDF</a>
          <a className="button" href={`/api${base}/export/docx`}>Word</a>
          <a className="button" href={`/api${base}/export/md`}>Markdown</a>
          <button onClick={toDrive} disabled={action.busy}>Déposer dans Drive</button>
          <button onClick={() => setEditing(true)}>Modifier</button>
          <button onClick={regenerate} disabled={action.busy}>Régénérer</button>
        </>
      }
    >
      {action.error && <Notice kind="error">{action.error}</Notice>}
      {notice && <Notice kind="success">{notice}</Notice>}
      {meeting.drive_links.length > 0 && (
        <p className="small">
          Dans Drive :{" "}
          {meeting.drive_links.map((link) => (
            <a key={link.url + link.kind} href={link.url} target="_blank" rel="noreferrer" className="chip">
              {link.kind}
            </a>
          ))}
        </p>
      )}
      <div className="report">
        {r.participants.length > 0 && <p className="muted">Participants : {r.participants.join(", ")}</p>}
        <h3>Résumé</h3>
        <p className="pre">{r.summary || "—"}</p>
        {r.topics.length > 0 && <h3>Points abordés</h3>}
        {r.topics.map((topic, i) => (
          <div key={i}>
            <h4>{topic.title}</h4>
            <ul>{topic.points.map((p, j) => <li key={j}>{p}</li>)}</ul>
          </div>
        ))}
        <List title="Décisions" items={r.decisions} />
        {r.action_items.length > 0 && (
          <>
            <h3>Actions</h3>
            <div className="table-wrap">
              <table>
                <thead><tr><th>Action</th><th>Responsable</th><th>Échéance</th></tr></thead>
                <tbody>
                  {r.action_items.map((a, i) => (
                    <tr key={i}><td>{a.task}</td><td>{a.owner || "—"}</td><td>{a.due || "—"}</td></tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
        <List title="Questions ouvertes" items={r.open_questions} />
        <p className="muted small">Généré avec {report.model} · modifié le {formatDate(report.updated_at)}</p>
      </div>
    </Card>
  );
}

function List({ title, items }: { title: string; items: string[] }) {
  if (!items.length) return null;
  return (
    <>
      <h3>{title}</h3>
      <ul>{items.map((item, i) => <li key={i}>{item}</li>)}</ul>
    </>
  );
}

function ReportEditor(props: { initial: ReportData; onSave: (d: ReportData) => void; onCancel: () => void; error: string }) {
  const { initial } = props;
  const [title, setTitle] = useState(initial.title);
  const [summary, setSummary] = useState(initial.summary);
  const [participants, setParticipants] = useState(initial.participants.join(", "));
  const [topics, setTopics] = useState(initial.topics.map((t) => [`# ${t.title}`, ...t.points].join("\n")).join("\n\n"));
  const [decisions, setDecisions] = useState(initial.decisions.join("\n"));
  const [actions, setActions] = useState(initial.action_items.map((a) => [a.task, a.owner, a.due].join(" | ")).join("\n"));
  const [questions, setQuestions] = useState(initial.open_questions.join("\n"));

  const save = () => {
    const parsedTopics: ReportData["topics"] = [];
    for (const line of lines(topics)) {
      if (line.startsWith("#") || !parsedTopics.length) parsedTopics.push({ title: line.replace(/^#+\s*/, ""), points: [] });
      else parsedTopics[parsedTopics.length - 1].points.push(line.replace(/^[-•]\s*/, ""));
    }
    props.onSave({
      title,
      summary,
      participants: participants.split(",").map((p) => p.trim()).filter(Boolean),
      topics: parsedTopics,
      decisions: lines(decisions),
      action_items: lines(actions).map((line) => {
        const [task = "", owner = "", due = ""] = line.split("|").map((part) => part.trim());
        return { task, owner, due };
      }),
      open_questions: lines(questions),
    });
  };

  return (
    <Card title="Modifier le compte rendu" actions={<><button onClick={props.onCancel}>Annuler</button><button className="primary" onClick={save}>Enregistrer</button></>}>
      {props.error && <Notice kind="error">{props.error}</Notice>}
      <div className="form">
        <label>Titre<input value={title} onChange={(e) => setTitle(e.target.value)} /></label>
        <label>Participants <span className="muted">(séparés par des virgules)</span><input value={participants} onChange={(e) => setParticipants(e.target.value)} /></label>
        <label>Résumé<textarea rows={6} value={summary} onChange={(e) => setSummary(e.target.value)} /></label>
        <label>Points abordés <span className="muted">(« # Sujet » puis un point par ligne)</span><textarea rows={8} value={topics} onChange={(e) => setTopics(e.target.value)} /></label>
        <label>Décisions <span className="muted">(une par ligne)</span><textarea rows={4} value={decisions} onChange={(e) => setDecisions(e.target.value)} /></label>
        <label>Actions <span className="muted">(action | responsable | échéance)</span><textarea rows={4} value={actions} onChange={(e) => setActions(e.target.value)} /></label>
        <label>Questions ouvertes <span className="muted">(une par ligne)</span><textarea rows={3} value={questions} onChange={(e) => setQuestions(e.target.value)} /></label>
      </div>
    </Card>
  );
}
