from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

MASK = "********"


# ---------- Paramètres applicatifs (modifiables dans l'admin) ----------


class GoogleSettings(BaseModel):
    client_id: str = ""
    client_secret: str = ""


class BotSettings(BaseModel):
    auto_join: bool = True
    join_minutes_before: int = 1
    command_prefix: str = "!"
    cmd_record: str = "enregistrer"
    cmd_stop: str = "stop"
    cmd_leave: str = "quitter"
    cmd_status: str = "statut"
    # Noms affichés autorisés à piloter le bot (vide = tout le monde).
    allowed_senders: list[str] = Field(default_factory=list)
    announce: bool = True
    admission_timeout_minutes: int = 10
    alone_timeout_minutes: int = 5
    max_duration_minutes: int = 240


class TranscriptionSettings(BaseModel):
    whisper_enabled: bool = True
    whisper_model: str = "small"
    device: str = "cpu"  # cpu | cuda
    language: str = "fr"


class LLMSettings(BaseModel):
    provider: str = "anthropic"  # anthropic | openai | gemini | mistral | ollama | openai_compatible
    model: str = "claude-sonnet-5-5"
    api_key: str = ""
    api_base: str = ""
    live_summary_minutes: int = 5  # 0 = désactivé
    report_language: str = "français"
    extra_instructions: str = ""


class ExportSettings(BaseModel):
    drive_enabled: bool = False
    drive_id: str = ""
    folder_id: str = ""
    folder_path: str = ""
    formats: list[str] = Field(default_factory=lambda: ["pdf", "gdoc"])  # pdf | docx | md | gdoc
    name_pattern: str = "{date} - {title}"
    subfolder_per_meeting: bool = True
    include_video: bool = False
    include_transcript: bool = True


class RetentionSettings(BaseModel):
    days: int = 0  # 0 = conservation illimitée


class AppSettings(BaseModel):
    google: GoogleSettings = Field(default_factory=GoogleSettings)
    bot: BotSettings = Field(default_factory=BotSettings)
    transcription: TranscriptionSettings = Field(default_factory=TranscriptionSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    export: ExportSettings = Field(default_factory=ExportSettings)
    retention: RetentionSettings = Field(default_factory=RetentionSettings)


SECRET_FIELDS = (("google", "client_secret"), ("llm", "api_key"))


# ---------- Compte rendu ----------


class Topic(BaseModel):
    title: str = ""
    points: list[str] = Field(default_factory=list)


class ActionItem(BaseModel):
    task: str = ""
    owner: str = ""
    due: str = ""


class ReportData(BaseModel):
    title: str = ""
    summary: str = ""
    participants: list[str] = Field(default_factory=list)
    topics: list[Topic] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
    action_items: list[ActionItem] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)


# ---------- API ----------


class Credentials(BaseModel):
    email: str
    password: str = Field(min_length=8)


class MeetingCreate(BaseModel):
    meet_url: str
    title: str = "Réunion manuelle"
    join_now: bool = True


class MeetingPatch(BaseModel):
    auto_join: bool | None = None
    title: str | None = None


class SegmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    source: str
    speaker: str
    text: str
    start_s: float
    end_s: float


class MeetingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str
    meet_url: str
    start_at: datetime
    end_at: datetime | None
    organizer: str
    attendees: list
    status: str
    status_message: str
    auto_join: bool
    has_recording: bool = False
    has_report: bool = False
    recording_seconds: float
    live_summary: str
    drive_links: list


class ReportOut(BaseModel):
    data: ReportData
    model: str
    updated_at: datetime
