"""Fetch donor/host repos into examples/, pinned to the exact commit used.

Accepts:  https://github.com/owner/repo, .../tree/<branch-or-sha>[/subdir], owner/repo,
or a local folder (used by tests and for repos you already have).
"""
from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import paths

GH = re.compile(r"^(?:https?://)?(?:www\.)?github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?"
                r"(?:/tree/([^/]+)(?:/(.+?))?)?/?$")
SHORT = re.compile(r"^([\w.-]+)/([\w.-]+)$")


@dataclass
class Repo:
    spec: str
    path: Path          # folder to analyze (repo root, or subdir if the link pointed into one)
    url: str = ""
    ref: str = ""
    commit: str = ""
    label: str = ""
    sub: str = ""

    def public(self):
        return {"spec": self.spec, "url": self.url, "ref": self.ref, "commit": self.commit,
                "label": self.label, "sub": self.sub, "path": str(self.path)}


def _git(args, cwd=None, timeout=300):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        last = (r.stderr.strip().splitlines() or ["unknown error"])[-1][:240]
        raise RuntimeError(last)
    return r.stdout.strip()


def parse(spec: str):
    spec = spec.strip()
    m = GH.match(spec)
    if m:
        return m.group(1), m.group(2), m.group(3) or "", m.group(4) or ""
    m = SHORT.match(spec)
    if m and not Path(spec).expanduser().exists():
        return m.group(1), m.group(2), "", ""
    return None


def fetch(spec: str, log=lambda m: None) -> Repo:
    spec = (spec or "").strip()
    if not spec:
        raise ValueError("Paste a GitHub link")
    local = Path(spec).expanduser()
    if local.is_dir():
        commit = ""
        if (local / ".git").exists() and shutil.which("git"):
            try:
                commit = _git(["rev-parse", "HEAD"], cwd=local)
            except Exception:
                pass
        return Repo(spec, local.resolve(), commit=commit, label=local.name)
    p = parse(spec)
    if not p:
        raise ValueError(f"'{spec}' is not a GitHub link (https://github.com/owner/repo)")
    owner, name, ref, sub = p
    if not shutil.which("git"):
        raise RuntimeError("git is required to download GitHub repos")
    url = f"https://github.com/{owner}/{name}.git"
    dest = paths.EXAMPLES / f"{owner}__{name}" / (re.sub(r"[^\w.-]", "_", ref) or "_default")
    try:
        if (dest / ".git").exists():
            log(f"Updating {owner}/{name}{'@' + ref if ref else ''} in examples/")
            _git(["fetch", "--depth", "1", "origin", ref or "HEAD"], cwd=dest)
            _git(["reset", "--hard", "FETCH_HEAD"], cwd=dest)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            log(f"Cloning {owner}/{name}{'@' + ref if ref else ''} into examples/")
            if ref and not re.fullmatch(r"[0-9a-f]{7,40}", ref):
                _git(["clone", "--depth", "1", "--branch", ref, url, str(dest)])
            else:
                _git(["clone", "--depth", "1" if not ref else "50", url, str(dest)])
                if ref:
                    _git(["checkout", ref], cwd=dest)
    except RuntimeError as e:
        raise RuntimeError(f"Could not download {owner}/{name}: {e}. Check the link and that the repo is public.")
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Downloading {owner}/{name} timed out")
    commit = _git(["rev-parse", "HEAD"], cwd=dest)
    path = dest / sub if sub else dest
    if not path.is_dir():
        raise RuntimeError(f"Folder '{sub}' not found in {owner}/{name}")
    return Repo(spec, path, url=f"https://github.com/{owner}/{name}", ref=ref, commit=commit,
                label=f"{owner}/{name}" + (f"/{sub}" if sub else ""), sub=sub)
