"""Loader for data/manual_inputs.csv (Bloomberg exports and team inputs).

Columns: country, spread_bp, spread_1m_bp, cds_5y_bp, local_10y, fx_1m_pct, fx_12m_pct, text_signal, note
FX sign convention in this file: + = local currency weakened vs USD.
Empty cells keep the existing value; filled cells override API values.
Bloomberg data: analysis/demo use only — never publish it in a public deployment.
"""
from __future__ import annotations

import csv
import io
import math
from pathlib import Path
from typing import Any

from core import config as C

NUMERIC_FIELDS = ["spread_bp", "spread_1m_bp", "cds_5y_bp", "local_10y", "fx_1m_pct", "fx_12m_pct", "text_signal"]
COLUMNS = ["country", *NUMERIC_FIELDS, "note"]

# Plausibility bounds; values outside are rejected (likely a units mistake).
BOUNDS = {
    "spread_bp": (0, 10000), "spread_1m_bp": (-2000, 2000), "cds_5y_bp": (0, 20000),
    "local_10y": (-5, 150), "fx_1m_pct": (-90, 500), "fx_12m_pct": (-90, 1000), "text_signal": (0, 100),
}


def parse_manual_csv(data: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Validate rows. Returns (rows, errors)."""
    rows, errors = [], []
    reader = csv.DictReader(io.StringIO(data))
    if reader.fieldnames is None or "country" not in [f.strip().lower() for f in reader.fieldnames]:
        return [], ["Missing header row with a 'country' column."]
    for n, raw in enumerate(reader, start=2):
        r = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
        if not r.get("country") or r["country"].startswith("#"):
            continue
        iso2 = C.to_iso2(r["country"])
        if iso2 is None:
            errors.append(f"Line {n}: country '{r['country']}' is not covered.")
            continue
        out: dict[str, Any] = {"iso2": iso2}
        for f in NUMERIC_FIELDS:
            v = r.get(f, "")
            if v in ("", "n/a", "NA", "#N/A", "N/A"):
                continue
            try:
                x = float(v.replace(",", ""))
            except ValueError:
                errors.append(f"Line {n}: {f}='{v}' is not a number.")
                continue
            lo, hi = BOUNDS[f]
            if not lo <= x <= hi:
                errors.append(f"Line {n}: {f}={x} is outside the plausible range [{lo}, {hi}].")
                continue
            out[f] = x
        if r.get("note"):
            out["note"] = r["note"]
        rows.append(out)
    return rows, errors


def _cell(v: float) -> str:
    return f"{v:g}"


def sync_manual(path: Path | None = None, write: bool = True) -> dict[str, Any]:
    """Two-way sync of the manual CSV and the ManualInput table.

    1. read the CSV; 2. upsert every non-empty CSV value into the DB (an empty CSV cell never overwrites a DB
    value; a filled cell that differs from the DB wins); 3. write the merged result back to the CSV so it always
    reflects the DB (values only in the DB appear in the CSV; cell text is kept when the value did not change);
    4. return one row per non-empty (country, field): origin csv | db | merged, and the previous DB value if a CSV
    cell overrode a different one. A value keeps its 'brief' / 'fred' provenance when the CSV agrees with it.
    """
    from core.db import session_scope
    from core.models import ManualInput

    path = path or C.get_settings().manual_inputs_csv
    text = path.read_text(encoding="utf-8-sig") if path.exists() else ""
    header, raw_rows = list(COLUMNS), []
    if text.strip():
        reader = csv.DictReader(io.StringIO(text))
        header = [(h or "").strip() for h in (reader.fieldnames or COLUMNS)]
        for r in reader:
            r = {(k or "").strip().lower(): (v or "").strip() for k, v in r.items()}
            if r.get("country") and not r["country"].startswith("#"):
                raw_rows.append(r)
    header += [c for c in COLUMNS if c not in header]
    parsed, errors = parse_manual_csv(text) if text.strip() else ([], [])
    csv_vals = {p["iso2"]: p for p in parsed}

    by_iso: dict[str, dict] = {}
    unknown_rows: list[dict] = []
    order: list[str] = []
    for r in raw_rows:
        iso2 = C.to_iso2(r["country"])
        if iso2 is None:
            unknown_rows.append(r)
        else:
            if iso2 not in by_iso:
                order.append(iso2)
            by_iso[iso2] = r
    order += [c["iso2"] for c in C.COUNTRIES if c["iso2"] not in by_iso]
    names = {c["iso2"]: c["name"] for c in C.COUNTRIES}

    table: list[dict] = []
    with session_scope() as s:
        db_rows = {m.iso2: m for m in s.query(ManualInput)}
        for iso2 in order:
            m = db_rows.get(iso2) or ManualInput(iso2=iso2, sources={})
            sources = dict(m.sources or {})
            raw = by_iso.get(iso2) or {"country": names[iso2]}
            cv = csv_vals.get(iso2, {})
            for f in [*NUMERIC_FIELDS, "note"]:
                c, d = cv.get(f), getattr(m, f)
                if c is None and d is None:
                    continue
                if c is None and (raw.get(f) or "").strip():
                    continue  # a filled but invalid CSV cell: leave both sides alone (error already reported)
                prev = None
                if c is None:
                    origin, val = "db", d
                elif d is None:
                    origin, val = "csv", c
                elif (c == d) if f == "note" else math.isclose(c, d, abs_tol=1e-9):
                    origin, val = "merged", c
                else:
                    origin, val, prev = "csv", c, d
                if origin != "db":
                    setattr(m, f, val)
                    if f != "note" and not (origin == "merged" and sources.get(f) not in (None, "illustrative")):
                        sources[f] = "manual"
                if origin == "db":
                    raw[f] = val if f == "note" else _cell(val)
                table.append({"country": names[iso2], "field": f, "value": val, "origin": origin, "prev": prev})
            m.sources = sources
            s.merge(m)
            by_iso[iso2] = raw

    if write:
        out_rows = [by_iso[i] for i in order] + unknown_rows
        with path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=header, restval="", extrasaction="ignore", lineterminator="\n")
            w.writeheader()
            w.writerows(out_rows)
    counts = {o: sum(1 for t in table if t["origin"] == o) for o in ("csv", "db", "merged")}
    return {"rows": table, "errors": errors, "counts": counts, "n_csv_rows": len(csv_vals), "path": str(path)}


def load_manual_csv(path: Path | None = None) -> dict[str, Any]:
    """CSV -> DB only (used inside refresh_all). Missing file is not an error."""
    path = path or C.get_settings().manual_inputs_csv
    if not path.exists():
        return {"status": "skipped", "detail": f"{path.name} not found", "rows": 0, "errors": []}
    res = sync_manual(path, write=False)
    n = res["n_csv_rows"]
    return {"status": "ok" if n else "empty", "detail": f"{n} rows", "rows": n, "errors": res["errors"]}


def fill_csv_empty_cells(field: str, values: dict[str, float], path: Path | None = None,
                         overwrite: bool = False) -> list[str]:
    """Write `values` ({iso2: number}) into `field` of the manual CSV, only where the cell is empty
    (or also over a filled cell when overwrite=True).
    Other columns, quoting and row order are preserved. Returns the iso2 codes actually filled."""
    path = path or C.get_settings().manual_inputs_csv
    if not path.exists() or not values:
        return []
    rows = list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8-sig"))))
    if not rows or field not in rows[0]:
        return []
    filled = []
    for r in rows:
        iso2 = C.to_iso2(r.get("country", ""))
        if iso2 in values and (overwrite or not (r.get(field) or "").strip()):
            r[field] = f"{values[iso2]:g}"
            filled.append(iso2)
    if filled:
        with path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()), lineterminator="\n")
            w.writeheader()
            w.writerows(rows)
    return filled
