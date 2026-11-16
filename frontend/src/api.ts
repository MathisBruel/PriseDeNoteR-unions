export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function api<T = unknown>(path: string, options: { method?: string; body?: unknown } = {}): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method: options.method ?? (options.body === undefined ? "GET" : "POST"),
    headers: options.body === undefined ? undefined : { "Content-Type": "application/json" },
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
    credentials: "same-origin",
  });
  if (!response.ok) {
    let message = `Erreur ${response.status}`;
    try {
      const data = await response.json();
      if (typeof data.detail === "string") message = data.detail;
      else if (Array.isArray(data.detail)) message = data.detail.map((d: { msg: string }) => d.msg).join(" · ");
    } catch {
      /* réponse non JSON */
    }
    throw new ApiError(response.status, message);
  }
  return response.status === 204 ? (undefined as T) : response.json();
}

export interface Meeting {
  id: number;
  title: string;
  meet_url: string;
  start_at: string;
  end_at: string | null;
  organizer: string;
  attendees: string[];
  status: string;
  status_message: string;
  auto_join: boolean;
  has_recording: boolean;
  has_report: boolean;
  recording_seconds: number;
  live_summary: string;
  drive_links: { kind: string; name: string; url: string }[];
}

export interface Segment {
  id: number;
  source: string;
  speaker: string;
  text: string;
  start_s: number;
  end_s: number;
}

export interface ReportData {
  title: string;
  summary: string;
  participants: string[];
  topics: { title: string; points: string[] }[];
  decisions: string[];
  action_items: { task: string; owner: string; due: string }[];
  open_questions: string[];
}

export interface Report {
  data: ReportData;
  model: string;
  updated_at: string;
}

export interface Settings {
  google: { client_id: string; client_secret: string };
  bot: {
    auto_join: boolean;
    join_minutes_before: number;
    command_prefix: string;
    cmd_record: string;
    cmd_stop: string;
    cmd_leave: string;
    cmd_status: string;
    allowed_senders: string[];
    announce: boolean;
    admission_timeout_minutes: number;
    alone_timeout_minutes: number;
    max_duration_minutes: number;
  };
  transcription: { whisper_enabled: boolean; whisper_model: string; device: string; language: string };
  llm: {
    provider: string;
    model: string;
    api_key: string;
    api_base: string;
    live_summary_minutes: number;
    report_language: string;
    extra_instructions: string;
  };
  export: {
    drive_enabled: boolean;
    drive_id: string;
    folder_id: string;
    folder_path: string;
    formats: string[];
    name_pattern: string;
    subfolder_per_meeting: boolean;
    include_video: boolean;
    include_transcript: boolean;
  };
  retention: { days: number };
}

export interface GoogleStatus {
  configured: boolean;
  connected: boolean;
  account: string;
  redirect_uri: string;
  browser_session: { ok: boolean; message: string; at: string } | null;
}

export const STATUS_LABELS: Record<string, string> = {
  scheduled: "Planifiée",
  cancelled: "Annulée",
  joining: "Connexion…",
  waiting: "En attente d'admission",
  in_meeting: "En réunion",
  recording: "Enregistrement",
  processing: "Traitement…",
  done: "Terminée",
  failed: "Échec",
};
export const ACTIVE = ["joining", "waiting", "in_meeting", "recording"];

/** Les dates de l'API sont en UTC sans suffixe. */
export function parseDate(value: string): Date {
  return new Date(/[zZ]|[+-]\d\d:\d\d$/.test(value) ? value : `${value}Z`);
}

export function formatDate(value: string): string {
  return parseDate(value).toLocaleString("fr-FR", {
    weekday: "short",
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatTime(seconds: number): string {
  const total = Math.floor(seconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = String(total % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${s}` : `${String(m).padStart(2, "0")}:${s}`;
}
