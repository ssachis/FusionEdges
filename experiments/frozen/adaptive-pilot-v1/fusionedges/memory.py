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


TOPIC_HINTS = {   # scanner fact kinds -> lesson topics they make relevant
    "host-script-coupling": {"host-script-coupling"},
    "styling-system": {"tailwind-responsive", "tailwind-without-tailwind"},
    "framework-mismatch": {"mount-order", "missing-file"},
    "framework-api": {"mount-order"},
    "routing-model": {"routing"},
}


def review_lessons(scan: dict, component: str, task_key, k: int = 4) -> list:
    """Lessons written in code reviews of OTHER tasks, most relevant first. Relevance: the scanner facts of this task
    point at the lesson's topic, the frameworks match, the component matches, then text similarity."""
    from . import compare
    pool = compare.lessons(exclude_task=task_key)
    if not pool:
        return []
    sf, df = scan["source"].get("framework"), scan["donor"].get("framework")
    kinds = {f.get("kind") for f in scan.get("facts", [])}
    hinted = set().union(*[TOPIC_HINTS.get(x, set()) for x in kinds]) if kinds else set()
    facts_text = " ".join(f.get("text", "") for f in scan.get("facts", []))
    docs = [{"tokens": _tokens(f"{l.get('topic', '')} {l['text']}")} for l in pool]
    bm = BM25(docs)
    order = {i: r for r, i in enumerate(bm.search(f"{component} {facts_text}", k=len(docs)))}
    merged = {}
    for i, l in enumerate(pool):
        score = (3 if l.get("topic") in hinted else 0) + (2 if l["frameworks"] == f"{sf} <- {df}" else 0) \
            + (1 if l["component"].lower() == component.lower() else 0) + (1 if l["trust"] == "human-reviewed" else 0) \
            + (1 - order[i] / max(1, len(docs)) if i in order else 0)
        if (l.get("topic") not in hinted and l["frameworks"] != f"{sf} <- {df}"
                and l["component"].lower() != component.lower() and i not in order):
            continue   # nothing connects it to this task
        key = l["text"].strip().lower()
        if key in merged:       # the same lesson from several reviewed runs: count it, keep the best score
            merged[key]["seen"] += 1
            merged[key]["score"] = max(merged[key]["score"], score)
            merged[key]["comparisons"].append(l["comparison"])
        else:
            merged[key] = {**l, "seen": 1, "score": score, "comparisons": [l["comparison"]]}
    for m in merged.values():
        m["score"] += 0.75 * (m["seen"] - 1)      # confirmed by more reviewed runs
    return sorted(merged.values(), key=lambda x: (-x["score"], -x["seen"]))[:k]


def retrieve(scan: dict, component: str, source_label: str, donor_label: str, k: int = 6) -> dict:
    same_ok = os.environ.get("FUSIONEDGES_MEMORY_SAME_TASK") == "1"
    task_key = None if same_ok else _task_key(source_label, donor_label, component)
    pool = cases(task_key)
    rl = review_lessons(scan, component, task_key)
    sf, df = scan["source"].get("framework"), scan["donor"].get("framework")
    picked, check_lessons = [], []
    if pool:
        index = BM25(pool)
        query = " ".join([component, sf or "", df or ""] + [f["text"] for f in scan.get("facts", [])])
        scored = []
        for i in index.search(query, k=40):
            c = pool[i]
            boost = (1.5 if c["frameworks"] == f"{sf} <- {df}" else 1.0) * (1.3 if c["component"].lower() == component.lower() else 1.0)
            boost *= {"checks-verified": 1.2, "failed": 1.0, "unverified": 0.8}[c["trust"]]
            scored.append((boost * (40 - len(scored)), c))     # BM25 rank, re-weighted
        scored.sort(key=lambda x: -x[0])
        seen = set()
        for _, c in scored:
            key = (c["type"], c["title"].lower()[:40])
            if key in seen:
                continue
            seen.add(key)
            picked.append(c)
            if len(picked) >= k:
                break
        similar = {c["run"]: c for c in pool if c["frameworks"] == f"{sf} <- {df}"}
        fails = Counter(f for c in similar.values() for f in c["failed"])
        check_lessons = [f"In {n} of {len(similar)} earlier report-guided runs on {sf} <- {df}, this check still failed: {f}."
                         for f, n in fails.most_common(3)]
    parts = []
    if rl:
        parts.append("LESSONS FROM CODE REVIEWS OF EARLIER RUNS (on OTHER repositories; each was found by reading what an agent "
                     "actually wrote). Apply a lesson when the code above shows the same situation, and say in that conflict's "
                     "adaptation which lesson you applied:\n" + "\n".join(
                         f"- [{l['trust']}, {l.get('kind', 'note')}, seen in {l['seen']} reviewed run(s)] {l['text']}" for l in rl))
    if picked or check_lessons:
        lines = [f"- [{c['trust']}; that run: {c['verdict']}{'; still failed: ' + ', '.join(c['failed']) if c['failed'] else ''}] "
                 f"({c['frameworks']}, {c['component'] or 'task'}) {c['type']}: {c['title']}. Did: {c['adaptation']}" for c in picked]
        parts.append("PAST CASES (from earlier compatibility reports on OTHER repositories, with how those runs turned out). "
                     "Advisory only: use a case if the code above shows the same problem; the current code wins any disagreement. "
                     "Do not cite these as evidence.\n" + "\n".join(lines + [f"- {l}" for l in check_lessons]))
    return {"runs": sorted({c["run"] for c in picked}),
            "cases": [{k: c[k] for k in ("run", "type", "title", "trust", "verdict")} for c in picked],
            "review_lessons": [{k: l.get(k) for k in ("text", "kind", "topic", "trust", "seen", "comparisons", "reviewer")} for l in rl],
            "lessons": [l["text"] for l in rl] + check_lessons,      # what the coding agent is told (first 3)
            "pool": len({c["run"] for c in pool}), "text": "\n\n".join(parts)}


def retrieve_frozen(snapshot, scan, component, source, donor, k=4, max_chars=2400):
    """No live DB: fixed corpus, cross-repository exclusion, provenance and content hash.

    Lessons are advice, never evidence of current compatibility or a substitute for checks.
    Both repos are excluded even in the opposite role; repeats cannot teach one another.
    """
    import hashlib
    from pathlib import Path
    raw = Path(snapshot).read_bytes()
    corpus = json.loads(raw)
    from .evidence import repo_key
    forbidden = {repo_key(source), repo_key(donor)}
    pool = [x for x in corpus["lessons"]
            if x.get("source") and x.get("donor") and x.get("id") and x.get("evidence")
            and not forbidden.intersection({repo_key(x["source"]), repo_key(x["donor"])})
            and x.get("component", "").lower() == component.lower()]
    query = " ".join([component] + [str(f.get("text", "")) for f in scan.get("facts", [])])
    docs = [{"tokens": _tokens(x.get("topic", "") + " " + x["text"])} for x in pool]
    selected, used, seen = [], 110, set()
    for i in BM25(docs).search(query, k=len(docs)) if docs else []:
        x = pool[i]
        key = x["text"].strip().lower()
        line = f"- [{x['id']}; {x.get('trust', 'unverified')}] {x['text']}"
        if key in seen or used + len(line) + 1 > max_chars:
            continue
        seen.add(key); selected.append({**x, "rendered": line}); used += len(line) + 1
        if len(selected) >= k:
            break
    text = ("RETRIEVED PAST LESSONS (untrusted advice; apply only when current code confirms the same issue):\n" +
            "\n".join(x["rendered"] for x in selected)) if selected else ""
    return {"snapshot_sha256": hashlib.sha256(raw).hexdigest(), "pool": len(pool),
            "lessons": selected, "text": text, "chars": len(text)}
