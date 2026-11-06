"""Which runs count as evidence: experiments/evidence-ledger.json labels every run with a batch and a status
(exploration, pilot, development, preliminary, study). The ledger is a file, not a database table, so labels are
reviewed in git and nothing has to be written to runs.db to change them."""
from __future__ import annotations

import json
from pathlib import Path

from . import paths

ORDER = ["study", "preliminary", "development", "pilot", "exploration"]


def load() -> dict:
    p = paths.HOME / "experiments" / "evidence-ledger.json"
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {"statuses": {}, "batches": []}


def _as_list(v):
    return v if isinstance(v, list) else [v]


def batch_of(row: dict, ledger: dict | None = None) -> dict:
    """The ledger batch a metrics row belongs to (first match wins); unmatched runs are 'unlabelled'."""
    ledger = ledger or load()
    exp, created = (row.get("experiment") or "").split(" [")[0], row.get("created") or ""
    mode = {"A": "prompt", "D": "report", "E": "history", "AD": "adaptive"}.get(row.get("condition"), row.get("condition"))
    for b in ledger.get("batches", []):
        m = b.get("match", {})
        if "experiment" in m and exp in _as_list(m["experiment"]):
            return b
        if "experiment_prefix" in m and exp and exp.startswith(m["experiment_prefix"]):
            return b
        if not exp and "untagged_before" in m and created < m["untagged_before"]:
            return b
        if not exp and "untagged_after" in m and created >= m["untagged_after"] and \
                (not m.get("modes") or mode in m["modes"] or row.get("condition") in ("A", "AD")):
            return b
    return {"id": "unlabelled", "status": "development", "title": "Unlabelled runs",
            "what": "Not matched by any batch in experiments/evidence-ledger.json; treated as development."}


def summarise(rows: list) -> list:
    """One line per batch, in ledger order, for the Analysis tab and EVIDENCE.md."""
    ledger = load()
    seen = {}
    for r in rows:
        b = batch_of(r, ledger)
        s = seen.setdefault(b["id"], {**{k: b.get(k) for k in ("id", "status", "title", "what")}, "runs": 0,
                                       "tasks": set(), "conditions": set(), "models": set(), "first": r.get("created") or ""})
        s["runs"] += 1
        s["tasks"].add(r.get("task"))
        s["conditions"].add(r.get("condition"))
        if r.get("models"):
            s["models"] |= {m.strip() for m in r["models"].split(",") if m.strip()}
        s["first"] = min(s["first"], r.get("created") or s["first"])
    order = {b["id"]: i for i, b in enumerate(ledger.get("batches", []))}
    out = []
    for s in sorted(seen.values(), key=lambda s: order.get(s["id"], 99)):
        s.update(tasks=len(s["tasks"]), conditions=sorted(s["conditions"]), models=sorted(s["models"]))
        out.append(s)
    for b in ledger.get("batches", []):     # planned batches with no runs yet still show
        if b["id"] not in seen:
            out.append({**{k: b.get(k) for k in ("id", "status", "title", "what")}, "runs": 0, "tasks": 0,
                        "conditions": [], "models": [], "first": ""})
    return out


def markdown(rows: list) -> str:
    """experiments/EVIDENCE.md: every batch, its status, and what it may be used for."""
    led = load()
    lines = ["# Evidence ledger", "",
             "Every FusionEdges run belongs to one batch below. **Only batches marked _study_ may support a result.** "
             "The rest is the record of how FusionEdges was built, and is cited as such. Labels live in "
             "`experiments/evidence-ledger.json`; regenerate this file with `python3 -m fusionedges evidence`.", "",
             "| Status | Meaning |", "|---|---|"]
    lines += [f"| **{k}** | {v} |" for k, v in led.get("statuses", {}).items()]
    lines += ["", "| Batch | Status | Runs | Tasks | Approaches | Model(s) | What it is |", "|---|---|---|---|---|---|---|"]
    for b in summarise(rows):
        lines.append(f"| {b['title']} | {b['status']} | {b['runs']} | {b['tasks']} | {', '.join(b['conditions']) or '-'} | "
                     f"{', '.join(m.replace('gemini:', '') for m in b['models']) or ('no AI' if b['runs'] else '-')} | {b['what']} |")
    lines += ["", "Approach letters: A agent alone · D agent + AI-written report · M FusionEdges port, no AI "
              "(M1, M2 earlier versions) · AD adaptive FusionEdges (rules first, report and AI only as needed).", ""]
    return "\n".join(lines)
