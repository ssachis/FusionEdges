"""Condition M: do the mechanical part of a component transfer without any AI call.

The professor's feedback (27 Oct): make the integration more manual and see whether token cost can be saved.
Reading the pilot diffs, most of what the model wrote was mechanical translation that every scanner fact already
names: strip TypeScript, rewrite `@/` imports, swap next/link for the site's router, add the packages, give
Tailwind classes CSS, keep the classes the site's own scripts look up. This module does those steps
deterministically and records, per scanner fact, whether it was resolved here or is left for the AI layer.

  port(source, donor, output, scan, component) -> record (files, packages, resolved / unresolved facts, mount)

Everything it writes goes under <src>/fusion/<component>/ in the OUTPUT project, plus package.json, the one
file that mounts the component, and copied public assets. Same input, same output: no model, no randomness.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from . import rag
from .scanner import read, walk

SHIMS = ("next/link", "next/image", "next/navigation", "next/router", "next/dynamic", "next/font/google",
         "next/font/local", "next/script", "next/head")
HOST_PROVIDED = {"react", "react-dom", "next", "react-router-dom", "react-router", "typescript"}
ASSET_EXT = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".avif", ".ico", ".woff", ".woff2", ".ttf", ".otf")
STYLE_EXT = (".css", ".scss")
_SPEC = re.compile(r"""(\bfrom\s*|\bimport\s*\(\s*|\bimport\s+|\brequire\s*\(\s*)(['"])([^'"\n]+)\2""")
_DIRECTIVE = re.compile(r"""^\s*['"]use (client|server)['"];?\s*\n""", re.M)
_PUBLIC_REF = re.compile(r"""(['"(])(/[\w./-]+\.(?:png|jpe?g|gif|svg|webp|avif|ico))(['")])""", re.I)
_IS_PKG = re.compile(r"^(?:@[\w.-]+/[\w.-]+|[\w.-]+)")
VERSION = 2   # 1: first 20 runs (1 Nov); 2: barrels, backend stand-ins, React-compatible versions, old router, CSS-first finds
TOOLS = Path(os.environ.get("FUSIONEDGES_TOOLS", Path.home() / ".cache" / "fusionedges"))


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "component"


def _pascal(s: str) -> str:
    return "".join(w.capitalize() for w in re.split(r"[^a-zA-Z0-9]+", s) if w) or "Component"


def _rel(from_file: str, to_file: str) -> str:
    r = os.path.relpath(to_file, os.path.dirname(from_file) or ".").replace(os.sep, "/")
    return r if r.startswith(".") else "./" + r


def _strip_ext(p: str) -> str:
    return re.sub(r"\.(tsx|ts|jsx|js|mjs)$", "", p)


# ------------------------------------------------------------------ tools (TypeScript, Tailwind)
def find_typescript() -> Path | None:
    """A `typescript` package folder: $FUSIONEDGES_TYPESCRIPT, the tool cache, or installed into the cache once."""
    env = os.environ.get("FUSIONEDGES_TYPESCRIPT")
    if env and (Path(env) / "lib" / "typescript.js").is_file():
        return Path(env)
    cached = TOOLS / "node_modules" / "typescript"
    if (cached / "lib" / "typescript.js").is_file():
        return cached
    if shutil.which("npm"):
        try:
            TOOLS.mkdir(parents=True, exist_ok=True)
            subprocess.run(["npm", "install", "--prefix", str(TOOLS), "--no-audit", "--no-fund", "typescript@5"],
                           capture_output=True, timeout=240)
        except (OSError, subprocess.SubprocessError):
            return None
    return cached if (cached / "lib" / "typescript.js").is_file() else None


_TS_JS = r"""
const ts = require(process.argv[process.argv.length - 1]);
const files = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const out = {};
for (const [name, text] of Object.entries(files)) {
  const r = ts.transpileModule(text, {fileName: name, reportDiagnostics: true, compilerOptions: {
    jsx: ts.JsxEmit.Preserve, target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext,
    verbatimModuleSyntax: false, isolatedModules: true, removeComments: false}});
  out[name] = {text: r.outputText, errors: (r.diagnostics || []).map(d => ts.flattenDiagnosticMessageText(d.messageText, ' '))};
}
process.stdout.write(JSON.stringify(out));
"""


def strip_types(files: dict, log=print) -> dict | None:
    """{name: tsx text} -> {name: jsx text}, types removed and JSX kept, via the TypeScript compiler."""
    if not files:
        return {}
    tsdir = find_typescript()
    node = shutil.which("node")
    if not tsdir or not node:
        log("[port] TypeScript compiler not available (no node or npm); leaving .ts/.tsx files for the AI layer")
        return None
    try:
        r = subprocess.run([node, "-e", _TS_JS, str(tsdir / "lib" / "typescript.js")], input=json.dumps(files),
                           capture_output=True, text=True, timeout=120)
        data = json.loads(r.stdout)
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        log(f"[port] stripping types failed: {e}")
        return None
    return {k: v["text"] for k, v in data.items()}


# ------------------------------------------------------------------ shims for Next.js APIs
def shims_source(router: str, hooks: bool = True) -> str:
    """next/link, next/image, next/navigation ... on top of whatever routing the host has. `hooks` False for
    react-router-dom before 5.1 (no useLocation): links still use the router, the pathname is read from the URL."""
    v6 = router.startswith("react-router-6") or router.startswith("react-router-7")
    v5 = router.startswith("react-router-5") or router.startswith("react-router-4")
    rr = v5 or v6
    if rr and not hooks:
        return _shims_old_router()
    lines = ['// Written by FusionEdges (deterministic port): Next.js APIs mapped onto this site.',
             'import React from "react";']
    if rr:
        lines.append(f'import {{ Link as RouterLink, useLocation{", useNavigate" if v6 else ", useHistory"} }} '
                     'from "react-router-dom";')
    lines.append("""
const EXTERNAL = /^(https?:|mailto:|tel:|#|\\/\\/)/;

export default function Link({ href = "#", children, prefetch, replace, scroll, shallow, locale, legacyBehavior, passHref, ...rest }) {
  const to = typeof href === "string" ? href : (href.pathname || "/") + (href.hash || "");""")
    if rr:
        lines.append("""  if (EXTERNAL.test(to) || rest.target === "_blank") return <a href={to} {...rest}>{children}</a>;
  return <RouterLink to={to} replace={replace} {...rest}>{children}</RouterLink>;
}""")
    else:
        lines.append("""  return <a href={to} {...rest}>{children}</a>;
}""")
    lines.append("""
export function Image({ src, alt = "", width, height, fill, priority, quality, placeholder, blurDataURL, loader, unoptimized, sizes, style, ...rest }) {
  const url = typeof src === "string" ? src : (src && src.src) || "";
  const s = fill ? { position: "absolute", inset: 0, width: "100%", height: "100%", objectFit: "cover", ...style } : style;
  return <img src={url} alt={alt} width={fill ? undefined : width} height={fill ? undefined : height} style={s} {...rest} />;
}
""")
    if rr:
        lines.append("export function usePathname() { return useLocation().pathname; }")
        lines.append("export function useSearchParams() { return new URLSearchParams(useLocation().search); }")
        if v6:
            lines.append("export function useRouter() { const n = useNavigate(); return { push: (p) => n(p), replace: (p) => n(p, { replace: true }), back: () => n(-1), forward: () => n(1), refresh: () => {}, prefetch: () => {}, pathname: useLocation().pathname, asPath: useLocation().pathname, query: {} }; }")
        else:
            lines.append("export function useRouter() { const h = useHistory(); const l = useLocation(); return { push: (p) => h.push(p), replace: (p) => h.replace(p), back: () => h.goBack(), forward: () => h.goForward(), refresh: () => {}, prefetch: () => {}, pathname: l.pathname, asPath: l.pathname, query: {} }; }")
    else:
        lines.append("export function usePathname() { return typeof window === \"undefined\" ? \"/\" : window.location.pathname; }")
        lines.append("export function useSearchParams() { return new URLSearchParams(typeof window === \"undefined\" ? \"\" : window.location.search); }")
        lines.append("export function useRouter() { return { push: (p) => { window.location.href = p; }, replace: (p) => window.location.replace(p), back: () => window.history.back(), forward: () => window.history.forward(), refresh: () => window.location.reload(), prefetch: () => {}, pathname: usePathname(), asPath: usePathname(), query: {} }; }")
    lines.append("""export function useParams() { return {}; }
export function redirect(p) { if (typeof window !== "undefined") window.location.href = p; }
export function notFound() {}

export function dynamic(load, opts = {}) {
  const Lazy = React.lazy(() => load().then((m) => (m && m.default ? m : { default: m })));
  const Fallback = opts.loading || (() => null);
  return function Dynamic(props) { return <React.Suspense fallback={<Fallback />}><Lazy {...props} /></React.Suspense>; };
}

// next/font: fonts are not downloaded here; the class names fall back to the site's own fonts.
export function font() { return { className: "", variable: "", style: {} }; }

export function Script({ src, strategy, onLoad, ...rest }) {
  React.useEffect(() => {
    if (!src) return;
    const s = document.createElement("script"); s.src = src; if (onLoad) s.onload = onLoad;
    Object.entries(rest).forEach(([k, v]) => typeof v === "string" && s.setAttribute(k, v));
    document.body.appendChild(s); return () => { s.remove(); };
  }, [src]);
  return null;
}
export function Head() { return null; }
""")
    return "\n".join(lines) + "\n"


def _shims_old_router() -> str:
    base = shims_source("", True).replace('import React from "react";',
                                          'import React from "react";\nimport { Link as RouterLink } from "react-router-dom";')
    base = base.replace('  return <a href={to} {...rest}>{children}</a>;\n}',
                        '  if (EXTERNAL.test(to) || rest.target === "_blank") return <a href={to} {...rest}>{children}</a>;\n'
                        '  return <RouterLink to={to} replace={replace} {...rest}>{children}</RouterLink>;\n}', 1)
    return base.replace('export function usePathname() { return typeof window === "undefined" ? "/" : window.location.pathname; }',
                        'export function usePathname() {\n'
                        '  const [p, setP] = React.useState(typeof window === "undefined" ? "/" : window.location.pathname);\n'
                        '  React.useEffect(() => { const t = setInterval(() => setP(window.location.pathname), 250); return () => clearInterval(t); }, []);\n'
                        '  return p;\n}')


def _shim_import(spec: str, clause: str) -> str | None:
    """How an `import <clause> from "<next module>"` reads against the shim file, or None to keep the clause."""
    clause = clause.strip()
    if spec == "next/link":
        return clause  # default export is Link
    if spec == "next/image":
        if clause.startswith("{"):
            return clause
        return "{ Image as " + clause.split(",")[0].strip() + " }" if clause else None
    if spec == "next/dynamic":
        return "{ dynamic as " + clause.split(",")[0].strip() + " }" if clause and not clause.startswith("{") else clause
    if spec == "next/script":
        return "{ Script as " + clause.split(",")[0].strip() + " }" if clause and not clause.startswith("{") else clause
    if spec == "next/head":
        return "{ Head as " + clause.split(",")[0].strip() + " }" if clause and not clause.startswith("{") else clause
    if spec.startswith("next/font"):
        names = [n.strip().split(" as ")[-1].strip() for n in clause.strip("{} ").split(",") if n.strip()]
        return "{ " + ", ".join(f"font as {n}" for n in names) + " }" if names else None
    return clause  # next/navigation, next/router: named hooks with the same names


# ------------------------------------------------------------------ Tailwind, compiled and scoped
def _css_blocks(css: str, selector_rx: str) -> list:
    """Bodies of top-level-ish blocks whose selector matches (no nesting parser: counts braces)."""
    out = []
    for m in re.finditer(selector_rx + r"\s*\{", css):
        i, depth = m.end(), 1
        while i < len(css) and depth:
            depth += {"{": 1, "}": -1}.get(css[i], 0)
            i += 1
        out.append(css[m.end():i - 1])
    return out


def donor_theme_css(donor: Path, scope: str) -> tuple:
    """(variables css scoped to `scope`, custom utility css, color names) from the donor's global stylesheets."""
    texts = []
    for p in walk(donor, (".css",)):
        rel = p.relative_to(donor).as_posix()
        if "node_modules" in rel or ".next/" in rel:
            continue
        t = read(p, 300_000)
        if "@tailwind" in t or '@import "tailwindcss"' in t or "@import 'tailwindcss'" in t or ":root" in t:
            texts.append(t)
    root_vars, dark_vars, theme_vars, utilities = [], [], [], []
    for t in texts:
        root_vars += _css_blocks(t, r"(?:^|\n)\s*:root")
        dark_vars += _css_blocks(t, r"(?:^|\n)\s*(?:\.dark|\[data-theme=['\"]?dark['\"]?\]|:root\.dark)")
        theme_vars += _css_blocks(t, r"(?:^|\n)\s*@theme(?:\s+inline)?")
        for m in re.finditer(r"@utility\s+([\w-]+)\s*\{", t):  # Tailwind v4 custom utilities
            body = _css_blocks(t[m.start():], r"@utility\s+" + re.escape(m.group(1)))
            if body:
                utilities.append(f".{m.group(1)} {{{body[0]}}}")
        for body in _css_blocks(t, r"@layer\s+(?:utilities|components)"):
            utilities.append(body)
    decl = lambda bodies: "\n".join(  # noqa: E731 - keep only custom-property declarations
        l for b in bodies for l in b.splitlines() if l.strip().startswith("--"))
    colors = {}
    for line in decl(theme_vars + root_vars).splitlines():
        m = re.match(r"\s*--(color-)?([\w-]+)\s*:\s*([^;]+);?", line)
        if not m:
            continue
        name, val = m.group(2), m.group(3).strip()
        if m.group(1):
            colors[name] = f"var(--color-{name})"
        elif re.fullmatch(r"[\d.]+\s+[\d.]+%\s+[\d.]+%(\s*/\s*[\d.]+%?)?", val):
            colors.setdefault(name, f"hsl(var(--{name}) / <alpha-value>)")
        elif re.match(r"(#|rgb|hsl|oklch|oklab|lab|lch|color\()", val):
            colors.setdefault(name, f"var(--{name})")
    css = ""
    if root_vars or theme_vars:
        css += f"{scope} {{\n{decl(theme_vars + root_vars)}\n}}\n"
    if dark_vars:
        css += f".dark {scope}, {scope}.dark {{\n{decl(dark_vars)}\n}}\n"
    return css, "\n".join(utilities), colors


def compile_tailwind(files: list, donor: Path, scope: str, out_css: Path, log=print) -> dict:
    """Generate only the utilities the ported files use, with no preflight (no global reset) and every rule
    behind `scope`, so the donor's styling cannot leak into the rest of the host site."""
    if not shutil.which("npx"):
        return {"ok": False, "reason": "npx not available"}
    vars_css, utils_css, colors = donor_theme_css(donor, scope)
    work = Path(tempfile.mkdtemp(prefix="fe-tw-"))
    try:
        conf = {"content": [str(Path(f).resolve()) for f in files], "darkMode": "class", "important": scope,
                "corePlugins": {"preflight": False}, "theme": {"extend": {"colors": colors}}}
        (work / "tailwind.config.js").write_text("module.exports = " + json.dumps(conf, indent=1) + ";\n")
        # v4-style custom utilities and @apply chains are kept inside @layer utilities so v3 can resolve them
        (work / "in.css").write_text("@tailwind components;\n@tailwind utilities;\n"
                                     + (f"@layer utilities {{\n{utils_css}\n}}\n" if utils_css.strip() else ""))
        cmd = ["npx", "--yes", "tailwindcss@3", "-c", "tailwind.config.js", "-i", "in.css", "-o", "out.css", "--minify"]
        r = subprocess.run(cmd, cwd=work, capture_output=True, text=True, timeout=300)
        if r.returncode != 0 and utils_css.strip():   # donor utilities can use v4-only syntax: retry without them
            (work / "in.css").write_text("@tailwind components;\n@tailwind utilities;\n")
            r = subprocess.run(cmd, cwd=work, capture_output=True, text=True, timeout=300)
            utils_dropped = True
        else:
            utils_dropped = False
        if r.returncode != 0 or not (work / "out.css").is_file():
            return {"ok": False, "reason": "tailwind CLI failed: " + (r.stderr or r.stdout)[-400:]}
        out_css.write_text("/* FusionEdges: Tailwind utilities used by this component only, scoped to "
                           f"{scope}, no preflight */\n" + vars_css + (work / "out.css").read_text())
        return {"ok": True, "colors": len(colors), "bytes": out_css.stat().st_size, "custom_utilities_dropped": utils_dropped}
    except (OSError, subprocess.SubprocessError) as e:
        return {"ok": False, "reason": str(e)}
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ------------------------------------------------------------------ mounting
def _main_export(text: str, component: str) -> tuple:
    """('default', None) or ('named', Name) for the component file's export."""
    if re.search(r"\bexport\s+default\b", text):
        return "default", None
    names = re.findall(r"\bexport\s+(?:const|function|class)\s+([A-Z]\w*)", text)
    names += [n.strip().split(" as ")[-1].strip() for b in re.findall(r"export\s*\{([^}]*)\}", text) for n in b.split(",")]
    names = [n for n in names if n and n[0].isupper()]
    kw = re.sub(r"[^a-z]", "", component.lower())
    for n in names:
        if kw and kw in n.lower():
            return "named", n
    return ("named", names[0]) if names else ("default", None)


def _replace_imports_of(host: Path, target: str, new_target_abs: str, files: list) -> list:
    """Point every import of the host's existing component at the fusion entry. Returns changed files."""
    changed = []
    rx = re.compile(r"""import\s+([^'";]+?)\s+from\s*(['"])([^'"]+)\2""")
    for rel in files:
        text = read(host / rel)
        new, hit = text, False
        for m in list(rx.finditer(text)):
            if rag.resolve_import(host, rel, m.group(3)) != target:
                continue
            clause = m.group(1).strip()
            if clause.startswith("{"):
                names = [n.strip() for n in clause.strip("{} ").split(",") if n.strip()]
                if len(names) != 1:
                    continue
                local = names[0].split(" as ")[-1].strip()
            elif "," in clause:
                local = clause.split(",")[0].strip()
            else:
                local = clause
            spec = re.sub(r"/index$", "", _strip_ext(_rel(rel, new_target_abs)))
            new = new.replace(m.group(0), f'import {local} from "{spec}"', 1)
            hit = True
        if hit and new != text:
            (host / rel).write_text(new)
            changed.append(rel)
    return changed


def _element_span(text: str, tag: str):
    """(start, end) of the first <tag ...>...</tag> element (same-tag nesting counted), or None."""
    m = re.search(r"<%s\b" % tag, text)
    if not m:
        return None
    depth, i = 0, m.start()
    for t in re.finditer(r"<(/?)%s\b[^>]*?(/?)>" % tag, text[m.start():]):
        if t.group(2):
            if depth == 0:
                return m.start(), m.start() + t.end()
            continue
        depth += -1 if t.group(1) else 1
        if depth == 0:
            return m.start(), m.start() + t.end()
    return None


def _add_import(text: str, line: str) -> str:
    last = list(re.finditer(r"^import[^\n]*\n(?:[ \t]+[^\n]*\n)*", text, re.M))
    at = last[-1].end() if last else 0
    return text[:at] + line + text[at:]


def _replace_inline(host: Path, app_rel: str, entry_abs: str, name: str) -> str | None:
    """The site writes its header inline (no component file): replace that element. Returns the tag replaced."""
    text = read(host / app_rel)
    for tag in ("header", "nav"):
        span = _element_span(text, tag)
        if span:
            body = text[:span[0]] + f"<{name} />" + text[span[1]:]
            (host / app_rel).write_text(_add_import(body, f'import {name} from "{re.sub(r"/index$", "", _strip_ext(_rel(app_rel, entry_abs)))}";\n'))
            return tag
    return None


def _insert_into_app(host: Path, app_rel: str, entry_abs: str, name: str) -> bool:
    text = read(host / app_rel)
    m = re.search(r"return\s*\(\s*<", text)
    if not m:
        return False
    i, depth, quote = m.end(), 0, None
    while i < len(text):
        c = text[i]
        if quote:
            quote = None if c == quote else quote
        elif c in "\"'`":
            quote = c
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        elif c == ">" and depth == 0:
            break
        i += 1
    if i >= len(text) or text[i - 1] == "/":
        return False
    body = text[:i + 1] + f"\n      <{name} />" + text[i + 1:]
    (host / app_rel).write_text(_add_import(body, f'import {name} from "{re.sub(r"/index$", "", _strip_ext(_rel(app_rel, entry_abs)))}";\n'))
    return True


# ------------------------------------------------------------------ the port
def port(source: Path, donor: Path, output: Path, scan: dict, component: str, log=print) -> dict:
    t0 = time.time()
    source, donor, output = Path(source), Path(donor), Path(output)
    rec = {"version": VERSION, "ok": False, "component": component, "files": [], "packages_added": {}, "assets": [], "mount": {},
           "resolved": [], "unresolved": [], "notes": []}
    main = rag.find_component_files(donor, component, limit=1)
    if not main:
        rec["reason"] = f"no '{component}' file found in the donor"
        rec["unresolved"].append({"kind": "component-not-found", "text": rec["reason"]})
        return rec
    ct = client_target(donor, main[0])
    if ct:
        rec["resolved"].append({"kind": "server-component", "action": f"{main[0]} only loads data on the server and renders "
                                f"{ct}; the client component is ported (its props use their defaults)"})
        main = [ct]
    cl = closure(donor, main)
    bad = tainted(cl, donor) - {main[0]}
    donor_files = cl["files"]
    slug = _slug(component)
    src = "src/" if (output / "src").is_dir() else ""
    base = f"{src}fusion/{slug}"
    scope = f".fe-{slug}"
    host_ts = bool((scan.get("source") or {}).get("typescript"))
    router = (scan.get("source") or {}).get("router") or ""
    amap = rag._alias_dirs(donor)  # noqa: SLF001 - same alias rules the closure used

    def target_of(rel: str) -> str:
        r = rel[4:] if rel.startswith("src/") else rel
        if r.startswith("app/") or r.startswith("pages/"):
            r = "routes/" + r.split("/", 1)[1]
        r = re.sub(r"\.tsx$", ".jsx", r) if not host_ts else r
        r = re.sub(r"\.ts$", ".js", r) if not host_ts else r
        return f"{base}/{r}"

    mapping = {r: target_of(r) for r in donor_files}
    shim_rel = f"{base}/next-shims.jsx"

    # 1. TypeScript -> JavaScript (types only; JSX kept)
    texts = dict(cl["texts"])
    if bad:
        for r in bad:
            why = ", ".join(cl["backend"].get(r) or ["imports a file that needs one"])
            texts[r] = stub_for(texts[r], r, why)
        rec["resolved"].append({"kind": "backend-feature", "action": "replaced by stand-ins (need a backend): "
                                + ", ".join(f"{Path(r).stem} ({', '.join(cl['backend'].get(r) or ['via import'])})" for r in sorted(bad))})
        # files only the stubs imported are no longer needed
        keep, need = set(), [main[0]]
        while need:
            r = need.pop()
            if r in keep:
                continue
            keep.add(r)
            if r in bad:
                continue
            for m in _SPEC.finditer(texts[r]):
                h = rag.resolve_import(donor, r, m.group(3), cl["aliases"])
                if h in texts:
                    need.append(h)
        donor_files = [r for r in donor_files if r in keep]
        mapping = {r: mapping[r] for r in donor_files}
    if main[0] in cl["backend"]:
        rec["unresolved"].append({"kind": "backend-feature", "text": f"the {component} itself needs a backend: "
                                  + ", ".join(cl["backend"][main[0]])})
    ts_files = {r: t for r, t in texts.items() if r.endswith((".ts", ".tsx")) and not host_ts}
    if ts_files:
        stripped = strip_types(ts_files, log)
        if stripped is None:
            for r in ts_files:
                mapping[r] = f"{base}/{r[4:] if r.startswith('src/') else r}"   # keep the extension
            rec["unresolved"].append({"kind": "language", "text": "TypeScript left in place: no TypeScript compiler here"})
        else:
            texts.update(stripped)
            rec["resolved"].append({"kind": "language", "action": f"types stripped from {len(stripped)} files (TypeScript compiler, JSX kept)"})

    # 2. imports: closure files -> new locations, Next.js -> shims, packages -> package.json
    used_shims, packages, unknown_next, missing_local, assets = set(), {}, set(), set(), {}
    stub_rel, stub_names, leaf_files = f"{base}/backend-stubs.js", set(), []
    for rel in donor_files:
        new_rel = mapping[rel]
        text = _DIRECTIVE.sub("", texts[rel]) if not rel.endswith(STYLE_EXT) else texts[rel]

        live = {m.group(3) for m in _SPEC.finditer(re.sub(r"/\*.*?\*/|(?<![:'\"])//[^\n]*", "", text, flags=re.S))}

        def fix(m, rel=rel, new_rel=new_rel, live=live):
            head, q, spec = m.group(1), m.group(2), m.group(3)
            if spec in SHIMS or spec.startswith("next/font/"):
                used_shims.add(spec)
                return f"{head}{q}{_strip_ext(_rel(new_rel, shim_rel))}{q}"
            if spec.startswith("next/") or spec == "next":
                unknown_next.add(spec)
                return m.group(0)
            hit = rag.resolve_import(donor, rel, spec, amap)
            if hit and hit in mapping:
                to = mapping[hit]
                return f"{head}{q}{_rel(new_rel, to) if to.endswith(STYLE_EXT) else _strip_ext(_rel(new_rel, to))}{q}"
            if spec.lower().endswith(ASSET_EXT) and (spec.startswith((".", "@/", "~/")) or any(spec.startswith(a) for a in amap)):
                cands = ([donor / Path(rel).parent / spec] if spec.startswith(".") else
                         [donor / d / spec[len(a):] for a, ds in amap.items() if spec.startswith(a) for d in ds])
                f = next((c for c in cands if c.is_file()), None)
                if f:
                    to = f"{base}/assets/{f.name}"
                    assets[to] = f
                    return f"{head}{q}{_rel(new_rel, to)}{q}"
            if spec.startswith((".", "@/", "~/")) or any(spec.startswith(a) for a in amap if a not in ("@/", "~/")):
                missing_local.add(f"{rel}: {spec}")
                return m.group(0)
            pm = _IS_PKG.match(spec)
            if pm and not spec.startswith(("http", "data:")) and spec in live:
                packages[pm.group(0)] = True
            return m.group(0)

        if rel.endswith(STYLE_EXT):
            new_text = text
        else:
            new_text = _SPEC.sub(fix, text)
            # `import Image from "<shim>"`: the shim's default export is Link, so name the other shims explicitly
            new_text = _rewrite_shim_clauses(texts[rel], new_text, new_rel, shim_rel)
            if rel not in bad:
                before_leaf = new_text
                new_text, nm = leaf_stubs(new_text, new_rel, stub_rel)
                if new_text != before_leaf:
                    stub_names |= nm | {"empty"}
                    leaf_files.append(rel)
        for pm in _PUBLIC_REF.finditer(new_text):
            p = donor / "public" / pm.group(2).lstrip("/")
            if p.is_file():
                assets[f"public{pm.group(2)}"] = p
        out = output / new_rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(new_text)
        rec["files"].append(new_rel)
    if stub_names:
        (output / stub_rel).write_text(stub_module(stub_names))
        rec["files"].append(stub_rel)
        rec["resolved"].append({"kind": "backend-feature", "action": "backend calls in " + ", ".join(Path(r).stem for r in leaf_files)
                                + " point to stand-ins (" + stub_rel.rsplit("/", 1)[-1] + "); their plain data is kept"})
    if used_shims:
        hooks = router_has_hooks(output)
        (output / shim_rel).write_text(shims_source(router, hooks))
        if not hooks:
            rec["notes"].append("the site's react-router-dom is older than 5.1 (no hooks): the shim reads the URL instead")
        rec["files"].append(shim_rel)
    if used_shims or any(f.get("kind") in ("framework-api", "routing-model") for f in scan.get("facts", [])):
        if unknown_next:
            rec["unresolved"].append({"kind": "framework-api", "text": "Next.js modules without a shim: " + ", ".join(sorted(unknown_next))})
        else:
            rec["resolved"].append({"kind": "framework-api", "action": "Next.js imports mapped to next-shims.jsx: " + ", ".join(sorted(used_shims))})
        rec["resolved"].append({"kind": "dependency-missing", "action": "'next' is not needed: its modules are shimmed"})
        rec["resolved"].append({"kind": "routing-model", "action": f"links and hooks use the site's router ({router or 'plain <a> links'}) through the shim"})
    if missing_local:
        rec["unresolved"].append({"kind": "missing-import", "text": "local imports outside the copied files: " + "; ".join(sorted(missing_local))[:600]})
    if any(f["kind"] == "path-alias" for f in scan.get("facts", [])):
        rec["resolved"].append({"kind": "path-alias", "action": "alias imports rewritten to relative paths"})

    # 3. assets
    for to, f in assets.items():
        dst = output / to
        if dst.exists():
            rec["notes"].append(f"asset {to} already exists in the site; kept the site's file")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(f, dst)
        rec["assets"].append(to)

    # 4. packages the ported code imports
    pkg_path = output / "package.json"
    if pkg_path.is_file():
        try:
            pkg = json.loads(pkg_path.read_text())
        except ValueError:
            pkg = None
        if pkg is not None:
            have = {**pkg.get("devDependencies", {}), **pkg.get("dependencies", {})}
            ddeps = rag._deps(donor)  # noqa: SLF001
            add = {n: ddeps.get(n, "*") for n in sorted(packages) if n not in have and n not in HOST_PROVIDED
                   and not n.startswith("@types/") and not _is_backend_spec(n)}
            add, vnotes = fit_versions(add, host_react(output), log)
            rec["notes"] += vnotes
            if any("using" in n for n in vnotes):
                rec["resolved"].append({"kind": "dependency-version-conflict", "action": "; ".join(n for n in vnotes if "using" in n)})
            if add:
                pkg.setdefault("dependencies", {}).update(add)
                pkg["dependencies"] = dict(sorted(pkg["dependencies"].items()))
                pkg_path.write_text(json.dumps(pkg, indent=2) + "\n")
            rec["packages_added"] = add
            rec["resolved"].append({"kind": "missing-package", "action": f"{len(add)} packages added from the donor's versions: " + ", ".join(add) if add else "no packages missing"})

    # 5. styling: Tailwind utilities compiled and scoped, donor globals not copied
    s_tw = bool((scan.get("source") or {}).get("tailwind"))
    d_tw = bool((scan.get("donor") or {}).get("tailwind")) or "tailwind" in " ".join((scan.get("donor") or {}).get("styling", []))
    css_rel = None
    if d_tw and not s_tw:
        css_rel = f"{base}/fusion.css"
        code = [str(output / f) for f in rec["files"] if f.endswith((".js", ".jsx", ".ts", ".tsx"))]
        tw = compile_tailwind(code, donor, scope, output / css_rel, log)
        rec["tailwind"] = tw
        if tw.get("ok"):
            rec["resolved"].append({"kind": "styling-system", "action": f"Tailwind utilities used by the component compiled to {css_rel} ({tw['bytes']:,} bytes, {tw['colors']} theme colors), all scoped to {scope}"})
            rec["files"].append(css_rel)
        else:
            css_rel = None
            rec["unresolved"].append({"kind": "styling-system", "text": "Tailwind classes have no CSS: " + tw.get("reason", "")[:300]})
    elif d_tw and s_tw:
        rec["notes"].append("both sites use Tailwind: the site's own Tailwind build styles the classes; donor theme tokens may be missing")
    if any(f["kind"] in ("global-selector-leak",) for f in scan.get("facts", [])):
        rec["resolved"].append({"kind": "global-selector-leak", "action": "donor global CSS not copied; no preflight; every generated rule sits behind " + scope})

    # 6. entry point: wrapper that carries the scope class and the classes the site's scripts look up
    main_new = mapping[main[0]]
    how, name = _main_export(read(output / main_new), component)
    keep_cls, keep_id = [], None
    for f in scan.get("facts", []):
        if f["kind"] == "host-script-coupling":
            toks = re.findall(r"([.#])([A-Za-z_][\w-]*)", f["text"].split(":", 1)[-1].split(". They")[0])
            keep_cls += [n for k, n in toks if k == "."]
            keep_id = keep_id or next((n for k, n in toks if k == "#"), None)
    entry = f"{base}/index.jsx"
    comp_name = f"Fusion{_pascal(component)}"
    imp = f'import Donor from "{_strip_ext(_rel(entry, main_new))}";' if how == "default" else \
        f'import {{ {name} as Donor }} from "{_strip_ext(_rel(entry, main_new))}";'
    cls = " ".join(dict.fromkeys([scope[1:]] + keep_cls))
    id_attr = f' id="{keep_id}"' if keep_id else ""
    (output / entry).write_text(
        "// Written by FusionEdges (deterministic port). The wrapper scopes the donor's styles and keeps the\n"
        "// class names / id this site's own scripts look up.\n"
        + (f'import "./{Path(css_rel).name}";\n' if css_rel else "") + "import React from \"react\";\n" + imp + "\n\n"
        f"export default function {comp_name}(props) {{\n"
        f"  return (\n    <div className=\"{cls}\"{id_attr}>\n      <Donor {{...props}} />\n    </div>\n  );\n}}\n"
        f"export {{ {comp_name} }};\n")
    rec["files"].append(entry)
    rec["entry"] = entry
    if keep_cls or keep_id:
        rec["resolved"].append({"kind": "host-script-coupling", "action": f"wrapper keeps {', '.join(['.' + c for c in keep_cls] + (['#' + keep_id] if keep_id else []))} for the site's scripts"})

    # 7. mount: replace the site's existing component everywhere it is imported, else add it to App
    existing = [e] if (e := existing_component(source, component)) else []
    code_files = [p.relative_to(output).as_posix() for p in walk(output, (".js", ".jsx", ".ts", ".tsx"))
                  if "node_modules" not in p.parts and not p.relative_to(output).as_posix().startswith(base)]
    changed = _replace_imports_of(output, existing[0], entry, code_files) if existing else []
    if changed:
        rec["mount"] = {"how": "replaced", "existing": existing[0], "files": changed}
        rec["resolved"].append({"kind": "mount", "action": f"imports of {existing[0]} now point to {entry} ({', '.join(changed)})"})
        rec["resolved"].append({"kind": "slot-occupied", "action": f"the site's {existing[0]} is replaced, not duplicated"})
    else:
        app = next((r for r in (f"{src}App.jsx", f"{src}App.js", f"{src}App.tsx") if (output / r).is_file()), None)
        tag = _replace_inline(output, app, entry, comp_name) if app else None
        if tag:
            rec["mount"] = {"how": f"replaced inline <{tag}>", "file": app, "files": [app]}
            rec["resolved"].append({"kind": "mount", "action": f"the site's inline <{tag}> in {app} replaced by <{comp_name} />"})
        elif app and _insert_into_app(output, app, entry, comp_name):
            rec["mount"] = {"how": "inserted", "file": app, "files": [app]}
            rec["resolved"].append({"kind": "mount", "action": f"<{comp_name} /> added at the top of {app}"})
        else:
            rec["mount"] = {"how": "none"}
            rec["unresolved"].append({"kind": "mount", "text": f"could not find where to mount: import {comp_name} from {entry} and render it where the site's {component} belongs"})

    # 8. facts this pass does not handle are left for the AI layer
    done = {r["kind"] for r in rec["resolved"]} | {u["kind"] for u in rec["unresolved"]}
    for f in scan.get("facts", []):
        if f["kind"] in ("framework-mismatch",):
            continue
        if f["kind"] not in done:
            rec["unresolved"].append({"kind": f["kind"], "text": f["text"][:300], "fact": f.get("id")})
            done.add(f["kind"])
    rec["ok"] = True
    rec["seconds"] = round(time.time() - t0, 1)
    log(f"[port] {len(rec['files'])} files under {base}, {len(rec['packages_added'])} packages, "
        f"{len(rec['assets'])} assets; resolved {len(rec['resolved'])}, left for AI {len(rec['unresolved'])}"
        f" ({', '.join(u['kind'] for u in rec['unresolved']) or 'none'}); mount: {rec['mount'].get('how')}")
    return rec


def _rewrite_shim_clauses(orig: str, new: str, new_rel: str, shim_rel: str) -> str:
    """Rename import clauses of shimmed Next modules (e.g. `import Image from "next/image"` -> `{ Image as Image }`)."""
    spec_new = _strip_ext(_rel(new_rel, shim_rel))
    rx = re.compile(r"""import\s+([^'";]+?)\s+from\s*(['"])(next/[\w/-]+)\2""")
    for m in rx.finditer(orig):
        spec = m.group(3)
        if spec not in SHIMS and not spec.startswith("next/font/"):
            continue
        clause = _shim_import(spec, m.group(1))
        if clause is None:
            continue
        old = f"import {m.group(1)} from {m.group(2)}{spec_new}{m.group(2)}"
        new = new.replace(old, f"import {clause} from {m.group(2)}{spec_new}{m.group(2)}", 1)
    return new


def unresolved_text(rec: dict) -> str:
    rows = [f"- {u['kind']}: {u['text']}" for u in rec.get("unresolved", [])]
    return "\n".join(rows) or "- none"


# ------------------------------------------------------------------ the AI layer (condition M+AI)
NEEDS_CODE = {"mount", "language", "missing-import", "framework-api", "styling-system", "component-not-found"}


def _files_for_ai(rec: dict, output: Path, error_text: str, room: int = 10000) -> list:
    """The few files the model must see: the ones named in the error first, then the entry, the mount file,
    the donor component, then the rest of the ported files while room lasts."""
    named = [f for f in rec.get("files", []) + rec.get("mount", {}).get("files", []) if Path(f).name in error_text]
    order = named + [rec.get("entry", "")] + rec.get("mount", {}).get("files", []) + rec.get("files", [])[:1] + rec.get("files", [])
    out, seen = [], set()
    for p in order:
        if not p or p in seen or p.endswith(".css"):
            continue
        seen.add(p)
        body = read(output / p)[:min(5000, max(room, 0))]
        if body:
            out.append(f"===== output:{p} =====\n{body}")
            room -= len(body)
        if room <= 0:
            break
    pkg = read(output / "package.json")[:1800]
    if pkg:
        out.append(f"===== output:package.json =====\n{pkg}")
    return out


def finish_with_ai(task: str, rec: dict, output: Path, component: str, build_enabled: bool, log,
                   after_build=None, max_repairs: int = 2) -> dict:
    """Build (and browser-check) the deterministic port; only when something fails, or a fact the port could not
    resolve needs code, ask the model, with a small message: what was already done, what is left, the errors,
    and only the files involved. No failure -> no AI call at all."""
    from .agents import Workspace, apply_blocks
    from .llm import LLMError, Session
    from .prompts import ONESHOT_SYSTEM
    from .verify import run_build

    s = Session("port-ai", log)
    ws = Workspace({"output": output}, writable="output", build_enabled=build_enabled)
    written, builds, failures, attempts, browser_failures, browser_repairs, browser_log = [], 0, 0, 0, 0, 0, []
    open_facts = [u for u in rec.get("unresolved", []) if u["kind"] in NEEDS_CODE]
    error_kind, error_text, run_error = "", "", ""
    for attempt in range(1 + max_repairs):
        problems = []
        if not (attempt == 0 and open_facts):   # first build the port as it is, unless it cannot work yet
            if build_enabled:
                res = run_build(output)
                if not res.get("skipped"):
                    builds += 1
                    if not res["ok"]:
                        failures += 1
                        error_kind, error_text = "build", res["output"][-3000:]
                        problems = [f"the build FAILED ({res.get('reason') or 'compile error'})"]
            if not problems and after_build is not None:
                try:
                    probs = after_build(output) or []
                except Exception as e:  # noqa: BLE001
                    log(f"[browser] check failed to run: {e}")
                    probs = []
                browser_log.append(probs)
                if probs:
                    browser_failures += 1
                    error_kind, error_text, problems = "browser", "\n".join(probs), probs
            if not problems:
                log("[port-ai] the deterministic port works" + (" after the AI fixes" if written else "; no AI call needed"))
                break
        if attempt >= max_repairs and attempt > 0:
            log("[port-ai] still failing; out of repair attempts")
            break
        attempts += 1
        if error_kind == "browser":
            browser_repairs += 1
        done = "\n".join(f"- {r['kind']}: {r['action']}" for r in rec.get("resolved", []))
        left = "\n".join(f"- {u['kind']}: {u['text']}" for u in rec.get("unresolved", [])) or "- none"
        msg = (f"TASK: {task}\n\nA deterministic tool already ported the donor's {component} into the OUTPUT project "
               f"(a copy of the site). Do NOT rewrite it from scratch; make the smallest change that makes it work.\n\n"
               f"ALREADY DONE (keep):\n{done}\n\nNOT DONE BY THE TOOL (your job if it matters):\n{left}\n\n"
               + (f"PROBLEMS NOW ({error_kind}):\n{error_text}\n\n" if error_text else
                  "The project has not been built yet because the items above must be handled first.\n\n")
               + "FILES:\n" + "\n\n".join(_files_for_ai(rec, output, error_text)) +
               "\n\nRules: keep the files under the fusion folder and the wrapper's class names; if a donor feature needs "
               "a backend or a package that cannot work in this site (auth, search services), remove that feature "
               "instead of faking it. Output only files that change, then <<<DONE>>>.")
        try:
            text = s.chat(ONESHOT_SYSTEM, [{"role": "user", "text": msg}], None, max_tokens=8000).get("text", "")
        except LLMError as e:
            run_error = str(e)
            log(f"[port-ai] AI call failed: {run_error[:200]}")
            break
        got = apply_blocks(text, ws)
        written += got["written"]
        try:
            with open(output.parent / "answers.txt", "a", encoding="utf-8") as fh:
                fh.write(f"\n\n===== port-ai attempt {attempts} ({len(text):,} chars) =====\n{text}")
        except OSError:
            pass
        log(f"[port-ai] attempt {attempts}: wrote {len(got['written'])} files" +
            (f", errors: {'; '.join(got['errors'][:2])}" if got["errors"] else ""))
        open_facts = []
        error_kind, error_text = "", ""
    t = s.totals()
    t.update({"summary": "deterministic port" + (f" + {attempts} AI fix(es)" if attempts else " (no AI call)"),
              "attempts": attempts, "builds": builds, "build_failures": failures, "retries": max(0, builds - 1),
              "files_written": sorted(set(rec.get("files", []) + written)), "files_deleted": [], "files_copied": rec.get("assets", []),
              "apply_errors": [], "tool_calls": 0, "context": {"mode": "deterministic-port"},
              "browser_failures": browser_failures, "browser_repairs": browser_repairs, "browser_log": browser_log,
              "port": {k: rec.get(k) for k in ("resolved", "unresolved", "mount", "packages_added", "seconds")}})
    if run_error:
        t["repair_error"] = run_error[:600]
    return t


def no_ai(rec: dict) -> dict:
    """Usage record for condition M (port only): zero calls, zero tokens."""
    return {"api_calls": 0, "input_tokens": 0, "output_tokens": 0, "est_cost_usd": 0.0, "models": [], "fallbacks": [],
            "summary": "deterministic port, no AI call", "attempts": 0, "builds": 0, "build_failures": 0, "retries": 0,
            "files_written": rec.get("files", []), "files_deleted": [], "files_copied": rec.get("assets", []),
            "apply_errors": [], "tool_calls": 0, "context": {"mode": "deterministic-port"},
            "port": {k: rec.get(k) for k in ("resolved", "unresolved", "mount", "packages_added", "seconds")}}


# ================================================================== v2 (1 Nov, after the first 20 M runs)
# Barrel files, backend-only features, peer-compatible package versions, old react-router, existing component.

BACKEND_PKGS = ("better-auth", "next-auth", "@auth/", "@prisma/", "prisma", "drizzle-orm", "@sanity/", "next-sanity",
                "sanity", "@supabase/", "firebase-admin", "stripe", "mongoose", "mongodb", "pg", "mysql2", "@vercel/kv",
                "@vercel/postgres", "@upstash/", "resend", "nodemailer", "server-only", "pliny/search", "contentlayer",
                "next-contentlayer", "@clerk/", "openai", "@anthropic-ai/", "googleapis")
BACKEND_NEXT = ("next/headers", "next/server", "next/cache")
_NAMED_IMPORT = re.compile(r"""import\s+(type\s+)?(?:([\w$]+)\s*,\s*)?\{([^}]*)\}\s*from\s*(['"])([^'"]+)\4\s*;?""")
_EXPORT_FROM = re.compile(r"""export\s+(type\s+)?(\*|\{[^}]*\})\s*from\s*(['"])([^'"]+)\3""")


def _is_backend_spec(spec: str) -> bool:
    return spec in BACKEND_NEXT or any(spec == p.rstrip("/") or spec.startswith(p if p.endswith("/") else p + "/")
                                       or spec == p for p in BACKEND_PKGS)


def _is_barrel(text: str) -> bool:
    body = re.sub(r"/\*.*?\*/|//[^\n]*", "", text, flags=re.S)
    stmts = [s.strip() for s in re.split(r";|\n", body) if s.strip()]
    return bool(stmts) and all(s.startswith("export") and " from " in s for s in stmts)


def _defines(text: str, name: str) -> bool:
    n = re.escape(name)
    return bool(re.search(r"\bexport\s+(?:default\s+)?(?:declare\s+)?(?:async\s+)?(?:const|let|var|function\*?|class|enum|"
                          r"interface|type|abstract\s+class)\s+%s\b" % n, text)
                or re.search(r"\bexport\s*\{[^}]*\b%s\b[^}]*\}(?!\s*from)" % n, text))


def _find_in_barrel(root: Path, barrel: str, name: str, amap, depth: int = 0):
    """(file, name inside that file) that the barrel re-exports as `name`, or None."""
    if depth > 4:
        return None
    text = read(root / barrel)
    for m in _EXPORT_FROM.finditer(text):
        what, spec = m.group(2), m.group(4)
        hit = rag.resolve_import(root, barrel, spec, amap)
        if not hit:
            continue
        if what == "*":
            sub = read(root / hit)
            if _defines(sub, name):
                return hit, name
            if _is_barrel(sub):
                r = _find_in_barrel(root, hit, name, amap, depth + 1)
                if r:
                    return r
        else:
            for part in what.strip("{} ").split(","):
                part = part.strip().replace("type ", "")
                if not part:
                    continue
                orig, _, alias = part.partition(" as ")
                if (alias or orig).strip() == name:
                    return hit, orig.strip()
    return None


def _debarrel(root: Path, rel: str, text: str, amap) -> tuple:
    """Rewrite `import { a, b } from "<barrel>"` into one import per defining file. Returns (text, n_rewritten)."""
    n = 0

    def sub(m):
        nonlocal n
        typeonly, default, names, q, spec = m.groups()
        hit = rag.resolve_import(root, rel, spec, amap)
        if typeonly or default or not hit or not _is_barrel(read(root / hit)):
            return m.group(0)
        lines = []
        for part in names.split(","):
            part = part.strip()
            if not part:
                continue
            is_type = part.startswith("type ")
            part = part[5:] if is_type else part
            imported, _, local = part.partition(" as ")
            imported, local = imported.strip(), (local or imported).strip()
            found = _find_in_barrel(root, hit, imported, amap)
            if not found:
                return m.group(0)          # cannot place one name: keep the barrel import as it is
            target, inner = found
            spec2 = _strip_ext(_rel(rel, target))
            kw = "import type" if is_type else "import"
            lines.append(f'{kw} {local} from "{spec2}";' if inner == "default" else
                         f'{kw} {{ {inner}{" as " + local if local != inner else ""} }} from "{spec2}";')
        n += 1
        return "\n".join(lines)

    return _NAMED_IMPORT.sub(sub, text), n


def closure(root: Path, starts: list, max_files: int = 60, max_depth: int = 6) -> dict:
    """Donor files the component needs, following imports, with barrel imports split per name first.
    Returns {files: [...], texts: {rel: text (de-barrelled)}, backend: {rel: [backend specs]}, packages: set}."""
    root = Path(root)
    amap = rag._alias_dirs(root)  # noqa: SLF001
    texts, order, backend, packages = {}, [], {}, set()
    queue = [(s, 0) for s in starts]
    seen = set(starts)
    while queue:
        rel, depth = queue.pop(0)
        raw = read(root / rel, 400_000)
        text, _ = _debarrel(root, rel, raw, amap) if not rel.endswith(STYLE_EXT) else (raw, 0)
        texts[rel] = text
        order.append(rel)
        if rel.endswith(STYLE_EXT):
            continue
        code = re.sub(r"/\*.*?\*/|(?<![:'\"])//[^\n]*", "", text, flags=re.S)   # imports in comments/JSDoc do not count
        specs = [m.group(3) for m in _SPEC.finditer(code)]
        bad = [s for s in specs if _is_backend_spec(s)]
        if bad or re.search(r"""^\s*['"]use server['"]""", text, re.M):
            backend[rel] = bad or ["'use server'"]
        for spec in specs:
            hit = rag.resolve_import(root, rel, spec, amap)
            if hit:
                if hit not in seen and depth + 1 <= max_depth and len(seen) < max_files and (
                        hit.endswith(STYLE_EXT) or rag._usable(root / hit, root)):  # noqa: SLF001
                    seen.add(hit)
                    queue.append((hit, depth + 1))
            elif not spec.startswith((".", "/", "http", "data:")) and not any(spec.startswith(a) for a in amap):
                pm = _IS_PKG.match(spec)
                if pm:
                    packages.add(pm.group(0))
    return {"files": order, "texts": texts, "backend": backend, "packages": packages, "aliases": amap}


def _is_component_name(n: str) -> bool:
    return bool(re.match(r"[A-Z][a-z0-9]", n or ""))     # PascalCase; ALL_CAPS constants are data


def _has_component_export(text: str) -> bool:
    jsx = "</" in text or "/>" in text
    return jsx and bool(re.search(r"\bexport\s+(?:default\s+)?(?:async\s+)?(?:const|let|function|class)\s+[A-Z][a-z0-9]", text)
                        or re.search(r"\bexport\s+default\s+[A-Z][a-z0-9]\w*\s*;?\s*$", text, re.M))


def _pure_backend(text: str) -> bool:
    """A data module that only wraps a backend (auth client, CMS client, queries): no plain data exported."""
    return not re.search(r"\bexport\s+const\s+\w+\s*(?::[^=]+)?=\s*(?:\[|\{|['\"`\d]|true|false)", text)


def tainted(cl: dict, root: Path) -> set:
    """Files replaced by stand-ins because their feature needs a backend:
      - a data module that only wraps a backend (better-auth client, Sanity client, CMS queries);
      - a component that imports a backend package, or such a data module (the sign-in button, a search box).
    Mixed data modules (static nav items + CMS getters) are kept; their backend imports get stand-ins instead.
    Taint stops there: the navbar that shows a sign-in button stays, only the button goes."""
    deps = {}
    for rel, text in cl["texts"].items():
        deps[rel] = {h for s in (m.group(3) for m in _SPEC.finditer(text))
                     if (h := rag.resolve_import(root, rel, s, cl["aliases"])) in cl["texts"]}
    pure = {r for r in cl["backend"] if not _has_component_export(cl["texts"][r]) and _pure_backend(cl["texts"][r])}
    # a data module that only re-wraps pure backend modules is backend too (queries/index -> client)
    changed = True
    while changed:
        changed = False
        for rel, ds in deps.items():
            t = cl["texts"][rel]
            if rel not in pure and ds & pure and not _has_component_export(t) and _pure_backend(t):
                pure.add(rel)
                changed = True
    comps = {r for r, ds in deps.items() if _has_component_export(cl["texts"][r]) and (r in cl["backend"] or ds & pure)}
    return pure | comps


def leaf_stubs(text: str, rel_out: str, stub_rel: str) -> tuple:
    """Point a kept file's backend-package imports at the local stand-in module. Returns (text, names)."""
    names = set()
    rx = re.compile(r"""import\s+(type\s+)?([^'";]+?)\s+from\s*(['"])([^'"]+)\3\s*;?""")

    def sub(m):
        if not _is_backend_spec(m.group(4)):
            return m.group(0)
        if m.group(1):
            return ""
        clause = m.group(2).strip()
        default, _, named = clause.partition("{")
        default = default.strip().rstrip(",").strip()
        parts = []
        if default.startswith("* as"):
            parts.append(default)
            names.add("*")
        elif default:
            parts.append(f"{{ empty as {default} }}")
        if named:
            items = []
            for x in named.rstrip("} ").split(","):
                x = x.strip()
                if not x or x.startswith("type "):
                    continue
                orig, _, local = x.partition(" as ")
                names.add(orig.strip())
                items.append(f"{orig.strip()} as {(local or orig).strip()}")
            if items:
                parts.append("{ " + ", ".join(items) + " }")
        spec = _strip_ext(_rel(rel_out, stub_rel))
        return "\n".join(f'import {p} from "{spec}";' for p in parts)

    return rx.sub(sub, text), names


def stub_module(names: set) -> str:
    out = ["// FusionEdges: stand-ins for backend packages this site does not have (auth, CMS, database).",
           'import React from "react";', "const noop = () => {};",
           "export const empty = new Proxy(noop, { get: (t, k) => (k === Symbol.toPrimitive ? () => \"\" : k === \"then\" ? undefined : empty), apply: () => empty });"]
    for n in sorted(names - {"*", "empty", "default"}):
        out.append(f"export function {n}() {{ return null; }}" if _is_component_name(n) else f"export const {n} = empty;")
    out.append("export default empty;")
    return "\n".join(out) + "\n"


def client_target(root: Path, main: str) -> str | None:
    """Next.js split: an async server component that loads data and renders one 'use client' component. The client
    component is the navbar a browser sees; its props fall back to their defaults."""
    text = read(root / main)
    if not (re.search(r"\basync\b", text) and "await" in text):
        return None
    amap = rag._alias_dirs(root)  # noqa: SLF001
    for m in re.finditer(r"import\s+(?:([A-Z]\w*)|\{\s*([A-Z]\w*)[^}]*\})\s+from\s*['\"]([^'\"]+)['\"]", text):
        name = m.group(1) or m.group(2)
        hit = rag.resolve_import(root, main, m.group(3), amap)
        if hit and re.search(r"<%s\b" % name, text) and re.search(r"""^\s*['"]use client['"]""", read(root / hit), re.M):
            return hit
    return None


def stub_for(text: str, rel: str, why: str) -> str:
    """A stand-in for a file whose feature needs a backend: components render nothing, hooks return empty data,
    anything else is a harmless no-op. Same export names, so the importers do not change."""
    names = set(re.findall(r"\bexport\s+(?:declare\s+)?(?:async\s+)?(?:const|let|var|function\*?|class)\s+([A-Za-z_$][\w$]*)", text))
    for b in re.findall(r"export\s*\{([^}]*)\}(?!\s*from)", text):
        names |= {p.split(" as ")[-1].strip() for p in b.split(",") if p.strip() and not p.strip().startswith("type ")}
    has_default = bool(re.search(r"\bexport\s+default\b", text))
    out = [f"// FusionEdges: this donor feature needs a backend this site does not have ({why}).",
           "// It is replaced by a stand-in with the same exports, so the component works without it.",
           'import React from "react";',
           "const noop = () => {};",
           "const empty = new Proxy(noop, { get: (t, k) => (k === Symbol.toPrimitive ? () => \"\" : k === \"then\" ? undefined : empty), apply: () => empty });"]
    for n in sorted(names):
        if _is_component_name(n):
            out.append(f"export function {n}() {{ return null; }}")
        elif n.startswith("use"):
            out.append(f"export function {n}() {{ return {{ data: null, error: null, isPending: false, isLoading: false }}; }}")
        else:
            out.append(f"export const {n} = empty;")
    if has_default:
        out.append("export default function Stub() { return null; }")
    return "\n".join(out) + "\n"


# ------------------------------------------------------------------ package versions that fit the site's React
_SEMVER = re.compile(r"^v?(\d+)(?:\.(\d+|x|\*))?(?:\.(\d+|x|\*))?(?:-([\w.]+))?$")


def _vt(v: str):
    m = _SEMVER.match(v.strip())
    if not m:
        return None
    nums = [int(x) if x and x.isdigit() else None for x in m.groups()[:3]]
    return nums, m.group(4)


def _cmp(a, b):
    return (a > b) - (a < b)


def satisfies(version: str, rng: str) -> bool:
    """Small semver range check: ||, spaces (and), ^ ~ >= > <= < =, x-ranges, *."""
    vt = _vt(version)
    if not vt or vt[1]:
        return False
    v = [x or 0 for x in vt[0]]
    for alt in (rng or "*").split("||"):
        alt = alt.strip()
        if alt in ("", "*", "x", "latest"):
            return True
        ok = True
        for c in re.findall(r"(\^|~|>=|<=|>|<|=)?\s*v?([\dx*]+(?:\.[\dx*]+){0,2}(?:-[\w.]+)?)", alt):
            op, ver = c
            t = _vt(ver)
            if not t:
                ok = False
                break
            parts = t[0]
            base = [x or 0 for x in parts]
            known = sum(1 for x in parts if x is not None)
            if op == "^":
                hi = [base[0] + 1, 0, 0] if base[0] else ([0, base[1] + 1, 0] if base[1] else [0, 0, base[2] + 1])
                ok &= _cmp(v, base) >= 0 and _cmp(v, hi) < 0
            elif op == "~":
                hi = [base[0], base[1] + 1, 0] if known >= 2 else [base[0] + 1, 0, 0]
                ok &= _cmp(v, base) >= 0 and _cmp(v, hi) < 0
            elif op == ">=":
                ok &= _cmp(v, base) >= 0
            elif op == ">":
                ok &= _cmp(v, base) > 0
            elif op == "<=":
                ok &= _cmp(v, base) <= 0
            elif op == "<":
                ok &= _cmp(v, base) < 0
            else:  # exact or x-range
                ok &= all(p is None or v[i] == p for i, p in enumerate(parts))
            if not ok:
                break
        if ok:
            return True
    return False


def _packument(name: str, timeout: int = 15):
    import urllib.request
    url = "https://registry.npmjs.org/" + name.replace("/", "%2F")
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.npm.install-v1+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:   # noqa: S310 - fixed registry host
        return json.loads(r.read())


def host_react(output: Path) -> str:
    """The React version the site will install: package-lock first, else the lowest version of its range."""
    lock = output / "package-lock.json"
    if lock.is_file():
        try:
            d = json.loads(read(lock, 5_000_000))
            v = ((d.get("packages") or {}).get("node_modules/react") or {}).get("version") or \
                ((d.get("dependencies") or {}).get("react") or {}).get("version")
            if v:
                return v
        except ValueError:
            pass
    try:
        rng = (json.loads(read(output / "package.json")).get("dependencies") or {}).get("react", "")
    except ValueError:
        rng = ""
    m = re.search(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?", rng)
    return ".".join(x or "0" for x in m.groups()) if m else ""


def fit_versions(packages: dict, react_version: str, log=print, fetch=None) -> tuple:
    """For each package, the newest version whose peer `react` range accepts the site's React, preferring the donor's
    own range. Returns ({name: version spec}, notes). Offline or no React: unchanged."""
    if not react_version:
        return dict(packages), []
    fetch = fetch or _packument
    out, notes = {}, []
    for name, spec in packages.items():
        try:
            doc = fetch(name)
        except Exception as e:  # noqa: BLE001 - offline: keep the donor's version
            out[name] = spec
            notes.append(f"{name}: registry not reachable ({str(e)[:60]}); kept {spec}")
            continue
        vers = doc.get("versions") or {}

        def fits(v):
            peer = ((vers.get(v) or {}).get("peerDependencies") or {})
            return all(satisfies(react_version, peer[k]) for k in ("react", "react-dom") if k in peer)

        stable = sorted((v for v in vers if _vt(v) and not _vt(v)[1]), key=lambda v: [x or 0 for x in _vt(v)[0]], reverse=True)
        in_range = [v for v in stable if satisfies(v, spec)]
        pick = next((v for v in in_range if fits(v)), None)
        if pick and in_range and pick == in_range[0]:
            out[name] = spec
            continue
        pick = pick or next((v for v in stable if fits(v)), None)
        if pick:
            out[name] = f"^{pick}" if not pick.startswith("0.") else pick
            notes.append(f"{name}: {spec} needs a newer React than the site's {react_version}; using {out[name]}")
        else:
            out[name] = spec
            notes.append(f"{name}: no version works with React {react_version}; kept {spec}")
    return out, notes


def router_has_hooks(output: Path) -> bool:
    """react-router-dom 5.0.x has no useLocation/useHistory hooks (added in 5.1)."""
    lock = output / "package-lock.json"
    v = ""
    if lock.is_file():
        try:
            d = json.loads(read(lock, 5_000_000))
            v = ((d.get("packages") or {}).get("node_modules/react-router-dom") or {}).get("version") or \
                ((d.get("dependencies") or {}).get("react-router-dom") or {}).get("version") or ""
        except ValueError:
            v = ""
    t = _vt(v) if v else None
    return not (t and t[0][0] == 5 and (t[0][1] or 0) < 1) and not (t and t[0][0] is not None and t[0][0] < 5)


def existing_component(source: Path, component: str) -> str | None:
    """The site's current component as a CODE file (Navigation/Navigation.css -> Navigation/index.js)."""
    for hit in rag.find_component_files(source, component, limit=3):
        if not hit.endswith(STYLE_EXT):
            return hit
        folder = (Path(source) / hit).parent
        for cand in [folder / f"index{e}" for e in (".jsx", ".js", ".tsx", ".ts")] + \
                [folder / f"{Path(hit).stem}{e}" for e in (".jsx", ".js", ".tsx", ".ts")]:
            if cand.is_file():
                return cand.relative_to(source).as_posix()
    return None
