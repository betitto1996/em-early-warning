"""SQLAlchemy 2.x models (spec §10)."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Country(Base):
    __tablename__ = "country"
    iso2: Mapped[str] = mapped_column(String(2), primary_key=True)
    iso3: Mapped[str] = mapped_column(String(3))
    name: Mapped[str] = mapped_column(String(64))
    currency: Mapped[str] = mapped_column(String(3))
    region: Mapped[str] = mapped_column(String(16))
    oil: Mapped[str] = mapped_column(String(16))  # importer | exporter | neutral


class IndicatorValue(Base):
    """Raw indicator values (World Bank, IMF, FX…) with year and source."""
    __tablename__ = "indicator_value"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    iso2: Mapped[str] = mapped_column(String(2), index=True)
    key: Mapped[str] = mapped_column(String(48), index=True)
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String(24))  # worldbank | imf | frankfurter | brief | illustrative
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ManualInput(Base):
    """Bloomberg exports and team inputs. FX sign: + = local currency weakened vs USD."""
    __tablename__ = "manual_input"
    iso2: Mapped[str] = mapped_column(String(2), primary_key=True)
    spread_bp: Mapped[float | None] = mapped_column(Float, nullable=True)
    spread_1m_bp: Mapped[float | None] = mapped_column(Float, nullable=True)
    cds_5y_bp: Mapped[float | None] = mapped_column(Float, nullable=True)
    local_10y: Mapped[float | None] = mapped_column(Float, nullable=True)
    fx_1m_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    fx_12m_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    text_signal: Mapped[float | None] = mapped_column(Float, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Per-field source: {"spread_bp": "illustrative" | "brief" | "manual", ...}
    sources: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class CountryScore(Base):
    __tablename__ = "country_score"
    iso2: Mapped[str] = mapped_column(String(2), primary_key=True)
    L: Mapped[float | None] = mapped_column(Float, nullable=True)
    S: Mapped[float | None] = mapped_column(Float, nullable=True)
    F: Mapped[float | None] = mapped_column(Float, nullable=True)
    M: Mapped[float | None] = mapped_column(Float, nullable=True)
    T: Mapped[float | None] = mapped_column(Float, nullable=True)
    overall: Mapped[float | None] = mapped_column(Float, nullable=True)
    fundamentals: Mapped[float | None] = mapped_column(Float, nullable=True)
    gap: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_1m_change: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Per-layer source: {"L": "illustrative" | "computed" | "ai" | "manual"}
    layer_sources: Mapped[dict] = mapped_column(JSON, default=dict)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CountryNote(Base):
    __tablename__ = "country_note"
    iso2: Mapped[str] = mapped_column(String(2), primary_key=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    driver: Mapped[str | None] = mapped_column(Text, nullable=True)  # "what's moving it"
    flags: Mapped[dict] = mapped_column(JSON, default=dict)


class Fund(Base):
    __tablename__ = "fund"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128))
    is_sample: Mapped[bool] = mapped_column(Boolean, default=False)


class FundWeight(Base):
    __tablename__ = "fund_weight"
    fund_id: Mapped[int] = mapped_column(ForeignKey("fund.id", ondelete="CASCADE"), primary_key=True)
    iso2: Mapped[str] = mapped_column(String(2), primary_key=True)
    weight: Mapped[float] = mapped_column(Float)


class Holding(Base):
    __tablename__ = "holding"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(String(8))  # ETF | Bond
    fund_id: Mapped[int | None] = mapped_column(ForeignKey("fund.id"), nullable=True)
    iso2: Mapped[str | None] = mapped_column(String(2), nullable=True)
    name: Mapped[str] = mapped_column(String(128))
    ccy: Mapped[str] = mapped_column(String(8))  # USD | Local
    yield_pct: Mapped[float] = mapped_column(Float)
    maturity: Mapped[str | None] = mapped_column(String(16), nullable=True)
    amount_k: Mapped[float] = mapped_column(Float)


class Macro(Base):
    __tablename__ = "macro"
    key: Mapped[str] = mapped_column(String(16), primary_key=True)  # UST10 | DXY | BRENT | FED
    label: Mapped[str] = mapped_column(String(48))
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    display: Mapped[str] = mapped_column(String(32))  # formatted value, e.g. "5.17%" or "~66–70%"
    change_text: Mapped[str | None] = mapped_column(String(64), nullable=True)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(24), default="brief")
    as_of: Mapped[str | None] = mapped_column(String(32), nullable=True)


class TextSignal(Base):
    """One row per scored statement (source='statement', spec §7.3) or GDELT news article (source='gdelt')."""
    __tablename__ = "text_signal"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    iso2: Mapped[str] = mapped_column(String(2), index=True)
    text_hash: Mapped[str] = mapped_column(String(64))
    score: Mapped[float] = mapped_column(Float)
    phrases: Mapped[list] = mapped_column(JSON, default=list)
    summary: Mapped[str] = mapped_column(Text)
    ai_used: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    source: Mapped[str] = mapped_column(String(16), default="statement")
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    tone: Mapped[float | None] = mapped_column(Float, nullable=True)      # GDELT tone, -100..+100
    published_at: Mapped[str | None] = mapped_column(String(32), nullable=True)  # GDELT seendate, e.g. 20260925T120000Z


class ChatTurn(Base):
    """Optional persistence for chat; the app keeps chat in st.session_state."""
    __tablename__ = "chat_turn"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    role: Mapped[str] = mapped_column(String(8))  # user | model
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
