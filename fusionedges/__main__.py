"""Command line (same pipeline as the web UI).

  python3 -m fusionedges run --source URL --donor URL [--report] [--no-migration | --no-style] [--prompt-file F]
  python3 -m fusionedges scan --source URL --donor URL                (facts only, no AI)
  python3 -m fusionedges providers                                     (which AI keys are set)
"""
import argparse
import sys
from pathlib import Path

from . import llm
from .pipeline import run, scan_for
from .prompts import default_prompt
from .repos import fetch
from .scanner import facts_markdown


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fusionedges")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--source", required=True, help="your site (GitHub link or folder)")
    r.add_argument("--donor", required=True, help="donor codebase (GitHub link or folder)")
    r.add_argument("--report", action="store_true", help="write a compatibility report first (tab 2)")
    r.add_argument("--no-migration", action="store_true")
    r.add_argument("--no-style", action="store_true")
    r.add_argument("--component", help="add this component from the donor (e.g. navbar) instead of migrating")
    r.add_argument("--agent", action="store_true", help="use the multi-step agent instead of one-shot + repairs")
    r.add_argument("--prompt-file", help="use this prompt instead of the default")
    r.add_argument("--no-build", action="store_true")
    s = sub.add_parser("scan")
    s.add_argument("--source", required=True)
    s.add_argument("--donor", required=True)
    s.add_argument("--no-migration", action="store_true")
    sub.add_parser("providers")
    b = sub.add_parser("bench", help="run a benchmark of many tasks under several conditions, or report on one")
    bs = b.add_subparsers(dest="bench_cmd", required=True)
    br = bs.add_parser("run")
    br.add_argument("--tasks", required=True, help="manifest: one JSON task per line")
    br.add_argument("--name", required=True)
    br.add_argument("--conditions", default="A,D", help="comma-separated condition ids (default A,D)")
    br.add_argument("--reps", type=int, default=3)
    br.add_argument("--seed", type=int, default=1)
    br.add_argument("--limit", type=int, help="run at most this many jobs now (the rest resume later)")
    bp = bs.add_parser("report")
    bp.add_argument("--name", required=True)
    bp.add_argument("--baseline", help="condition to compare against (default: first)")
    a = ap.parse_args(argv)

    if a.cmd == "bench":
        from . import bench, benchreport
        out = bench.bench_dir(a.name)
        if a.bench_cmd == "run":
            bench.run_bench(a.name, a.tasks, [c.strip() for c in a.conditions.split(",") if c.strip()], a.reps, a.seed,
                            limit=a.limit)
        res = benchreport.generate(a.name, out / "results.jsonl", out, getattr(a, "baseline", None), a.seed if a.bench_cmd == "run" else 1)
        print(f"{res['runs']} runs -> {res['report']}")
        return 0

    if a.cmd == "providers":
        for p in llm.status():
            print(f"{'ON ' if p['has_key'] else 'off'}  {p['name']:<11} {p['model']:<40} {'' if p['has_key'] else 'set ' + p['key_env']}")
        return 0
    if a.cmd == "scan":
        src, don = fetch(a.source, print), fetch(a.donor, print)
        print(facts_markdown(scan_for(src.path, don.path, not a.no_migration)))
        return 0
    mig, sty = not a.no_migration, not a.no_style
    prompt = Path(a.prompt_file).read_text() if a.prompt_file else default_prompt(mig, sty, a.component or "")
    rec = run({"mode": "report" if a.report else "prompt", "source": a.source, "donor": a.donor,
               "migration": mig, "style": sty, "add_component": bool(a.component), "component": a.component or "",
               "edit": "agent" if a.agent else "oneshot", "prompt": prompt, "build": not a.no_build},
              log=print, stage=lambda sid, state, note="": print(f"-- {sid}: {state} {note}"))
    c = rec["checks"]
    print(f"\n{c['outcome'].upper()} ({c['score']} checks)")
    for v in c["checks"].values():
        mark = {True: "✓", False: "✗", None: "-"}[v["pass"]]
        print(f"  {mark} {v['label']}: {v['value']}")
    print(f"\nNew project: {rec['project']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
