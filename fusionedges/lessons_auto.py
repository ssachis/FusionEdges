"""Turn what the browser saw into proposed lessons for future reports.

A browser problem ("5 of the navbar's links are hidden on desktop") is matched to a known pattern and drafted as a
lesson with a cause:
  report advice       the agent followed a report item and that produced the problem (the report misled it)
  not in the report   a report run hit a problem the report never warned about (a blind spot)
  agent alone         an agent without the report made this mistake (the report should warn about it)
Drafts are stored as 'proposed' (table proposed_lessons). Only lessons a person keeps reach the memory that
condition E retrieves, labelled human-reviewed. Templates only, no AI call: cheap, deterministic, reviewable.
"""
from __future__ import annotations

import json
import re
import sqlite3

from . import store

PATTERNS = [   # (topic, problem regex, kind, lesson text)
    ("tailwind-responsive", r"links are (probably )?hidden|of the \w+'s links are hidden", "avoid",
     "Do not express responsive classes (hidden md:flex, sm:hidden, lg:block) as inline styles: inline styles have no "
     "media queries, so the links stay hidden at every width. Use a CSS file with an @media rule, or the site's own "
     "responsive classes (e.g. Bootstrap d-none d-md-flex)."),
    ("tailwind-without-tailwind", r"probably unstyled", "avoid",
     "Port the component's look to the site's own styling system (its CSS files or its Bootstrap classes). Class names "
     "from a styling system the site does not use (e.g. Tailwind on a site without Tailwind) do nothing, and the "
     "component renders unstyled."),
    ("host-script-coupling", r"throws (while scrolling|when the page loads)", "do",
     "Before replacing a component, check the site's own scripts (public/*.js, jQuery) for the classes and ids they "
     "look up, and keep those on the new component's markup; otherwise the page throws at runtime and the build does "
     "not notice."),
    ("mount-order", r"no \w+ is visible|page is blank", "avoid",
     "Write the mount change first (the file that renders the old component), and make sure the new component is "
     "imported from there; otherwise nothing new reaches the page even when the build passes."),
    ("phone-width", r"scrolls sideways", "avoid",
     "Keep the component within a phone's width: no fixed widths or min-widths above about 360px, and let menus "
     "wrap or collapse below the site's breakpoint."),
]
HINTS = {   # words in a report item that show the report gave the advice behind a problem
    "tailwind-responsive": r"inline style|style=|inline css",
    "tailwind-without-tailwind": r"keep (the )?tailwind|tailwind class",
    "mount-order": r"helper|create .* first",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS proposed_lessons (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, created TEXT, source TEXT, donor TEXT, component TEXT,
  source_framework TEXT, donor_framework TEXT, mode TEXT, kind TEXT, topic TEXT, text TEXT, evidence TEXT,
  cause TEXT, report_item TEXT, status TEXT, decided_at TEXT, decided_by TEXT, UNIQUE (run_id, topic)
);
"""


def connect():
    db = store.connect()
    db.executescript(SCHEMA)
    return db


def _report_items(rec: dict) -> list:
    rep = rec.get("report") or {}
    return [f"{c.get('title', '')}: {c.get('adaptation', '')}" for c in rep.get("conflicts") or [] if isinstance(c, dict)]


def draft(rec: dict, problems: list) -> list:
    """Proposed lessons for one run's browser problems (one per pattern)."""
    facts = {f.get("kind") for f in rec.get("facts") or []}
    items = _report_items(rec)
    out, seen = [], set()
    for prob in problems or []:
        for topic, rx, kind, text in PATTERNS:
            if topic in seen or not re.search(rx, prob, re.I):
                continue
            if topic == "host-script-coupling" and "host-script-coupling" not in facts:
                topic, kind, text = ("runtime-error", "avoid",
                                     f"The page threw after the transfer ({prob.split(': ', 1)[-1][:120]}). Check for code "
                                     "that runs on load or scroll and reads elements the new component removed or renamed.")
            seen.add(topic)
            used = next((i for i in items if re.search(HINTS.get(topic, r"$^"), i, re.I)), None)
            if rec.get("mode") == "prompt":
                cause = "agent alone"
            elif used:
                cause = "report advice"
            else:
                cause = "not in the report"
            out.append({"kind": kind, "topic": topic, "text": text, "evidence": prob, "cause": cause,
                        "report_item": used})
    return out


def store_drafts(rec: dict, problems: list) -> list:
    drafts = draft(rec, problems)
    if not drafts:
        return []
    with connect() as db:
        for d in drafts:
            db.execute("""INSERT OR IGNORE INTO proposed_lessons (run_id, created, source, donor, component, source_framework,
                          donor_framework, mode, kind, topic, text, evidence, cause, report_item, status)
                          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'proposed')""",
                       (rec["id"], store.now_iso(), rec["source"].get("label") or rec["source"].get("spec"),
                        rec["donor"].get("label") or rec["donor"].get("spec"), (rec.get("options") or {}).get("component") or "",
                        rec.get("source_framework"), rec.get("donor_framework"), rec.get("mode"), d["kind"], d["topic"],
                        d["text"], d["evidence"], d["cause"], d["report_item"]))
    return drafts


def for_runs(run_ids) -> list:
    ids = [i for i in run_ids if i]
    if not ids:
        return []
    with connect() as db:
        db.row_factory = sqlite3.Row
        return [dict(r) for r in db.execute(
            f"SELECT * FROM proposed_lessons WHERE run_id IN ({','.join('?' * len(ids))}) ORDER BY id", ids)]


def decide(lesson_id: int, status: str, who: str = "you") -> dict:
    if status not in ("kept", "discarded", "proposed"):
        raise ValueError("status must be kept, discarded or proposed")
    with connect() as db:
        db.execute("UPDATE proposed_lessons SET status=?, decided_at=?, decided_by=? WHERE id=?",
                   (status, store.now_iso(), who, lesson_id))
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT * FROM proposed_lessons WHERE id=?", (lesson_id,)).fetchone()
    if not row:
        raise ValueError("unknown lesson")
    return dict(row)


def kept(exclude_task=None) -> list:
    """Kept lessons in the shape compare.lessons() returns, for the memory."""
    with connect() as db:
        db.row_factory = sqlite3.Row
        rows = [dict(r) for r in db.execute("SELECT * FROM proposed_lessons WHERE status='kept' ORDER BY id")]
    out = []
    for r in rows:
        if exclude_task and (r["source"], r["donor"], (r["component"] or "").lower()) == exclude_task:
            continue
        out.append({"kind": r["kind"], "topic": r["topic"], "text": r["text"], "evidence": r["evidence"],
                    "comparison": f"run:{r['run_id']}", "task": f"{r['source']} <- {r['donor']} ({r['component']})",
                    "frameworks": f"{r['source_framework']} <- {r['donor_framework']}", "component": r["component"] or "",
                    "review_verdict": None, "reviewer": f"{r['decided_by']} (kept from the browser check)",
                    "trust": "human-reviewed", "cause": r["cause"]})
    return out
