"""Benchmark runner: the same tasks under several conditions, one comparable row per run.

  python3 -m fusionedges bench run --tasks benchmark/tasks.jsonl --name pilot --conditions A,D --reps 3 --seed 1
  python3 -m fusionedges bench report --name pilot

A task is one JSON object per line in the manifest:
  {"id": "navbar-01", "source": "<github link or folder>", "donor": "<github link or folder>",
   "component": "navbar", "category": "react->next"}
Every (task, condition, repetition) is one job. Jobs run in a seeded random order (so no condition always runs
first) and each finished job is appended to output/bench/<name>/results.jsonl right away, so an interrupted
benchmark resumes where it stopped and never repeats a finished job.

Success means the run's outcome is "passed": it built and every applicable check passed. `runtime_errors` is
reserved for the browser check (page and console errors) and stays null until that check exists.
"""
from __future__ import annotations

import json
import random
import re
import time
from pathlib import Path

from . import paths

# A condition changes only what the agent is given. Add B (retrieval only) and C (rules only) here when the
# pipeline supports them; the report generator handles any condition ids it finds.
CONDITIONS = {
    "A": {"label": "Agent alone", "mode": "prompt"},
    "D": {"label": "Agent + report", "mode": "report"},
}
REQUIRED = ("id", "source", "donor", "component")


class ManifestError(ValueError):
    pass


def load_tasks(path) -> list[dict]:
    tasks, seen = [], set()
    for n, line in enumerate(Path(path).read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            t = json.loads(line)
        except json.JSONDecodeError as e:
            raise ManifestError(f"{path}:{n}: not valid JSON ({e})") from None
        missing = [k for k in REQUIRED if not str(t.get(k) or "").strip()]
        if missing:
            raise ManifestError(f"{path}:{n}: missing {', '.join(missing)}")
        if not re.fullmatch(r"[A-Za-z0-9._-]+", t["id"]):
            raise ManifestError(f"{path}:{n}: id may only use letters, digits, . _ -")
        if t["id"] in seen:
            raise ManifestError(f"{path}:{n}: duplicate id {t['id']}")
        seen.add(t["id"])
        t.setdefault("category", "")
        tasks.append(t)
    if not tasks:
        raise ManifestError(f"{path}: no tasks")
    return tasks


def plan(tasks: list[dict], conditions: list[str], reps: int, seed: int) -> list[dict]:
    """Every (task, condition, rep) job, in a deterministic shuffled order."""
    unknown = [c for c in conditions if c not in CONDITIONS]
    if unknown:
        raise ManifestError(f"unknown condition {', '.join(unknown)} (known: {', '.join(CONDITIONS)})")
    jobs = [{"task": t, "condition": c, "rep": r} for t in tasks for c in conditions for r in range(1, reps + 1)]
    random.Random(seed).shuffle(jobs)
    return jobs


def job_key(task_id: str, condition: str, rep: int) -> str:
    return f"{task_id}|{condition}|{rep}"


def bench_dir(name: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", name):
        raise ManifestError("name may only use letters, digits, . _ -")
    return paths.OUTPUT / "bench" / name


def read_results(path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def _tokens(u) -> int:
    return ((u or {}).get("input_tokens") or 0) + ((u or {}).get("output_tokens") or 0)


def result_row(job: dict, rec: dict | None, error: str | None, bench: str, run_id: str) -> dict:
    """One flat, comparable row. `rec` is the pipeline's run record (None if the run raised before finishing)."""
    t, rec = job["task"], rec or {}
    checks = rec.get("checks") or {}
    agent, rep = rec.get("agent") or {}, rec.get("report_usage") or {}
    score = checks.get("score") or ""
    passed, _, total = score.partition("/")
    outcome = checks.get("outcome")
    return {
        "bench": bench, "task": t["id"], "category": t.get("category", ""), "condition": job["condition"],
        "rep": job["rep"], "run_id": run_id, "status": rec.get("status") or "error", "outcome": outcome,
        "success": outcome == "passed",
        "checks_passed": int(passed) if passed.isdigit() else None,
        "checks_total": int(total) if total.isdigit() else None,
        "build_ok": (rec.get("build") or {}).get("ok"),
        "runtime_errors": None,
        "agent_tokens": _tokens(agent), "report_tokens": _tokens(rep),
        "tokens_total": _tokens(agent) + _tokens(rep),
        "cost": round((agent.get("est_cost_usd") or 0) + (rep.get("est_cost_usd") or 0), 5),
        "retries": agent.get("retries"), "seconds": rec.get("seconds"),
        "models": rec.get("models") or [], "source_framework": rec.get("source_framework"),
        "donor_framework": rec.get("donor_framework"), "error": error or rec.get("error") or agent.get("error"),
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


def run_bench(name: str, tasks_path, conditions: list[str], reps: int, seed: int, run_fn=None, limit: int | None = None,
              log=print) -> list[dict]:
    """Run every job not already in results.jsonl. `run_fn(params, log=...)` defaults to the real pipeline."""
    if run_fn is None:
        from .pipeline import run as run_fn  # imported late so the report tools need no pipeline
    from .prompts import default_prompt

    tasks = load_tasks(tasks_path)
    out = bench_dir(name)
    out.mkdir(parents=True, exist_ok=True)
    results_path = out / "results.jsonl"
    (out / "config.json").write_text(json.dumps({
        "name": name, "tasks": str(tasks_path), "conditions": conditions, "reps": reps, "seed": seed,
        "providers": __import__("os").environ.get("FUSIONEDGES_PROVIDERS", ""), "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }, indent=1))
    done = {job_key(r["task"], r["condition"], r["rep"]) for r in read_results(results_path)}
    jobs = [j for j in plan(tasks, conditions, reps, seed)
            if job_key(j["task"]["id"], j["condition"], j["rep"]) not in done]
    if limit is not None:
        jobs = jobs[:limit]
    log(f"{len(done)} jobs already done, {len(jobs)} to run")
    for i, j in enumerate(jobs, 1):
        t = j["task"]
        run_id = f"bench-{name}-{t['id']}-{j['condition']}-r{j['rep']}"
        log(f"[{i}/{len(jobs)}] {t['id']}  condition {j['condition']}  rep {j['rep']}")
        params = {"mode": CONDITIONS[j["condition"]]["mode"], "source": t["source"], "donor": t["donor"],
                  "migration": False, "style": False, "add_component": True, "component": t["component"],
                  "edit": "oneshot", "prompt": default_prompt(False, False, t["component"]), "build": True}
        rec, error = None, None
        try:
            rec = run_fn(params, log=lambda *_a, **_k: None, run_id=run_id)
        except Exception as e:  # a failed run is a data point, not a reason to stop the benchmark
            error = f"{type(e).__name__}: {e}"
            log(f"    run raised: {error}")
        row = result_row(j, rec, error, name, run_id)
        with open(results_path, "a") as f:
            f.write(json.dumps(row) + "\n")
        log(f"    {row['outcome'] or row['status']}  {row['tokens_total']} tokens  ${row['cost']}")
    return read_results(results_path)
