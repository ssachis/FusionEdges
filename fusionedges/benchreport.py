"""Turn a benchmark's results.jsonl into a comparable, auto-generated report (Markdown + CSV).

  python3 -m fusionedges bench report --name pilot [--baseline A]

Everything here is plain arithmetic on the recorded runs (standard library only). Comparisons are paired by
task: each task's rate in a condition is the mean over its repetitions, then the per-task differences against the
baseline condition are summarised with a seeded bootstrap interval and an exact sign test. With few tasks the
report says so; every comparison is exploratory unless you decided on one in advance.
"""
from __future__ import annotations

import csv
import json
import math
import random
from collections import defaultdict
from pathlib import Path

from .bench import CONDITIONS, read_results

BOOT = 2000


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def sign_test(diffs: list) -> dict:
    """Exact two-sided sign test on the non-zero paired differences."""
    wins, losses = sum(1 for d in diffs if d > 0), sum(1 for d in diffs if d < 0)
    n = wins + losses
    if n == 0:
        return {"wins": 0, "losses": 0, "ties": len(diffs), "p": None}
    k = min(wins, losses)
    p = min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n)
    return {"wins": wins, "losses": losses, "ties": len(diffs) - n, "p": p}


def bootstrap_ci(diffs: list, seed: int = 1, level: float = 0.95):
    """Percentile bootstrap interval for the mean paired difference (resampling tasks)."""
    if len(diffs) < 2:
        return None
    rng = random.Random(seed)
    means = sorted(sum(rng.choice(diffs) for _ in diffs) / len(diffs) for _ in range(BOOT))
    lo = means[int(BOOT * (1 - level) / 2)]
    hi = means[min(BOOT - 1, int(BOOT * (1 + level) / 2))]
    return lo, hi


def per_task(rows: list, metric: str) -> dict:
    """{condition: {task: mean of metric over reps}} (ignoring runs where the metric is missing)."""
    by = defaultdict(lambda: defaultdict(list))
    for r in rows:
        v = r.get(metric)
        if v is not None:
            by[r["condition"]][r["task"]].append(float(v))
    return {c: {t: sum(v) / len(v) for t, v in ts.items()} for c, ts in by.items()}


def paired(rows: list, metric: str, cond: str, base: str, seed: int = 1) -> dict | None:
    pt = per_task(rows, metric)
    a, b = pt.get(base, {}), pt.get(cond, {})
    tasks = sorted(set(a) & set(b))
    if not tasks:
        return None
    diffs = [b[t] - a[t] for t in tasks]
    return {"tasks": len(tasks), "mean_diff": sum(diffs) / len(diffs), "ci": bootstrap_ci(diffs, seed),
            "sign": sign_test(diffs)}


def summarize(rows: list) -> dict:
    """Per-condition totals over all runs."""
    out = {}
    for c in sorted({r["condition"] for r in rows}):
        rs = [r for r in rows if r["condition"] == c]
        ok = [r for r in rs if r["success"]]
        cost = sum(r["cost"] or 0 for r in rs)
        out[c] = {
            "runs": len(rs), "tasks": len({r["task"] for r in rs}),
            "errors": sum(1 for r in rs if r["status"] != "done"),
            "success_rate": len(ok) / len(rs) if rs else None,
            "build_rate": mean([1.0 if r["build_ok"] else 0.0 for r in rs if r["build_ok"] is not None]),
            "mean_tokens": mean([r["tokens_total"] for r in rs]),
            "mean_report_tokens": mean([r["report_tokens"] for r in rs]),
            "mean_cost": mean([r["cost"] for r in rs]),
            "mean_retries": mean([r["retries"] for r in rs]),
            "mean_seconds": mean([r["seconds"] for r in rs]),
            "cost_per_success": (cost / len(ok)) if ok else None,
            "runtime_error_rate": mean([1.0 if (r["runtime_errors"] or 0) > 0 else 0.0
                                        for r in rs if r["runtime_errors"] is not None]),
        }
    return out


def _f(x, digits=2, pct=False, money=False):
    if x is None:
        return "n/a"
    if pct:
        return f"{x * 100:.0f}%"
    if money:
        return f"${x:.3f}"
    return f"{x:,.{digits}f}"


def _label(c):
    return CONDITIONS.get(c, {}).get("label", c)


def write_csv(rows: list, path: Path):
    cols = ["task", "category", "condition", "rep", "status", "outcome", "success", "checks_passed", "checks_total",
            "build_ok", "runtime_errors", "agent_tokens", "report_tokens", "tokens_total", "cost", "retries", "seconds",
            "models", "run_id", "error"]
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in sorted(rows, key=lambda r: (r["task"], r["condition"], r["rep"])):
            w.writerow([";".join(r[c]) if isinstance(r.get(c), list) else r.get(c) for c in cols])


def markdown(rows: list, name: str, baseline: str | None = None, seed: int = 1) -> str:
    conds = sorted({r["condition"] for r in rows})
    if not conds:
        return f"# Benchmark {name}\n\nNo results yet.\n"
    base = baseline if baseline in conds else conds[0]
    tasks = sorted({r["task"] for r in rows})
    summ = summarize(rows)
    models = sorted({m for r in rows for m in (r.get("models") or [])})
    L = [f"# Benchmark report: {name}", "",
         f"{len(rows)} runs · {len(tasks)} tasks · conditions {', '.join(f'{c} ({_label(c)})' for c in conds)} · "
         f"baseline {base} · models: {', '.join(models) or 'unknown'}", ""]
    if len({tuple(sorted(r.get('models') or [])) for r in rows}) > 1:
        L += ["> **Warning:** runs used different models, so differences between conditions may come from the model, "
              "not the condition. Pin one provider (`FUSIONEDGES_PROVIDERS`) for a fair comparison.", ""]
    if len(tasks) < 12:
        L += [f"> **Small sample:** {len(tasks)} tasks. Treat everything below as descriptive, not as evidence of an effect.", ""]

    L += ["## Results by condition", "",
          "| Condition | Runs | Verified success | Builds | Runtime errors | Mean tokens | of which report | Mean cost | Cost per success | Mean retries | Errored runs |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for c in conds:
        s = summ[c]
        L.append(f"| {c} {_label(c)} | {s['runs']} | {_f(s['success_rate'], pct=True)} | {_f(s['build_rate'], pct=True)} | "
                 f"{_f(s['runtime_error_rate'], pct=True)} | {_f(s['mean_tokens'], 0)} | {_f(s['mean_report_tokens'], 0)} | "
                 f"{_f(s['mean_cost'], money=True)} | {_f(s['cost_per_success'], money=True)} | {_f(s['mean_retries'])} | {s['errors']} |")
    L += ["", "Verified success = the run built and passed every applicable check. Runtime errors stay n/a until the browser "
              "check exists; until then a site that builds but crashes in the browser still counts as a success.", ""]

    others = [c for c in conds if c != base]
    if others:
        L += [f"## Paired differences against {base} ({_label(base)})", "",
              "Positive means higher than the baseline. Each task counts once (mean over its repetitions). 95% bootstrap "
              "interval over tasks; sign test on tasks whose result changed.", "",
              "| Condition | Metric | Tasks | Mean difference | 95% interval | Better / worse / tied | Sign test p |", "|---|---|---|---|---|---|---|"]
        for c in others:
            for metric, label, pct in (("success", "Verified success", True), ("tokens_total", "Total tokens", False),
                                       ("cost", "Cost (USD)", False), ("retries", "Retries", False)):
                p = paired(rows, metric, c, base, seed)
                if not p:
                    L.append(f"| {c} | {label} | 0 | n/a | n/a | n/a | n/a |")
                    continue
                d, ci, s = p["mean_diff"], p["ci"], p["sign"]
                fmt = (lambda x: f"{x * 100:+.0f} pts") if pct else (lambda x: f"{x:+,.3f}" if metric == "cost" else f"{x:+,.1f}")
                better_is_up = metric == "success"
                b, w = (s["wins"], s["losses"]) if better_is_up else (s["losses"], s["wins"])
                ci_txt = "n/a" if ci is None else fmt(ci[0]) + " to " + fmt(ci[1])
                p_txt = "n/a" if s["p"] is None else "%.3f" % s["p"]
                L.append(f"| {c} {_label(c)} | {label} | {p['tasks']} | {fmt(d)} | "
                         f"{ci_txt} | {b} / {w} / {s['ties']} | {p_txt} |")
        L += [""]

    cats = sorted({r["category"] for r in rows if r.get("category")})
    if cats:
        L += ["## Success by category", "", "| Category | " + " | ".join(f"{c}" for c in conds) + " |",
              "|---|" + "---|" * len(conds)]
        for cat in cats:
            cells = []
            for c in conds:
                rs = [r for r in rows if r["category"] == cat and r["condition"] == c]
                cells.append(f"{_f(sum(1 for r in rs if r['success']) / len(rs), pct=True)} ({len(rs)})" if rs else "n/a")
            L.append(f"| {cat} | " + " | ".join(cells) + " |")
        L += ["", "Numbers in brackets are runs."]
        L += [""]

    L += ["## Per task", "", "Successes out of runs, then mean tokens.", "",
          "| Task | " + " | ".join(f"{c} success | {c} tokens" for c in conds) + " |",
          "|---|" + "---|---|" * len(conds)]
    for t in tasks:
        cells = []
        for c in conds:
            rs = [r for r in rows if r["task"] == t and r["condition"] == c]
            cells += ([f"{sum(1 for r in rs if r['success'])}/{len(rs)}", _f(mean([r['tokens_total'] for r in rs]), 0)]
                      if rs else ["n/a", "n/a"])
        L.append(f"| {t} | " + " | ".join(cells) + " |")
    L += [""]

    bad = [r for r in rows if not r["success"]]
    if bad:
        L += ["## Runs that did not succeed", "", "| Task | Condition | Rep | Outcome | Why |", "|---|---|---|---|---|"]
        for r in sorted(bad, key=lambda r: (r["task"], r["condition"], r["rep"])):
            why = (r.get("error") or "").replace("|", "/").replace("\n", " ")[:140]
            L.append(f"| {r['task']} | {r['condition']} | {r['rep']} | {r['outcome'] or r['status']} | {why} |")
        L += [""]

    L += ["## How to read this", "",
          "- Conditions differ only in what the coding agent is shown. Everything else (model, retry cap, files shown) should be identical.",
          "- Token and cost figures include the report step, so a condition is not credited with savings it paid for.",
          "- Small task counts give wide intervals. A difference whose interval includes zero is not evidence of an effect.",
          "- Static checks cannot see runtime breakage. Add the browser check before drawing conclusions about safety.", ""]
    return "\n".join(L)


def generate(name: str, results_path, out_dir, baseline: str | None = None, seed: int = 1) -> dict:
    rows = read_results(results_path)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.md").write_text(markdown(rows, name, baseline, seed))
    write_csv(rows, out / "results.csv")
    (out / "summary.json").write_text(json.dumps(summarize(rows), indent=1))
    return {"report": str(out / "report.md"), "csv": str(out / "results.csv"), "runs": len(rows)}
