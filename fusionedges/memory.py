"""Second retriever ("dual RAG"): past compatibility reports and how they turned out.

Retriever 1 (rag.py) finds code in the two repos. This one finds CASES: conflicts from earlier report runs,
each tagged with what happened when the agent used that report (from compare.py: improved / no change /
worse, and which checks still failed). It is the proposal's condition E ("D plus history"):

- advisory only: the report agent is told the current code wins any disagreement;
- leakage guard: cases from the same task (same source, donor and component) are never retrieved, so a
  task cannot learn from its own earlier runs; FUSIONEDGES_MEMORY_SAME_TASK=1 lifts this for demos only;
- trust state per case: 'checks-verified' (report run passed every check), 'failed' (it failed some; kept and
  shown as failed), 'unverified' (no comparison yet);
- what was retrieved is saved in report["memory"], so every condition-E run is auditable.
"""
from __future__ import annotations

import json
import os
import sqlite3
from collections import Counter

from . import store
from .rag import BM25, _tokens


def _task_key(source, donor, component):
    return (source or "", donor or "", (component or "").lower())


def cases(exclude_task=None) -> list:
    """One case per conflict of every finished report run, with the run's outcome attached."""
    from . import compare
    with compare.connect() as db:
        db.row_factory = sqlite3.Row
        runs = db.execute("SELECT id, source, donor, component, source_framework, donor_framework, outcome, data "
                          "FROM runs WHERE mode IN ('report','history') AND status='done'").fetchall()
        comps = {r["report_run"]: dict(r) for r in db.execute("SELECT * FROM comparisons")}
    out = []
    for r in runs:
        if exclude_task and _task_key(r["source"], r["donor"], r["component"]) == exclude_task:
            continue
        try:
            rec = json.loads(r["data"])
        except (TypeError, json.JSONDecodeError):
            continue
        rep = rec.get("report") or {}
        if rep.get("agent_failed"):
            continue
        c = comps.get(r["id"]) or {}
        failed = json.loads(c["report_failed"]) if c.get("report_failed") else [
            v["label"] for v in ((rec.get("checks") or {}).get("checks") or {}).values() if v.get("pass") is False]
        trust = "failed" if failed else ("checks-verified" if (rec.get("checks") or {}).get("outcome", "").startswith("passed") else "unverified")
        base = {"run": r["id"], "source": r["source"], "donor": r["donor"], "component": r["component"] or "",
                "frameworks": f"{r['source_framework']} <- {r['donor_framework']}",
                "verdict": c.get("verdict") or "not compared", "failed": failed, "trust": trust}
        for k in rep.get("conflicts") or []:
            if not isinstance(k, dict) or not k.get("adaptation") or k.get("citation") == "unverified":
                continue
            text = f"{k.get('type', '')} {k.get('title', '')} {k.get('impact', '')} {k.get('adaptation', '')}"
            out.append({**base, "kind": "conflict", "type": k.get("type", ""), "title": k.get("title", ""),
                        "adaptation": k.get("adaptation", ""), "severity": k.get("severity", ""),
                        "tokens": _tokens(f"{text} {r['source_framework']} {r['donor_framework']} {r['component'] or ''}")})
    return out


def retrieve(scan: dict, component: str, source_label: str, donor_label: str, k: int = 6) -> dict:
    same_ok = os.environ.get("FUSIONEDGES_MEMORY_SAME_TASK") == "1"
    pool = cases(None if same_ok else _task_key(source_label, donor_label, component))
    sf, df = scan["source"].get("framework"), scan["donor"].get("framework")
    if not pool:
        return {"runs": [], "cases": [], "lessons": [], "pool": 0, "text": ""}
    index = BM25(pool)
    query = " ".join([component, sf or "", df or ""] + [f["text"] for f in scan.get("facts", [])])
    scored = []
    for i in index.search(query, k=40):
        c = pool[i]
        boost = (1.5 if c["frameworks"] == f"{sf} <- {df}" else 1.0) * (1.3 if c["component"].lower() == component.lower() else 1.0)
        boost *= {"checks-verified": 1.2, "failed": 1.0, "unverified": 0.8}[c["trust"]]
        scored.append((boost * (40 - len(scored)), c))     # BM25 rank, re-weighted
    scored.sort(key=lambda x: -x[0])
    seen, picked = set(), []
    for _, c in scored:
        key = (c["type"], c["title"].lower()[:40])
        if key in seen:
            continue
        seen.add(key)
        picked.append(c)
        if len(picked) >= k:
            break
    # aggregate lessons: checks that report-guided runs on similar frameworks still failed
    similar = {c["run"]: c for c in pool if c["frameworks"] == f"{sf} <- {df}"}
    fails = Counter(f for c in similar.values() for f in c["failed"])
    lessons = [f"In {n} of {len(similar)} earlier report-guided runs on {sf} <- {df}, this check still failed: {f}."
               for f, n in fails.most_common(3)]
    lines = [f"- [{c['trust']}; that run: {c['verdict']}{'; still failed: ' + ', '.join(c['failed']) if c['failed'] else ''}] "
             f"({c['frameworks']}, {c['component'] or 'task'}) {c['type']}: {c['title']}. Did: {c['adaptation']}" for c in picked]
    text = ""
    if picked or lessons:
        text = ("PAST CASES (from earlier compatibility reports on OTHER repositories, with how those runs turned out). "
                "Advisory only: use a case if the code above shows the same problem; the current code wins any disagreement. "
                "Do not cite these as evidence.\n" + "\n".join(lines + [f"- {l}" for l in lessons]))
    return {"runs": sorted({c["run"] for c in picked}), "cases": [{k: c[k] for k in ("run", "type", "title", "trust", "verdict")} for c in picked],
            "lessons": lessons, "pool": len({c["run"] for c in pool}), "text": text}
