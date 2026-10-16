"""FusionEdges compatibility analyzer.

Deterministic (no LLM, stdlib only): given a host repo, a donor repo and a
requested UI section (e.g. "footer"), produce a structured report of the
framework, styling, dependency and naming conflicts an agent is likely to hit.
Same inputs always produce byte-identical output.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path

SKIP_DIRS = {"node_modules", ".git", "dist", "build", ".next", "__pycache__"}
JS_EXTS = {".js", ".jsx", ".ts", ".tsx"}
GLOBAL_FIRST_TOKENS = {"*", "html", "body", ":root"}
SEV_ORDER = {"high": 0, "medium": 1, "low": 2}
SEV_WEIGHT = {"high": 3, "medium": 2, "low": 1}


# ----------------------------------------------------------------- utilities
def _files(root: Path, exts):
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in exts:
            continue
        if set(p.relative_to(root).parts) & SKIP_DIRS:
            continue
        yield p


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _rel(p: Path, root: Path) -> str:
    return p.relative_to(root).as_posix()


def _major(version: str):
    m = re.search(r"(\d+)", version or "")
    return int(m.group(1)) if m else None


# ----------------------------------------------------------------------- CSS
_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
_KEYFRAME_STEP = re.compile(r"^(from|to|\d+(\.\d+)?%)$")


def parse_css(text: str, file: str = ""):
    """Return (rules, variables). Each rule is one selector from a rule list."""
    text = _COMMENT.sub("", text)
    rules, variables = [], {}
    for sel_block, body in _RULE.findall(text):
        sel_block = sel_block.strip()
        if sel_block.startswith("@"):
            continue
        for decl in re.finditer(r"(--[\w-]+)\s*:\s*([^;]+)", body):
            variables[decl.group(1)] = decl.group(2).strip()
        for sel in sel_block.split(","):
            sel = sel.strip()
            if not sel or _KEYFRAME_STEP.match(sel):
                continue
            plain = re.sub(r"\[[^\]]*\]", "", sel)
            first = re.split(r"[\s>+~]", plain, maxsplit=1)[0]
            first_base = re.split(r"[.#:\[]", first, maxsplit=1)[0] or first
            rules.append({
                "selector": sel,
                "file": file,
                "classes": sorted(set(re.findall(r"\.([A-Za-z_][\w-]*)", plain))),
                "ids": sorted(set(re.findall(r"#([A-Za-z_][\w-]*)", plain))),
                "elements": sorted(set(re.findall(r"(?<![\w.#:-])([a-zA-Z][\w-]*)", plain))),
                "is_global": first in GLOBAL_FIRST_TOKENS or first_base in GLOBAL_FIRST_TOKENS,
                "var_uses": sorted(set(re.findall(r"var\((--[\w-]+)", body))),
            })
    return rules, variables


# ------------------------------------------------------------------- profile
@dataclass
class RepoProfile:
    path: str
    framework: str
    deps: dict
    styling: list
    css_files: list
    html_files: list
    components: dict


def _package_json(root: Path) -> dict:
    pkg = root / "package.json"
    if not pkg.exists():
        return {}
    try:
        data = json.loads(_read(pkg))
    except json.JSONDecodeError:
        return {}
    deps = {}
    deps.update(data.get("devDependencies", {}))
    deps.update(data.get("dependencies", {}))
    return deps


def profile_repo(root: Path) -> RepoProfile:
    deps = _package_json(root)
    if "react" in deps:
        framework = "react-vite" if "vite" in deps else "react"
    else:
        framework = "html"
    css = [_rel(p, root) for p in _files(root, {".css"})]
    html = [_rel(p, root) for p in _files(root, {".html", ".htm"})]
    styling = []
    if "tailwindcss" in deps or any(root.glob("tailwind.config.*")):
        styling.append("tailwind")
    if any(c.endswith(".module.css") for c in css):
        styling.append("css-modules")
    if any(not c.endswith(".module.css") for c in css):
        styling.append("plain-css")
    for dep, name in (("styled-components", "styled-components"),
                      ("@emotion/react", "emotion"), ("bootstrap", "bootstrap")):
        if dep in deps:
            styling.append(name)
    if any("bootstrap" in _read(root / h).lower() for h in html) and "bootstrap" not in styling:
        styling.append("bootstrap")
    components = {}
    if framework.startswith("react"):
        for p in _files(root, JS_EXTS):
            if p.stem[:1].isupper():
                components[p.stem] = _rel(p, root)
    return RepoProfile(str(root), framework, dict(sorted(deps.items())),
                       sorted(set(styling)), css, html, dict(sorted(components.items())))


# ------------------------------------------------------------ section finder
@dataclass
class Section:
    found: bool
    keyword: str
    kind: str = ""
    name: str = ""
    file: str = ""
    source: str = ""


def _element_at(text: str, start: int) -> str:
    m = re.match(r"<([A-Za-z][\w-]*)", text[start:])
    if not m:
        return ""
    tag = m.group(1)
    depth, pos = 0, start
    for t in re.finditer(r"<(/?)%s\b[^>]*?(/?)>" % re.escape(tag), text[start:], re.I):
        if t.group(1):
            depth -= 1
        elif not t.group(2):
            depth += 1
        pos = start + t.end()
        if depth <= 0:
            break
    return text[start:pos]


def locate_section(profile: RepoProfile, root: Path, keyword: str) -> Section:
    kw = keyword.lower()
    if profile.framework.startswith("react"):
        for name, rel in profile.components.items():
            if kw in name.lower():
                return Section(True, keyword, "component", name, rel, _read(root / rel))
        for p in _files(root, JS_EXTS):
            src = _read(p)
            m = re.search(r"<%s\b" % re.escape(kw), src, re.I)
            if m:
                return Section(True, keyword, "jsx-element", p.stem, _rel(p, root),
                               _element_at(src, m.start()))
        return Section(False, keyword)
    for h in profile.html_files:
        src = _read(root / h)
        m = re.search(r"<%s\b" % re.escape(kw), src, re.I)
        if not m:
            m = re.search(r"<\w+[^>]*\b(?:class|id)\s*=\s*[\"'][^\"']*%s[^\"']*[\"']" % re.escape(kw), src, re.I)
        if m:
            return Section(True, keyword, "html-element", kw, h, _element_at(src, m.start()))
    return Section(False, keyword)


# ---------------------------------------------------------- section analysis
_CLASS_ATTR = re.compile(r"""(?:class|className)\s*=\s*\{?\s*["'`]([^"'`]+)["'`]""")
_VOID = r"<(?:img|br|hr|input|meta|link|source)\b(?:[^>/]|/(?!>))*>"


def analyze_section(sec: Section, profile: RepoProfile, root: Path) -> dict:
    src = sec.source
    classes = sorted({c for m in _CLASS_ATTR.findall(src) for c in m.split()})
    ids = sorted(set(re.findall(r"\bid\s*=\s*[\"']([^\"']+)[\"']", src)))
    tags = sorted({t.lower() for t in re.findall(r"<([A-Za-z][\w-]*)", src)})
    imports = sorted({m for m in re.findall(r"""from\s+["']([^"']+)["']""", src)} |
                     {m for m in re.findall(r"""import\s+["']([^"']+)["']""", src)})
    packages = sorted({i.split("/")[0] if not i.startswith("@") else "/".join(i.split("/")[:2])
                       for i in imports if not i.startswith((".", "/"))})
    css_imports = [i for i in imports if i.endswith(".css")]
    assets = sorted(set(re.findall(
        r"""(?:src|href)\s*=\s*["']((?!https?:|//|#|mailto:|tel:)[^"']+)["']""", src)))
    assets = [a for a in assets if not a.endswith(".css") and not a.startswith("/")]
    external = sorted(set(re.findall(r"""(?:src|href)\s*=\s*["'](https?://[^"']+)["']""", src)))
    css_files = []
    base = (root / sec.file).parent if sec.file else root
    if profile.framework.startswith("react"):
        for i in css_imports:
            p = (base / i).resolve()
            if p.exists():
                css_files.append(_rel(p, root))
    else:
        page = _read(root / sec.file)
        for href in re.findall(r"""<link[^>]+href\s*=\s*["']([^"']+\.css)["']""", page, re.I):
            if href.startswith(("http:", "https:", "//")):
                continue
            p = (base / href).resolve()
            if p.exists():
                css_files.append(_rel(p, root))
    fallback = False
    if not css_files:
        css_files, fallback = list(profile.css_files), True
    return {
        "classes": classes, "ids": ids, "tags": tags, "packages": packages,
        "assets": assets, "external": external, "css_files": sorted(set(css_files)),
        "css_fallback_all_files": fallback,
        "source_chars": len(src),
        "_src": src,
    }


# ------------------------------------------------------------------ findings
@dataclass
class Finding:
    category: str
    severity: str
    title: str
    detail: str
    evidence: list = field(default_factory=list)
    recommendation: str = ""


def _cap(items, n=12):
    items = list(items)
    return items[:n] + ([f"... +{len(items) - n} more"] if len(items) > n else [])


def _host_inventory(host: RepoProfile, root: Path):
    rules, variables, ids = [], {}, set()
    for c in host.css_files:
        r, v = parse_css(_read(root / c), c)
        rules += r
        variables.update(v)
    for p in _files(root, JS_EXTS | {".html", ".htm"}):
        ids |= set(re.findall(r"\bid\s*=\s*[\"']([^\"']+)[\"']", _read(p)))
    return rules, variables, ids


def analyze(host_path, donor_path, section: str) -> dict:
    host_root, donor_root = Path(host_path).resolve(), Path(donor_path).resolve()
    host, donor = profile_repo(host_root), profile_repo(donor_root)
    sec = locate_section(donor, donor_root, section)
    report = {
        "request": {"section": section},
        "host": {k: v for k, v in asdict(host).items() if k != "path"},
        "donor": {k: v for k, v in asdict(donor).items() if k != "path"},
        "findings": [], "summary": {},
    }
    if not sec.found:
        report["donor_section"] = {"found": False}
        report["summary"] = {"error": f"no '{section}' section found in donor"}
        return report

    info = analyze_section(sec, donor, donor_root)
    src = info.pop("_src")
    report["donor_section"] = {"found": True, "kind": sec.kind, "name": sec.name,
                               "file": sec.file, **info}
    F: list[Finding] = []

    h_rules, h_vars, h_ids = _host_inventory(host, host_root)
    d_rules, d_vars = [], {}
    for c in info["css_files"]:
        r, v = parse_css(_read(donor_root / c), c)
        d_rules += r
        d_vars.update(v)
    sec_classes, sec_tags = set(info["classes"]), set(info["tags"])
    relevant = []
    for r in d_rules:
        scoped = bool(set(r["classes"]) & sec_classes)
        bare = not r["classes"] and not r["ids"] and set(r["elements"]) & sec_tags
        if scoped or r["is_global"] or bare:
            relevant.append(r)
    report["donor_section"]["relevant_css_rules"] = len(relevant)

    # 1. framework mismatch
    hf, df = host.framework, donor.framework
    if hf.startswith("react") != df.startswith("react"):
        if df == "html":
            checks = [
                (r"\bclass\s*=", "class= must become className="),
                (r"\bfor\s*=", "for= must become htmlFor="),
                (r"\bstyle\s*=\s*\"", "inline style strings must become style objects"),
                (r"\bon[a-z]+\s*=\s*\"", "inline event handlers (onclick=...) must become React handlers"),
                (r"<!--", "HTML comments must become {/* */}"),
                (_VOID, "void tags (<img>, <br>, <input>) must be self-closed"),
                (r"<script\b", "<script> tags cannot be inlined into JSX"),
            ]
            ev = [msg for pat, msg in checks if re.search(pat, src, re.I)]
            rec = "Convert the donor markup to JSX in a new component; do not paste HTML."
        else:
            checks = [
                (r"\buse[A-Z]\w*\(", "uses React hooks; needs static rendering or vanilla JS"),
                (r"\bprops\b|\(\s*\{[^}]*\}\s*\)\s*(=>|\{)", "takes props; values must be inlined"),
                (r"\.map\(", "renders lists with .map(); must be expanded to static HTML"),
                (r"\{[^}]+\}", "contains JSX expressions that must be evaluated"),
                (r"\b(Link|NavLink|useNavigate)\b", "uses router components; links must become plain <a>"),
            ]
            ev = [msg for pat, msg in checks if re.search(pat, src)]
            rec = "Render the component to static HTML/CSS (or vanilla JS) for the HTML host."
        F.append(Finding("framework-mismatch", "high", f"Donor is {df}, host is {hf}",
                         "Markup cannot be copied verbatim across frameworks.", ev, rec))

    # 2. slot occupied / component name collision
    kw = section.lower()
    occupied = [n for n in host.components if kw in n.lower()]
    for h in host.html_files:
        if re.search(r"<%s\b" % re.escape(kw), _read(host_root / h), re.I):
            occupied.append(h)
    if sec.kind == "component" and sec.name in host.components:
        F.append(Finding("component-name-collision", "high",
                         f"Host already has a component named {sec.name}",
                         "Copying the donor file to the same name would overwrite the host's component.",
                         [host.components[sec.name]],
                         f"Use a distinct name (e.g. Donor{sec.name}) or merge deliberately."))
    elif occupied:
        F.append(Finding("slot-occupied", "medium", f"Host already renders a {section}",
                         "Adding the donor's section may duplicate the existing one.",
                         _cap(sorted(set(occupied))),
                         "Decide up front whether to replace or add alongside; remove or rewire the old one."))

    # 3. class collisions
    host_classes = {}
    for r in h_rules:
        for c in r["classes"]:
            host_classes.setdefault(c, set()).add(r["file"])
    coll = sorted(c for c in sec_classes if c in host_classes)
    if coll:
        F.append(Finding("css-class-collision", "high",
                         f"{len(coll)} donor class name(s) already styled in host",
                         "Host rules will apply to the donor markup, and donor rules will restyle host elements.",
                         _cap(f".{c} (host: {', '.join(sorted(host_classes[c]))})" for c in coll),
                         "Namespace donor classes (e.g. prefix with fe-) or use CSS modules."))

    # 4. global / bare selector leaks
    leaks = sorted({r["selector"] for r in relevant if r["is_global"] or (not r["classes"] and not r["ids"])})
    if leaks:
        sev = "high" if any(r["is_global"] for r in relevant) else "medium"
        F.append(Finding("global-selector-leak", sev,
                         f"{len(leaks)} donor rule(s) target bare elements or global scope",
                         "Unscoped selectors will restyle the host's other pages and components.",
                         _cap(leaks), "Scope each rule under the section's root class before merging."))

    # 5. CSS variables
    diff = sorted(v for v in d_vars if v in h_vars and d_vars[v] != h_vars[v])
    if diff:
        F.append(Finding("css-variable-conflict", "high",
                         f"{len(diff)} CSS variable(s) defined in both with different values",
                         "Importing the donor stylesheet would change host theming.",
                         _cap(f"{v}: host={h_vars[v]} donor={d_vars[v]}" for v in diff),
                         "Rename donor variables or scope them to the section root."))
    used = sorted({v for r in relevant for v in r["var_uses"]})
    undefined = sorted(v for v in used if v not in d_vars and v not in h_vars)
    if undefined:
        F.append(Finding("css-variable-undefined", "medium",
                         f"{len(undefined)} variable(s) used by the section are defined nowhere",
                         "Not defined in either repo's scanned stylesheets; may come from an unscanned file.",
                         _cap(undefined), "Locate the definition or supply fallback values."))

    # 6. styling system
    if "tailwind" in donor.styling and "tailwind" not in host.styling:
        F.append(Finding("styling-system-mismatch", "high", "Donor uses Tailwind, host does not",
                         "Utility classes will not resolve in the host.", [],
                         "Convert utilities to plain CSS or add Tailwind to the host."))
    elif "tailwind" in host.styling and "tailwind" not in donor.styling:
        F.append(Finding("styling-system-mismatch", "medium",
                         "Host uses Tailwind (preflight reset), donor uses plain CSS",
                         "Tailwind's reset can change donor margins, list styles and headings.", [],
                         "Re-check spacing/typography after import; add explicit resets in donor CSS."))
    others = sorted((set(donor.styling) ^ set(host.styling)) - {"tailwind", "plain-css"})
    if others:
        F.append(Finding("styling-system-mismatch", "medium", "Styling systems differ",
                         "Donor/host differ in: " + ", ".join(others), others,
                         "Translate donor styles into the host's system."))

    # 7. dependencies
    miss, ver = [], []
    for pkg in info["packages"]:
        if pkg in host.deps:
            a, b = _major(donor.deps.get(pkg, "")), _major(host.deps[pkg])
            if a is not None and b is not None and a != b:
                ver.append(f"{pkg}: host {host.deps[pkg]} vs donor {donor.deps[pkg]}")
        else:
            miss.append(pkg)
    if ver:
        F.append(Finding("dependency-version-conflict", "high",
                         "Major version mismatch on shared packages",
                         "APIs used by the donor may not exist in the host's version.",
                         _cap(ver), "Port to the host's version rather than upgrading the host."))
    if miss:
        F.append(Finding("dependency-missing", "medium",
                         f"{len(miss)} package(s) imported by donor missing in host",
                         "Build will fail until installed or replaced.",
                         _cap(miss), "Install the minimum set, or inline/replace trivial uses."))
    ext = list(info["external"])
    if sec.file.endswith((".html", ".htm")):
        ext += re.findall(r"""<link[^>]+href\s*=\s*["'](https?://[^"']+)["']""",
                          _read(donor_root / sec.file), re.I)
    if ext:
        F.append(Finding("external-resource", "low", "Donor references external URLs",
                         "CDN scripts, fonts or links the section's look or behavior may rely on.",
                         _cap(sorted(set(ext))),
                         "Confirm each is needed; add to the host's entry HTML if so."))

    # 8. assets
    if info["assets"]:
        F.append(Finding("asset-reference", "low",
                         f"{len(info['assets'])} relative asset path(s) in section",
                         "Paths will break after moving the markup.",
                         _cap(info["assets"]),
                         "Copy assets into the host (public/ for Vite) and rewrite paths."))

    # 9. id collisions
    idc = sorted(set(info["ids"]) & h_ids)
    if idc:
        F.append(Finding("id-collision", "medium", "Donor ids already used in host",
                         "Duplicate ids break anchors, label/for pairing and tests.",
                         _cap(idc), "Rename the donor ids."))

    F.sort(key=lambda f: (SEV_ORDER[f.severity], f.category, f.title))
    report["findings"] = [asdict(f) for f in F]
    counts = {s: sum(1 for f in F if f.severity == s) for s in SEV_ORDER}
    report["summary"] = {
        "counts": counts,
        "risk_score": sum(SEV_WEIGHT[f.severity] for f in F),
        "categories": sorted({f.category for f in F}),
    }
    return report


# ------------------------------------------------------------------ rendering
def render_markdown(report: dict) -> str:
    out = ["# Compatibility report", ""]
    h, d = report["host"], report["donor"]
    out += [f"- **Request:** transplant `{report['request']['section']}` from donor into host",
            f"- **Host:** {h['framework']} | styling: {', '.join(h['styling']) or 'none'}",
            f"- **Donor:** {d['framework']} | styling: {', '.join(d['styling']) or 'none'}"]
    s = report.get("donor_section", {})
    if not s.get("found"):
        return "\n".join(out + ["", "**Section not found in donor.**"])
    out += [f"- **Donor source:** `{s['file']}` ({s['kind']})",
            f"- **Classes:** {', '.join(s['classes']) or 'none'}", ""]
    c = report["summary"]["counts"]
    out += [f"**Findings:** {c['high']} high, {c['medium']} medium, {c['low']} low", ""]
    for f in report["findings"]:
        out += [f"## [{f['severity'].upper()}] {f['title']}  `{f['category']}`", f["detail"]]
        out += [f"- {e}" for e in f["evidence"]]
        if f["recommendation"]:
            out.append(f"\n**Do:** {f['recommendation']}")
        out.append("")
    return "\n".join(out)
