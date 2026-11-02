"""Run a set of tasks under each condition and record whether the report helped.

  python3 -m fusionedges experiment experiments/next-to-react-navbar.json
  python3 -m fusionedges experiment FILE --conditions prompt,report,history --repeats 3
  python3 -m fusionedges results                       (verdict table from output/runs.db)

Conditions: prompt (A, agent alone), report (D, with the compatibility report),
history (E, report + past cases from other tasks). Each repeat runs every condition back to back on the same
pinned commits, in a shuffled order, and pairs each treated run with that repeat's agent-alone run.
"""
from __future__ import annotations

import json
import random
import re
import time
from pathlib import Path

from . import compare
from .pipeline import run
from .prompts import default_prompt

NAMES = {"prompt": "A agent alone", "report": "D with report", "history": "E report + history",
         "agent": "A2 multi-step agent alone", "agent+report": "D2 multi-step agent + report",
         "port": "M deterministic port, no AI", "port-ai": "MA deterministic port + AI only on failure"}
# condition -> (pipeline mode, edit mode). Each treated condition is compared with the baseline of the same harness.
CONDITIONS = {"prompt": ("prompt", "oneshot"), "report": ("report", "oneshot"), "history": ("history", "oneshot"),
              "agent": ("prompt", "agent"), "agent+report": ("report", "agent"),
              "port": ("port", "oneshot"), "port-ai": ("port-ai", "oneshot")}
PAIRS = [("prompt", "report", ""), ("prompt", "history", ""), ("agent", "agent+report", " [multi-step]"),
         ("prompt", "port", " [deterministic]"), ("prompt", "port-ai", " [deterministic + AI]")]


def run_experiment(spec: dict, log=print, conditions=None, repeats=None, seed=None, build=True) -> list:
    """`spec["env"]` (e.g. pin one model: FUSIONEDGES_PROVIDERS=gemini, FUSIONEDGES_GEMINI_MODEL=gemini-3.6-flash) is set
    for the whole experiment and restored afterwards."""
    import os
    saved = {k: os.environ.get(k) for k in (spec.get("env") or {})}
    os.environ.update({k: str(v) for k, v in (spec.get("env") or {}).items()})
    try:
        return _run(spec, log, conditions, repeats, seed, build)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class QuotaReached(RuntimeError):
    """The AI provider's daily quota is used up: stop the experiment; running the same command later resumes it."""


QUOTA = re.compile(r"quota|rate limit|429|resource.?exhausted|too many requests", re.I)


def _progress_path(name: str) -> Path:
    from . import paths
    return paths.HOME / "experiments" / "progress" / f"{re.sub(r'[^A-Za-z0-9._-]+', '_', name)}.json"


def _run(spec: dict, log, conditions, repeats, seed, build) -> list:
    """Runs every (repeat, task, condition) once. Finished runs are recorded in experiments/progress/<name>.json,
    so running the same command again skips them: a free-tier quota can spread an experiment over several days."""
    from . import store
    conditions = conditions or spec.get("conditions") or ["prompt", "report"]
    repeats = repeats or spec.get("repeats") or 1
    name = spec.get("name") or "experiment"
    prog_file = _progress_path(name)
    progress = json.loads(prog_file.read_text()) if prog_file.exists() else {}
    runs_in = lambda: sum(1 for k in progress if not k.startswith("pair|"))   # noqa: E731
    done_before = runs_in()
    total = repeats * len(spec["tasks"]) * len(conditions)
    log(f"{name}: {total} runs planned, {done_before} already done" + (" (resuming)" if done_before else ""))

    def save_progress():
        prog_file.parent.mkdir(parents=True, exist_ok=True)
        prog_file.write_text(json.dumps(progress, indent=1, sort_keys=True))

    results, screening = [], []
    stop = None
    for rep in range(repeats):
        for t_i, task in enumerate(spec["tasks"], 1):
            comp = task.get("component", "navbar")
            order = list(conditions)
            # randomized but reproducible run order (proposal, "Runs and controls"): the same on every resume
            random.Random(f"{seed if seed is not None else spec.get('seed', 7)}-{rep}-{t_i}").shuffle(order)
            recs = {}
            for cond in order:
                key = f"r{rep + 1}|t{t_i}|{cond}"
                if key in progress:
                    rec = store.load(progress[key])
                    if rec:
                        recs[cond] = rec
                        continue
                if stop:
                    continue
                log(f"\n=== {name} · task {t_i}/{len(spec['tasks'])} · repeat {rep + 1}/{repeats} · {NAMES.get(cond, cond)} "
                    f"· {runs_in() + 1}/{total} ===")
                log(f"    {task['source']}  <-  {task['donor']}  ({comp})")
                mode, edit = CONDITIONS.get(cond, (cond, "oneshot"))
                params = {"mode": mode, "edit": edit, "source": task["source"], "donor": task["donor"], "add_component": True,
                          "component": comp, "prompt": task.get("prompt") or default_prompt(False, False, comp),
                          "build": build, "auto_compare": False, "experiment": name}
                try:
                    rec = run(params, log=lambda m: log("    " + m),
                              run_id=f"{time.strftime('%Y%m%d-%H%M%S')}-{cond.replace('+', '-')}")
                except Exception as e:
                    log(f"    -> stopped: {e}")
                    if QUOTA.search(str(e)):
                        stop = QuotaReached(str(e)[:300])
                    time.sleep(1)
                    continue
                agent_err = (rec.get("agent") or {}).get("error") or ""
                if QUOTA.search(agent_err):       # the AI call itself ran out of quota: not a real result, redo it later
                    log(f"    -> quota reached during this run; it will be redone when you resume")
                    stop = QuotaReached(agent_err[:300])
                    continue
                recs[cond] = rec
                progress[key] = rec["id"]
                save_progress()
                c = rec.get("checks") or {}
                log(f"    -> {c.get('outcome')} {c.get('score')}")
                time.sleep(1)     # run ids are per-second
            for base, treated, tag in PAIRS:
                if base in recs and treated in recs:
                    from . import compare as _c
                    cid = f"{recs[base]['id']}__{recs[treated]['id']}"
                    c = _c.get(cid) or _c.record(recs[base]["id"], recs[treated]["id"], experiment=name + tag)
                    if f"pair|{cid}" not in progress:
                        log(f"\n*** {NAMES[treated]} vs {NAMES[base]}: {c['verdict'].upper()}\n    {c['note']}")
                        progress[f"pair|{cid}"] = c["verdict"]
                        save_progress()
                    results.append(c)
            if recs:
                screening.append({"task": f"{task['source'].split('/')[-1]} <- {task['donor'].split('/')[-1]}",
                                  "repeat": rep + 1, "outcomes": {c: (r.get("checks") or {}).get("outcome") for c, r in recs.items()},
                                  "models": {c: r.get("models") for c, r in recs.items()}})
    runs_done = runs_in()
    if stop:
        log(f"\nQUOTA REACHED after {runs_done - done_before} new run(s) today ({runs_done}/{total} done). "
            f"Run the same command again after the quota resets (midnight Pacific) to continue.")
    else:
        log(f"\nAll {total} runs done.")
    if screening and set(conditions) <= {"prompt", "agent"}:
        log("\n" + screen_table(screening))
    return results


def screen_table(rows: list) -> str:
    """Proposal's screening rule: drop a task the baseline always passes or never passes (no headroom either way)."""
    from collections import defaultdict
    by = defaultdict(list)
    for r in rows:
        for c, o in r["outcomes"].items():
            by[(r["task"], c)].append(o or "error")
    out = ["SCREENING (baseline only)", f"{'task':<60} {'baseline':<9} outcomes -> decision"]
    for (task, c), outs in by.items():
        passed = [o.startswith("passed") for o in outs]
        decision = "DROP: always passes" if all(passed) else "DROP: never works" if all(o in ("failed", "error") for o in outs) else "KEEP"
        out.append(f"{task[:60]:<60} {c:<9} {', '.join(outs)} -> {decision}")
    return "\n".join(out)


def table(rows: list) -> str:
    if not rows:
        return "No comparisons yet."
    out = [f"{'when':<16} {'cond':<4} {'task':<58} {'A':>5} {'treated':>7}  verdict"]
    for r in sorted(rows, key=lambda r: r["created"]):
        task = f"{r['source'].split('/')[-1]} <- {r['donor'].split('/')[-1]} ({r['component']})"
        out.append(f"{r['created'][5:16].replace('T', ' '):<16} {r.get('condition') or 'D':<4} {task[:58]:<58} "
                   f"{r['prompt_score'] or '-':>5} {r['report_score'] or '-':>7}  {r['verdict']}")
    s = compare.summary(rows)
    out.append(f"\n{s['total']} comparisons: {s['improved']} improved, {s['no change']} no change, "
               f"{s['worse']} worse, {s['inconclusive']} inconclusive")
    return "\n".join(out)


def load(path: str) -> dict:
    return json.loads(Path(path).read_text())
