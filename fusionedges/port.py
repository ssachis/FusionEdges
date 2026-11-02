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
def shims_source(router: str) -> str:
    """next/link, next/image, next/navigation ... on top of whatever routing the host has."""
    v6 = router.startswith("react-router-6") or router.startswith("react-router-7")
    v5 = router.startswith("react-router-5") or router.startswith("react-router-4")
    rr = v5 or v6
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
    rec = {"ok": False, "component": component, "files": [], "packages_added": {}, "assets": [], "mount": {},
           "resolved": [], "unresolved": [], "notes": []}
    main = rag.find_component_files(donor, component, limit=1)
    if not main:
        rec["reason"] = f"no '{component}' file found in the donor"
        rec["unresolved"].append({"kind": "component-not-found", "text": rec["reason"]})
        return rec
    closure, _ = rag.import_closure(donor, main, max_files=30, max_depth=4)
    donor_files = [r for r, _, _ in closure]
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
    texts = {r: read(donor / r, 400_000) for r in donor_files}
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
    for rel in donor_files:
        new_rel = mapping[rel]
        text = _DIRECTIVE.sub("", texts[rel]) if not rel.endswith(STYLE_EXT) else texts[rel]

        def fix(m, rel=rel, new_rel=new_rel):
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
            if pm and not spec.startswith(("http", "data:")):
                packages[pm.group(0)] = True
            return m.group(0)

        if rel.endswith(STYLE_EXT):
            new_text = text
        else:
            new_text = _SPEC.sub(fix, text)
            # `import Image from "<shim>"`: the shim's default export is Link, so name the other shims explicitly
            new_text = _rewrite_shim_clauses(texts[rel], new_text, new_rel, shim_rel)
        for pm in _PUBLIC_REF.finditer(new_text):
            p = donor / "public" / pm.group(2).lstrip("/")
            if p.is_file():
                assets[f"public{pm.group(2)}"] = p
        out = output / new_rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(new_text)
        rec["files"].append(new_rel)
    if used_shims:
        (output / shim_rel).write_text(shims_source(router))
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
                   and not n.startswith("@types/")}
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
    existing = rag.find_component_files(source, component, limit=1)
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
