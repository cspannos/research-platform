from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from research_platform.core.config import get_settings


class Base(DeclarativeBase):
    pass


class Target(Base):
    __tablename__ = "targets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    mission: Mapped[str] = mapped_column(String(32))
    external_id: Mapped[str] = mapped_column(String(64))
    notes: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(default=True)
    # Phase B sky position + cached Gaia cone (shared across candidates)
    ra: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    dec: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    neighbours_json: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    lightcurves: Mapped[list["LightCurve"]] = relationship(back_populates="target")
    candidates: Mapped[list["Candidate"]] = relationship(back_populates="target")


class LightCurve(Base):
    __tablename__ = "lightcurves"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    target_id: Mapped[int] = mapped_column(ForeignKey("targets.id"), index=True)
    mission: Mapped[str] = mapped_column(String(32))
    cache_path: Mapped[str] = mapped_column(String(512))
    n_points: Mapped[int] = mapped_column(Integer, default=0)
    downloaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(64), default="mast")

    target: Mapped["Target"] = relationship(back_populates="lightcurves")


class Candidate(Base):
    __tablename__ = "candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    target_id: Mapped[int] = mapped_column(ForeignKey("targets.id"), index=True)
    period_days: Mapped[float] = mapped_column(Float)
    depth_ppm: Mapped[float] = mapped_column(Float, default=0.0)
    snr: Mapped[float] = mapped_column(Float, default=0.0)
    flag_reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    # Phase A transit geometry (nullable when LC cache / fold fails)
    t0: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    duration_hours: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    odd_depth_ppm: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    even_depth_ppm: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    odd_even_delta_ppm: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    geometry_note: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    plots_ready: Mapped[bool] = mapped_column(default=False)
    # Phase B neighbour / centroid snapshots (JSON text)
    neighbours_json: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    centroid_json: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    # Phase C statistical validation (FPP / NFPP JSON)
    validation_json: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    target: Mapped["Target"] = relationship(back_populates="candidates")
    summaries: Mapped[list["ReviewSummary"]] = relationship(back_populates="candidate")
    comments: Mapped[list["ReviewComment"]] = relationship(back_populates="candidate")


class ReviewSummary(Base):
    __tablename__ = "review_summaries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id"), index=True)
    summary_text: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(16), default="template")
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    candidate: Mapped["Candidate"] = relationship(back_populates="summaries")


class ReviewComment(Base):
    __tablename__ = "review_comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id"), index=True)
    author: Mapped[str] = mapped_column(String(64), default="reviewer")
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    candidate: Mapped["Candidate"] = relationship(back_populates="comments")


_engine = None
_SessionLocal = None


def get_engine():
    global _engine
    if _engine is None:
        settings = get_settings()
        dsn = (
            f"postgresql://{settings.postgres_user}:{settings.postgres_password}"
            f"@{settings.postgres_host}:{settings.postgres_port}/tenant_exoplanet"
        )
        _engine = create_engine(dsn, pool_pre_ping=True)
    return _engine


def get_session_factory():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), autoflush=False, autocommit=False)
    return _SessionLocal


# Columns added after initial deploy; create_all will not ALTER existing tables.
_CANDIDATE_VETTING_COLUMNS: tuple[tuple[str, str], ...] = (
    ("t0", "DOUBLE PRECISION"),
    ("duration_hours", "DOUBLE PRECISION"),
    ("odd_depth_ppm", "DOUBLE PRECISION"),
    ("even_depth_ppm", "DOUBLE PRECISION"),
    ("odd_even_delta_ppm", "DOUBLE PRECISION"),
    ("geometry_note", "TEXT"),
    ("plots_ready", "BOOLEAN DEFAULT FALSE"),
    ("neighbours_json", "TEXT"),
    ("centroid_json", "TEXT"),
    ("validation_json", "TEXT"),
)

_TARGET_VETTING_COLUMNS: tuple[tuple[str, str], ...] = (
    ("ra", "DOUBLE PRECISION"),
    ("dec", "DOUBLE PRECISION"),
    ("neighbours_json", "TEXT"),
)

_schema_ready = False


def _existing_columns(conn, table: str) -> set[str]:
    return {
        row[0]
        for row in conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = :table_name"
            ),
            {"table_name": table},
        )
    }


def ensure_vetting_schema(engine=None) -> None:
    """Apply missing Phase A/B/C columns only (no-op when already present).

    Important: do not run unconditional ALTER on every request — Postgres still
    takes relation locks for ADD COLUMN IF NOT EXISTS and can stall the pool.
    """
    eng = engine or get_engine()
    with eng.begin() as conn:
        for table, columns in (
            ("candidates", _CANDIDATE_VETTING_COLUMNS),
            ("targets", _TARGET_VETTING_COLUMNS),
        ):
            existing = _existing_columns(conn, table)
            if not existing:
                continue
            for column, ddl_type in columns:
                if column in existing:
                    continue
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}"))


def init_db() -> None:
    """Create tables / migrate once per process."""
    global _schema_ready
    if _schema_ready:
        return
    engine = get_engine()
    Base.metadata.create_all(engine)
    ensure_vetting_schema(engine)
    _schema_ready = True


def get_db_session():
    factory = get_session_factory()
    return factory()
