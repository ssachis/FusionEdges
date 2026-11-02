"""Open the result in a real browser, the way a user would, and report what only a user would notice.

The build catches import and syntax errors. It does not catch a page that throws when you scroll, a navbar whose
links are hidden on desktop, or a component that renders unstyled. This module serves the built site, opens it
in headless Chromium at desktop (1280px) and phone (390px) width, scrolls, and records:
  - the component (navbar/header, footer, ...) and how many of its links are visible;
  - page errors and console errors at load and while scrolling;
  - whether the page scrolls sideways on a phone;
  - a screenshot per width.

Errors that the ORIGINAL site already throws (built once per commit and cached) do not count against a run.
Needs Python Playwright (`pip install playwright && python -m playwright install chromium`); without it the
check is skipped and says so.
"""
from __future__ import annotations

import functools
import hashlib
import http.server
import json
import shutil
import socketserver
import tempfile
import threading
from pathlib import Path

VIEWPORTS = {"desktop": (1280, 800), "mobile": (390, 844)}
NETWORK_NOISE = ("Failed to load resource", "net::ERR_")   # third-party CDNs etc. are not the agent's doing

ALIASES = {"nav": "navbar", "menu": "navbar", "topbar": "navbar", "navigation": "navbar", "appbar": "navbar"}
ROLE_SELECTORS = {
    "navbar": 'header, nav, [class*="navbar"], [class*="Navbar"], [class*="menubar"], [class*="header"]',
    "header": 'header, nav, [class*="header"]',
    "footer": 'footer, [class*="footer"], [class*="Footer"]',
}

PROBE = r"""
([selector, top]) => {
  const vis = el => { const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' && +s.opacity !== 0; };
  let els = selector ? [...document.querySelectorAll(selector)].filter(vis) : [];
  els = els.filter(el => top ? el.getBoundingClientRect().top + scrollY < 160
                             : el.getBoundingClientRect().bottom + scrollY > document.body.scrollHeight - 400);
  // the navbar is the element that holds the most visible links; among equals the smallest (innermost) one, so a
  // tall hero <header> that merely contains the nav is not mistaken for it (browser check v2, 1 Nov)
  const nlinks = el => [...el.querySelectorAll('a, button')].filter(vis).length;
  const area = el => { const q = el.getBoundingClientRect(); return q.width * q.height; };
  const ranked = els.map(el => [el, nlinks(el), area(el)]);
  const most = ranked.reduce((m, x) => Math.max(m, x[1]), 0);
  const bar = most ? ranked.filter(x => x[1] === most).sort((a, b) => a[2] - b[2])[0][0]
                   : els.sort((a, b) => b.getBoundingClientRect().width - a.getBoundingClientRect().width)[0];
  const r = bar ? bar.getBoundingClientRect() : null;
  const links = bar ? [...bar.querySelectorAll('a, button')].filter(vis)
    .map(a => (a.innerText || a.getAttribute('aria-label') || '').trim().replace(/\s+/g, ' ')).filter(Boolean) : [];
  // links the navbar contains but does not show (display:none, zero size): fine on a phone (collapsed menu),
  // a bug on desktop when they outnumber the visible ones
  const hidden = bar ? [...bar.querySelectorAll('a')].filter(a => !vis(a))
    .map(a => (a.textContent || a.getAttribute('aria-label') || '').trim().replace(/\s+/g, ' ')).filter(Boolean) : [];
  return {
    found: !!bar,
    hidden: [...new Set(hidden)].slice(0, 30),
    tag: bar ? bar.tagName.toLowerCase() : null,
    classes: bar ? String(bar.className || '').slice(0, 160) : null,
    height: r ? Math.round(r.height) : null,
    width: r ? Math.round(r.width) : null,
    links: [...new Set(links)].slice(0, 30),
    sideways: document.documentElement.scrollWidth > innerWidth + 2,
    overflow_px: Math.max(0, document.documentElement.scrollWidth - innerWidth),
    text: (document.body.innerText || '').trim().length,
  };
}
"""


def available() -> tuple:
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        return False, "Playwright is not installed: pip install playwright && python -m playwright install chromium"
    return True, ""


def site_root(project: Path):
    """The folder to serve: a built site (build/, dist/, out/) or, for a plain HTML project, the project itself."""
    from .verify import site_dir
    project = Path(project)
    d = site_dir(project)
    if d:
        return d
    if (project / "index.html").exists() and not (project / "package.json").exists():
        return project
    return None


class _Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send_head(self):   # single-page apps: unknown paths fall back to index.html
        path = Path(self.translate_path(self.path))
        if not path.exists() and "." not in Path(self.path.split("?")[0]).name:
            self.path = "/index.html"
        return super().send_head()


def _serve(root: Path):
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), functools.partial(_Handler, directory=str(root)))
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}/"


def _errors(lst):
    return [e for e in lst if not any(n in e for n in NETWORK_NOISE)]


def inspect(root: Path, component: str = "navbar", shots: Path | None = None, wait_ms: int = 2500, contract: dict | None = None) -> dict:
    """Open the site at both widths. Returns {viewport: {...}} plus 'skipped' when it could not run."""
    ok, why = available()
    if not ok:
        return {"skipped": True, "reason": why}
    from playwright.sync_api import sync_playwright
    kw = (component or "").lower().strip()
    selector = ROLE_SELECTORS.get(ALIASES.get(kw, kw))     # None: an unknown component, only errors are checked
    top = kw != "footer"
    httpd, url = _serve(Path(root))
    out = {"skipped": False, "url": url, "component": kw, "viewports": {}}
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            for vp, (w, h) in VIEWPORTS.items():
                ctx = browser.new_context(viewport={"width": w, "height": h})
                page = ctx.new_page()
                errs = {"load": [], "scroll": []}
                phase = ["load"]
                page.on("pageerror", lambda e: errs[phase[0]].append("pageerror: " + str(e).split("\n")[0][:200]))
                page.on("console", lambda m: errs[phase[0]].append("console: " + m.text.split("\n")[0][:200])
                        if m.type == "error" else None)
                try:
                    page.goto(url, wait_until="networkidle", timeout=30000)
                except Exception as e:      # noqa: BLE001 - a page that never settles is still checked
                    errs["load"].append("navigation: " + str(e).split("\n")[0][:200])
                page.wait_for_timeout(wait_ms)
                probe = page.evaluate(PROBE, [selector, top])
                if contract:
                    from .contracts import evaluate
                    probe["contract_problems"] = evaluate(page, contract, shots=shots / vp if shots else None)
                shot = None
                if shots:
                    shots.mkdir(parents=True, exist_ok=True)
                    shot = shots / f"{vp}.png"
                    if contract and page.locator(contract["selector"]).count() == 1:
                        target = page.locator(contract["selector"])
                        if target.is_visible():
                            target.screenshot(path=str(shots / f"{vp}-component.png"))
                    page.screenshot(path=str(shots / f"{vp}-full.png"), full_page=True)
                    page.evaluate("window.scrollTo(0,0)")
                    page.screenshot(path=str(shot))
                phase[0] = "scroll"
                for dy in (300, 900, -5000):
                    page.mouse.wheel(0, dy)
                    page.wait_for_timeout(400)
                out["viewports"][vp] = {**probe, "errors_load": _errors(errs["load"]), "errors_scroll": _errors(errs["scroll"]),
                                        "network_errors": len(errs["load"]) + len(errs["scroll"])
                                        - len(_errors(errs["load"])) - len(_errors(errs["scroll"])),
                                        "screenshot": str(shot) if shot else None,
                                        "screenshot_sha256": hashlib.sha256(shot.read_bytes()).hexdigest() if shot else None}
                ctx.close()
            browser.close()
    except Exception as e:      # noqa: BLE001
        return {"skipped": True, "reason": f"browser check could not run: {str(e).splitlines()[0][:200]}"}
    finally:
        httpd.shutdown()
        httpd.server_close()
    return out


def _norm(e: str) -> str:
    return e.split(":", 1)[-1].strip().lower()[:120]


def problems(result: dict, baseline: dict | None = None, component: str = "navbar") -> list:
    """What a user would notice, in plain words. Errors the original site already had are ignored."""
    if not result or result.get("skipped"):
        return []
    known = set()
    for v in ((baseline or {}).get("viewports") or {}).values():
        known |= {_norm(e) for e in v.get("errors_load", []) + v.get("errors_scroll", [])}
    out = []
    name = component or "component"
    vps = result.get("viewports", {})
    for vp, v in vps.items():
        label = "desktop (1280px)" if vp == "desktop" else "phone (390px)"
        out.extend(f"On {label}: {p}" for p in v.get("contract_problems", []))
        if v.get("text", 0) < 20:
            out.append(f"On {label} the page is blank (almost no text rendered).")
            continue
        if ALIASES.get(result.get("component"), result.get("component")) in ROLE_SELECTORS:
            if not v.get("found"):
                out.append(f"On {label} no {name} is visible.")
            elif result.get("component") != "footer" and vp == "desktop" and len(v.get("links", [])) < 2:
                out.append(f"On {label} the {name} shows only {len(v.get('links', []))} link(s) "
                           f"({', '.join(v.get('links', [])) or 'none'}): its links are probably hidden.")
            elif result.get("component") != "footer" and vp == "desktop" and len(v.get("hidden", [])) >= 2 and len(v.get("hidden", [])) >= len(v.get("links", [])):
                out.append(f"On {label} {len(v['hidden'])} of the {name}'s links are hidden "
                           f"({', '.join(v['hidden'][:5])}); only {', '.join(v.get('links', [])) or 'nothing'} is visible.")
            elif result.get("component") != "footer" and vp == "mobile" and not v.get("links"):
                out.append(f"On {label} the {name} has no visible link or menu button.")
            if result.get("component") != "footer" and v.get("found") and (v.get("height") or 0) > 160:
                out.append(f"On {label} the {name} is {v['height']}px tall: it is probably unstyled.")
        base_vp = ((baseline or {}).get("viewports") or {}).get(vp) or {}
        if vp == "mobile" and v.get("sideways") and not base_vp.get("sideways"):   # the original site may already do this
            out.append(f"On {label} the page scrolls sideways (something is wider than the screen).")
        elif (vp == "mobile" and v.get("sideways") and base_vp.get("sideways")
              and v.get("overflow_px") is not None and base_vp.get("overflow_px") is not None
              and v["overflow_px"] > base_vp["overflow_px"] + 4):
            out.append(f"On {label} horizontal overflow increased from {base_vp['overflow_px']}px to {v['overflow_px']}px.")
        for phase, key in (("when the page loads", "errors_load"), ("while scrolling", "errors_scroll")):
            new = [e for e in v.get(key, []) if _norm(e) not in known]
            if new:
                out.append(f"On {label} the page throws {phase}: {new[0]}" + (f" (+{len(new) - 1} more)" if len(new) > 1 else ""))
    seen, uniq = set(), []
    for p in out:       # the same error at both widths is one problem
        k = p.split(": ", 1)[-1] if "throws" in p else p
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    return uniq


def baseline(repo: dict, component: str, log=print) -> dict | None:
    """Browser result for the ORIGINAL site at the run's commit, built once and cached in examples/.browser/."""
    from . import paths
    from .verify import run_build
    commit = repo.get("commit") or ""
    if not commit:
        return None
    cache = paths.EXAMPLES / ".browser" / f"{(repo.get('label') or 'site').replace('/', '__')}-{commit[:12]}-{component}.json"
    if cache.exists():
        try:
            return json.loads(cache.read_text())
        except ValueError:
            pass
    ok, _ = available()
    if not ok:
        return None
    tmp = Path(tempfile.mkdtemp(prefix="fe-base-"))
    try:
        from .rescore import _export
        site = _export(repo, tmp / "site")
        if site is None:
            return None
        if site != tmp / "site":
            shutil.copytree(site, tmp / "site", dirs_exist_ok=True, ignore=shutil.ignore_patterns("node_modules", ".git"))
            site = tmp / "site"
        log("[browser] building the original site once, to know which errors it already has")
        b = run_build(site)
        root = site_root(site)
        if b.get("ok") is False or root is None:
            return None
        res = inspect(root, component, shots=None, wait_ms=2000)
        if not res.get("skipped"):
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(res))
        return res
    except Exception as e:      # noqa: BLE001
        log(f"[browser] could not check the original site ({e})")
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def check(project: Path, component: str, shots: Path | None = None, base: dict | None = None, contract: dict | None = None) -> dict:
    """inspect + problems, for the pipeline. Returns {'skipped', 'reason'} or the result with 'problems' and 'pass'."""
    root = site_root(Path(project))
    if root is None:
        return {"skipped": True, "reason": "no built site to open (no build/, dist/ or out/ with index.html)"}
    res = inspect(root, component, shots, contract=contract)
    if res.get("skipped"):
        return res
    res["problems"] = problems(res, base, component)
    res["pass"] = not res["problems"]
    res["baseline_used"] = bool(base)
    return res
