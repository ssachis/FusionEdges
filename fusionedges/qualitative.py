"""Side-by-side qualitative review: what each run looks like to a person, next to the other approach on the same task.

Two tables in runs.db:
  qual_runs   one row per reviewed run: does it look like the donor's navbar, is the rest of the site intact,
              how big a change it made to the site's existing code (computed from the diff), and a note.
  qual_tasks  one row per task: which approach gave the better result for a user, and why.
Reviews live in experiments/reviews/*.json (versioned with git) and are loaded with
`python3 -m fusionedges import-sidebyside FILE`. Automatic checks are never overwritten; both are reported.
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path

from . import store

SCHEMA = """
CREATE TABLE IF NOT EXISTS qual_runs (
  run_id TEXT, reviewer TEXT, reviewed_at TEXT, task TEXT, condition TEXT,
  looks_like_donor TEXT, site_intact TEXT, verdict TEXT, note TEXT, causes TEXT,
  site_files_edited INTEGER, site_lines_removed INTEGER, site_lines_added INTEGER, new_files INTEGER,
  contained INTEGER, auto_success INTEGER, PRIMARY KEY (run_id, reviewer)
);
CREATE TABLE IF NOT EXISTS qual_tasks (
  task TEXT, reviewer TEXT, reviewed_at TEXT, review_set TEXT, donor_has_navbar TEXT, better TEXT, summary TEXT,
  PRIMARY KEY (task, reviewer, review_set)
);
"""
SCALE = {"yes": 2, "partly": 1, "no": 0}
NOT_SITE = ("package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml")


def connect():
    db = store.connect()
    db.executescript(SCHEMA)
    return db


def footprint(run_id: str) -> dict:
    """How much of the site's EXISTING code a run changed, from diff_files (lockfiles and package.json aside)."""
    with store.connect() as db:
        rows = db.execute("SELECT path, status, kind, added, removed FROM diff_files WHERE run_id=?", (run_id,)).fetchall()
    edited = [r for r in rows if r[1] in ("modified", "deleted") and r[2] == "code" and Path(r[0]).name not in NOT_SITE]
    new = [r for r in rows if r[1] == "added" and r[2] in ("code", "binary")]
    tops = {"/".join(Path(r[0]).parts[:3]) for r in new if r[2] == "code"}
    return {"site_files_edited": len(edited), "site_lines_removed": sum(r[4] or 0 for r in edited),
            "site_lines_added": sum(r[3] or 0 for r in edited), "new_files": len(new),
            "contained": int(len(tops) <= 1 and bool(new))}


def import_file(path: Path) -> dict:
    data = json.loads(Path(path).read_text())
    who, when, rset = data["reviewer"], data["reviewed_at"], data.get("set") or Path(path).stem
    n_runs = n_tasks = 0
    with connect() as db:
        auto = dict(db.execute("SELECT run_id, verified_success FROM run_metrics").fetchall()) \
            if db.execute("SELECT name FROM sqlite_master WHERE name='run_metrics'").fetchone() else {}
        for t in data["tasks"]:
            db.execute("INSERT OR REPLACE INTO qual_tasks VALUES (?,?,?,?,?,?,?)",
                       (t["task"], who, when, rset, t.get("donor_has_navbar", "yes"), t["better"], t["summary"]))
            n_tasks += 1
            for r in t["runs"]:
                fp = footprint(r["run_id"])
                db.execute("INSERT OR REPLACE INTO qual_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (r["run_id"], who, when, t["task"], r["condition"], r["looks_like_donor"], r["site_intact"],
                            r.get("verdict", ""), r.get("note", ""), json.dumps(r.get("causes", [])),
                            fp["site_files_edited"], fp["site_lines_removed"], fp["site_lines_added"], fp["new_files"],
                            fp["contained"], auto.get(r["run_id"])))
                n_runs += 1
    return {"runs": n_runs, "tasks": n_tasks, "reviewer": who, "set": rset}


def load_pending() -> list:
    """Import every experiments/reviews/sidebyside-*.json whose review set is not in the database yet."""
    from . import paths
    done = []
    with connect() as db:
        have = {r[0] for r in db.execute("SELECT DISTINCT review_set FROM qual_tasks")}
    for f in sorted((paths.HOME / "experiments" / "reviews").glob("sidebyside-*.json")):
        try:
            rset = json.loads(f.read_text()).get("set") or f.stem
        except (OSError, ValueError):
            continue
        if rset not in have:
            done.append(import_file(f))
    return done


def summary(only: set | None = None) -> dict:
    """What the Analysis tab shows: per approach, how results look to a person, and the footprint of the change.
    `only`: restrict to these run ids (the batch on screen)."""
    with connect() as db:
        db.row_factory = sqlite3.Row
        runs = [dict(r) for r in db.execute("SELECT * FROM qual_runs ORDER BY task, condition, run_id")]
        tasks = [dict(r) for r in db.execute("SELECT * FROM qual_tasks ORDER BY task")]
    if only is not None:
        runs = [r for r in runs if r["run_id"] in only]
        tasks = [t for t in tasks if t["task"] in {r["task"] for r in runs}]
    for r in runs:
        r["causes"] = json.loads(r["causes"] or "[]")
    by = {}
    for cond in sorted({r["condition"] for r in runs}):
        xs = [r for r in runs if r["condition"] == cond]
        med = lambda k: sorted(x[k] for x in xs)[len(xs) // 2] if xs else None   # noqa: E731
        by[cond] = {
            "runs": len(xs),
            "looks_like_donor": dict(Counter(x["looks_like_donor"] for x in xs)),
            "site_intact": dict(Counter(x["site_intact"] for x in xs)),
            "good_for_user": sum(1 for x in xs if x["looks_like_donor"] == "yes" and x["site_intact"] == "yes"),
            "median_site_lines_removed": med("site_lines_removed"), "median_site_files_edited": med("site_files_edited"),
            "contained": sum(x["contained"] for x in xs),
            "auto_vs_human": {"agree": sum(1 for x in xs if x["auto_success"] is not None and
                                             bool(x["auto_success"]) == (x["verdict"] == "works")),
                              "checked": sum(1 for x in xs if x["auto_success"] is not None)},
            "causes": [{"cause": k, "count": n} for k, n in Counter(c for x in xs for c in x["causes"]).most_common(8)],
        }
    better = Counter(t["better"] for t in tasks)
    return {"runs": runs, "tasks": tasks, "by_condition": by, "better": dict(better),
            "reviewers": sorted({r["reviewer"] for r in runs})}


def overall(metric_rows: list, conds=("A", "AD", "MA", "M"), only: set | None = None) -> dict:
    """The one result the Analysis tab leads with. On the tasks a person reviewed, per approach:
      - works by the checks (automatic), looks right to a person, and BOTH (the main measure);
      - each as an average over tasks, so a task run 9 times still counts once;
      - tokens per working navbar (all tokens spent ÷ runs that work by both), time, and how much of the site's own
        code it changed."""
    q = summary(only)
    person = {r["run_id"]: r for r in q["runs"]}
    tasks = sorted({r["task"] for r in q["runs"]})
    out = {"tasks": len(tasks), "reviewers": q["reviewers"], "approaches": []}
    for c in conds:
        xs = [m for m in metric_rows if m["condition"] == c and m["task"] in tasks and m["run_id"] in person and not m["ai_error"]]
        if not xs:
            continue
        def ok_person(m):
            p = person[m["run_id"]]
            return p["verdict"] == "works" or (p["looks_like_donor"] == "yes" and p["site_intact"] == "yes")
        def avg(f):
            rates = []
            for t in tasks:
                ys = [m for m in xs if m["task"] == t]
                if ys:
                    rates.append(sum(1 for m in ys if f(m)) / len(ys))
            return round(sum(rates) / len(rates), 3) if rates else 0
        both = [m for m in xs if m["verified_success"] and ok_person(m)]
        toks = sum(m["tokens_total"] or 0 for m in xs)
        secs = sorted(m["seconds_total"] or 0 for m in xs)
        rem = sorted(person[m["run_id"]]["site_lines_removed"] for m in xs)
        out["approaches"].append({
            "condition": c, "runs": len(xs), "tasks": len({m["task"] for m in xs}),
            "checks": avg(lambda m: bool(m["verified_success"])), "person": avg(ok_person),
            "both": avg(lambda m: bool(m["verified_success"]) and ok_person(m)), "both_runs": len(both),
            "tokens_per_run": round(toks / len(xs)), "tokens_per_working": round(toks / len(both)) if both else None,
            "median_seconds": secs[len(secs) // 2], "median_site_lines_removed": rem[len(rem) // 2],
            "disagree": sum(1 for m in xs if bool(m["verified_success"]) != ok_person(m)),
            "top_causes": [c_["cause"] for c_ in (q["by_condition"].get(c) or q["by_condition"].get(c[:1]) or {}).get("causes", [])[:3]],
        })
    return out
