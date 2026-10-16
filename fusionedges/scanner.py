"""Deterministic fact scanner (no LLM).

Collects facts about the donor and host that commonly break a transplant, each with
an id (F1, F2, ...) and file/line evidence, so the report agent can cite them and
we can check its citations. Facts are things code can prove; judgment is left to
the report agent.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .analyzer import SKIP_DIRS, analyze

CODE_EXTS = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".vue", ".svelte", ".astro"}
STYLE_EXTS = {".css", ".scss", ".sass", ".less"}
TEXT_EXTS = CODE_EXTS | STYLE_EXTS | {".html", ".htm", ".json", ".md"}
MAX_FILES = 4000


# ------------------------------------------------------------------ helpers
def walk(root: Path, exts=None):
    n = 0
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if set(rel.parts) & (SKIP_DIRS | {"coverage", ".vercel", ".turbo", ".cache", "out"}):
            continue
        if not p.is_file() or (exts and p.suffix.lower() not in exts):
            continue
        n += 1
        if n > MAX_FILES:
            return
        yield p


def read(p: Path, limit=400_000) -> str:
    try:
        if p.stat().st_size > limit:
            return ""
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _strip_json_comments(text: str) -> str:
    out, i, n, in_str = [], 0, len(text), False
    while i < n:
        ch = text[i]
        if in_str:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
            out.append(ch)
        elif text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def _json_loose(text: str):
    """tsconfig/jsconfig allow comments and trailing commas (string-aware, so "@/*" survives)."""
    text = re.sub(r",\s*([}\]])", r"\1", _strip_json_comments(text))
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}


def _pkg_line(root: Path, key: str) -> int:
    for i, line in enumerate(read(root / "package.json").splitlines(), 1):
        if f'"{key}"' in line:
            return i
    return 1


def _line_of(text: str, idx: int) -> int:
    return text.count("\n", 0, idx) + 1


def _major(v: str):
    m = re.search(r"(\d+)", v or "")
    return int(m.group(1)) if m else None


def _find(root: Path, pattern: str, exts=CODE_EXTS, flags=0, limit=8):
    """[(relpath, line, snippet, match)] for a regex across files."""
    out = []
    rx = re.compile(pattern, flags)
    for p in walk(root, exts):
        t = read(p)
        for m in rx.finditer(t):
            ln = _line_of(t, m.start())
            snippet = t.splitlines()[ln - 1].strip()[:160] if t else ""
            out.append((p.relative_to(root).as_posix(), ln, snippet, m))
            if len(out) >= limit * 20:
                return out
    return out


# ------------------------------------------------------------------ profile
def profile(root: Path) -> dict:
    pkg_path = root / "package.json"
    pkg = _json_loose(read(pkg_path)) if pkg_path.exists() else {}
    deps = {**pkg.get("devDependencies", {}), **pkg.get("dependencies", {})}
    has = lambda *names: any(n in deps for n in names)  # noqa: E731

    def exists(*rels):
        return [r for r in rels if (root / r).exists()]

    if has("next"):
        app_dirs = [d for d in ("app", "src/app") if (root / d).is_dir() and list((root / d).glob("layout.*"))]
        pages_dirs = exists("pages", "src/pages")
        framework = "next"
        router = "next-app" if app_dirs else ("next-pages" if pages_dirs else "next")
    elif has("@remix-run/react", "@remix-run/node"):
        framework, router = "remix", "remix"
    elif has("astro"):
        framework, router = "astro", "astro"
    elif has("vue", "nuxt"):
        framework, router = ("nuxt" if has("nuxt") else "vue"), ("vue-router" if has("vue-router") else "")
    elif has("svelte", "@sveltejs/kit"):
        framework, router = "svelte", "sveltekit" if has("@sveltejs/kit") else ""
    elif has("@angular/core"):
        framework, router = "angular", "angular"
    elif has("react"):
        framework = "react-vite" if has("vite") else ("react-cra" if has("react-scripts") else "react")
        router = ("react-router-%s" % (_major(deps.get("react-router-dom") or deps.get("react-router")) or "")
                  if has("react-router-dom", "react-router") else "")
    else:
        framework, router = "html", "static-files"

    ts = bool(exists("tsconfig.json")) or any(True for _ in walk(root, {".ts", ".tsx"}))
    tsconf = _json_loose(read(root / "tsconfig.json")) if exists("tsconfig.json") else \
        (_json_loose(read(root / "jsconfig.json")) if exists("jsconfig.json") else {})
    aliases = sorted((tsconf.get("compilerOptions", {}) or {}).get("paths", {}) or {})
    for vc in exists("vite.config.js", "vite.config.ts", "vite.config.mjs"):
        aliases += [a for a in re.findall(r"""['"](@[\w/-]*|~[\w/-]*)['"]\s*:""", read(root / vc)) if a not in aliases]

    styling, tw = [], {}
    tw_cfg = exists("tailwind.config.js", "tailwind.config.ts", "tailwind.config.cjs", "tailwind.config.mjs")
    css_v4 = _find(root, r"""@import\s+["']tailwindcss["']|@theme\b""", STYLE_EXTS, limit=2)
    if has("tailwindcss") or tw_cfg or css_v4:
        styling.append("tailwind")
        major = _major(deps.get("tailwindcss", "")) or (4 if css_v4 and not tw_cfg else None)
        custom = []
        for c in tw_cfg:
            t = read(root / c)
            m = re.search(r"extend\s*:\s*\{(.*)", t, re.S)
            if m:
                custom = sorted(set(re.findall(r"\b(colors|fontFamily|spacing|screens|borderRadius|boxShadow|fontSize|animation|keyframes)\s*:", m.group(1))))
        tw = {"major": major, "config": tw_cfg[0] if tw_cfg else ("css (@theme)" if css_v4 else ""),
              "custom_theme_keys": custom}
    files = [p.relative_to(root).as_posix() for p in walk(root, STYLE_EXTS)]
    if any(f.endswith((".scss", ".sass")) for f in files) or has("sass", "node-sass"):
        styling.append("sass")
    if any(f.endswith(".less") for f in files):
        styling.append("less")
    if any(".module." in f for f in files):
        styling.append("css-modules")
    if any(f.endswith(".css") and ".module." not in f for f in files):
        styling.append("plain-css")
    for dep, name in (("styled-components", "styled-components"), ("@emotion/react", "emotion"),
                      ("bootstrap", "bootstrap"), ("react-bootstrap", "bootstrap"), ("@mui/material", "mui"),
                      ("@chakra-ui/react", "chakra"), ("antd", "antd")):
        if dep in deps and name not in styling:
            styling.append(name)
    if "bootstrap" not in styling and _find(root, r"bootstrap(\.min)?\.css", {".html", ".htm"}, limit=1):
        styling.append("bootstrap")

    env = {}
    for rel, ln, snip, m in _find(root, r"\b(?:process\.env|import\.meta\.env)\.([A-Z_][A-Z0-9_]*)"):
        if m.group(1) not in ("NODE_ENV", "MODE", "DEV", "PROD", "BASE_URL", "SSR"):
            env.setdefault(m.group(1), (rel, ln, snip))
    for ef in exists(".env.example", ".env.sample", ".env.local.example", ".env"):
        for i, line in enumerate(read(root / ef).splitlines(), 1):
            mm = re.match(r"\s*([A-Z_][A-Z0-9_]*)\s*=", line)
            if mm:
                env.setdefault(mm.group(1), (ef, i, line.split("=")[0]))

    return {
        "framework": framework, "router": router, "typescript": ts,
        "module_type": pkg.get("type", "commonjs") if pkg else "",
        "versions": {k: deps[k] for k in ("react", "react-dom", "next", "vite", "react-router-dom",
                                          "tailwindcss", "typescript", "vue", "svelte") if k in deps},
        "deps": dict(sorted(deps.items())), "scripts": pkg.get("scripts", {}),
        "node_engine": (pkg.get("engines") or {}).get("node", ""),
        "aliases": aliases, "styling": styling, "tailwind": tw, "env": env,
        "entry_files": exists("index.html", "src/main.jsx", "src/main.tsx", "src/index.js", "src/index.jsx",
                              "src/index.tsx", "src/App.jsx", "src/App.tsx", "app/layout.tsx", "app/layout.jsx",
                              "app/layout.js", "src/app/layout.tsx", "src/app/layout.js", "app/page.tsx",
                              "app/page.jsx", "app/page.js", "src/app/page.tsx", "pages/_app.js",
                              "pages/_app.tsx", "pages/index.js", "pages/index.tsx", "src/pages/_app.tsx",
                              "next.config.js", "next.config.mjs", "next.config.ts", "vite.config.js",
                              "vite.config.ts", "tailwind.config.js", "tailwind.config.ts", "tsconfig.json",
                              "postcss.config.js", "postcss.config.cjs", "postcss.config.mjs"),
    }


def tree(root: Path, limit=250) -> list:
    out = []
    for p in walk(root):
        out.append(p.relative_to(root).as_posix())
        if len(out) >= limit:
            out.append("... (more files not listed)")
            break
    return out


# ------------------------------------------------------------------ facts
class _Facts:
    def __init__(self):
        self.items = []

    def add(self, kind, severity, text, evidence=(), repo="both"):
        fid = f"F{len(self.items) + 1}"
        self.items.append({"id": fid, "kind": kind, "severity": severity, "repo": repo, "text": text,
                           "evidence": [{"repo": e[0], "path": e[1], "line": e[2], "snippet": e[3] if len(e) > 3 else ""}
                                        for e in evidence]})
        return fid


_SEL_CALL = re.compile(r"""\$\(\s*(["'])(.+?)\1|querySelector(?:All)?\(\s*(["'])(.+?)\3|getElementById\(\s*(["'])(.+?)\5""")
_SEL_TOKEN = re.compile(r"(?<![\w-])([.#])([A-Za-z_][\w-]*)")


def script_selectors(host_root: Path, keyword: str = "") -> dict:
    """{token: (script, line, snippet)} for class and id selectors that the host's own global scripts
    (`<script src>` in index.html, e.g. an old jQuery main.js) look up, and that its source also uses.
    Replacing a component that carries one of these breaks those scripts at runtime, and no build sees it.
    With a `keyword` ("navbar") only the selectors used in the same source files as a selector containing the
    keyword are kept: those files are the component being replaced."""
    host_root = Path(host_root)
    page = next((host_root / r for r in ("public/index.html", "index.html") if (host_root / r).exists()), None)
    if page is None:
        return {}
    html = read(page)
    srcs = [m.group(1) for m in re.finditer(r"""<script[^>]+src=["'](?!https?:|//)([^"']+)["']""", html)]
    files = {p: read(p) for p in walk(host_root / "src", CODE_EXTS)} if (host_root / "src").is_dir() else {}
    src_text = "\n".join(files.values())
    found = {}
    for ref in srcs:
        ref = ref.split("?")[0].replace("%PUBLIC_URL%", "").lstrip("/")
        path = next((host_root / d / ref for d in ("public", "") if (host_root / d / ref).is_file()), None)
        if path is None or "min" in path.name:
            continue
        rel = path.relative_to(host_root).as_posix()
        text = read(path)
        for m in _SEL_CALL.finditer(text):
            sel = m.group(2) or m.group(4) or m.group(6) or ""
            if m.group(6):
                sel = "#" + sel
            ln = _line_of(text, m.start())
            for kind, name in _SEL_TOKEN.findall(sel):
                tok = kind + name
                if tok not in found and re.search(r"(?<![\w-])" + re.escape(name) + r"(?![\w-])", src_text):
                    found[tok] = (rel, ln, text.splitlines()[ln - 1].strip()[:160])
    key = (keyword or "").lower().strip()
    if not key:
        return found
    def used_in(tok):
        rx = re.compile(r"(?<![\w-])" + re.escape(tok[1:]) + r"(?![\w-])")
        return {p for p, t in files.items() if rx.search(t)}
    comp = set()
    for tok in found:
        if key in tok.lower():
            comp |= used_in(tok)
    return {tok: v for tok, v in found.items() if used_in(tok) & comp}


REACTISH = ("react", "react-vite", "react-cra", "next", "remix")


ENV_READ = r"\b(?:process\.env|import\.meta\.env)\.([A-Z_][A-Z0-9_]*)"
ENV_IGNORE = ("NODE_ENV", "MODE", "DEV", "PROD", "BASE_URL", "SSR")


def env_reads(root: Path, files) -> dict:
    """{VAR: (file, line, snippet)} for env vars read in just these files (repo-relative paths)."""
    env = {}
    for rel in sorted(files):
        text = read(Path(root) / rel)
        for m in re.finditer(ENV_READ, text):
            if m.group(1) not in ENV_IGNORE:
                ln = _line_of(text, m.start())
                env.setdefault(m.group(1), (rel, ln, text.splitlines()[ln - 1].strip()[:160]))
    return env


def scan(host_root: Path, donor_root: Path, request: str = "", section: str = "", donor_scope=None) -> dict:
    """`donor_scope`: for "add one component", the donor files that make up the component. Donor env vars are then
    only those the files read; otherwise every variable anywhere in the donor repo becomes a 'conflict'."""
    host_root, donor_root = Path(host_root), Path(donor_root)
    H, D = profile(host_root), profile(donor_root)
    if donor_scope is not None:
        D = {**D, "env": env_reads(donor_root, donor_scope)}
    F = _Facts()
    roots = {"host": host_root, "donor": donor_root}

    def pj(r, key=""):
        ln = _pkg_line(roots[r], key) if key else 1
        snip = read(roots[r] / "package.json").splitlines()[ln - 1].strip() if key and (roots[r] / "package.json").exists() else ""
        return (r, "package.json", ln, snip)

    # framework / rendering model
    hf, df = H["framework"], D["framework"]
    if hf != df:
        sev = "high"
        notes = {
            ("react-vite", "next"): "Donor is a Vite SPA, host is Next.js: components render on the server first; "
                                    "browser-only code and hooks need 'use client' (app router) or dynamic import.",
            ("react-cra", "next"): "Donor is Create React App, host is Next.js: server rendering, file routing, "
                                   "no index.html, env vars need NEXT_PUBLIC_.",
            ("next", "react-vite"): "Donor is Next.js, host is a Vite SPA: next/* imports (Image, Link, router, "
                                    "font) do not exist; server components and data fetching must be rewritten.",
            ("html", "react-vite"): "Donor is static HTML/CSS, host is React: markup must be converted to JSX.",
            ("html", "next"): "Donor is static HTML/CSS, host is Next.js: markup must become JSX; global CSS "
                              "can only be imported from the root layout/_app.",
        }
        text = notes.get((df, hf)) or f"Donor framework is {df}, host is {hf}; code cannot be copied verbatim."
        F.add("framework-mismatch", sev, text,
              [pj(r) for r, root in (("host", host_root), ("donor", donor_root)) if (root / "package.json").exists()])
    real = lambda r: r not in ("", "static-files")  # noqa: E731
    if H["router"] != D["router"] and (real(H["router"]) or (real(D["router"]) and real(H["router"]))):
        F.add("routing-model", "high" if "next" in H["router"] or "react-router" in H["router"] else "medium",
              f"Routing differs: donor uses '{D['router'] or 'none'}', host uses '{H['router'] or 'none'}'. "
              "Links and navigation code must be converted to the host's mechanism "
              "(e.g. <a href> vs <Link>, useNavigate vs next/navigation).")

    # versions of shared packages
    for pkgname in ("react", "react-dom", "next", "react-router-dom", "tailwindcss", "vite", "typescript"):
        a, b = D["versions"].get(pkgname), H["versions"].get(pkgname)
        if a and b and _major(a) != _major(b):
            sev = "high" if pkgname in ("react", "react-router-dom", "tailwindcss", "next") else "medium"
            hint = {"react-router-dom": "v5 uses <Switch>/useHistory, v6+ uses <Routes>/useNavigate.",
                    "tailwindcss": "v4 is configured in CSS (@theme), v3 in tailwind.config.js; several utilities were renamed.",
                    "react": "check for removed APIs (e.g. ReactDOM.render, defaultProps on function components in 19).",
                    "next": "check app vs pages router APIs and next/image, next/link behavior."}.get(pkgname, "")
            F.add("version-mismatch", sev, f"{pkgname} major version differs: donor {a}, host {b}. {hint}".strip(),
                  [pj("donor", pkgname), pj("host", pkgname)])

    # language
    if D["typescript"] and not H["typescript"]:
        F.add("language", "medium", "Donor is TypeScript, host is JavaScript: types must be stripped or TS added to the host.")
    elif H["typescript"] and not D["typescript"] and df != "html":
        F.add("language", "low", "Host is TypeScript, donor is JavaScript: props may need types if the host uses strict mode.")

    # module aliases used by donor but not defined in host
    donor_alias_uses = _find(donor_root, r"""from\s+['"]((?:@|~)/[^'"]+)['"]""")
    if donor_alias_uses:
        prefixes = sorted({m.group(1).split("/")[0] + "/" for _, _, _, m in donor_alias_uses})
        host_has = {a.rstrip("*") for a in H["aliases"]}
        missing = [p for p in prefixes if not any(h.startswith(p.rstrip("/")) for h in host_has)]
        if missing:
            F.add("path-alias", "high", f"Donor imports use path aliases {', '.join(missing)} that the host does not define; "
                  "imports will fail to resolve unless rewritten to relative paths or the alias is added.",
                  [("donor", r, ln, s) for r, ln, s, _ in donor_alias_uses[:4]])

    # styling systems
    hs, ds = set(H["styling"]), set(D["styling"])
    if "tailwind" in ds and "tailwind" not in hs:
        F.add("styling-system", "high", "Donor uses Tailwind utility classes, host has no Tailwind: classes will not resolve "
              "(convert to host CSS, or add Tailwind to the host).", [pj("donor")])
    elif "tailwind" in hs and "tailwind" not in ds:
        F.add("styling-system", "medium", "Host uses Tailwind (Preflight reset removes default margins, list styles, "
              "heading sizes); donor's plain CSS may look different after import.", [pj("host")])
    elif "tailwind" in hs and "tailwind" in ds:
        dt, ht = D["tailwind"], H["tailwind"]
        if dt.get("major") and ht.get("major") and dt["major"] != ht["major"]:
            F.add("styling-system", "high", f"Tailwind v{dt['major']} (donor) vs v{ht['major']} (host): config format "
                  "and some utility names differ.")
        if dt.get("custom_theme_keys"):
            F.add("styling-config", "high", f"Donor's Tailwind config extends {', '.join(dt['custom_theme_keys'])}; "
                  "custom utilities (e.g. bg-brand-500) will not exist in the host unless the theme is merged.",
                  [("donor", dt["config"], 1)] if dt.get("config", "").endswith((".js", ".ts", ".cjs", ".mjs")) else [])
    for s in sorted((ds - hs) - {"tailwind", "plain-css"}):
        F.add("styling-system", "high" if s in ("sass", "less", "styled-components", "emotion", "mui", "chakra") else "medium",
              f"Donor styling uses {s}, which the host does not have.", [pj("donor")])

    # env vars
    host_env = set(H["env"])
    for name, (rel, ln, snip) in sorted(D["env"].items()):
        if name in host_env:
            continue
        extra = ""
        if hf == "next" and not name.startswith("NEXT_PUBLIC_"):
            extra = " In Next.js, only NEXT_PUBLIC_* variables reach browser code."
        elif hf == "react-vite" and not name.startswith("VITE_"):
            extra = " In Vite, only VITE_* variables are exposed via import.meta.env."
        F.add("env-var", "medium", f"Donor reads env var {name}; the host does not define it.{extra}",
              [("donor", rel, ln, snip)])
    if hf != df and D["env"]:
        acc = {"next": "process.env.NEXT_PUBLIC_X", "react-vite": "import.meta.env.VITE_X",
               "react-cra": "process.env.REACT_APP_X"}.get(hf)
        if acc:
            F.add("env-access", "medium", f"Env vars must be read the host's way ({acc}).")

    # next-specific code going into a non-next host, and client-only code going into next
    if hf != "next":
        uses = _find(donor_root, r"""from\s+['"](next/[\w/-]+)['"]""")
        if uses:
            mods = sorted({m.group(1) for *_, m in uses})
            F.add("framework-api", "high", f"Donor imports Next.js modules ({', '.join(mods)}) that do not exist in the host.",
                  [("donor", r, ln, s) for r, ln, s, _ in uses[:4]])
    if H["router"] == "next-app" and df != "next":
        browser = _find(donor_root, r"\b(window\.|document\.|localStorage|sessionStorage|useState|useEffect|useRef|onClick=)")
        if browser:
            files = sorted({r for r, *_ in browser})
            F.add("server-rendering", "high", "Host uses the Next.js app router (server components by default). Donor code "
                  "uses hooks/browser APIs/event handlers, so transplanted components need 'use client' at the top, and "
                  "window/document access must move into useEffect.",
                  [("donor", r, ln, s) for r, ln, s, _ in browser[:4]])
            if len(files) > 4:
                F.items[-1]["text"] += f" ({len(files)} donor files affected.)"

    # global css entry
    if hf == "next":
        F.add("css-entry", "medium", "Next.js only allows global CSS imports from the root layout (app router) or "
              "_app (pages router); other files must use CSS Modules or Tailwind.",
              [("host", e, 1) for e in H["entry_files"] if "layout" in e or "_app" in e][:2])

    # donor-only browser behavior in plain HTML donors
    if df == "html":
        scripts = _find(donor_root, r"""<script[^>]+src=["'](?!https?:|//)([^"']+)["']""", {".html", ".htm"})
        if scripts:
            F.add("behavior-js", "high", "Donor pages load local scripts (" + ", ".join(sorted({m.group(1) for *_, m in scripts})[:6]) +
                  "). Interactive parts (menus, sliders, toggles) are driven by these; moving only markup gives dead UI. "
                  "Port the logic to the host's framework (e.g. React state).",
                  [("donor", r, ln, s) for r, ln, s, _ in scripts[:4]])
        cdn = _find(donor_root, r"""<(?:link|script)[^>]+(?:href|src)=["'](https?://[^"']+)["']""", {".html", ".htm"})
        if cdn:
            F.add("external-resource", "medium", "Donor depends on CDN resources (fonts, icon sets, libraries) the host "
                  "does not load: " + ", ".join(sorted({m.group(1).split("?")[0] for *_, m in cdn})[:6]),
                  [("donor", r, ln, s) for r, ln, s, _ in cdn[:4]])

    # assets referenced from CSS
    urls = _find(donor_root, r"""url\(\s*['"]?(?!data:|https?:|#)([^'")]+)['"]?\s*\)""", STYLE_EXTS)
    if urls:
        F.add("css-assets", "medium", "Donor CSS references files via url() (images/fonts) that must be copied and "
              "re-pathed: " + ", ".join(sorted({m.group(1) for *_, m in urls})[:8]),
              [("donor", r, ln, s) for r, ln, s, _ in urls[:4]])

    # layout traps
    fixed = _find(donor_root, r"position\s*:\s*(fixed|sticky)", STYLE_EXTS)
    if fixed:
        F.add("layout-positioning", "medium", "Donor uses fixed/sticky positioning; it can overlap the host's own header or "
              "content and often relies on body padding set elsewhere.",
              [("donor", r, ln, s) for r, ln, s, _ in fixed[:3]])
    hb = sorted({int(m.group(1)) for *_, m in _find(host_root, r"@media[^{]*?(\d{3,4})px", STYLE_EXTS)})
    db = sorted({int(m.group(1)) for *_, m in _find(donor_root, r"@media[^{]*?(\d{3,4})px", STYLE_EXTS)})
    if hb and db and not set(hb) & set(db):
        F.add("responsive", "low", f"Breakpoints differ: donor {db[:6]}px, host {hb[:6]}px; the transplanted part may "
              "switch layouts at different widths from the rest of the page.")

    # section-level CSS/name checks from the original analyzer, when the section can be located
    sec_report = None
    if section:
        try:
            sec_report = analyze(host_root, donor_root, section)
        except Exception:  # analyzer is best-effort here
            sec_report = None
        if sec_report and sec_report.get("donor_section", {}).get("found"):
            for f in sec_report["findings"]:
                if f["category"] in ("framework-mismatch", "styling-system-mismatch", "external-resource"):
                    continue  # covered above
                F.add(f["category"], f["severity"], f"{f['title']}. {f['detail']}"
                      + (" Evidence: " + "; ".join(f["evidence"][:5]) if f["evidence"] else ""))


    # the host's own global scripts expect certain classes/ids on the markup the new component may replace
    coupled = script_selectors(host_root, section)
    if coupled:
        key = (section or "").lower()
        toks = sorted(coupled, key=lambda t: (key not in t.lower() if key else False, t))[:8]
        names = sorted({coupled[t][0] for t in toks})
        F.add("host-script-coupling", "high",
              f"The site's own scripts ({', '.join(names)}) look up elements by class or id: {', '.join(toks)}. "
              "They run on page load, scroll or click and throw at runtime when such an element is missing "
              "(for example $('.navbar').offset().top). The build cannot see this. Keep these classes/ids on the "
              "replacement component's markup.",
              [("host", coupled[t][0], coupled[t][1], coupled[t][2]) for t in toks[:4]], repo="host")

    return {"host": {k: v for k, v in H.items() if k != "env"} | {"env": sorted(H["env"])},
            "donor": {k: v for k, v in D.items() if k != "env"} | {"env": sorted(D["env"])},
            "section_keyword": section,
            "donor_section_file": (sec_report or {}).get("donor_section", {}).get("file", ""),
            "facts": F.items}


def facts_markdown(scan_result: dict) -> str:
    out = []
    for f in scan_result["facts"]:
        ev = "; ".join(f"{e['repo']}:{e['path']}:{e['line']}" for e in f["evidence"])
        out.append(f"- {f['id']} [{f['severity']}] ({f['kind']}) {f['text']}" + (f"  <{ev}>" if ev else ""))
    return "\n".join(out) or "- (no static conflicts found)"
