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
    r.add_argument("--history", action="store_true", help="report + past cases from other tasks (condition E)")
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
    e = sub.add_parser("experiment", help="run every task of an experiment file under each condition")
    e.add_argument("file")
    e.add_argument("--conditions", help="comma-separated: prompt,report,history")
    e.add_argument("--repeats", type=int)
    e.add_argument("--no-build", action="store_true")
    ri = sub.add_parser("import-reviews", help="store manual code reviews from a JSON file in runs.db")
    ri.add_argument("file")
    sub.add_parser("backfill", help="store the code diff of every earlier run in runs.db")
    ib = sub.add_parser("import-browser", help="store a browser check (labels.json + results.json) in runs.db")
    ib.add_argument("folder")
    rx = sub.add_parser("record-experiment", help="register an experiment and where its records live")
    rx.add_argument("name")
    rx.add_argument("--date", required=True)
    rx.add_argument("--task-file", default="")
    rx.add_argument("--writeup", default="")
    rx.add_argument("--reviews", nargs="*", default=[])
    rx.add_argument("--browser", default="")
    rx.add_argument("--note", default="")
    ex = sub.add_parser("export", help="write one experiment's records to experiments/results/ (for git)")
    ex.add_argument("name")
    ex.add_argument("--date", default="")
    rs = sub.add_parser("rescore", help="re-run the checks on finished runs (no AI calls)")
    rs.add_argument("ids", nargs="*")
    res = sub.add_parser("results", help="verdicts recorded so far")
    res.add_argument("--experiment")
    a = ap.parse_args(argv)

    if a.cmd == "providers":
        for p in llm.status():
            print(f"{'ON ' if p['has_key'] else 'off'}  {p['name']:<11} {p['model']:<40} {'' if p['has_key'] else 'set ' + p['key_env']}")
        return 0
    if a.cmd == "import-reviews":
        from . import compare, diffs
        diffs.backfill()
        print(f"Stored {compare.import_reviews(a.file)} review(s).")
        return 0
    if a.cmd in ("import-browser", "record-experiment", "export"):
        from . import records
        if a.cmd == "import-browser":
            records.import_browser(a.folder)
        elif a.cmd == "record-experiment":
            print(records.record_experiment(a.name, a.date, a.task_file, a.writeup, a.reviews, a.browser, a.note))
        else:
            print(f"Wrote {records.export(a.name, a.date)}")
        return 0
    if a.cmd == "rescore":
        from . import rescore
        print(f"Re-scored {rescore.rescore_all(a.ids or None)} run(s).")
        return 0
    if a.cmd == "backfill":
        from . import diffs
        diffs.backfill()
        return 0
    if a.cmd in ("experiment", "results"):
        from . import compare, experiment
        if a.cmd == "experiment":
            spec = experiment.load(a.file)
            rows = experiment.run_experiment(spec, conditions=a.conditions.split(",") if a.conditions else None,
                                             repeats=a.repeats, build=not a.no_build)
            print("\n" + experiment.table(rows))
        else:
            rows = [r for r in compare.all_rows() if not a.experiment or r["experiment"] == a.experiment]
            print(experiment.table(rows))
            for r in rows:
                if r.get("review"):
                    print(f"  review of {r['source'].split('/')[-1]} <- {r['donor'].split('/')[-1]}: "
                          f"{r['review']['verdict']} (automatic said {r['verdict']}) - {r['review']['summary']}")
        return 0
    if a.cmd == "scan":
        src, don = fetch(a.source, print), fetch(a.donor, print)
        print(facts_markdown(scan_for(src.path, don.path, not a.no_migration)))
        return 0
    mig, sty = not a.no_migration, not a.no_style
    prompt = Path(a.prompt_file).read_text() if a.prompt_file else default_prompt(mig, sty, a.component or "")
    rec = run({"mode": "history" if a.history else "report" if a.report else "prompt", "source": a.source, "donor": a.donor,
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
