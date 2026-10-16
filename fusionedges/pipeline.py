"""One FusionEdges run.

  clone source + donor into examples/ (pinned commits)
  scan both (deterministic facts)
  [report tab only] retrieve code (RAG) -> report agent -> compatibility report
  create output/<run>/project  (copy of the donor for a migration, of the source for a style-only change)
  coding agent builds the result in project/
  install + build, migration checks, git commit, zip
  save run.json + output/runs.db
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import zipfile
from pathlib import Path

from . import paths, rag, store
from .agents import fit_to_limit, report_markdown, run_edit_agent, run_oneshot, run_report_agent
from .checks import component_checks, run_checks
from .repos import fetch
from .scanner import scan
from .preview import run_script
from .verify import build_command, run_build

COPY_IGNORE = shutil.ignore_patterns("node_modules", "dist", "build", ".next", "out", ".git", ".turbo",
                                     ".cache", ".vercel", ".env", ".env.local")
GITIGNORE = "node_modules/\n.next/\ndist/\nbuild/\nout/\n.env*.local\n"


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:32] or "run"


def remap(sc: dict, roles: dict) -> dict:
    """The scanner thinks in 'donor code goes into host'. Rename its repos to this run's roles:
    migration   -> scanner host = donor codebase, scanner donor = source site (content flows into the donor)
    style only  -> scanner host = source site,    scanner donor = donor (styles flow into the source)"""
    sc = json.loads(json.dumps(sc))
    out = {k: v for k, v in sc.items() if k not in ("host", "donor")}
    out[roles["host"]], out[roles["donor"]] = sc["host"], sc["donor"]

    def words(t):
        t = re.sub(r"\b(Host|host|Donor|donor)\b", lambda m: "\x00" + m.group(1), t)
        for a, b in (("Host", roles["host"].capitalize()), ("host", roles["host"]),
                     ("Donor", roles["donor"].capitalize()), ("donor", roles["donor"])):
            t = t.replace("\x00" + a, b)
        return t
    for f in out["facts"]:
        f["text"] = words(f["text"])
        f["repo"] = roles.get(f.get("repo"), f.get("repo"))
        for e in f["evidence"]:
            e["repo"] = roles.get(e["repo"], e["repo"])
    return out


def scan_for(source: Path, donor: Path, migration: bool, component: str = "") -> dict:
    if migration:
        return remap(scan(donor, source), {"host": "donor", "donor": "source"})
    scope = None
    if component.strip():
        main = rag.find_component_files(donor, component)
        if main:  # facts about the donor are limited to the component and what it imports
            scope = {rel for rel, _, _ in rag.import_closure(donor, main)[0]}
    return remap(scan(source, donor, section=component.lower().strip(), donor_scope=scope),
                 {"host": "source", "donor": "donor"})


def donor_style_files(donor: Path, prof: dict) -> list:
    files = []
    tw = (prof.get("tailwind") or {}).get("config", "")
    if tw.endswith((".js", ".ts", ".cjs", ".mjs")):
        files.append(tw)
    for pat in ("app/globals.css", "src/app/globals.css", "styles/globals.css", "src/styles/globals.css",
                "src/index.css", "app/global.css"):
        if (donor / pat).exists():
            files.append(pat)
    return files


def _zip(folder: Path, dest: Path):
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(folder.rglob("*")):
            rel = p.relative_to(folder)
            if set(rel.parts) & {"node_modules", ".next", ".git", "dist", "out"} or p.is_symlink() or not p.is_file():
                continue
            z.write(p, Path(folder.name) / rel)


def _git_commit(folder: Path, message: str, log):
    if not shutil.which("git"):
        return
    gi = folder / ".gitignore"
    existing = gi.read_text() if gi.exists() else ""
    missing = [l for l in GITIGNORE.splitlines() if l and l not in existing]
    if missing:
        gi.write_text(existing.rstrip("\n") + ("\n" if existing else "") + "\n".join(missing) + "\n")
    g = ["git", "-c", "user.name=FusionEdges", "-c", "user.email=fusionedges@localhost", "-c", "init.defaultBranch=main"]
    try:
        if not (folder / ".git").exists():
            subprocess.run(g + ["init", "-q"], cwd=folder, check=True, capture_output=True, timeout=60)
        subprocess.run(g + ["add", "-A"], cwd=folder, check=True, capture_output=True, timeout=120)
        subprocess.run(g + ["commit", "-q", "-m", message, "--allow-empty"], cwd=folder, check=True,
                       capture_output=True, timeout=120)
    except Exception as e:
        log(f"note: could not create the git commit ({e})")


def run(params: dict, log=print, stage=lambda *a: None, run_id: str | None = None) -> dict:
    t0 = time.time()
    mode = params.get("mode", "prompt")
    component = (params.get("component") or "").strip() if params.get("add_component") or params.get("component") else ""
    if params.get("add_component") and not component:
        raise ValueError("Say which component to add (e.g. navbar)")
    opts = {"migration": bool(params.get("migration")) and not component,
            "style": bool(params.get("style")) and not component, "component": component,
            "edit": params.get("edit") or os.environ.get("FUSIONEDGES_EDIT", "oneshot")}
    prompt = (params.get("prompt") or "").strip()
    if not (opts["migration"] or opts["style"] or component):
        raise ValueError("Pick codebase migration, style transfer, or add a component")
    if not prompt:
        raise ValueError("The prompt is empty")
    run_id = run_id or f"{time.strftime('%Y%m%d-%H%M%S')}-{mode}"
    folder = paths.OUTPUT / run_id
    folder.mkdir(parents=True, exist_ok=True)
    project = folder / "project"
    rec = {"id": run_id, "created": store.now_iso(), "mode": mode, "options": opts, "prompt": prompt,
           "status": "running", "folder": str(folder), "project": str(project),
           "source": {"spec": params.get("source", "")}, "donor": {"spec": params.get("donor", "")}}

    def save():
        (folder / "run.json").write_text(json.dumps(rec, indent=1, default=str))
        try:
            store.save(rec)
        except Exception as e:
            log(f"warning: could not write the database: {e}")

    try:
        stage("fetch", "running")
        source = fetch(params.get("source", ""), log)
        donor = fetch(params.get("donor", ""), log)
        rec["source"], rec["donor"] = source.public(), donor.public()
        at = lambda r: f"{r.label} @ {r.commit[:7]}" if r.commit else r.label  # noqa: E731
        stage("fetch", "done", f"{at(source)}  ·  {at(donor)}")

        stage("scan", "running")
        sc = scan_for(source.path, donor.path, opts["migration"], component)
        rec["source_framework"], rec["donor_framework"] = sc["source"]["framework"], sc["donor"]["framework"]
        rec["facts"] = sc["facts"]
        (folder / "facts.json").write_text(json.dumps(sc, indent=1))
        stage("scan", "done", f"source: {rec['source_framework']} · donor: {rec['donor_framework']} · {len(sc['facts'])} facts")
        save()

        report, report_usage = None, None
        fitted = {}  # the code budget the report step settled on; the coding step starts from it
        if mode == "report":
            stage("report", "running")
            if component:
                report, report_usage = fit_to_limit(lambda b: run_report_agent(
                    prompt, source.path, donor.path, sc, log, component=component, budget_chars=b), log, used=fitted)
            else:
                report, report_usage = run_report_agent(prompt, source.path, donor.path, sc, log, component=component)
            (folder / "report.json").write_text(json.dumps(report, indent=1))
            (folder / "report.md").write_text(report_markdown(report))
            rec["report"], rec["report_usage"] = report, report_usage
            hi = sum(1 for c in report["conflicts"] if c["severity"] == "high")
            mapped = (f"{len(report.get('donor_files', []))} donor files" if component
                      else f"{len(report.get('page_map', []))} pages mapped")
            stage("report", "done", f"{mapped} · {len(report['conflicts'])} conflicts ({hi} high)")
            save()

        stage("agent", "running")
        start_from = donor if opts["migration"] else source
        shutil.copytree(start_from.path, project, ignore=COPY_IGNORE, symlinks=False)
        rec["started_from"] = "donor" if opts["migration"] else "source"
        can_build = bool(params.get("build", True)) and build_command(project)[0] is not None
        try:
            if opts["edit"] == "agent":
                agent = run_edit_agent(prompt, source.path, donor.path, project, sc, can_build, log, report=report)
            else:
                agent = fit_to_limit(lambda b: run_oneshot(
                    prompt, source.path, donor.path, project, sc, can_build, log, report=report,
                    component=component, started_from=rec["started_from"], budget_chars=b), log,
                    start=min(fitted.get('budget') or 10**9, rag.context_budget_chars())) if component else run_oneshot(
                    prompt, source.path, donor.path, project, sc, can_build, log, report=report,
                    component=component, started_from=rec["started_from"])
        except Exception as e:
            agent = {"error": str(e)}
            log(f"[agent] stopped: {e}")
        rec["agent"] = agent
        stage("agent", "failed" if agent.get("error") else "done",
              agent.get("error", "")[:120] or f"{len(agent.get('files_written', []))} files written, "
                                                f"{len(agent.get('files_deleted', []))} deleted")
        save()

        stage("check", "running")
        if can_build or (bool(params.get("build", True)) and build_command(project)[0] is not None):
            log("Installing dependencies and building the output (first time can take a few minutes)...")
            build = run_build(project)
        else:
            why = "build turned off" if not params.get("build", True) else build_command(project)[1]
            build = {"skipped": True, "ok": None, "reason": why, "output": ""}
        (folder / "build.log").write_text(build.get("output", ""))
        rec["run_script"] = run_script(project)
        rec["build"] = {k: build.get(k) for k in ("ok", "skipped", "reason")}
        rec["build_tail"] = (build.get("output") or "")[-2000:]
        if component:
            rec["checks"] = component_checks(source.path, project, component, build, sc["source"]["framework"],
                                               donor=donor.path)
        else:
            rec["checks"] = run_checks(source.path, donor.path, project, opts["migration"], opts["style"], build,
                                       donor_style_files(donor.path, sc["donor"]) if opts["migration"] and opts["style"] else [])
        (folder / "checks.json").write_text(json.dumps(rec["checks"], indent=1))
        what = (f"add {component}" if component else
                " + ".join(x for x, on in (("migration", opts["migration"]), ("style", opts["style"])) if on))
        _git_commit(project, f"FusionEdges {mode} run: {what} ({source.label} with {donor.label})", log)
        _zip(project, folder / "project.zip")
        o = rec["checks"]["outcome"]
        stage("check", "done" if o.startswith("passed") else "failed" if o == "failed" else "warn",
              f"{o} · {rec['checks']['score']} checks")
        rec["models"] = sorted(set(agent.get("models", [])) | set((report_usage or {}).get("models", [])))
        rec["status"] = "done"
    except Exception as e:
        rec["status"], rec["error"] = "error", str(e)
        rec["seconds"] = round(time.time() - t0, 1)
        save()
        raise
    rec["seconds"] = round(time.time() - t0, 1)
    save()
    return rec
