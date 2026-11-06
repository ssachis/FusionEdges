"""Study 1 readiness check (no AI): every pinned host builds, renders, and shows its preservation text once;
every donor file still matches its pinned hash. Writes experiments/study-1/readiness.json.

    python3 experiments/study1_ready.py experiments/study-1-navbar.json [workdir]
"""
import hashlib, http.server, json, os, socketserver, subprocess, sys, threading, time
from pathlib import Path

spec_path = Path(sys.argv[1])
work = Path(sys.argv[2] if len(sys.argv) > 2 else "/tmp/fe-study1-ready")
work.mkdir(parents=True, exist_ok=True)
spec = json.loads(spec_path.read_text())
out_path = spec_path.parent / "study-1" / "readiness.json"
out_path.parent.mkdir(parents=True, exist_ok=True)
result = json.loads(out_path.read_text()) if out_path.exists() else {"hosts": {}, "donors": {}}


def sh(cmd, cwd=None, timeout=900, env=None):
    r = subprocess.run(cmd, cwd=cwd, shell=True, capture_output=True, text=True, timeout=timeout,
                       env={**os.environ, **(env or {})})
    return r.returncode, (r.stdout + r.stderr)[-1500:]


def checkout(url):
    repo, sha = url.split("github.com/")[1].split("/tree/")
    d = work / repo.replace("/", "__")
    if not (d / ".git").is_dir():
        sh(f"git clone -q --filter=blob:none https://github.com/{repo} {d}", timeout=600)
    sh(f"git fetch -q --depth 1 origin {sha}", cwd=d)
    code, msg = sh(f"git checkout -q -f {sha}", cwd=d)
    return d, code == 0, msg


def serve(root):
    class H(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k): super().__init__(*a, directory=str(root), **k)
        def log_message(self, *a): pass
        def send_head(self):
            p = Path(self.translate_path(self.path))
            if not p.exists():
                self.path = "/index.html"
            return super().send_head()
    s = socketserver.TCPServer(("127.0.0.1", 0), H)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s


def check_page(port, contract):
    from playwright.sync_api import sync_playwright
    found = []
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        for vp in ((1280, 800), (390, 844)):
            pg = b.new_page(viewport={"width": vp[0], "height": vp[1]})
            errors = []
            pg.on("pageerror", lambda e: errors.append(str(e)[:200]))
            pg.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle", timeout=45000)
            time.sleep(1.5)
            row = {"viewport": vp[0], "errors": errors[:3], "preserve": []}
            for p in contract["preserve"]:
                loc = pg.locator(p["selector"])
                n = loc.count()
                row["preserve"].append({"selector": p["selector"], "count": n,
                                        "visible": bool(n == 1 and loc.is_visible())})
            row["has_nav"] = pg.locator("nav, header").count()
            found.append(row)
        b.close()
    return found


hosts = {}
for t in spec["tasks"]:
    hosts.setdefault(t["source"], t)
for url, t in hosts.items():
    if result["hosts"].get(url, {}).get("ok") is not None and "--redo" not in sys.argv:
        continue
    rec = {"build_tool": t.get("host_build")}
    print("host", url.split("github.com/")[1][:60], flush=True)
    d, ok, msg = checkout(url)
    if not ok:
        rec.update(ok=False, why="checkout: " + msg[-300:]); result["hosts"][url] = rec; continue
    rec["nav_file_exists"] = (d / t["host_nav_file"]).is_file()
    code, msg = sh("npm install --no-audit --no-fund --loglevel=error", cwd=d, timeout=1200)
    if code:
        code, msg = sh("npm install --no-audit --no-fund --loglevel=error --legacy-peer-deps", cwd=d, timeout=1200)
        rec["legacy_peer_deps"] = True
    if code:
        rec.update(ok=False, why="install: " + msg[-400:]); result["hosts"][url] = rec; continue
    code, msg = sh("npm run build", cwd=d, timeout=900, env={"CI": "false", "DISABLE_ESLINT_PLUGIN": "true"})
    if code:
        rec.update(ok=False, why="build: " + msg[-400:]); result["hosts"][url] = rec; continue
    root = next((d / x for x in ("dist", "build") if (d / x / "index.html").is_file()), None)
    if not root:
        rec.update(ok=False, why="no dist/ or build/ index.html"); result["hosts"][url] = rec; continue
    s = serve(root)
    try:
        rec["page"] = check_page(s.server_address[1], t["acceptance"])
    except Exception as e:
        rec.update(ok=False, why="browser: " + str(e)[:300]); result["hosts"][url] = rec; continue
    finally:
        s.shutdown()
    desk = rec["page"][0]
    rec["ok"] = bool(rec["nav_file_exists"] and all(p["count"] == 1 and p["visible"] for p in desk["preserve"]))
    result["hosts"][url] = rec
    out_path.write_text(json.dumps(result, indent=1))
    print("   ", "OK" if rec["ok"] else "PROBLEM", [(p["count"], p["visible"]) for p in desk["preserve"]], desk["errors"][:1], flush=True)

for t in spec["tasks"]:
    if t["donor"] in result["donors"]:
        continue
    d, ok, msg = checkout(t["donor"])
    f = d / t["donor_component_file"]
    same = f.is_file() and hashlib.sha256(f.read_bytes()).hexdigest() == t["donor_file_sha256"]
    result["donors"][t["donor"]] = {"ok": bool(ok and same), "file_matches": same}
out_path.write_text(json.dumps(result, indent=1))
bad = [u for u, r in result["hosts"].items() if not r.get("ok")] + [u for u, r in result["donors"].items() if not r.get("ok")]
print(f"\nhosts ok {sum(1 for r in result['hosts'].values() if r.get('ok'))}/{len(result['hosts'])}, "
      f"donors ok {sum(1 for r in result['donors'].values() if r.get('ok'))}/{len(result['donors'])}")
for u in bad:
    print("  needs attention:", u, (result["hosts"].get(u) or result["donors"].get(u)).get("why", ""))
