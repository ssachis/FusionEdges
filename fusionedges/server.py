"""Local web UI. Run: python3 -m fusionedges.server   (http://127.0.0.1:8000)"""
from __future__ import annotations

import argparse
import html
import json
import mimetypes
import platform
import shutil
import subprocess
import threading
import time
import traceback
import urllib.parse
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import llm, paths, preview, store
from .pipeline import run
from .prompts import default_prompt

STATIC = Path(__file__).resolve().parent / "static"
JOBS: dict = {}
RUN_LOCK = threading.Lock()  # one run at a time (shared provider limits, npm installs)


def _stages(mode):
    s = [("fetch", "Clone both repos"), ("scan", "Scan both codebases")]
    if mode == "report":
        s.append(("report", "Retrieve code + write compatibility report"))
    return s + [("agent", "AI writes the code (+ fixes build errors)"), ("check", "Build + check the result")]


class Job:
    def __init__(self, mode):
        self.id = uuid.uuid4().hex[:10]
        self.status, self.error, self.record, self.run_id = "queued", None, None, None
        self.stages = [{"id": i, "label": l, "state": "pending", "note": ""} for i, l in _stages(mode)]
        self.log = []

    def public(self):
        rec = self.record
        if rec is None and self.run_id:
            f = paths.OUTPUT / self.run_id / "run.json"
            if f.exists():
                try:
                    rec = json.loads(f.read_text())
                except json.JSONDecodeError:
                    rec = None
        return {"id": self.id, "status": self.status, "error": self.error, "stages": self.stages,
                "log": self.log[-500:], "record": rec}


def _worker(job: Job, params: dict):
    def log(m):
        job.log.append(f"{time.strftime('%H:%M:%S')} {m}")

    def stage(sid, state, note=""):
        for s in job.stages:
            if s["id"] == sid:
                s["state"], s["note"] = state, note

    with RUN_LOCK:
        job.status = "running"
        job.run_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{params['mode']}"
        try:
            job.record = run(params, log, stage, run_id=job.run_id)
            job.status = "done"
        except Exception as e:
            job.status, job.error = "error", str(e)
            for s in job.stages:
                if s["state"] == "running":
                    s["state"], s["note"] = "failed", str(e)[:160]
            log("error: " + str(e))
            traceback.print_exc()


def _under(base: Path, rel: str):
    base = base.resolve()
    t = (base / urllib.parse.unquote(rel)).resolve()
    return t if (t == base or base in t.parents) else None


def _listing(url_path: str, folder: Path) -> bytes:
    rows = ['<li><a href="../">../</a></li>'] if url_path.rstrip("/").count("/") > 2 else []
    for p in sorted(folder.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
        if p.name in ("node_modules", ".git", ".next"):
            continue
        n = p.name + ("/" if p.is_dir() else "")
        rows.append(f'<li><a href="{urllib.parse.quote(n)}">{html.escape(n)}</a></li>')
    return (f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
            f"<title>{html.escape(url_path)}</title><style>body{{font:14px/1.7 system-ui;margin:24px;max-width:900px}}"
            f"a{{text-decoration:none}}code{{background:#8881;padding:2px 6px;border-radius:4px}}</style>"
            f"<p><a href='/'>← FusionEdges</a></p><h2>{html.escape(url_path)}</h2>"
            f"<p>On your computer: <code>{html.escape(str(folder))}</code></p><ul>{''.join(rows)}</ul>").encode()


def status():
    return {"providers": llm.status(), "configured": llm.configured(), "npm": bool(shutil.which("npm")),
            "git": bool(shutil.which("git")), "output": str(paths.OUTPUT), "examples": str(paths.EXAMPLES),
            "prompts": {"both": default_prompt(True, True), "migration": default_prompt(True, False),
                        "style": default_prompt(False, True), "component": default_prompt(False, False, "{name}")},
            "preview": preview.status()}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body: bytes, ctype: str, code=200, extra=None):
        self.send_response(code)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(body)))
        self.send_header("cache-control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(json.dumps(obj, default=str).encode(), "application/json", code)

    def _file(self, p: Path, download=False):
        if not p or not p.is_file():
            return self.send_error(404)
        ctype = mimetypes.guess_type(p.name)[0] or "text/plain; charset=utf-8"
        if p.suffix in (".diff", ".log", ".md", ".jsx", ".tsx", ".ts", ".mjs", ".cjs", ".json", ".css", ".js") \
                and "/output/" in self.path:
            ctype = "text/plain; charset=utf-8"  # browsing code shows it as text
        self._send(p.read_bytes(), ctype, extra={"content-disposition": f'attachment; filename="{p.name}"'} if download else None)

    def do_GET(self):
        p = urllib.parse.urlparse(self.path).path
        if p in ("/", "/index.html"):
            return self._file(STATIC / "index.html")
        if p == "/api/status":
            return self._json(status())
        if p == "/api/history":
            return self._json({"runs": store.history()})
        if p.startswith("/api/job/"):
            j = JOBS.get(p.rsplit("/", 1)[1])
            return self._json(j.public()) if j else self._json({"error": "unknown job"}, 404)
        if p.startswith("/api/run/"):
            r = store.load(p.rsplit("/", 1)[1])
            return self._json(r) if r else self._json({"error": "unknown run"}, 404)
        if p.startswith("/output/"):
            t = _under(paths.OUTPUT, p[len("/output/"):])
            if t is None:
                return self.send_error(403)
            if t.is_dir():
                if not p.endswith("/"):
                    return self._send(b"", "text/plain", 301, {"location": p + "/"})
                return self._send(_listing(p, t), "text/html; charset=utf-8")
            return self._file(t, download=t.suffix == ".zip")
        if p.startswith("/site/"):  # static HTML output, served as a website
            parts = p.split("/", 3)
            if len(parts) < 3:
                return self.send_error(404)
            base = _under(paths.OUTPUT, f"{parts[2]}/project")
            if base is None or not base.is_dir():
                return self.send_error(404)
            if len(parts) == 3:
                return self._send(b"", "text/plain", 301, {"location": p + "/"})
            t = _under(base, parts[3] or "index.html")
            if t is not None and t.is_dir():
                t = t / "index.html"
            return self._file(t)
        self.send_error(404)

    def do_POST(self):
        n = int(self.headers.get("content-length", 0) or 0)
        try:
            params = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return self._json({"error": "bad json"}, 400)
        if self.path == "/api/run":
            if not (params.get("source") and params.get("donor")):
                return self._json({"error": "Paste both GitHub links"}, 400)
            if params.get("add_component") and not (params.get("component") or "").strip():
                return self._json({"error": "Say which component to add (e.g. navbar)"}, 400)
            if not (params.get("migration") or params.get("style") or params.get("add_component")):
                return self._json({"error": "Pick codebase migration, style transfer, or add a component"}, 400)
            if not (params.get("prompt") or "").strip():
                return self._json({"error": "The prompt is empty"}, 400)
            if not llm.configured():
                return self._json({"error": "No AI key is set. Export GROQ_API_KEY, GEMINI_API_KEY, ANTHROPIC_API_KEY "
                                            "or OPENAI_API_KEY in the terminal and restart the server."}, 400)
            params["mode"] = "report" if params.get("mode") == "report" else "prompt"
            job = Job(params["mode"])
            JOBS[job.id] = job
            busy = RUN_LOCK.locked()
            threading.Thread(target=_worker, args=(job, params), daemon=True).start()
            return self._json({"id": job.id, "queued": busy})
        if self.path == "/api/preview":
            rid = str(params.get("run_id", ""))
            folder = _under(paths.OUTPUT, f"{rid}/project")
            if not rid or folder is None or not folder.is_dir():
                return self._json({"error": "unknown run"}, 404)
            res = preview.start(rid, folder)
            if res.get("static"):
                res = {"url": f"/site/{urllib.parse.quote(rid)}/"}
            return self._json(res, 200 if "url" in res else 500)
        if self.path == "/api/open":
            t = _under(paths.OUTPUT, params.get("path", ""))
            if t is None or not t.exists():
                return self._json({"error": "not found"}, 404)
            cmd = {"Darwin": ["open"], "Windows": ["explorer"]}.get(platform.system(), ["xdg-open"])
            try:
                subprocess.Popen(cmd + [str(t)])
                return self._json({"ok": True})
            except OSError as e:
                return self._json({"error": str(e)}, 500)
        self.send_error(404)


def make_server(port=8000):
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-open", action="store_true")
    a = ap.parse_args()
    try:
        n = store.mark_interrupted()
        if n:
            print(f"Marked {n} earlier run(s) that were cut off by a restart as 'interrupted'.")
    except Exception as e:
        print(f"note: could not clean up the run history ({e})")
    srv = make_server(a.port)
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    print(f"FusionEdges running at {url}  (Ctrl+C to stop)")
    print(f"Repos are cloned to {paths.EXAMPLES}; results go to {paths.OUTPUT}")
    chain = llm.configured()
    print("AI providers (fallback order): " + (" -> ".join(chain) if chain else
          "NONE. Set GROQ_API_KEY, GEMINI_API_KEY, ANTHROPIC_API_KEY or OPENAI_API_KEY."))
    if not a.no_open:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        preview.stop()


if __name__ == "__main__":
    main()
