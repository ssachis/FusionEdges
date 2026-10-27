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
import time
from pathlib import Path

from . import compare
from .pipeline import run
from .prompts import default_prompt

NAMES = {"prompt": "A agent alone", "report": "D with report", "history": "E report + history"}


def run_experiment(spec: dict, log=print, conditions=None, repeats=None, seed=None, build=True) -> list:
    conditions = conditions or spec.get("conditions") or ["prompt", "report"]
    repeats = repeats or spec.get("repeats") or 1
    name = spec.get("name") or "experiment"
    rnd = random.Random(seed if seed is not None else spec.get("seed", 7))
    results = []
    for rep in range(repeats):
        for t_i, task in enumerate(spec["tasks"], 1):
            comp = task.get("component", "navbar")
            order = list(conditions)
            rnd.shuffle(order)    # randomized run order (proposal, "Runs and controls")
            recs = {}
            for cond in order:
                log(f"\n=== {name} · task {t_i}/{len(spec['tasks'])} · repeat {rep + 1}/{repeats} · {NAMES.get(cond, cond)} ===")
                log(f"    {task['source']}  <-  {task['donor']}  ({comp})")
                params = {"mode": cond, "source": task["source"], "donor": task["donor"], "add_component": True,
                          "component": comp, "prompt": task.get("prompt") or default_prompt(False, False, comp),
                          "build": build, "auto_compare": False}
                try:
                    recs[cond] = run(params, log=lambda m: log("    " + m),
                                     run_id=f"{time.strftime('%Y%m%d-%H%M%S')}-{cond}")
                    c = recs[cond].get("checks") or {}
                    log(f"    -> {c.get('outcome')} {c.get('score')}")
                except Exception as e:
                    log(f"    -> stopped: {e}")
                time.sleep(1)     # run ids are per-second
            if "prompt" in recs:
                for cond in ("report", "history"):
                    if cond in recs:
                        c = compare.record(recs["prompt"]["id"], recs[cond]["id"], experiment=name)
                        log(f"\n*** {NAMES[cond]} vs agent alone: {c['verdict'].upper()}\n    {c['note']}")
                        results.append(c)
    return results


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
