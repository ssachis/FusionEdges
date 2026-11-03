"""Start the output project's dev server so the user can click a link and see it.

One dev server at a time (starting a new one stops the previous). Static HTML outputs need no
server: the FusionEdges server serves them directly under /site/<run>/.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

from .verify import ensure_modules

_LOCK = threading.Lock()
MAX_OPEN = 2          # previews kept running side by side; starting a third closes the oldest
_OPEN: dict = {}      # run id -> {"proc", "port", "url", "log"}, oldest first


def _free_port(start=3100, taken=()):
    for port in range(start, start + 100):
        if port in taken:
            continue
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError("no free port for the preview")


def run_script(folder: Path):
    """Name of the npm script that starts the project ('dev' for Vite/Next.js, 'start' for Create React App), or None."""
    try:
        scripts = json.loads((Path(folder) / "package.json").read_text(encoding="utf-8")).get("scripts", {})
    except (OSError, ValueError):
        return None
    return "dev" if "dev" in scripts else ("start" if "start" in scripts else None)


def _listening(port: int) -> bool:
    """True when something accepts connections on this port, on IPv4 or IPv6 (Vite binds ::1 on newer Node)."""
    for fam, addr in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
        try:
            with socket.socket(fam) as s:
                s.settimeout(1)
                if s.connect_ex((addr, port)) == 0:
                    return True
        except OSError:
            continue
    return False


def dev_command(script: str, script_text: str, port: int) -> list:
    """npm command that starts the dev server on `port`. Create React App ignores --port (it reads the
    PORT environment variable, set by start()); Vite, Next.js and most others accept --port."""
    cmd = ["npm", "run", script]
    if "react-scripts" not in script_text and "craco" not in script_text:
        cmd += ["--", "--port", str(port)]
    return cmd


def _alive(entry) -> bool:
    return bool(entry and entry["proc"].poll() is None)


def _stop_one(run_id: str):
    entry = _OPEN.pop(run_id, None)
    p = entry and entry["proc"]
    if p and p.poll() is None:
        p.terminate()
        try:
            p.wait(10)
        except subprocess.TimeoutExpired:
            p.kill()


def stop(run_id: str = None):
    """Stop one preview, or all of them when no run id is given."""
    for rid in ([run_id] if run_id else list(_OPEN)):
        _stop_one(rid)


def status():
    live = {rid: e["url"] for rid, e in _OPEN.items() if _alive(e)}
    last = next(reversed(live), None) if live else None
    return {"run": last, "url": live.get(last), "runs": live}


def start(run_id: str, folder: Path, timeout: int = 180) -> dict:
    folder = Path(folder)
    pkg = folder / "package.json"
    if not pkg.exists():
        return {"static": True}
    scripts = json.loads(pkg.read_text(encoding="utf-8")).get("scripts", {})
    script = run_script(folder)
    if not script:
        return {"error": "package.json has no dev or start script"}
    if not shutil.which("npm"):
        return {"error": "npm is not installed"}
    with _LOCK:
        if _alive(_OPEN.get(run_id)):
            return {"url": _OPEN[run_id]["url"]}
        _OPEN.pop(run_id, None)
        for rid in [r for r, e in _OPEN.items() if not _alive(e)]:
            _OPEN.pop(rid, None)
        while len(_OPEN) >= MAX_OPEN:
            _stop_one(next(iter(_OPEN)))      # the oldest one makes room
        inst = ensure_modules(folder)
        if not inst["ok"]:
            return {"error": "npm install failed:\n" + inst["output"][-1500:]}
        taken = {e["port"] for e in _OPEN.values()}
        port = _free_port(taken=taken)
        logfile = folder.parent / "preview.log"
        log = open(logfile, "w")
        # PORT: read by Create React App and Next.js; BROWSER=none: do not open a second tab (the UI opens one)
        env = dict(os.environ, PORT=str(port), BROWSER="none")
        proc = subprocess.Popen(dev_command(script, scripts.get(script, ""), port), cwd=folder, env=env,
                                stdout=log, stderr=subprocess.STDOUT)
        url = f"http://localhost:{port}"
        _OPEN[run_id] = {"proc": proc, "port": port, "url": url, "log": str(logfile)}
    t0 = time.time()
    while time.time() - t0 < timeout:
        if proc.poll() is not None:
            _OPEN.pop(run_id, None)
            return {"error": "dev server exited:\n" + Path(logfile).read_text(errors="replace")[-1500:]}
        if _listening(port):
            return {"url": url}
        time.sleep(1)
    return {"url": url, "note": "still starting; refresh in a moment"}
