import pytest

from core import config, db


@pytest.fixture()
def seeded_db(tmp_path, monkeypatch):
    """Fresh SQLite DB seeded with the DEMO_MODE data, no AI key, no network."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("MANUAL_INPUTS_CSV", str(tmp_path / "manual_inputs.csv"))
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("DEMO_MODE", "true")
    config.reset_settings()
    db.reset_engine()
    db.init_db()
    yield
    db.reset_engine()
    config.reset_settings()
