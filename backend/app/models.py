from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.db import utcnow


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict | list | str | None] = mapped_column(JSON)


class MeetingStatus:
    SCHEDULED = "scheduled"
    CANCELLED = "cancelled"
    JOINING = "joining"
    WAITING = "waiting"  # en attente d'admission
    IN_MEETING = "in_meeting"
    RECORDING = "recording"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"

    ACTIVE = (JOINING, WAITING, IN_MEETING, RECORDING)


class Meeting(Base):
    __tablename__ = "meetings"

    id: Mapped[int] = mapped_column(primary_key=True)
    google_event_id: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    title: Mapped[str] = mapped_column(String(500), default="Réunion")
    meet_url: Mapped[str] = mapped_column(String(500))
    start_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    end_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    organizer: Mapped[str] = mapped_column(String(255), default="")
    attendees: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(32), default=MeetingStatus.SCHEDULED, index=True)
    status_message: Mapped[str] = mapped_column(Text, default="")
    auto_join: Mapped[bool] = mapped_column(Boolean, default=True)
    recording_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    audio_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    recording_seconds: Mapped[float] = mapped_column(Float, default=0)
    live_summary: Mapped[str] = mapped_column(Text, default="")
    live_summary_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    live_summary_segment_id: Mapped[int] = mapped_column(Integer, default=0)
    drive_links: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    segments: Mapped[list["TranscriptSegment"]] = relationship(back_populates="meeting", cascade="all, delete-orphan")
    report: Mapped["Report | None"] = relationship(
        back_populates="meeting", cascade="all, delete-orphan", uselist=False
    )


class TranscriptSegment(Base):
    __tablename__ = "transcript_segments"

    id: Mapped[int] = mapped_column(primary_key=True)
    meeting_id: Mapped[int] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(16), default="captions")  # captions | whisper
    speaker: Mapped[str] = mapped_column(String(255), default="")
    text: Mapped[str] = mapped_column(Text)
    start_s: Mapped[float] = mapped_column(Float, default=0)
    end_s: Mapped[float] = mapped_column(Float, default=0)

    meeting: Mapped[Meeting] = relationship(back_populates="segments")


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    meeting_id: Mapped[int] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), unique=True)
    data: Mapped[dict] = mapped_column(JSON)
    model: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    meeting: Mapped[Meeting] = relationship(back_populates="report")


class EventLog(Base):
    __tablename__ = "event_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    meeting_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    level: Mapped[str] = mapped_column(String(16), default="info")
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
