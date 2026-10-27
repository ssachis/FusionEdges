"""Did the compatibility report help? One row per (agent-alone run, agent+report run) on the same task.

Stored in output/runs.db, table `comparisons`. Each row has the verdict (improved / no change / worse /
inconclusive), a plain-language note saying why, the checks each run failed, what each run changed in the
code (from diffs.py), tokens and cost, and room for your own note. The memory retriever (memory.py) reads
these rows so later reports can learn from them.
"""
from __future__ import annotations

import json
import sqlite3

from . import adherence as adh, diffs, store

SCHEMA = """
CREATE TABLE IF NOT EXISTS comparisons (
  id TEXT PRIMARY KEY, created TEXT, experiment TEXT, source TEXT, donor TEXT, component TEXT,
  source_framework TEXT, donor_framework TEXT, prompt_run TEXT, report_run TEXT, condition TEXT,
  prompt_score TEXT, report_score TEXT, prompt_passed INTEGER, report_passed INTEGER, total_checks INTEGER,
  prompt_build INTEGER, report_build INTEGER, prompt_failed TEXT, report_failed TEXT, fixed TEXT, broke TEXT,
  prompt_files INTEGER, report_files INTEGER, prompt_added INTEGER, prompt_removed INTEGER,
  report_added INTEGER, report_removed INTEGER, only_prompt TEXT, only_report TEXT,
  prompt_tokens INTEGER, report_tokens INTEGER, prompt_cost REAL, report_cost REAL,
  memory_used INTEGER, adherence TEXT, prompt_blast TEXT, report_blast TEXT, models TEXT, verdict TEXT, note TEXT, human_note TEXT,
  fair INTEGER, helped TEXT, helped_basis TEXT
);
"""
COLS = ["id", "created", "experiment", "source", "donor", "component", "source_framework", "donor_framework",
        "prompt_run", "report_run", "condition", "prompt_score", "report_score", "prompt_passed", "report_passed", "total_checks",
        "prompt_build", "report_build", "prompt_failed", "report_failed", "fixed", "broke",
        "prompt_files", "report_files", "prompt_added", "prompt_removed", "report_added", "report_removed",
        "only_prompt", "only_report", "prompt_tokens", "report_tokens", "prompt_cost", "report_cost",
        "memory_used", "adherence", "prompt_blast", "report_blast", "models", "verdict", "note", "human_note",
        "fair", "helped", "helped_basis"]
JSON_COLS = {"prompt_failed", "report_failed", "fixed", "broke", "only_prompt", "only_report", "adherence",
             "prompt_blast", "report_blast"}


def connect():
    db = store.connect()
    db.executescript(SCHEMA)
    have = {r[1] for r in db.execute("PRAGMA table_info(comparisons)")}
    for col in ("condition", "adherence", "prompt_blast", "report_blast", "fair", "helped", "helped_basis"):   # older tables
        if col not in have:
            db.execute(f"ALTER TABLE comparisons ADD COLUMN {col} TEXT")
    return db


def _checks(rec):
    c = ((rec.get("checks") or {}).get("checks") or {})
    return {k: v for k, v in c.items() if v.get("pass") is not None}


def _tokens(rec):
    t = 0
    for u in (rec.get("agent"), rec.get("report_usage")):
        u = u or {}
        t += (u.get("input_tokens") or 0) + (u.get("output_tokens") or 0)
    return t


def _cost(rec):
    return round(((rec.get("agent") or {}).get("est_cost_usd") or 0) + ((rec.get("report_usage") or {}).get("est_cost_usd") or 0), 5)


def _build(rec):
    ok = (rec.get("build") or {}).get("ok")
    return None if ok is None else int(bool(ok))


def _plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


HELPED = {"improved": "yes", "worse": "no", "no change": "no", "inconclusive": "unclear"}


def helped(verdict: str, review_verdict: str | None = None) -> tuple:
    """Did the report help? yes / no / unclear, from the review when there is one, else the automatic verdict.
    'no change' counts as no: the report cost tokens and changed nothing."""
    v = review_verdict or verdict
    return HELPED.get(v, "unclear"), ("review" if review_verdict else "automatic")


def judge(p: dict, r: dict, dp: dict, dr: dict) -> dict:
    """Verdict and note for prompt run p vs report run r (dp/dr: their diffs.for_run results)."""
    cp, cr = _checks(p), _checks(r)
    label = {k: v.get("label", k) for k, v in {**cp, **cr}.items()}
    fp = sorted(k for k, v in cp.items() if not v["pass"])
    fr = sorted(k for k, v in cr.items() if not v["pass"])
    fixed, broke = [k for k in fp if k not in fr], [k for k in fr if k not in fp]
    pp, rp = sum(1 for v in cp.values() if v["pass"]), sum(1 for v in cr.values() if v["pass"])
    total = max(len(cp), len(cr))
    bp, br = _build(p), _build(r)
    files_p = {f["path"] for f in dp.get("files", []) if f["kind"] != "generated"}
    files_r = {f["path"] for f in dr.get("files", []) if f["kind"] != "generated"}
    tp, tr = dp.get("totals") or {}, dr.get("totals") or {}

    mp, mr = sorted(set(p.get("models") or [])), sorted(set(r.get("models") or []))
    agent_err = [n for n, x in (("agent alone", p), ("the report run", r)) if (x.get("agent") or {}).get("error")]
    if p.get("status") != "done" or r.get("status") != "done" or not cp or not cr:
        verdict = "inconclusive"
        head = "Inconclusive: " + ("a run stopped with an error" if "error" in (p.get("status"), r.get("status"))
                                   else "a run has no check results") + "."
    elif agent_err:
        verdict = "inconclusive"
        head = f"Inconclusive: the AI call failed for {' and '.join(agent_err)}, so nothing was written to compare."
    elif mp != mr:
        verdict = "inconclusive"
        head = (f"Inconclusive: the runs used different models (agent alone: {', '.join(mp) or '?'}; "
                f"with the report: {', '.join(mr) or '?'}), so the difference may be the model, not the report. "
                f"Checks: {rp}/{total} with the report, {pp}/{total} without.")
    elif rp > pp or (rp == pp and br == 1 and bp == 0):
        verdict = "improved"
        head = f"Improved: with the report {rp}/{total} checks passed, without it {pp}/{total}."
    elif rp < pp or (rp == pp and br == 0 and bp == 1):
        verdict = "worse"
        head = f"Worse: with the report {rp}/{total} checks passed, without it {pp}/{total}."
    else:
        verdict = "no change"
        head = f"No change in the checks: both runs passed {rp}/{total}."
    parts = [head]
    if fixed:
        parts.append("The report run fixed: " + ", ".join(label[k] for k in fixed) + ".")
    if broke:
        parts.append("The report run newly failed: " + ", ".join(label[k] for k in broke) + ".")
    still = [k for k in fr if k in fp]
    if still:
        parts.append("Both failed: " + ", ".join(label[k] for k in still) + ".")
    if tp and tr:
        parts.append(f"Code: agent alone changed {_plural(tp.get('files', 0), 'file')} (+{tp.get('added', 0)} -{tp.get('removed', 0)}), "
                     f"with the report {_plural(tr.get('files', 0), 'file')} (+{tr.get('added', 0)} -{tr.get('removed', 0)}).")
        op, orr = sorted(files_p - files_r), sorted(files_r - files_p)
        if op:
            parts.append("Only agent alone wrote: " + ", ".join(op[:6]) + (" …" if len(op) > 6 else "") + ".")
        if orr:
            parts.append("Only the report run wrote: " + ", ".join(orr[:6]) + (" …" if len(orr) > 6 else "") + ".")
    else:
        op, orr = [], []
    rep = r.get("report") or {}
    ad, bp_files, br_files = None, [], []
    if dp.get("ok") and dr.get("ok"):
        roots = [diffs._relocate((r.get(k) or {}).get("path"), "examples", diffs.paths.EXAMPLES) for k in ("source", "donor")]
        vocab = adh.repo_vocab(*[x for x in roots if x.is_dir()]) if any(x.is_dir() for x in roots) else None
        ad = adh.adherence(rep, r.get("facts") or [], dr, dp, vocab)
        bp_files, br_files = adh.blast_radius(dp, rep), adh.blast_radius(dr, rep)
        if ad["testable"]:
            parts.append(f"Report adherence (read from the diff, automatic): the agent fully acted on {ad['addressed']} of "
                         f"{ad['testable']} testable report items ({ad['partial']} partly); agent alone did {ad['alone_addressed']} "
                         f"of the same {ad['testable']} without being told.")
            missed = [it["title"] for it in ad["items"] if it["report_run"]["status"] == "addressed"
                      and it.get("agent_alone", {}).get("status") in ("not addressed", "partial")]
            if missed:
                parts.append("Handled only with the report: " + "; ".join(missed[:4]) + ".")
        if bp_files or br_files:
            parts.append(f"Existing files changed beyond the mount point: agent alone {len(bp_files)}"
                         f"{' (' + ', '.join(bp_files[:4]) + ')' if bp_files else ''}, with the report {len(br_files)}"
                         f"{' (' + ', '.join(br_files[:4]) + ')' if br_files else ''}.")
    kp, kr = _tokens(p), _tokens(r)
    if kp and kr:
        parts.append(f"Tokens: {kr:,} with the report vs {kp:,} without ({(kr - kp) / kp:+.0%}).")
    mem = len(((r.get("report") or {}).get("memory") or {}).get("runs", []))
    if mem:
        parts.append(f"The report drew on {_plural(mem, 'earlier report run')} (memory).")
    return {"verdict": verdict, "note": " ".join(parts), "fixed": [label[k] for k in fixed], "broke": [label[k] for k in broke],
            "prompt_failed": [label[k] for k in fp], "report_failed": [label[k] for k in fr],
            "prompt_passed": pp, "report_passed": rp, "total_checks": total, "prompt_build": bp, "report_build": br,
            "only_prompt": op, "only_report": orr, "memory_used": mem, "adherence": ad or {},
            "prompt_blast": bp_files, "report_blast": br_files}


def record(prompt_id: str, report_id: str, experiment: str = "", human_note: str | None = None) -> dict:
    p, r = store.load(prompt_id), store.load(report_id)
    if not p or not r:
        raise ValueError("unknown run")
    if p["mode"] != "prompt" and r["mode"] == "prompt":
        p, r = r, p
    if p["mode"] != "prompt" or r["mode"] == "prompt":
        raise ValueError("compare an agent-alone run with a report run")
    dp, dr = diffs.for_run(p), diffs.for_run(r)
    j = judge(p, r, dp, dr)
    cid = f"{p['id']}__{r['id']}"
    old = get(cid)
    tp, tr = dp.get("totals") or {}, dr.get("totals") or {}
    row = {"id": cid, "created": store.now_iso(), "experiment": experiment or (old or {}).get("experiment") or "",
           "source": p["source"].get("label") or p["source"].get("spec"), "donor": p["donor"].get("label") or p["donor"].get("spec"),
           "component": p["options"].get("component") or "", "source_framework": p.get("source_framework"),
           "donor_framework": p.get("donor_framework"), "prompt_run": p["id"], "report_run": r["id"],
           "condition": "E" if r["mode"] == "history" else "D",
           "prompt_score": (p.get("checks") or {}).get("score"), "report_score": (r.get("checks") or {}).get("score"),
           "prompt_files": tp.get("files"), "report_files": tr.get("files"),
           "prompt_added": tp.get("added"), "prompt_removed": tp.get("removed"),
           "report_added": tr.get("added"), "report_removed": tr.get("removed"),
           "prompt_tokens": _tokens(p), "report_tokens": _tokens(r), "prompt_cost": _cost(p), "report_cost": _cost(r),
           "models": ", ".join(sorted(set(p.get("models", [])) | set(r.get("models", [])))),
           "human_note": human_note if human_note is not None else (old or {}).get("human_note") or "", **j}
    mp, mr = sorted(set(p.get("models") or [])), sorted(set(r.get("models") or []))
    row["fair"] = int(bool(mp) and mp == mr)
    last = (reviews_for(cid) or [None])[-1] if old else None
    row["helped"], row["helped_basis"] = helped(j["verdict"], (last or {}).get("verdict"))
    fb = feedback_for(cid) if old else []
    if fb:                                       # a person's answer outranks the review and the automatic verdict
        row["helped"], row["helped_basis"] = fb[-1]["helped"], "user"
    with connect() as db:
        db.execute(f"INSERT OR REPLACE INTO comparisons ({','.join(COLS)}) VALUES ({','.join('?' * len(COLS))})",
                   [json.dumps(row[c]) if c in JSON_COLS else row[c] for c in COLS])
    return get(cid)


def _row(r) -> dict:
    d = dict(r)
    try:
        rv = reviews_for(d["id"])
    except Exception:
        rv = []
    d["review"] = rv[-1] if rv else None
    try:
        fb = feedback_for(d["id"])
    except Exception:
        fb = []
    d["feedback"] = fb[-1] if fb else None
    for c in JSON_COLS:
        try:
            d[c] = json.loads(d[c]) if d.get(c) else []
        except (TypeError, json.JSONDecodeError):
            d[c] = []
    return d


def get(cid: str):
    with connect() as db:
        db.row_factory = sqlite3.Row
        r = db.execute("SELECT * FROM comparisons WHERE id=?", (cid,)).fetchone()
        return _row(r) if r else None


def all_rows(limit: int = 500) -> list:
    with connect() as db:
        db.row_factory = sqlite3.Row
        return [_row(r) for r in db.execute("SELECT * FROM comparisons ORDER BY created DESC LIMIT ?", (limit,))]


def set_note(cid: str, note: str) -> dict:
    with connect() as db:
        db.execute("UPDATE comparisons SET human_note=? WHERE id=?", (note, cid))
    return get(cid)


def summary(rows: list | None = None) -> dict:
    rows = all_rows() if rows is None else rows
    out = {"total": len(rows)}
    for v in ("improved", "no change", "worse", "inconclusive"):
        out[v] = sum(1 for r in rows if r["verdict"] == v)
    return out


def auto_pair(rec: dict, log=lambda m: None):
    """After a run: pair it with the newest unpaired run of the other condition on the same task and commits."""
    if rec.get("status") != "done" or not rec.get("checks"):
        return None
    treated = rec["mode"] != "prompt"
    others = ("prompt",) if treated else ("report", "history")
    with connect() as db:
        if treated:
            if db.execute("SELECT 1 FROM comparisons WHERE report_run=?", (rec["id"],)).fetchone():
                return None
            cond = "E" if rec["mode"] == "history" else "D"   # an agent-alone run can serve one D and one E comparison
            used = {x[0] for x in db.execute("SELECT prompt_run FROM comparisons WHERE condition=?", (cond,))}
        else:
            used = {x[0] for x in db.execute("SELECT report_run FROM comparisons")}
        cands = db.execute(f"""SELECT id, data FROM runs WHERE mode IN ({','.join('?' * len(others))}) AND status='done' AND source=? AND donor=?
                              AND COALESCE(component,'')=? AND COALESCE(source_commit,'')=? AND COALESCE(donor_commit,'')=?
                              ORDER BY created DESC""",
                           (*others, rec["source"].get("label") or rec["source"].get("spec"),
                            rec["donor"].get("label") or rec["donor"].get("spec"), rec["options"].get("component") or "",
                            rec["source"].get("commit") or "", rec["donor"].get("commit") or "")).fetchall()
    for cid, _ in cands:
        if cid not in used and cid != rec["id"]:
            c = record(cid, rec["id"]) if treated else record(rec["id"], cid)
            log(f"Compared with {cid}: {c['verdict']}. {c['note']}")
            return c
    return None


REVIEW_JSON = ("prompt_run_notes", "report_run_notes", "failure_categories", "check_errors", "lessons")


def feedback_for(comparison_id: str) -> list:
    with connect() as db:
        db.row_factory = sqlite3.Row
        return [dict(r) for r in db.execute("SELECT * FROM feedback WHERE comparison_id=? ORDER BY id", (comparison_id,))]


def add_feedback(comparison_id: str, helped_: str, note: str = "", as_lesson: bool = False, user: str = "you") -> dict:
    """A person answers 'did the report help here?'. Kept as history; the newest answer sets comparisons.helped.
    With as_lesson and a note, the note also becomes a human-reviewed lesson that condition E retrieves."""
    if helped_ not in ("yes", "no"):
        raise ValueError("answer yes or no")
    if not get(comparison_id):
        raise ValueError("unknown comparison")
    note = (note or "").strip()
    if as_lesson and note:
        add_review(comparison_id, f"{user} (feedback)", "improved" if helped_ == "yes" else "no change",
                   f"{user.capitalize()} answered {helped_}: {note}",
                   lessons=[{"kind": "do" if helped_ == "yes" else "avoid", "topic": "feedback", "text": note,
                             "evidence": f"{user} answered '{helped_}' after looking at this comparison"}])
    with connect() as db:
        db.execute("INSERT INTO feedback (comparison_id, created, user, helped, note, as_lesson) VALUES (?,?,?,?,?,?)",
                   (comparison_id, store.now_iso(), user, helped_, note, int(bool(as_lesson and note))))
        db.execute("UPDATE comparisons SET helped=?, helped_basis='user' WHERE id=?", (helped_, comparison_id))
    return get(comparison_id)


def add_review(comparison_id: str, reviewer: str, verdict: str, summary: str, **notes) -> dict:
    """A reviewer's verdict after reading the code (can differ from the automatic one). Kept as history, never overwritten."""
    if verdict not in ("improved", "no change", "worse", "inconclusive"):
        raise ValueError("verdict must be improved, no change, worse or inconclusive")
    with connect() as db:
        h, basis = helped("", verdict)
        db.execute("UPDATE comparisons SET helped=?, helped_basis=? WHERE id=? AND COALESCE(helped_basis,'')<>'user'",
                   (h, basis, comparison_id))
        db.execute("INSERT INTO reviews (comparison_id, created, reviewer, verdict, summary, prompt_run_notes, "
                   "report_run_notes, failure_categories, check_errors, lessons) VALUES (?,?,?,?,?,?,?,?,?,?)",
                   (comparison_id, store.now_iso(), reviewer, verdict, summary,
                    *[json.dumps(notes.get(k) or ([] if k in ("failure_categories", "check_errors", "lessons") else ""))
                      for k in REVIEW_JSON]))
    return reviews_for(comparison_id)[-1]


def reviews_for(comparison_id: str) -> list:
    with connect() as db:
        db.row_factory = sqlite3.Row
        out = []
        for r in db.execute("SELECT * FROM reviews WHERE comparison_id=? ORDER BY id", (comparison_id,)):
            d = dict(r)
            for k in REVIEW_JSON:
                try:
                    d[k] = json.loads(d[k]) if d[k] else None
                except json.JSONDecodeError:
                    pass
            out.append(d)
        return out


def import_reviews(path: str) -> int:
    """Load reviews from a JSON file: {"reviewer": ..., "reviews": [{"prompt_run", "report_run", "verdict", "summary", ...}]}.
    Skips a review that is already stored (same comparison, reviewer and summary)."""
    from pathlib import Path
    spec = json.loads(Path(path).read_text())
    n = 0
    for r in spec["reviews"]:
        cid = f"{r['prompt_run']}__{r['report_run']}"
        if not get(cid):
            record(r["prompt_run"], r["report_run"], experiment=spec.get("experiment", ""))
        same = [x for x in reviews_for(cid) if x["summary"] == r["summary"] and x["reviewer"] == spec["reviewer"]]
        if same:
            if r.get("lessons") and same[-1].get("lessons") != r["lessons"]:   # lessons added to an existing review
                with connect() as db:
                    db.execute("UPDATE reviews SET lessons=? WHERE id=?", (json.dumps(r["lessons"]), same[-1]["id"]))
                n += 1
            continue
        add_review(cid, spec["reviewer"], r["verdict"], r["summary"],
                   **{k: r.get(k) for k in REVIEW_JSON})
        n += 1
    return n


def lessons(exclude_task=None) -> list:
    """Every lesson from the latest review of each comparison, with where it came from."""
    out = []
    for c in all_rows():
        if exclude_task and (c["source"], c["donor"], (c["component"] or "").lower()) == exclude_task:
            continue
        hist = reviews_for(c["id"])
        # the latest review speaks for the comparison; lessons people gave as feedback always count
        fb = [r for r in hist if (r.get("reviewer") or "").endswith("(feedback)")]
        other = [r for r in hist if r not in fb]
        use = other[-1:] + fb
        seen = set()
        for rv in use:
            human = not (rv.get("reviewer") or "").lower().startswith("claude")
            for l in rv.get("lessons") or []:
                if isinstance(l, str):
                    l = {"text": l}
                if l.get("text") in seen:
                    continue
                seen.add(l.get("text"))
                out.append({**l, "comparison": c["id"], "task": f"{c['source']} <- {c['donor']} ({c['component']})",
                            "frameworks": f"{c['source_framework']} <- {c['donor_framework']}", "component": c["component"] or "",
                            "review_verdict": rv["verdict"], "reviewer": rv.get("reviewer"),
                            "trust": "human-reviewed" if human else "AI-reviewed"})
    return out
