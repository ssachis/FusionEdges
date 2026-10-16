"""Verification: build check, change diff, and a static regression scan."""
from __future__ import annotations

import difflib
import os
import shutil
import subprocess
from collections import Counter
from pathlib import Path

from .analyzer import SKIP_DIRS, _files, _read, _rel, parse_css, _package_json  # noqa: F401
import json


def build_command(root: Path):
    pkg = root / "package.json"
    if not pkg.exists():
        return None, "no package.json"
    try:
        scripts = json.loads(pkg.read_text(encoding="utf-8")).get("scripts", {})
    except (json.JSONDecodeError, OSError):
        return None, "unreadable package.json"
    if "build" not in scripts:
        return None, "no build script"
    if not shutil.which("npm"):
        return None, "npm not installed"
    return ["npm", "run", "build"], ""


def _deps(pkg: Path) -> dict:
    try:
        d = json.loads(pkg.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {**d.get("devDependencies", {}), **d.get("dependencies", {})}


def _npm_install(root: Path, timeout: int):
    lock = (root / "package-lock.json").exists()
    cmd = ["npm", "ci" if lock else "install", "--no-audit", "--no-fund", "--loglevel=error"]
    r = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0 and lock:  # lockfile out of sync after an edit: fall back to install
        r = subprocess.run(["npm", "install", "--no-audit", "--no-fund", "--loglevel=error"], cwd=root,
                           capture_output=True, text=True, timeout=timeout)
    return r


def ensure_modules(root: Path, timeout: int = 900) -> dict:
    """Make node_modules match package.json. A shared (symlinked) node_modules is replaced
    by a real install when the agent changed the dependencies."""
    nm = root / "node_modules"
    if nm.is_symlink():
        shared_pkg = nm.resolve().parent / "package.json"
        if _deps(shared_pkg) == _deps(root / "package.json"):
            return {"ok": True, "output": ""}
        nm.unlink()
    elif nm.exists():
        return {"ok": True, "output": ""}
    try:
        r = _npm_install(root, timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "output": "npm install timed out"}
    return {"ok": r.returncode == 0, "output": (r.stdout + r.stderr)[-3000:]}


def run_build(root: Path, timeout: int = 900) -> dict:
    cmd, why = build_command(root)
    if cmd is None:
        return {"skipped": True, "ok": None, "reason": why, "output": ""}
    deps = _package_json(root)
    env = dict(os.environ, CI="true", NEXT_TELEMETRY_DISABLED="1", BROWSER="none")
    if "vite" in deps and "next" not in deps:
        cmd = cmd + ["--", "--base=./"]  # relative asset paths so the build can be previewed under /preview/...
    if "react-scripts" in deps:
        env["PUBLIC_URL"] = "."
        env["CI"] = "false"  # CRA treats lint warnings as errors when CI=true
    inst = ensure_modules(root, timeout)
    out = inst["output"]
    if not inst["ok"]:
        return {"skipped": False, "ok": False, "reason": "npm install failed", "output": out[-3000:]}
    try:
        r = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return {"skipped": False, "ok": False, "reason": "build timed out", "output": out[-3000:]}
    out += r.stdout + r.stderr
    return {"skipped": False, "ok": r.returncode == 0, "reason": "" if r.returncode == 0 else "build failed",
            "output": out[-4000:]}


def site_dir(root: Path):
    """Folder of a built static site (Vite dist/, CRA build/, Next export out/), or None."""
    for d in ("dist", "build", "out"):
        if (root / d / "index.html").exists():
            return root / d
    return None


def _walk(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and d != "node_modules"]
        for f in filenames:
            p = Path(dirpath) / f
            yield p.relative_to(root).as_posix(), p


def make_diff(before: Path, after: Path, limit: int = 20000) -> list:
    b = dict(_walk(before))
    a = dict(_walk(after))
    changes = []
    for rel in sorted(set(a) | set(b)):
        try:
            old = b[rel].read_text(encoding="utf-8") if rel in b else None
            new = a[rel].read_text(encoding="utf-8") if rel in a else None
        except UnicodeDecodeError:
            continue
        if old == new:
            continue
        status = "added" if old is None else "removed" if new is None else "modified"
        lines = list(difflib.unified_diff((old or "").splitlines(), (new or "").splitlines(),
                                          f"a/{rel}", f"b/{rel}", lineterm="", n=2))
        added = sum(1 for l in lines if l.startswith("+") and not l.startswith("+++"))
        removed = sum(1 for l in lines if l.startswith("-") and not l.startswith("---"))
        changes.append({"path": rel, "status": status, "added": added, "removed": removed,
                        "diff": "\n".join(lines)[:limit]})
    return changes


def _css_state(root: Path):
    rules, variables = [], {}
    for p in _files(root, {".css"}):
        r, v = parse_css(_read(p), _rel(p, root))
        rules += r
        variables.update(v)
    return rules, variables


def _leaky(r):
    return r["is_global"] or (not r["classes"] and not r["ids"])


def regression_scan(before: Path, after: Path) -> list:
    """Static check of what the change did to the host's existing styling."""
    b_rules, b_vars = _css_state(before)
    a_rules, a_vars = _css_state(after)
    out = []

    def add(kind, sev, detail):
        out.append({"type": kind, "severity": sev, "detail": detail})

    for k in sorted(b_vars):
        if k in a_vars and a_vars[k] != b_vars[k]:
            add("css-variable-changed", "high", f"{k} was {b_vars[k]}, now {a_vars[k]}")
    b_leaky = {r["selector"] for r in b_rules if _leaky(r)}
    a_leaky = {r["selector"]: r for r in a_rules if _leaky(r)}
    for sel in sorted(set(a_leaky) - b_leaky):
        add("global-selector-added", "high" if a_leaky[sel]["is_global"] else "medium",
            f"new unscoped rule `{sel}` in {a_leaky[sel]['file']}")
    b_cnt = Counter(c for r in b_rules for c in r["classes"])
    a_cnt = Counter(c for r in a_rules for c in r["classes"])
    for c in sorted(b_cnt):
        if a_cnt[c] > b_cnt[c]:
            add("class-redefined", "high", f".{c} is now defined in {a_cnt[c]} rules (was {b_cnt[c]})")
    b_selcnt = Counter(r["selector"] for r in b_rules)
    a_selcnt = Counter(r["selector"] for r in a_rules)
    class_sels = {r["selector"] for r in b_rules if r["classes"]}
    for sel in sorted(b_selcnt):
        if sel not in class_sels and sel != ":root" and a_selcnt[sel] > b_selcnt[sel]:
            add("selector-redefined", "high", f"`{sel}` is now defined {a_selcnt[sel]} times (was {b_selcnt[sel]}), so the later rule can override the host's")
    b_sel = {r["selector"] for r in b_rules}
    a_sel = {r["selector"] for r in a_rules}
    for sel in sorted(b_sel - a_sel):
        add("host-rule-removed", "medium", f"existing rule `{sel}` no longer present")
    return out
