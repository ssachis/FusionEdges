"""What the agent actually changed: a file-by-file diff of output/<run>/project against its starting copy.

The starting copy (baseline) comes from, in order:
  1. the project's own git history: new runs commit "FusionEdges baseline" right after copying the
     source/donor and before the agent writes anything, so `git diff <baseline> HEAD` is exactly the agent's work;
  2. for older runs without that commit: the pinned commit of the repo the run started from, re-exported
     from its clone in examples/ (`git archive`), filtered the same way the copy was.

The result is cached in output/<run>/changes.json, with a plain patch in changes.diff.
"""
from __future__ import annotations

import difflib
import io
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

from . import paths

BASELINE_MSG = "FusionEdges baseline"
SKIP_DIRS = {"node_modules", "dist", "build", ".next", "out", ".git", ".turbo", ".cache", ".vercel"}
SKIP_FILES = {".env", ".env.local", ".DS_Store"}
GENERATED = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb", "npm-shrinkwrap.json"}
GITIGNORE_ADDED = {"node_modules/", ".next/", "dist/", "build/", "out/", ".env*.local"}
MAX_FILE = 400_000      # bytes; bigger text files are listed but not diffed
MAX_PATCH = 60_000      # characters of patch kept per file
VERSION = 2             # bump to invalidate cached changes.json


def _git(args, cwd, binary=False):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError((r.stderr.decode("utf-8", "replace").strip().splitlines() or ["git failed"])[-1])
    return r.stdout if binary else r.stdout.decode("utf-8", "replace").strip()


def _files(root: Path) -> dict:
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for f in filenames:
            p = Path(dirpath) / f
            if f in SKIP_FILES or p.is_symlink():
                continue
            out[p.relative_to(root).as_posix()] = p
    return out


def _extract(tar_bytes: bytes, dest: Path, strip: str = ""):
    with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as t:
        for m in t.getmembers():
            if not m.isfile():
                continue
            name = m.name
            if strip:
                if not name.startswith(strip.rstrip("/") + "/"):
                    continue
                name = name[len(strip.rstrip("/")) + 1:]
            target = (dest / name).resolve()
            if dest.resolve() not in target.parents:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(t.extractfile(m).read())


def _relocate(path: str, anchor: str, base: Path) -> Path:
    """Recorded absolute paths break if the FusionEdges folder moved; re-anchor them on the current one."""
    p = Path(path or "/nonexistent")
    if p.exists():
        return p
    parts = p.as_posix().split(f"/{anchor}/", 1)
    return base / parts[1] if len(parts) == 2 else p


def baseline_commit(project: Path) -> str:
    """The 'FusionEdges baseline' commit in the project's git history, or ''."""
    if not (project / ".git").exists() or not shutil.which("git"):
        return ""
    try:
        log = _git(["log", "--format=%H %s", "--reverse"], project)
    except Exception:
        return ""
    for line in log.splitlines():
        sha, _, subject = line.partition(" ")
        if subject.startswith(BASELINE_MSG):
            return sha
    return ""


def _baseline(rec: dict, project: Path, tmp: Path):
    """Fill tmp with the starting copy. Returns (how, note)."""
    sha = baseline_commit(project)
    if sha:
        _extract(_git(["archive", "--format=tar", sha], project, binary=True), tmp)
        return "git", f"baseline commit {sha[:7]} in the project"
    started = rec.get("started_from") or ("donor" if (rec.get("options") or {}).get("migration") else "source")
    repo = rec.get(started) or {}
    path = _relocate(repo.get("path"), "examples", paths.EXAMPLES)
    commit, sub = repo.get("commit") or "", repo.get("sub") or ""
    clone = path
    for _ in range(len(Path(sub).parts)) if sub else ():
        clone = clone.parent
    if commit and (clone / ".git").exists():
        try:
            _extract(_git(["archive", "--format=tar", commit] + ([sub] if sub else []), clone, binary=True), tmp, sub)
            return "examples", f"{repo.get('label') or started} @ {commit[:7]} from examples/ (run predates baseline commits)"
        except RuntimeError as e:
            raise RuntimeError(f"the {started} clone in examples/ no longer has commit {commit[:7]} ({e})")
    if path.is_dir() and not commit:  # a local folder used as the repo (tests)
        for rel, p in _files(path).items():
            (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, tmp / rel)
        return "folder", f"{path} as it is now"
    raise RuntimeError(f"the starting copy of the {started} is not available (clone missing from examples/)")


def _text(p: Path):
    data = p.read_bytes()
    if b"\x00" in data[:8192]:
        return None, len(data)
    try:
        return data.decode("utf-8"), len(data)
    except UnicodeDecodeError:
        return None, len(data)


def _only_our_gitignore(old: str, new: str) -> bool:
    o, n = set(old.splitlines()), set(new.splitlines())
    return o <= n and {l for l in n - o if l.strip()} <= GITIGNORE_ADDED


def compare(before: Path, after: Path) -> list:
    b, a = _files(before), _files(after)
    out = []
    for rel in sorted(set(a) | set(b)):
        bp, ap = b.get(rel), a.get(rel)
        if bp and ap and bp.read_bytes() == ap.read_bytes():
            continue
        status = "added" if bp is None else "deleted" if ap is None else "modified"
        old, osz = _text(bp) if bp else ("", 0)
        new, nsz = _text(ap) if ap else ("", 0)
        name = rel.rsplit("/", 1)[-1]
        if name == ".gitignore" and old is not None and new is not None and _only_our_gitignore(old, new):
            continue
        f = {"path": rel, "status": status, "added": 0, "removed": 0, "patch": "", "kind": "code"}
        if name in GENERATED:
            f["kind"] = "generated"
        elif old is None or new is None:
            f["kind"] = "binary"
        elif max(osz, nsz) > MAX_FILE:
            f["kind"] = "large"
        if f["kind"] == "code" or f["kind"] == "generated":
            lines = list(difflib.unified_diff(old.splitlines(), new.splitlines(),
                                              "/dev/null" if status == "added" else f"a/{rel}",
                                              "/dev/null" if status == "deleted" else f"b/{rel}",
                                              lineterm="", n=3))
            f["added"] = sum(1 for l in lines if l.startswith("+") and not l.startswith("+++"))
            f["removed"] = sum(1 for l in lines if l.startswith("-") and not l.startswith("---"))
            if f["kind"] == "code":
                patch = "\n".join(lines)
                f["truncated"] = len(patch) > MAX_PATCH
                f["patch"] = patch[:MAX_PATCH]
        out.append(f)
    order = {"code": 0, "large": 1, "binary": 2, "generated": 3}
    out.sort(key=lambda f: (order[f["kind"]], f["path"]))
    return out


def for_run(rec: dict, refresh: bool = False) -> dict:
    folder = paths.OUTPUT / rec["id"]
    if not folder.is_dir():
        folder = _relocate(rec.get("folder"), "output", paths.OUTPUT)
    project = folder / "project"
    cache = folder / "changes.json"
    if cache.exists() and not refresh:
        try:
            c = json.loads(cache.read_text())
            if c.get("version") == VERSION and c.get("mtime") == _mtime(project):
                return c
        except (json.JSONDecodeError, OSError):
            pass
    if not project.is_dir():
        return {"ok": False, "error": "this run has no project folder"}
    tmp = Path(tempfile.mkdtemp(prefix="fe-base-"))
    try:
        how, note = _baseline(rec, project, tmp)
        files = compare(tmp, project)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    code = [f for f in files if f["kind"] == "code"]
    res = {"ok": True, "version": VERSION, "mtime": _mtime(project), "run": rec.get("id"),
           "baseline": how, "baseline_note": note,
           "totals": {"files": len(files), "code_files": len(code),
                      "added": sum(f["added"] for f in code), "removed": sum(f["removed"] for f in code),
                      "new": sum(1 for f in files if f["status"] == "added"),
                      "changed": sum(1 for f in files if f["status"] == "modified"),
                      "deleted": sum(1 for f in files if f["status"] == "deleted")},
           "files": files}
    try:
        from . import store
        store.save_diff(rec["id"], res)
    except Exception:
        pass
    try:
        cache.write_text(json.dumps(res))
        (folder / "changes.diff").write_text("\n".join(f["patch"] for f in code if f["patch"]) + "\n")
    except OSError:
        pass
    return res


def _mtime(project: Path) -> float:
    """Changes when the project's last commit changes (or its HEAD file, for runs without git)."""
    for p in (project / ".git" / "refs" / "heads" / "main", project / ".git" / "HEAD", project):
        if p.exists():
            return round(p.stat().st_mtime, 3)
    return 0.0


def backfill(log=print) -> int:
    """Store the diff of every finished run that is not in the database yet (older runs)."""
    from . import store
    n = 0
    for rid in store.runs_without_diff():
        rec = store.load(rid)
        res = for_run(rec) if rec else {}
        if res.get("ok"):
            store.save_diff(rid, res)   # for_run may have answered from its file cache without saving
            n += 1
    if n:
        log(f"Stored the code diffs of {n} earlier run(s) in runs.db")
    return n
