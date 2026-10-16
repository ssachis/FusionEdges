"""SQLite record of every run and every compatibility-report conflict (output/runs.db)."""
from __future__ import annotations

import json
import sqlite3
import time

from . import paths

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY, created TEXT, mode TEXT, migration INTEGER, style INTEGER, component TEXT, edit TEXT, prompt TEXT,
  source TEXT, source_commit TEXT, source_framework TEXT,
  donor TEXT, donor_commit TEXT, donor_framework TEXT,
  status TEXT, error TEXT, outcome TEXT, score TEXT, build_ok INTEGER,
  pages TEXT, text_kept INTEGER, html_links INTEGER, missing_assets INTEGER, source_css_copied INTEGER,
  agent_tokens INTEGER, report_tokens INTEGER, total_cost REAL, retries INTEGER, models TEXT,
  seconds REAL, folder TEXT, data TEXT
);
CREATE TABLE IF NOT EXISTS conflicts (
  run_id TEXT, cid TEXT, type TEXT, severity TEXT, confidence TEXT, basis TEXT, citation TEXT,
  title TEXT, impact TEXT, adaptation TEXT, evidence TEXT, source_framework TEXT, donor_framework TEXT,
  outcome TEXT, PRIMARY KEY (run_id, cid)
);
"""
COLS = ["id", "created", "mode", "migration", "style", "component", "edit", "prompt", "source", "source_commit", "source_framework",
        "donor", "donor_commit", "donor_framework", "status", "error", "outcome", "score", "build_ok", "pages",
        "text_kept", "html_links", "missing_assets", "source_css_copied", "agent_tokens", "report_tokens",
        "total_cost", "retries", "models", "seconds", "folder", "data"]


def connect():
    paths.OUTPUT.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(paths.DB)
    db.executescript(SCHEMA)
    have = {r[1] for r in db.execute("PRAGMA table_info(runs)")}
    for col in ("component", "edit"):  # databases from v0.4
        if col not in have:
            db.execute(f"ALTER TABLE runs ADD COLUMN {col} TEXT")
    return db


def _tok(u):
    u = u or {}
    return (u.get("input_tokens") or 0) + (u.get("output_tokens") or 0)


def save(run: dict):
    ch = (run.get("checks") or {}).get("checks", {})
    agent, rep = run.get("agent") or {}, run.get("report_usage") or {}
    build = (run.get("build") or {}).get("ok")
    row = {
        "id": run["id"], "created": run["created"], "mode": run["mode"],
        "migration": int(bool(run["options"].get("migration"))), "style": int(bool(run["options"].get("style"))),
        "component": run["options"].get("component") or None, "edit": run["options"].get("edit"),
        "prompt": run["prompt"],
        "source": run["source"].get("label") or run["source"].get("spec"), "source_commit": run["source"].get("commit"),
        "source_framework": run.get("source_framework"),
        "donor": run["donor"].get("label") or run["donor"].get("spec"), "donor_commit": run["donor"].get("commit"),
        "donor_framework": run.get("donor_framework"),
        "status": run["status"], "error": run.get("error"),
        "outcome": (run.get("checks") or {}).get("outcome"), "score": (run.get("checks") or {}).get("score"),
        "build_ok": None if build is None else int(bool(build)),
        "pages": (ch.get("pages") or {}).get("value"), "text_kept": (ch.get("text") or {}).get("percent"),
        "html_links": len((ch.get("links") or {}).get("missing", [])) if "links" in ch else None,
        "missing_assets": len((ch.get("assets") or {}).get("missing", [])) if "assets" in ch else None,
        "source_css_copied": (ch.get("style") or {}).get("percent_copied"),
        "agent_tokens": _tok(agent), "report_tokens": _tok(rep),
        "total_cost": round((agent.get("est_cost_usd") or 0) + (rep.get("est_cost_usd") or 0), 5),
        "retries": agent.get("retries"), "models": ", ".join(run.get("models", [])),
        "seconds": run.get("seconds"), "folder": run["folder"], "data": json.dumps(run, default=str),
    }
    with connect() as db:
        db.execute(f"INSERT OR REPLACE INTO runs ({','.join(COLS)}) VALUES ({','.join('?' * len(COLS))})",
                   [row[c] for c in COLS])
        db.execute("DELETE FROM conflicts WHERE run_id=?", (run["id"],))
        for c in (run.get("report") or {}).get("conflicts", []):
            db.execute("INSERT INTO conflicts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                run["id"], c["id"], c.get("type"), c.get("severity"), c.get("confidence"), c.get("basis"),
                c.get("citation"), c.get("title"), c.get("impact"), c.get("adaptation"),
                json.dumps(c.get("evidence", [])), run.get("source_framework"), run.get("donor_framework"),
                row["outcome"]))


def mark_interrupted() -> int:
    """Call once at server start: nothing can still be running then, so 'running' rows were cut off."""
    with connect() as db:
        return db.execute("UPDATE runs SET status='interrupted' WHERE status='running'").rowcount


def history(limit=50) -> list:
    with connect() as db:
        db.row_factory = sqlite3.Row
        return [dict(r) for r in db.execute(
            """SELECT id, created, mode, migration, style, component, edit, source, donor, status, outcome, score, build_ok, pages,
                      text_kept, agent_tokens, report_tokens, total_cost, models
               FROM runs ORDER BY created DESC, rowid DESC LIMIT ?""", (limit,)).fetchall()]


def load(run_id: str):
    with connect() as db:
        row = db.execute("SELECT data FROM runs WHERE id=?", (run_id,)).fetchone()
        return json.loads(row[0]) if row else None


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S")
