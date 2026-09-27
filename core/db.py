"""Engine and session management. Seeds illustrative data on first run in DEMO_MODE."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from core.config import get_settings
from core.models import Base, Country

_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None


def get_engine() -> Engine:
    global _engine, _SessionLocal
    if _engine is None:
        url = get_settings().database_url
        if url.startswith("sqlite:///"):
            Path(url[len("sqlite:///"):]).parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(url, future=True, connect_args={"check_same_thread": False} if url.startswith("sqlite") else {})
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def reset_engine() -> None:
    """Drop the cached engine (tests, or after changing DATABASE_URL)."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None


@contextmanager
def session_scope() -> Iterator[Session]:
    get_engine()
    assert _SessionLocal is not None
    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _migrate_text_signal_columns() -> None:
    """Lightweight, idempotent migration: add TextSignal's GDELT-news columns to a DB created before they
    existed. SQLAlchemy's create_all() only creates missing tables, never alters existing ones."""
    from sqlalchemy import inspect, text

    engine = get_engine()
    insp = inspect(engine)
    if "text_signal" not in insp.get_table_names():
        return
    existing = {c["name"] for c in insp.get_columns("text_signal")}
    additions = {"source": "VARCHAR(16) DEFAULT 'statement'", "title": "TEXT", "url": "VARCHAR(512)",
                "tone": "FLOAT", "published_at": "VARCHAR(32)"}
    with engine.begin() as conn:
        for col, ddl in additions.items():
            if col not in existing:
                conn.execute(text(f"ALTER TABLE text_signal ADD COLUMN {col} {ddl}"))


def init_db(seed_if_empty: bool | None = None) -> bool:
    """Create tables; seed illustrative data if the DB is empty. Returns True if seeded."""
    Base.metadata.create_all(get_engine())
    _migrate_text_signal_columns()
    if seed_if_empty is None:
        seed_if_empty = get_settings().demo_mode
    with session_scope() as s:
        empty = s.execute(select(Country).limit(1)).first() is None
    if empty and seed_if_empty:
        from core.seed import seed_all

        seed_all()
        return True
    if empty:
        # Outside DEMO_MODE we still need the coverage table.
        from core.seed import seed_countries

        seed_countries()
    return False
