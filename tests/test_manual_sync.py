import csv
import io

from core.db import session_scope
from core.manual_inputs import sync_manual
from core.models import ManualInput


def _write(tmp_path, text):
    p = tmp_path / "manual_inputs.csv"
    p.write_text(text)
    return p


def _rows(p):
    return {r["country"]: r for r in csv.DictReader(io.StringIO(p.read_text()))}


def test_csv_to_db_and_db_only_to_csv(seeded_db, tmp_path):
    with session_scope() as s:
        s.get(ManualInput, "KE").cds_5y_bp = 480          # only in the DB
    p = _write(tmp_path, "country,spread_bp,spread_1m_bp,cds_5y_bp,local_10y,fx_1m_pct,fx_12m_pct,text_signal,note\n"
                         'Kenya,610,+25,,,,,,"a, b"\nEgypt,620,+45,,24.00,3.1,7.6,60,n\n')
    res = sync_manual(p)
    ke = {t["field"]: t for t in res["rows"] if t["country"] == "Kenya"}
    assert ke["spread_bp"]["origin"] == "csv" and ke["spread_bp"]["prev"] == 600   # CSV overrode the seeded 600
    assert ke["cds_5y_bp"]["origin"] == "db"                                        # DB-only value
    assert ke["note"]["origin"] == "csv"
    with session_scope() as s:
        assert s.get(ManualInput, "KE").spread_bp == 610 and s.get(ManualInput, "KE").sources["spread_bp"] == "manual"
    rows = _rows(p)
    assert rows["Kenya"]["cds_5y_bp"] == "480"           # DB-only value written back to the CSV
    assert rows["Kenya"]["spread_1m_bp"] == "+25"        # unchanged cell text preserved
    assert rows["Kenya"]["note"] == "a, b"
    assert len(rows) == 18                               # every covered country is in the CSV afterwards


def test_empty_csv_cells_never_overwrite_db(seeded_db, tmp_path):
    p = _write(tmp_path, "country,spread_bp,local_10y\nKenya,,\n")
    sync_manual(p)
    with session_scope() as s:
        m = s.get(ManualInput, "KE")
        assert m.spread_bp == 600 and m.local_10y == 13.5     # seeded values survive the empty cells
    assert _rows(p)["Kenya"]["spread_bp"] == "600"            # ...and are restored into the CSV


def test_provenance_kept_when_csv_agrees_and_idempotent(seeded_db, tmp_path):
    p = _write(tmp_path, "country,local_10y,fx_1m_pct\nHungary,5.64,\nEgypt,24,3.1\n")
    first = sync_manual(p)
    with session_scope() as s:
        assert s.get(ManualInput, "HU").sources["local_10y"] == "brief"     # confirmed value keeps its source
        assert s.get(ManualInput, "EG").sources["local_10y"] == "manual"    # illustrative -> now team-owned
    second = sync_manual(p)
    assert all(t["origin"] != "csv" or t["prev"] is None for t in second["rows"])
    assert second["counts"]["csv"] == 0                                       # nothing left to import


def test_invalid_cell_is_reported_and_kept(seeded_db, tmp_path):
    p = _write(tmp_path, "country,spread_bp\nKenya,abc\nNorway,100\n")
    res = sync_manual(p)
    assert len(res["errors"]) == 2
    text = p.read_text()
    assert "Kenya,abc" in text and "Norway,100" in text     # raw cells are never destroyed


def test_creates_csv_when_missing(seeded_db, tmp_path):
    p = tmp_path / "manual_inputs.csv"
    sync_manual(p)
    assert len(_rows(p)) == 18


def test_seed_has_no_placeholder_fx_zeros(seeded_db):
    with session_scope() as s:
        assert s.get(ManualInput, "AR").fx_1m_pct is None       # unknown FX is n/a, not 0
        assert s.get(ManualInput, "EG").fx_1m_pct == 3.1        # brief value kept (CSV convention)
