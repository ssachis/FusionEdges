"""Qualitative records next to the numbers: browser checks, experiments, and a git-friendly export.

  python3 -m fusionedges import-browser experiments/browser/2026-10-27      (needs labels.json + results.json there)
  python3 -m fusionedges record-experiment next-to-react-navbar --date 2026-10-27 --task-file ... --writeup ...
        --reviews FILE [FILE...] --browser DIR
  python3 -m fusionedges export next-to-react-navbar [--date 2026-10-27]  -> experiments/results/<name>-<date>.json

What goes where: anything you will count or compare (browser results, reviews, verdicts, diffs) is a table in
output/runs.db. Narrative (the write-up) and images (screenshots) stay as files in git; the database stores their
path and, for screenshots, a SHA-256 so a row can be matched to the exact image. output/ is not in git, so `export`
writes everything for one experiment to experiments/results/, which is.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from pathlib import Path

from . import compare, paths, store

J = lambda x: json.dumps(x)   # noqa: E731


def _rel(p: Path) -> str:
    for base, path in ((paths.HOME, Path(p).absolute()), (paths.HOME.resolve(), Path(p).resolve())):
        try:
            return path.relative_to(base).as_posix()
        except ValueError:
            continue
    return str(p)


def import_browser(folder: str, log=print) -> int:
    d = Path(folder)
    meta = json.loads((d / "labels.json").read_text())
    res = json.loads((d / "results.json").read_text())
    n = 0
    with store.connect() as db:
        for label, rid in meta["labels"].items():
            for vp, r in (res.get(label) or {}).items():
                a, e = r["atLoad"], r["errors"]
                shot = d / f"{label}-{vp}.png"
                sha = hashlib.sha256(shot.read_bytes()).hexdigest() if shot.exists() else None
                nav = a.get("navbar") or {}
                db.execute("INSERT OR REPLACE INTO browser_checks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                    rid, vp, meta["checked_at"], meta.get("checker"), nav.get("tag"), nav.get("classes"), nav.get("height"),
                    J(a.get("visibleLinks", [])), len(a.get("visibleLinks", [])), J(a.get("inlineHidden", [])),
                    int(bool(a.get("hasNavbarClass"))), int(bool(a.get("hasNavbarDefault"))),
                    J([x for x in e["load"] if not x.startswith("console: Failed to load resource")]),   # network noise of the checker
                    J(e["scroll"]), _rel(shot) if shot.exists() else None, sha, _rel(d / "results.json"), meta.get("note")))
                n += 1
    log(f"Stored {n} browser checks from {folder}")
    return n


def browser_for(run_id: str) -> list:
    with store.connect() as db:
        db.row_factory = sqlite3.Row
        rows = [dict(r) for r in db.execute("SELECT * FROM browser_checks WHERE run_id=? ORDER BY checked_at, viewport", (run_id,))]
    for r in rows:
        for k in ("visible_links", "inline_hidden", "load_errors", "scroll_errors"):
            r[k] = json.loads(r[k] or "[]")
    return rows


def record_experiment(name: str, date: str, task_file="", writeup="", reviews=(), browser_dir="", note="") -> dict:
    try:
        where = paths.HOME / "experiments" if (paths.HOME / "experiments").is_dir() else paths.HOME
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=where, capture_output=True, text=True).stdout.strip()
    except OSError:
        commit = ""
    with store.connect() as db:
        db.execute("INSERT OR REPLACE INTO experiments VALUES (?,?,?,?,?,?,?,?,?)",
                   (name, date, task_file, writeup, J(list(reviews)), browser_dir, commit, note, store.now_iso()))
    return {"name": name, "date": date, "git_commit": commit}


def export(name: str, date: str = "", out: str = "") -> Path:
    with store.connect() as db:
        db.row_factory = sqlite3.Row
        exp = db.execute("SELECT * FROM experiments WHERE name=?" + (" AND date=?" if date else "") + " ORDER BY created DESC",
                         (name, date) if date else (name,)).fetchone()
        exp = dict(exp) if exp else {"name": name, "date": date}
        date = date or exp.get("date") or store.now_iso()[:10]
        ids = set()
        comps = [c for c in compare.all_rows() if c["experiment"] == name]
        for c in comps:
            ids |= {c["prompt_run"], c["report_run"]}
        runs = {}
        for i in sorted(ids):
            row = db.execute("SELECT data FROM runs WHERE id=?", (i,)).fetchone()
            rec = json.loads(row[0]) if row else {}
            runs[i] = {k: rec.get(k) for k in ("id", "created", "mode", "options", "source", "donor", "source_framework",
                                               "donor_framework", "models", "checks", "checks_history", "build", "report",
                                               "agent", "seconds")}
            runs[i]["diff"] = [dict(r) for r in db.execute(
                "SELECT path,status,kind,added,removed,truncated,patch FROM diff_files WHERE run_id=? ORDER BY path", (i,))]
            runs[i]["browser"] = browser_for(i)
    reviews = {c["id"]: compare.reviews_for(c["id"]) for c in comps}
    for c in comps:
        c.pop("review", None)       # the full review history is under "reviews"
    data = {"experiment": exp, "exported": store.now_iso(), "comparisons": comps, "reviews": reviews, "runs": runs}
    path = Path(out) if out else paths.HOME / "experiments" / "results" / f"{name}-{date}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, default=str))
    return path
