import { ReactNode, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, GoogleStatus, Settings } from "../api";
import { Card, Notice, useAction, useFetch } from "../ui";

const PROVIDERS: Record<string, { label: string; example: string }> = {
  anthropic: { label: "Claude (Anthropic)", example: "claude-sonnet-5-5" },
  openai: { label: "OpenAI", example: "gpt-4o" },
  gemini: { label: "Gemini (Google)", example: "gemini-2.5-pro" },
  mistral: { label: "Mistral", example: "mistral-large-latest" },
  ollama: { label: "Ollama (local)", example: "llama3.1" },
  openai_compatible: { label: "Endpoint compatible OpenAI", example: "nom-du-modèle" },
};
const FORMATS: Record<string, string> = { pdf: "PDF", docx: "Word (.docx)", md: "Markdown", gdoc: "Google Docs" };

export default function SettingsPage() {
  const [params] = useSearchParams();
  const [settings, setSettings] = useState<Settings | null>(null);
  const [saved, setSaved] = useState("");
  const google = useFetch<GoogleStatus>("/google/status", 5000);
  const action = useAction();

  useEffect(() => {
    api<Settings>("/settings").then(setSettings);
  }, []);
  if (!settings) return <p className="muted">Chargement…</p>;

  /** Modifie un champ d'une section, p. ex. set("bot", "cmd_stop", "stop"). */
  const set = <S extends keyof Settings, K extends keyof Settings[S]>(section: S, key: K, value: Settings[S][K]) =>
    setSettings({ ...settings, [section]: { ...settings[section], [key]: value } });

  const save = async () => {
    setSaved("");
    const ok = await action.run(async () => setSettings(await api<Settings>("/settings", { method: "PUT", body: settings })));
    if (ok) {
      setSaved("Paramètres enregistrés.");
      google.reload();
    }
    return ok;
  };

  const text = <S extends keyof Settings>(section: S, key: keyof Settings[S], label: ReactNode, extra: Record<string, unknown> = {}) => (
    <label>
      {label}
      <input
        value={String(settings[section][key] ?? "")}
        onChange={(e) => set(section, key, (extra.type === "number" ? Number(e.target.value) : e.target.value) as never)}
        {...extra}
      />
    </label>
  );
  const check = <S extends keyof Settings>(section: S, key: keyof Settings[S], label: string) => (
    <label className="check">
      <input type="checkbox" checked={Boolean(settings[section][key])} onChange={(e) => set(section, key, e.target.checked as never)} />
      {label}
    </label>
  );

  const p = settings.bot.command_prefix;
  return (
    <>
      <div className="page-head">
        <h1>Paramètres</h1>
        <button className="primary" onClick={save} disabled={action.busy}>
          Enregistrer
        </button>
      </div>
      {params.get("google") === "ok" && <Notice kind="success">Compte Google connecté.</Notice>}
      {params.get("google") === "error" && <Notice kind="error">La connexion Google a échoué (voir les journaux).</Notice>}
      {action.error && <Notice kind="error">{action.error}</Notice>}
      {saved && <Notice kind="success">{saved}</Notice>}

      <Card title="1. Compte Google Workspace">
        <p className="muted">
          Créez un identifiant OAuth « Application Web » dans Google Cloud (API Calendar et Drive activées) avec cette URI
          de redirection : <code>{google.data?.redirect_uri}</code>
        </p>
        <div className="form cols">
          {text("google", "client_id", "Identifiant client OAuth")}
          {text("google", "client_secret", "Secret client OAuth", { type: "password" })}
        </div>
        <GoogleAccount status={google.data} save={save} reload={google.reload} />
      </Card>

      <Card title="2. Navigateur du bot">
        <BrowserLogin status={google.data} reload={google.reload} />
      </Card>

      <Card title="Bot et commandes de chat">
        <div className="form cols">
          {text("bot", "command_prefix", "Préfixe des commandes")}
          {text("bot", "cmd_record", "Démarrer l'enregistrement")}
          {text("bot", "cmd_stop", "Arrêter l'enregistrement")}
          {text("bot", "cmd_leave", "Quitter la réunion")}
          {text("bot", "cmd_status", "Afficher l'état")}
          {text("bot", "join_minutes_before", "Entrer X minutes avant le début", { type: "number", min: 0 })}
          {text("bot", "admission_timeout_minutes", "Attente d'admission max (min)", { type: "number", min: 1 })}
          {text("bot", "alone_timeout_minutes", "Quitter si seul depuis (min)", { type: "number", min: 1 })}
          {text("bot", "max_duration_minutes", "Durée maximale (min)", { type: "number", min: 5 })}
        </div>
        <p className="muted">
          Dans le chat de la réunion : <code>{p}{settings.bot.cmd_record}</code>, <code>{p}{settings.bot.cmd_stop}</code>,{" "}
          <code>{p}{settings.bot.cmd_leave}</code>, <code>{p}{settings.bot.cmd_status}</code>
        </p>
        <label>
          Personnes autorisées à piloter le bot <span className="muted">(noms affichés dans Meet, un par ligne ; vide = tout le monde)</span>
          <textarea
            rows={3}
            value={settings.bot.allowed_senders.join("\n")}
            onChange={(e) => set("bot", "allowed_senders", e.target.value.split("\n").map((s) => s.trimStart()).filter((s, i, all) => s || i === all.length - 1))}
          />
        </label>
        {check("bot", "auto_join", "Rejoindre automatiquement les nouvelles réunions de l'agenda")}
        {check("bot", "announce", "Annoncer le bot et ses commandes dans le chat à son arrivée")}
      </Card>

      <Card title="Transcription">
        <p className="muted">
          Le transcript en direct vient des sous-titres Meet. À la fin, Whisper retranscrit l'enregistrement pour une
          meilleure qualité.
        </p>
        {check("transcription", "whisper_enabled", "Retranscrire avec Whisper après la réunion")}
        <div className="form cols">
          <label>
            Modèle Whisper
            <select value={settings.transcription.whisper_model} onChange={(e) => set("transcription", "whisper_model", e.target.value)}>
              {["tiny", "base", "small", "medium", "large-v3"].map((m) => <option key={m}>{m}</option>)}
            </select>
          </label>
          <label>
            Matériel
            <select value={settings.transcription.device} onChange={(e) => set("transcription", "device", e.target.value)}>
              <option value="cpu">Processeur (CPU)</option>
              <option value="cuda">Carte graphique (CUDA)</option>
            </select>
          </label>
          {text("transcription", "language", "Langue parlée (code ISO, vide = détection)")}
        </div>
      </Card>

      <Card title="Intelligence artificielle">
        <div className="form cols">
          <label>
            Fournisseur
            <select value={settings.llm.provider} onChange={(e) => set("llm", "provider", e.target.value)}>
              {Object.entries(PROVIDERS).map(([key, v]) => <option key={key} value={key}>{v.label}</option>)}
            </select>
          </label>
          {text("llm", "model", "Modèle", { placeholder: PROVIDERS[settings.llm.provider]?.example })}
          {text("llm", "api_key", "Clé API", { type: "password", placeholder: settings.llm.provider === "ollama" ? "non requise" : "" })}
          {text("llm", "api_base", "URL de l'API (optionnel)", { placeholder: settings.llm.provider === "ollama" ? "http://ollama:11434" : "" })}
          {text("llm", "report_language", "Langue des comptes rendus")}
          {text("llm", "live_summary_minutes", "Résumé en direct toutes les X min (0 = désactivé)", { type: "number", min: 0 })}
        </div>
        <label>
          Consignes supplémentaires pour le compte rendu
          <textarea rows={3} value={settings.llm.extra_instructions} onChange={(e) => set("llm", "extra_instructions", e.target.value)} placeholder="Ex. : ton formel, mettre en avant les risques, tutoyer…" />
        </label>
        <LlmTest save={save} />
      </Card>

      <Card title="Dépôt automatique dans Google Drive">
        {check("export", "drive_enabled", "Déposer automatiquement les documents à la fin de chaque réunion")}
        <DrivePicker
          connected={!!google.data?.connected}
          path={settings.export.folder_path}
          onPick={(driveId, folderId, path) =>
            setSettings({ ...settings, export: { ...settings.export, drive_id: driveId, folder_id: folderId, folder_path: path } })
          }
        />
        <div className="row wrap">
          Formats :
          {Object.entries(FORMATS).map(([key, label]) => (
            <label className="check" key={key}>
              <input
                type="checkbox"
                checked={settings.export.formats.includes(key)}
                onChange={(e) => set("export", "formats", e.target.checked ? [...settings.export.formats, key] : settings.export.formats.filter((f) => f !== key))}
              />
              {label}
            </label>
          ))}
        </div>
        <div className="form cols">
          {text("export", "name_pattern", <>Nom des fichiers <span className="muted">({"{date}"}, {"{time}"}, {"{title}"})</span></>)}
        </div>
        {check("export", "subfolder_per_meeting", "Créer un sous-dossier par réunion")}
        {check("export", "include_transcript", "Joindre le transcript complet (.txt)")}
        {check("export", "include_video", "Joindre l'enregistrement vidéo (.mp4)")}
      </Card>

      <Card title="Conservation des données">
        <div className="form cols">
          {text("retention", "days", "Supprimer les réunions après X jours (0 = jamais)", { type: "number", min: 0 })}
        </div>
      </Card>
    </>
  );
}

function GoogleAccount({ status, save, reload }: { status: GoogleStatus | null; save: () => Promise<boolean>; reload: () => void }) {
  const action = useAction();
  const connect = () =>
    action.run(async () => {
      if (!(await save())) return;
      const { url } = await api<{ url: string }>("/google/oauth/start");
      window.location.href = url;
    });
  return (
    <div className="row wrap">
      {status?.connected ? (
        <>
          <span className="ok">Connecté : {status.account || "compte Workspace"}</span>
          <button onClick={connect}>Reconnecter</button>
          <button onClick={() => action.run(() => api("/google/disconnect", { method: "POST" }).then(reload))}>Déconnecter</button>
        </>
      ) : (
        <button className="primary" onClick={connect} disabled={action.busy}>
          Enregistrer et connecter le compte Google
        </button>
      )}
      {action.error && <Notice kind="error">{action.error}</Notice>}
    </div>
  );
}

function BrowserLogin({ status, reload }: { status: GoogleStatus | null; reload: () => void }) {
  const [open, setOpen] = useState(false);
  const [ready, setReady] = useState(false);
  const action = useAction();
  const session = status?.browser_session;

  const start = async () => {
    if (!(await action.run(() => api("/google/browser/start", { method: "POST" })))) return;
    setOpen(true);
    setReady(false);
    setTimeout(() => setReady(true), 4000); // le temps que l'écran virtuel démarre
  };
  const close = async (finish: boolean) => {
    await action.run(() => api(`/google/browser/${finish ? "finish" : "cancel"}`, { method: "POST" }));
    setOpen(false);
    setTimeout(reload, 6000);
  };

  return (
    <>
      <p className="muted">
        Pour entrer dans les réunions, le bot utilise un navigateur connecté au même compte Google. Google refusant les
        connexions automatisées, connectez-vous une fois vous-même dans la fenêtre ci-dessous (profitez-en pour régler
        la langue des sous-titres dans Meet si besoin).
      </p>
      <p className={session?.ok ? "ok" : "muted"}>
        État : {session ? session.message : "jamais connecté"}
      </p>
      {action.error && <Notice kind="error">{action.error}</Notice>}
      {!open ? (
        <button className={session?.ok ? "" : "primary"} onClick={start} disabled={action.busy}>
          {session?.ok ? "Reconnecter le navigateur" : "Ouvrir la fenêtre de connexion"}
        </button>
      ) : (
        <>
          {ready ? (
            <iframe className="novnc" title="Navigateur du bot" src="/novnc/vnc.html?autoconnect=true&resize=scale&path=novnc/websockify" />
          ) : (
            <p className="muted">Ouverture du navigateur…</p>
          )}
          <div className="row">
            <button className="primary" onClick={() => close(true)} disabled={action.busy}>
              J'ai terminé la connexion
            </button>
            <button onClick={() => close(false)} disabled={action.busy}>
              Annuler
            </button>
          </div>
        </>
      )}
    </>
  );
}

function LlmTest({ save }: { save: () => Promise<boolean> }) {
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null);
  const action = useAction();
  const test = () =>
    action.run(async () => {
      setResult(null);
      if (await save()) setResult(await api("/settings/llm/test", { method: "POST" }));
    });
  return (
    <>
      <button onClick={test} disabled={action.busy}>
        {action.busy ? "Test en cours…" : "Enregistrer et tester"}
      </button>
      {result && <Notice kind={result.ok ? "success" : "error"}>{result.message}</Notice>}
    </>
  );
}

interface Crumb {
  id: string;
  name: string;
}

/** Navigation dans les Drives partagés pour choisir le dossier de destination. */
function DrivePicker({ connected, path, onPick }: { connected: boolean; path: string; onPick: (driveId: string, folderId: string, path: string) => void }) {
  const [open, setOpen] = useState(false);
  const [crumbs, setCrumbs] = useState<Crumb[]>([]);
  const [driveId, setDriveId] = useState("");
  const [items, setItems] = useState<Crumb[]>([]);
  const action = useAction();

  const load = (nextCrumbs: Crumb[], nextDrive: string) =>
    action.run(async () => {
      const current = nextCrumbs[nextCrumbs.length - 1];
      const list = !current
        ? [{ id: "root", name: "Mon Drive" }, ...(await api<Crumb[]>("/drive/drives"))]
        : await api<Crumb[]>(`/drive/folders?parent_id=${encodeURIComponent(current.id)}&drive_id=${encodeURIComponent(nextDrive)}`);
      setItems(list);
      setCrumbs(nextCrumbs);
      setDriveId(nextDrive);
    });
  const enter = (item: Crumb) => load([...crumbs, item], crumbs.length ? driveId : item.id === "root" ? "" : item.id);
  const current = crumbs[crumbs.length - 1];

  return (
    <div className="picker">
      <div className="row wrap">
        <span>
          Dossier de destination : <strong>{path || "aucun"}</strong>
        </span>
        <button
          disabled={!connected}
          onClick={() => {
            setOpen(!open);
            if (!open) load([], "");
          }}
        >
          {open ? "Fermer" : "Choisir un dossier…"}
        </button>
        {!connected && <span className="muted small">Connectez d'abord le compte Google.</span>}
      </div>
      {open && (
        <div className="picker-body">
          {action.error && <Notice kind="error">{action.error}</Notice>}
          <div className="row wrap small">
            <button className="link" onClick={() => load([], "")}>Drives</button>
            {crumbs.map((c, i) => (
              <span key={c.id}>
                {" / "}
                <button className="link" onClick={() => load(crumbs.slice(0, i + 1), driveId)}>{c.name}</button>
              </span>
            ))}
          </div>
          <ul>
            {items.map((item) => (
              <li key={item.id}>
                <button className="link" onClick={() => enter(item)}>📁 {item.name}</button>
              </li>
            ))}
            {!items.length && !action.busy && <li className="muted">Aucun sous-dossier.</li>}
          </ul>
          {current && (
            <button
              className="primary"
              onClick={() => {
                onPick(driveId, current.id, crumbs.map((c) => c.name).join(" / "));
                setOpen(false);
              }}
            >
              Choisir « {current.name} »
            </button>
          )}
        </div>
      )}
    </div>
  );
}
