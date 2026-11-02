"""One row per run with everything needed to compare FusionEdges with a plain AI agent (table run_metrics).

Quantitative: tokens (report step vs coding step), API calls, time (total, per stage, waiting on the AI), repairs,
lines changed, build, checks, browser, verified success. Qualitative: the failure kinds seen in that run, in plain
words, and what the deterministic port resolved or left. Filled automatically at the end of every run; older runs
are added with `python3 -m fusionedges metrics --backfill`.
"""
from __future__ import annotations

import json
from collections import Counter
from statistics import median

from . import store

# condition letter, short name, and the sentence the Analysis tab uses
CONDITIONS = {
    "A": ("Agent alone", "One AI coding call (+ up to 2 repairs). What any AI-wrapper app does."),
    "D": ("Agent + report", "Same, plus an AI-written compatibility report in the prompt."),
    "E": ("Report + history", "Report that also sees lessons from earlier runs."),
    "M": ("FusionEdges port", "Rule-based port, no AI at all."),
    "M1": ("FusionEdges port v1", "First version of the rule-based port (superseded on 1 Nov); kept for the record."),
    "M2": ("FusionEdges port v2", "Second version (superseded after its browser check on 1 Nov); kept for the record."),
    "MA": ("FusionEdges port + AI", "Rule-based port; the AI is called only if the result does not work."),
    "A2": ("Multi-step agent", "Agent with tools, many steps."),
    "D2": ("Multi-step agent + report", "Same, plus the report."),
}
MODE_TO_COND = {("prompt", "oneshot"): "A", ("report", "oneshot"): "D", ("history", "oneshot"): "E",
                ("port", "oneshot"): "M", ("port-ai", "oneshot"): "MA",
                ("prompt", "agent"): "A2", ("report", "agent"): "D2"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS run_metrics (
  run_id TEXT PRIMARY KEY, created TEXT, experiment TEXT, condition TEXT, task TEXT, component TEXT,
  source TEXT, donor TEXT, source_commit TEXT, donor_commit TEXT, models TEXT, single_model INTEGER,
  api_calls INTEGER, tokens_in INTEGER, tokens_out INTEGER, tokens_report INTEGER, tokens_coding INTEGER,
  tokens_total INTEGER, cost_usd REAL,
  seconds_total REAL, seconds_ai REAL, seconds_scan REAL, seconds_report REAL, seconds_port REAL,
  seconds_agent REAL, seconds_check REAL,
  repairs INTEGER, build_repairs INTEGER, browser_repairs INTEGER,
  files_changed INTEGER, lines_added INTEGER, lines_removed INTEGER,
  built INTEGER, checks_passed INTEGER, checks_total INTEGER, browser_pass INTEGER, browser_source TEXT,
  host_regression INTEGER, verified_success INTEGER, ai_error INTEGER,
  port_resolved INTEGER, port_left INTEGER, failures TEXT, observation TEXT, data TEXT
);
"""

# check key -> plain-language failure kind
CHECK_FAILURE = {"build": "build fails", "mounted": "new component not on the page", "single": "two navbars",
                 "deps": "package missing", "assets": "image missing", "donor_code": "donor component not used",
                 "host_scripts": "site's own script breaks", "changed": "nothing changed",
                 "router": "wrong router", "use_client": "missing 'use client'", "browser_api": "browser API on server",
                 "css_import": "CSS import in the wrong place"}


def connect():
    db = store.connect()
    db.executescript(SCHEMA)
    return db


def _browser(rec: dict):
    """(pass, source, problems): from the run itself, else from the browser_checks table (older runs)."""
    b = rec.get("browser") or {}
    if b and not b.get("skipped") and b.get("pass") is not None:
        return int(bool(b["pass"])), "pipeline", list(b.get("problems") or [])
    try:
        with store.connect() as db:
            rows = db.execute("SELECT viewport, link_count, scroll_errors, load_errors, inline_hidden, note FROM browser_checks "
                              "WHERE run_id=? ORDER BY checked_at", (rec["id"],)).fetchall()
    except Exception:  # noqa: BLE001 - no table yet
        rows = []
    if not rows:
        return None, "", []
    probs = []
    for vp, links, scroll, load, hidden, note in rows:
        if not links:
            probs.append(f"{vp}: no navbar links visible")
        if scroll and json.loads(scroll or "[]"):
            probs.append(f"{vp}: page errors on scroll")
        if hidden and json.loads(hidden or "[]"):
            probs.append(f"{vp}: links hidden by inline style")
    return int(not probs), "browser_checks table", probs


def _browser_kind(p: str) -> str:
    """Plain name for a browser-check problem (the check's own sentence stays in data.browser_problems)."""
    p = p.lower()
    if "blank" in p:
        return "page blank in the browser"
    if "error" in p:
        return "page errors in the browser"
    if "unstyled" in p or "tall" in p:
        return "navbar unstyled"
    if "no navbar is visible" in p or "no navbar" in p:
        return "navbar not on the page"
    if "hidden" in p or "only" in p and "link" in p:
        return "navbar links hidden"
    if "no visible link or menu button" in p:
        return "no menu on a phone"
    if "sideways" in p or "overflow" in p:
        return "page scrolls sideways on a phone"
    return "other browser problem"


def condition_of(rec: dict) -> str:
    c = MODE_TO_COND.get((rec.get("mode"), (rec.get("options") or {}).get("edit") or "oneshot"), rec.get("mode", "?"))
    if c in ("M", "MA"):
        from .port import VERSION
        v = (rec.get("port") or {}).get("version", 1)
        if v < VERSION:
            return f"M{v}" if c == "M" else f"MA{v}"
    return c


def from_run(rec: dict, experiment: str = "") -> dict:
    a, ru = rec.get("agent") or {}, rec.get("report_usage") or {}
    checks = (rec.get("checks") or {}).get("checks") or {}
    applicable = [v for v in checks.values() if v.get("pass") is not None]
    passed = sum(1 for v in applicable if v["pass"])
    built = (rec.get("build") or {}).get("ok")
    bpass, bsrc, bprobs = _browser(rec)
    t_rep = (ru.get("input_tokens") or 0) + (ru.get("output_tokens") or 0)
    t_cod = (a.get("input_tokens") or 0) + (a.get("output_tokens") or 0)
    models = sorted(set(rec.get("models") or []))
    # the "browser" check is summarised by the problems themselves, named below
    fails = [CHECK_FAILURE.get(k, k) for k, v in checks.items() if v.get("pass") is False and k != "browser"]
    fails += [_browser_kind(p) for p in bprobs]
    if a.get("error"):
        fails.append("AI call failed")
    all_checks = bool(applicable) and passed == len(applicable)
    success = int(bool(built) and all_checks and bpass != 0) if built is not None else 0
    # verified = also checked in a browser; without a browser result success stays "unverified" (browser_pass NULL)
    host_reg = int(bool(built) and (checks.get("host_scripts", {}).get("pass") is False or bpass == 0))
    t = rec.get("timings") or {}
    port = rec.get("port") or (a.get("port") or {})
    ch = rec.get("changes") or {}
    row = {
        "run_id": rec["id"], "created": rec.get("created"), "experiment": experiment, "condition": condition_of(rec),
        "task": f"{(rec.get('source') or {}).get('label') or (rec.get('source') or {}).get('spec', '')} ← "
                f"{(rec.get('donor') or {}).get('label') or (rec.get('donor') or {}).get('spec', '')}",
        "component": (rec.get("options") or {}).get("component") or "",
        "source": (rec.get("source") or {}).get("label") or (rec.get("source") or {}).get("spec"),
        "donor": (rec.get("donor") or {}).get("label") or (rec.get("donor") or {}).get("spec"),
        "source_commit": (rec.get("source") or {}).get("commit") or "", "donor_commit": (rec.get("donor") or {}).get("commit") or "",
        "models": ", ".join(models), "single_model": int(len(models) <= 1),
        "api_calls": (a.get("api_calls") or 0) + (ru.get("api_calls") or 0),
        "tokens_in": (a.get("input_tokens") or 0) + (ru.get("input_tokens") or 0),
        "tokens_out": (a.get("output_tokens") or 0) + (ru.get("output_tokens") or 0),
        "tokens_report": t_rep, "tokens_coding": t_cod, "tokens_total": t_rep + t_cod,
        "cost_usd": round((a.get("est_cost_usd") or 0) + (ru.get("est_cost_usd") or 0), 5),
        "seconds_total": rec.get("seconds"), "seconds_ai": round((a.get("ai_seconds") or 0) + (ru.get("ai_seconds") or 0), 1) or None,
        "seconds_scan": t.get("scan"), "seconds_report": t.get("report"), "seconds_port": t.get("port"),
        "seconds_agent": t.get("agent"), "seconds_check": t.get("check"),
        "repairs": max(0, (a.get("attempts") or 1) - 1) if a.get("api_calls") else 0,
        "build_repairs": a.get("build_failures") or 0, "browser_repairs": a.get("browser_repairs") or 0,
        "files_changed": ch.get("files"), "lines_added": ch.get("added"), "lines_removed": ch.get("removed"),
        "built": None if built is None else int(bool(built)), "checks_passed": passed, "checks_total": len(applicable),
        "browser_pass": bpass, "browser_source": bsrc, "host_regression": host_reg, "verified_success": success,
        "ai_error": int(bool(a.get("error"))),
        "port_resolved": len(port.get("resolved") or []) if port else None,
        "port_left": len(port.get("unresolved") or []) if port else None,
        "failures": json.dumps(sorted(set(fails))), "observation": "",
        "data": json.dumps({"browser_problems": bprobs, "failed_checks": {k: v.get("value") for k, v in checks.items() if v.get("pass") is False},
                            "port_left": [u.get("kind") for u in (port.get("unresolved") or [])] if port else []}),
    }
    row["observation"] = observe(row, rec)
    return row


def observe(m: dict, rec: dict) -> str:
    """One plain sentence about this run, for the Analysis tab."""
    name = CONDITIONS.get(m["condition"], (m["condition"],))[0]
    f = json.loads(m["failures"] or "[]")
    cost = f"{m['tokens_total']:,} tokens" if m["tokens_total"] else "no AI tokens"
    took = f"{round(m['seconds_total'] or 0)} s"
    if m["ai_error"]:
        return f"{name}: the AI call failed ({cost}, {took}); not a real result."
    if m["verified_success"]:
        how = " after " + (f"{m['repairs']} repair{'s' if m['repairs'] != 1 else ''}" if m["repairs"] else "") if m["repairs"] else " first time"
        chk = " and checked in a browser" if m["browser_pass"] is not None else " (not yet checked in a browser)"
        return f"{name}: works{how}{chk}. {cost}, {took}."
    return f"{name}: does not fully work: {', '.join(f) or 'see checks'}. {cost}, {took}."


def save(rec: dict, experiment: str = "") -> dict | None:
    if not rec.get("id") or rec.get("status") != "done" or not (rec.get("options") or {}).get("component"):
        return None
    row = from_run(rec, experiment)
    with connect() as db:
        if not experiment:   # keep an experiment name set earlier
            old = db.execute("SELECT experiment FROM run_metrics WHERE run_id=?", (row["run_id"],)).fetchone()
            row["experiment"] = old[0] if old and old[0] else ""
        cols = list(row)
        db.execute(f"INSERT OR REPLACE INTO run_metrics ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                   [row[c] for c in cols])
    return row


def tag_experiment(run_id: str, experiment: str):
    with connect() as db:
        db.execute("UPDATE run_metrics SET experiment=? WHERE run_id=?", (experiment, run_id))


def backfill() -> int:
    from . import paths
    n = 0
    with store.connect() as db:
        ids = [r[0] for r in db.execute("SELECT id FROM runs ORDER BY created")]
        exp = dict(db.execute("SELECT prompt_run, experiment FROM comparisons WHERE experiment<>''").fetchall())
        exp.update(dict(db.execute("SELECT report_run, experiment FROM comparisons WHERE experiment<>''").fetchall()))
    for f in sorted((paths.HOME / "experiments" / "progress").glob("*.json")):   # the experiment runner's own record
        try:
            prog = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        for k, v in prog.items():
            if not k.startswith("pair|") and isinstance(v, str):
                exp.setdefault(v, f.stem)
    for rid in ids:
        rec = store.load(rid)
        if rec and save(rec, (exp.get(rid) or "").split(" [")[0]):
            n += 1
    return n


def rows(experiment: str = "") -> list:
    with connect() as db:
        db.row_factory = __import__("sqlite3").Row
        q = "SELECT * FROM run_metrics" + (" WHERE experiment=?" if experiment else "") + " ORDER BY created"
        out = [dict(r) for r in db.execute(q, (experiment,) if experiment else ())]
    for r in out:
        r["failures"] = json.loads(r["failures"] or "[]")
    return out


def _med(xs):
    xs = [x for x in xs if x is not None]
    return round(median(xs), 1) if xs else None


def by_condition(rs: list) -> list:
    """Per condition: success rate, tokens, time, tokens per working result, top failures. Only real results
    (the AI call did not fail)."""
    out = []
    for cond in [c for c in CONDITIONS if any(r["condition"] == c for r in rs)]:
        xs = [r for r in rs if r["condition"] == cond and not r["ai_error"]]
        if not xs:
            continue
        ok = [r for r in xs if r["verified_success"]]
        browsed = [r for r in xs if r["browser_pass"] is not None]
        tok = sum(r["tokens_total"] or 0 for r in xs)
        fails = Counter(f for r in xs for f in r["failures"])
        out.append({"condition": cond, "name": CONDITIONS[cond][0], "what": CONDITIONS[cond][1], "runs": len(xs),
                    "tasks": len({r["task"] for r in xs}), "works": len(ok), "browser_checked": len(browsed),
                    "success_rate": round(len(ok) / len(xs), 3), "host_regressions": sum(r["host_regression"] for r in xs),
                    "median_tokens": _med([r["tokens_total"] for r in xs]), "total_tokens": tok,
                    "median_seconds": _med([r["seconds_total"] for r in xs]),
                    "median_api_calls": _med([r["api_calls"] for r in xs]),
                    "median_repairs": _med([r["repairs"] for r in xs]),
                    "tokens_per_working": round(tok / len(ok)) if ok else None,
                    "top_failures": [{"kind": k, "count": n} for k, n in fails.most_common(6)]})
    return out


def task_matrix(rs: list) -> list:
    """Task x condition: how many runs worked, of how many, plus median tokens/time."""
    conds = [c for c in CONDITIONS if any(r["condition"] == c for r in rs)]
    # tasks run by the most approaches first (those are the comparisons), then by name
    tasks = sorted({r["task"] for r in rs}, key=lambda t: (-len({r["condition"] for r in rs if r["task"] == t}), t.lower()))
    out = []
    for t in tasks:
        cells = {}
        for c in conds:
            xs = [r for r in rs if r["task"] == t and r["condition"] == c and not r["ai_error"]]
            if xs:
                cells[c] = {"works": sum(r["verified_success"] for r in xs), "runs": len(xs),
                            "tokens": _med([r["tokens_total"] for r in xs]), "seconds": _med([r["seconds_total"] for r in xs]),
                            "failures": sorted({f for r in xs for f in r["failures"]}),
                            "run_ids": [r["run_id"] for r in xs]}
        out.append({"task": t, "cells": cells})
    return out


def sign_test(better: int, worse: int) -> float:
    """Two-sided exact sign test over the tasks that differ (ties dropped)."""
    from math import comb
    n, k = better + worse, max(better, worse)
    if n == 0:
        return 1.0
    return min(1.0, 2 * sum(comb(n, i) for i in range(k, n + 1)) / 2 ** n)


def paired(rs: list, a: str, b: str) -> dict:
    """Only the tasks both approaches ran: success, tokens and time on the same tasks (a fair comparison)."""
    ok = lambda r: not r["ai_error"]   # noqa: E731
    ta = {r["task"] for r in rs if r["condition"] == a and ok(r)}
    tb = {r["task"] for r in rs if r["condition"] == b and ok(r)}
    tasks = sorted(ta & tb)
    out = {"tasks": len(tasks), "a": a, "b": b, "better": 0, "worse": 0, "tie": 0}
    for side in (a, b):
        xs = [r for r in rs if r["condition"] == side and r["task"] in tasks and ok(r)]
        # every task counts once, however many times it was run (a task re-run 9 times must not weigh 9x)
        rates = []
        for t in tasks:
            ys = [r for r in xs if r["task"] == t]
            rates.append(sum(r["verified_success"] for r in ys) / len(ys))
        out[side] = {"runs": len(xs), "works": sum(r["verified_success"] for r in xs),
                     "task_rate": round(sum(rates) / len(rates), 3) if rates else 0,
                     "median_tokens": _med([r["tokens_total"] for r in xs]), "median_seconds": _med([r["seconds_total"] for r in xs])}
    for t in tasks:   # per task: success rate of b vs a (the sign test unit)
        rate = lambda c: (lambda xs: sum(r["verified_success"] for r in xs) / len(xs))(   # noqa: E731
            [r for r in rs if r["condition"] == c and r["task"] == t and ok(r)])
        d = rate(b) - rate(a)
        out["better" if d > 0 else "worse" if d < 0 else "tie"] += 1
    return out


def headline(conds: list, rs: list | None = None) -> dict:
    """Plain answer to 'does FusionEdges beat a plain AI agent?', on the tasks both ran, or why not yet."""
    by = {c["condition"]: c for c in conds}
    best = next((k for k in ("MA", "M", "D") if k in by), None)
    if "A" not in by or not best:
        return {"answer": "not yet", "text": "No comparison yet: run the same tasks as 'Agent alone' and with FusionEdges."}
    p = paired(rs or [], "A", best)
    if not p["tasks"]:
        return {"answer": "not yet", "text": f"{CONDITIONS[best][0]} and the agent alone have not run the same task yet."}
    A, B = p["A"], p[best]
    tok = ""
    if A["median_tokens"]:
        cut = 1 - (B["median_tokens"] or 0) / A["median_tokens"]
        tok = f", with {round(cut * 100)}% fewer tokens" if cut > 0 else f", but with {round(-cut * 100)}% more tokens"
    t = ""
    if A["median_seconds"] and B["median_seconds"]:
        tc = 1 - B["median_seconds"] / A["median_seconds"]
        t = f" and {round(abs(tc) * 100)}% {'less' if tc > 0 else 'more'} time"
    ra, rb = A["task_rate"], B["task_rate"]
    verb = "works more often than" if rb > ra else "works as often as" if rb == ra else "works less often than"
    small = p["tasks"] < 8
    good = rb >= ra and tok.startswith(", with")
    pct = lambda x: f"{round(x * 100)}%"   # noqa: E731
    pv = sign_test(p["better"], p["worse"])
    p["sign_test_p"] = round(pv, 3)
    # "Yes" only when the per-task difference is also unlikely to be chance; cheaper-and-not-worse is "promising"
    answer = "not yet" if small else ("yes" if good and pv < 0.05 and rb > ra else "promising" if good else "mixed")
    return {"answer": answer, "vs": best, "paired": p,
            "text": f"On the {p['tasks']} task{'s' if p['tasks'] != 1 else ''} both ran, {CONDITIONS[best][0]} {verb} the agent alone: "
                    f"{pct(rb)} vs {pct(ra)} average success per task (each task counts once){tok}{t}. "
                    f"Per task: better on {p['better']}, worse on {p['worse']}, same on {p['tie']}"
                    f" (sign test p = {pv:.2f}{': could still be chance' if pv >= 0.05 else ''})."
                    + (" Too few tasks to call it yet (need 8+)." if small else "")}
