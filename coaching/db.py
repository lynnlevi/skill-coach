"""Portable schema and database setup. No Streamlit dependency."""
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import (
    MetaData, Table, Column, Integer, String, Text, Boolean, Float, DateTime,
    JSON, ForeignKey, CheckConstraint, Index, UniqueConstraint, create_engine, event,
)
from sqlalchemy.engine import make_url

metadata = MetaData()


def utcnow():
    return datetime.now(timezone.utc)


users = Table(
    "users", metadata,
    Column("id", Integer, primary_key=True),
    Column("username", String(64), nullable=False, unique=True),
    Column("password_hash", Text, nullable=False),
    Column("name", String(120), nullable=False),
    Column("role", String(10), nullable=False),
    Column("active", Boolean, nullable=False, default=True),
    Column("session_version", Integer, nullable=False, default=1),
    Column("failed_logins", Integer, nullable=False, default=0),
    Column("locked_until", Float, nullable=False, default=0),
    Column("created_at", DateTime(timezone=True), nullable=False, default=utcnow),
    CheckConstraint("role IN ('admin', 'user')", name="valid_role"),
)
configs = Table(
    "user_config", metadata,
    Column("user_id", ForeignKey("users.id"), primary_key=True),
    Column("training_goal", Text, nullable=False),
    Column("context", Text, nullable=False),
    Column("rubric", JSON, nullable=False),
    Column("coaching_instructions", Text, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False, default=utcnow),
    Column("updated_by", ForeignKey("users.id"), nullable=False),
)
questions = Table(
    "questions", metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", ForeignKey("users.id"), nullable=False),
    Column("text", Text, nullable=False),
    Column("category", String(100), nullable=False),
    Column("active", Boolean, nullable=False, default=True),
    UniqueConstraint("user_id", "text", name="uq_questions_user_text"),
)
Index("ix_questions_user_active", questions.c.user_id, questions.c.active)
attempts = Table(
    "practice_attempts", metadata,
    Column("id", Integer, primary_key=True),
    Column("submission_id", String(36), nullable=False, unique=True),
    Column("user_id", ForeignKey("users.id"), nullable=False),
    Column("created_by", ForeignKey("users.id"), nullable=False),
    Column("question_id", ForeignKey("questions.id"), nullable=False),
    Column("question_text", Text, nullable=False),
    Column("answer", Text, nullable=False),
    Column("answer_source", String(10), nullable=False),
    Column("raw_transcript", Text),
    Column("transcription_model", String(100)),
    Column("config_snapshot", JSON, nullable=False),
    Column("evaluation", JSON),
    Column("overall_score", Float),
    Column("status", String(16), nullable=False, default="pending"),
    Column("error_message", Text),
    Column("evaluation_lease_until", Float, nullable=False, default=0),
    Column("evaluation_token", String(36)),
    Column("model", String(100), nullable=False),
    Column("prompt_version", String(40), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, default=utcnow),
    Column("evaluated_at", DateTime(timezone=True)),
    CheckConstraint("status IN ('pending', 'evaluating', 'failed', 'completed')", name="valid_attempt_status"),
    CheckConstraint("answer_source IN ('typed', 'audio')", name="valid_answer_source"),
    CheckConstraint("overall_score IS NULL OR (overall_score >= 0 AND overall_score <= 10)", name="valid_score"),
)
Index("ix_attempts_user_time", attempts.c.user_id, attempts.c.created_at)
analyses = Table(
    "progress_analyses", metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", ForeignKey("users.id"), nullable=False),
    Column("created_by", ForeignKey("users.id"), nullable=False),
    Column("attempt_ids", JSON, nullable=False),
    Column("attempt_start", DateTime(timezone=True), nullable=False),
    Column("attempt_end", DateTime(timezone=True), nullable=False),
    Column("config_snapshot", JSON, nullable=False),
    Column("analysis", JSON, nullable=False),
    Column("model", String(100), nullable=False),
    Column("prompt_version", String(40), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, default=utcnow),
)
Index("ix_analyses_user_time", analyses.c.user_id, analyses.c.created_at)


def build_engine(database_url: str):
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql+psycopg://", 1)
    elif database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    url = make_url(database_url)
    kwargs = {"pool_pre_ping": True, "hide_parameters": True}
    if url.get_backend_name() == "sqlite":
        if url.database and url.database != ":memory:":
            Path(url.database).expanduser().parent.mkdir(parents=True, exist_ok=True)
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    engine = create_engine(url, **kwargs)
    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def sqlite_setup(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA journal_mode=WAL")
    return engine
