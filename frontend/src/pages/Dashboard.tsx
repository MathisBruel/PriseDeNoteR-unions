import { FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ACTIVE, api, GoogleStatus, Meeting } from "../api";
import { Card, MeetingTable, Notice, useAction, useFetch } from "../ui";

export default function Dashboard() {
  const google = useFetch<GoogleStatus>("/google/status", 15000);
  const meetings = useFetch<Meeting[]>("/meetings?scope=upcoming", 5000);
  const action = useAction();
  const navigate = useNavigate();
  const [url, setUrl] = useState("");

  const active = (meetings.data ?? []).filter((m) => ACTIVE.includes(m.status));
  const upcoming = (meetings.data ?? []).filter((m) => m.status === "scheduled");

  const joinByLink = async (event: FormEvent) => {
    event.preventDefault();
    await action.run(async () => {
      const meeting = await api<Meeting>("/meetings", { body: { meet_url: url } });
      navigate(`/meetings/${meeting.id}`);
    });
  };
  const sync = () => action.run(() => api("/google/sync", { method: "POST" }).then(meetings.reload));
  const toggleAuto = (m: Meeting) =>
    action.run(() => api(`/meetings/${m.id}`, { method: "PATCH", body: { auto_join: !m.auto_join } }).then(meetings.reload));
  const joinNow = (m: Meeting) =>
    action.run(() => api(`/meetings/${m.id}/join`, { method: "POST" }).then(() => navigate(`/meetings/${m.id}`)));

  const g = google.data;
  return (
    <>
      <h1>Tableau de bord</h1>
      {action.error && <Notice kind="error">{action.error}</Notice>}

      {g && (!g.connected || !g.browser_session?.ok) && (
        <Notice kind="warning">
          Configuration incomplète :{" "}
          {!g.connected && "le compte Google (agenda et Drive) n'est pas connecté. "}
          {!g.browser_session?.ok &&
            `le navigateur du bot n'est pas connecté à Google${g.browser_session?.message ? ` (${g.browser_session.message})` : ""}. `}
          <Link to="/settings">Ouvrir les paramètres</Link>
        </Notice>
      )}

      <div className="grid-2">
        <Card title="Compte Google">
          <p className={g?.connected ? "ok" : "muted"}>{g?.connected ? `Connecté : ${g.account || "compte Workspace"}` : "Non connecté"}</p>
          <p className={g?.browser_session?.ok ? "ok" : "muted"}>
            Navigateur du bot : {g?.browser_session?.ok ? "connecté" : "non connecté"}
          </p>
        </Card>
        <Card title="Rejoindre une réunion par lien">
          <form className="row" onSubmit={joinByLink}>
            <input
              placeholder="https://meet.google.com/abc-defg-hij"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              required
            />
            <button className="primary" disabled={action.busy}>
              Envoyer le bot
            </button>
          </form>
        </Card>
      </div>

      <Card title={`Bots actifs (${active.length})`}>
        <MeetingTable meetings={active} extra={(m) => <Link to={`/meetings/${m.id}`}>Suivre en direct →</Link>} />
      </Card>

      <Card
        title="Réunions à venir"
        actions={
          <button onClick={sync} disabled={action.busy || !g?.connected}>
            Synchroniser l'agenda
          </button>
        }
      >
        <MeetingTable
          meetings={upcoming}
          extra={(m) => (
            <>
              <label className="check">
                <input type="checkbox" checked={m.auto_join} onChange={() => toggleAuto(m)} /> Entrée auto
              </label>
              <button onClick={() => joinNow(m)} disabled={action.busy}>
                Rejoindre maintenant
              </button>
            </>
          )}
        />
      </Card>
    </>
  );
}
