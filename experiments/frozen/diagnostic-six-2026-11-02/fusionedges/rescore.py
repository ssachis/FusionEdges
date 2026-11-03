"""Re-run the checks on finished runs after the checks change (no AI calls).

  python3 -m fusionedges rescore                 every finished add-a-component run
  python3 -m fusionedges rescore RUN_ID ...

The starting copy and the donor are re-exported at the commits the run used, so a re-score is identical to what the
checks would have said at run time. The old result is kept in run.json under checks_history, then every comparison
that involves the run is recorded again (the verdict and note are recomputed; your notes and reviews stay).
"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from . import compare, diffs, paths, store
from .checks import CHECKS_VERSION, component_checks


def _export(repo: dict, dest: Path) -> Path | None:
    path = diffs._relocate(repo.get("path"), "examples", paths.EXAMPLES)
    commit, sub = repo.get("commit") or "", repo.get("sub") or ""
    clone = path
    for _ in Path(sub).parts if sub else ():
        clone = clone.parent
    if commit and (clone / ".git").exists():
        diffs._extract(diffs._git(["archive", "--format=tar", commit] + ([sub] if sub else []), clone, binary=True), dest, sub)
        return dest
    return path if path.is_dir() else None


def rescore(rec: dict, log=print) -> dict | None:
    comp = (rec.get("options") or {}).get("component")
    if rec.get("status") != "done" or not comp:
        return None
    folder = paths.OUTPUT / rec["id"]
    project = folder / "project"
    if not project.is_dir():
        log(f"{rec['id']}: no project folder, skipped")
        return None
    tmp = Path(tempfile.mkdtemp(prefix="fe-rescore-"))
    try:
        base, donor = tmp / "base", tmp / "donor"
        base.mkdir()
        donor.mkdir()
        diffs._baseline(rec, project, base)
        donor_path = _export(rec.get("donor") or {}, donor)
        old = rec.get("checks") or {}
        new = component_checks(base, project, comp, rec.get("build") or {}, rec.get("source_framework") or "",
                               donor=donor_path, facts=rec.get("facts") or [])
        new["version"] = CHECKS_VERSION
        if old and old.get("version") != CHECKS_VERSION:
            rec.setdefault("checks_history", []).append(old)
        rec["checks"] = new
        (folder / "run.json").write_text(json.dumps(rec, indent=1, default=str))
        (folder / "checks.json").write_text(json.dumps(new, indent=1))
        store.save(rec)
        changed = [v["label"] for k, v in new["checks"].items() if (old.get("checks") or {}).get(k, {}).get("pass") != v["pass"]]
        log(f"{rec['id']}: {old.get('score')} -> {new['score']}" + (f"  (changed: {', '.join(changed)})" if changed else ""))
        return new
    except Exception as e:
        log(f"{rec['id']}: could not re-score ({e})")
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def rescore_all(ids=None, log=print) -> int:
    with store.connect() as db:
        rows = [r[0] for r in db.execute("SELECT id FROM runs WHERE status='done' AND COALESCE(component,'')<>''")]
    ids = ids or rows
    n = sum(1 for i in ids if (store.load(i) and rescore(store.load(i), log)))
    for c in compare.all_rows():
        if c["prompt_run"] in ids or c["report_run"] in ids:
            new = compare.record(c["prompt_run"], c["report_run"])
            if new["verdict"] != c["verdict"]:
                log(f"comparison {c['source'].split('/')[-1]} <- {c['donor'].split('/')[-1]}: {c['verdict']} -> {new['verdict']}")
    return n
