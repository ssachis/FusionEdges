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


def _fair(v) -> bool:
    """comparisons.fair may come back as 1/0 or, in tables migrated with ALTER TABLE, as the text '1'/'0'."""
    return str(v).strip().lower() in ("1", "true")


def answer(rows: list) -> dict:
    """The one-line answer to 'did the report help?', from the fair (same-model) comparisons only."""
    fair = [r for r in rows if _fair(r.get("fair"))]
    yes, no = sum(r["helped"] == "yes" for r in fair), sum(r["helped"] == "no" and r.get("final") == "worse" for r in fair)
    same = sum(r["helped"] == "no" and r.get("final") != "worse" for r in fair)
    pl = lambda n, w: f"{n} {w}{'' if n == 1 else 's'}"   # noqa: E731
    if not rows:
        return {"answer": "not yet", "reason": "No comparisons yet: run the same task with and without the report."}
    if not fair:
        return {"answer": "not yet", "reason": f"{pl(len(rows), 'comparison')} so far, but none used the same model in both runs, so none is a fair test."}
    if yes > no:
        return {"answer": "yes", "reason": f"It helped in {yes} of {pl(len(fair), 'fair comparison')} (same model in both runs)"
                                           + (f", made {no} worse" if no else "") + (f", changed nothing in {same}" if same else "") + "."}
    if no > yes:
        return {"answer": "no", "reason": f"It made {no} of {pl(len(fair), 'fair comparison')} worse" + (f" and helped in {yes}" if yes else "") + "."}
    return {"answer": "not clear yet", "reason": f"{yes} better, {no} worse, {same} unchanged across {pl(len(fair), 'fair comparison')}."}


def _answer_rows(experiment: str = "") -> list:
    return [{"fair": c.get("fair"), "helped": c.get("helped") or compare.helped(c["verdict"], (c.get("review") or {}).get("verdict"))[0],
             "final": (c.get("review") or {}).get("verdict") or c["verdict"]}
            for c in compare.all_rows() if not experiment or c["experiment"] == experiment]


def store_answer(name: str, date: str = "") -> dict:
    a = answer(_answer_rows(name))
    with store.connect() as db:
        db.execute("UPDATE experiments SET answer=?, answer_reason=?, answered_at=? WHERE name=?" + (" AND date=?" if date else ""),
                   (a["answer"], a["reason"], store.now_iso(), name, *([date] if date else [])))
    return a


def record_experiment(name: str, date: str, task_file="", writeup="", reviews=(), browser_dir="", note="") -> dict:
    try:
        where = paths.HOME / "experiments" if (paths.HOME / "experiments").is_dir() else paths.HOME
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=where, capture_output=True, text=True).stdout.strip()
    except OSError:
        commit = ""
    with store.connect() as db:
        db.execute("INSERT OR REPLACE INTO experiments (name, date, task_file, writeup, reviews, browser_dir, git_commit, note, created) "
                   "VALUES (?,?,?,?,?,?,?,?,?)",
                   (name, date, task_file, writeup, J(list(reviews)), browser_dir, commit, note, store.now_iso()))
    return {"name": name, "date": date, "git_commit": commit, **store_answer(name, date)}


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
    if exp.get("name"):
        exp.update({k: v for k, v in store_answer(name, date).items()})
    data = {"experiment": exp, "exported": store.now_iso(), "comparisons": comps, "reviews": reviews, "runs": runs}
    path = Path(out) if out else paths.HOME / "experiments" / "results" / f"{name}-{date}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, default=str))
    return path


def _browser_summary(run_id: str) -> dict:
    rows = browser_for(run_id)
    if not rows:
        return {}
    last = rows[-1]["checked_at"]
    out = {}
    for r in rows:
        if r["checked_at"] == last:
            out[r["viewport"]] = {"links": r["link_count"], "scroll_errors": len(r["scroll_errors"]),
                                  "screenshot": r["screenshot"], "navbar": r["navbar_classes"]}
    return out


def analysis(experiment: str = "") -> dict:
    """Everything the Analysis tab shows, computed from runs.db."""
    from collections import Counter
    comps = [c for c in compare.all_rows() if not experiment or c["experiment"] == experiment]
    rows = []
    for c in sorted(comps, key=lambda c: c["created"]):
        p, r = store.load(c["prompt_run"]) or {}, store.load(c["report_run"]) or {}
        mp, mr = sorted(set(p.get("models") or [])), sorted(set(r.get("models") or []))
        rv = c.get("review") or {}
        rows.append({
            "fair": _fair(c.get("fair")) if c.get("fair") is not None else None,
            "helped": c.get("helped") or compare.helped(c["verdict"], rv.get("verdict"))[0],
            "helped_basis": c.get("helped_basis"),
            "id": c["id"], "experiment": c["experiment"], "condition": c.get("condition") or "D",
            "task": f"{c['source'].split('/')[-1]} ← {c['donor'].split('/')[-1]}", "source": c["source"], "donor": c["donor"],
            "component": c["component"], "prompt_run": c["prompt_run"], "report_run": c["report_run"],
            "models_alone": mp, "models_report": mr, "same_model": bool(mp) and mp == mr,
            "score_alone": c["prompt_score"], "score_report": c["report_score"],
            "auto_verdict": c["verdict"], "reviewed_verdict": rv.get("verdict"), "reviewer": rv.get("reviewer"),
            "review_summary": rv.get("summary"), "note": c["note"],
            "tokens_alone": c["prompt_tokens"], "tokens_report": c["report_tokens"],
            "cost_alone": c["prompt_cost"], "cost_report": c["report_cost"],
            "browser_alone": _browser_summary(c["prompt_run"]), "browser_report": _browser_summary(c["report_run"]),
            "failure_categories": rv.get("failure_categories") or [], "lessons": rv.get("lessons") or [],
            "adherence": {k: (c.get("adherence") or {}).get(k) for k in ("testable", "addressed", "partial", "alone_addressed")},
        })
    final = lambda x: x["reviewed_verdict"] or x["auto_verdict"]   # noqa: E731
    count = lambda xs, key: dict(Counter(key(x) for x in xs))       # noqa: E731
    fair = [x for x in rows if x["same_model"]]
    cats = Counter()
    for x in rows:
        for f in x["failure_categories"]:
            side = "report run" if f.lower().startswith("report") else "agent alone" if f.lower().startswith("agent") else "pair"
            kind = f.split(":", 1)[1].split("(")[0].split(" - ")[0].strip() if ":" in f else f
            cats[(side, kind)] += 1
    lessons, seen = [], set()
    for x in rows:
        for l in x["lessons"]:
            t = l.get("text") if isinstance(l, dict) else l
            if t and t not in seen:
                seen.add(t)
                lessons.append({**(l if isinstance(l, dict) else {"text": l}), "task": x["task"],
                                "trust": "AI-reviewed" if (x["reviewer"] or "").lower().startswith("claude") else "human-reviewed"})
    tok = [(x["tokens_alone"], x["tokens_report"]) for x in rows if x["tokens_alone"] and x["tokens_report"]]
    with store.connect() as db:
        exps = [e[0] for e in db.execute("SELECT DISTINCT experiment FROM comparisons WHERE experiment<>'' ORDER BY 1")]
    for x in rows:
        x["final"] = final(x)
        if x["fair"] is None:
            x["fair"] = x["same_model"]
    return {
        "answer": answer(rows),
        "experiments": exps, "experiment": experiment, "rows": rows,
        "summary": {
            "pairs": len(rows), "fair_pairs": len(fair), "reviewed": sum(1 for x in rows if x["reviewed_verdict"]),
            "auto": count(rows, lambda x: x["auto_verdict"]), "final": count(rows, final), "fair_final": count(fair, final),
            "agree": sum(1 for x in rows if x["reviewed_verdict"] and x["reviewed_verdict"] == x["auto_verdict"]),
            "tokens_alone": round(sum(a for a, _ in tok) / len(tok)) if tok else None,
            "tokens_report": round(sum(b for _, b in tok) / len(tok)) if tok else None,
            "scroll_errors_alone": sum(1 for x in rows if any(v["scroll_errors"] for v in x["browser_alone"].values())),
            "scroll_errors_report": sum(1 for x in rows if any(v["scroll_errors"] for v in x["browser_report"].values())),
        },
        "failures": [{"side": s, "kind": k, "count": n} for (s, k), n in cats.most_common()],
        "lessons": lessons,
    }
