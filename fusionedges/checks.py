"""Static checks of a migration result (no browser needed).

pages     every source page has a page/route in the output
text      share of the source's visible text blocks found in the output code
links     internal ".html" links left in the output (should be 0 after a migration)
assets    images/icons/fonts the source uses that are missing from the output
styles    style transfer: source CSS rules carried over (should be low) and donor style files kept;
          migration only: source CSS rules carried over (should be high)
build     output builds (when a build is available)
"""
from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from pathlib import Path

from .analyzer import parse_css
from .scanner import read, walk

CODE = {".html", ".htm", ".js", ".jsx", ".ts", ".tsx", ".mdx", ".md", ".vue", ".svelte", ".astro", ".json"}
STYLE = {".css", ".scss", ".sass", ".less"}
IMG = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".avif", ".woff", ".woff2", ".ttf", ".otf", ".mp4"}


class _Text(HTMLParser):
    BLOCK_SKIP = {"script", "style", "head", "noscript", "svg", "template"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip, self.parts = 0, []

    def handle_starttag(self, tag, attrs):
        if tag in self.BLOCK_SKIP:
            self.skip += 1
        for k, v in attrs:
            if k in ("alt", "placeholder", "title", "aria-label") and v and not self.skip:
                self.parts.append(v)

    def handle_endtag(self, tag):
        if tag in self.BLOCK_SKIP and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip and data.strip():
            self.parts.append(data)


def _norm(s: str) -> str:
    s = html.unescape(s)
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def source_pages(source: Path) -> list:
    return sorted(p.relative_to(source).as_posix() for p in walk(source, {".html", ".htm"})
                  if not p.name.startswith(("_", ".")) and "node_modules" not in p.parts)


def expected_route(page: str) -> str:
    r = re.sub(r"\.html?$", "", page)
    r = re.sub(r"(^|/)index$", "", r)
    return "/" + r.strip("/")


def output_routes(out: Path) -> set:
    routes = set()
    for app in ("app", "src/app"):
        base = out / app
        if base.is_dir():
            for p in base.rglob("page.*"):
                if p.suffix in (".tsx", ".jsx", ".ts", ".js", ".mdx"):
                    segs = [s for s in p.parent.relative_to(base).parts if not (s.startswith("(") and s.endswith(")"))]
                    routes.add("/" + "/".join(segs))
    for pages in ("pages", "src/pages"):
        base = out / pages
        if base.is_dir():
            for p in base.rglob("*"):
                if p.suffix in (".tsx", ".jsx", ".ts", ".js", ".mdx", ".astro", ".vue") and \
                        not p.name.startswith("_") and "api" not in p.relative_to(base).parts:
                    r = p.relative_to(base).with_suffix("").as_posix()
                    r = re.sub(r"(^|/)index$", "", r)
                    routes.add("/" + r.strip("/"))
    for p in walk(out, {".html", ".htm"}):  # static html output
        if "public" not in p.relative_to(out).parts and not (out / "package.json").exists():
            routes.add(expected_route(p.relative_to(out).as_posix()))
    # client-side routers: <Route path="/about"> or { path: "/about" }
    for p in walk(out, {".jsx", ".tsx", ".js", ".ts"}):
        for m in re.finditer(r"""path\s*[:=]\s*\{?\s*["'](/[^"']*)["']""", read(p)):
            routes.add(m.group(1).rstrip("/") or "/")
    return routes


def _corpus(out: Path) -> str:
    parts = []
    for p in walk(out, CODE):
        if p.name in ("package.json", "package-lock.json", "tsconfig.json"):
            continue
        parts.append(read(p))
    return _norm(" ".join(parts))


def _source_refs(source: Path) -> set:
    refs = set()
    for p in walk(source, {".html", ".htm"}):
        t = read(p)
        refs |= set(re.findall(r"""<(?:img|source|video|link)[^>]+(?:src|href|srcset)=["']([^"' ]+)""", t, re.I))
        refs |= set(re.findall(r"""url\(\s*['"]?([^'")]+)""", t))
    for p in walk(source, STYLE):
        refs |= set(re.findall(r"""url\(\s*['"]?([^'")]+)""", read(p)))
    names = set()
    for r in refs:
        if r.startswith(("http:", "https:", "//", "data:", "#")):
            continue
        name = r.split("?")[0].split("#")[0].rstrip("/").split("/")[-1]
        if Path(name).suffix.lower() in IMG and (any(source.rglob(name))):
            names.add(name)
    return names


def _class_rules(root: Path, exclude_dirs=("node_modules",)) -> set:
    sels = set()
    for p in walk(root, STYLE):
        rules, _ = parse_css(read(p))
        sels |= {r["selector"] for r in rules if r["classes"]}
    return sels


def run_checks(source: Path, donor: Path, out: Path, migration: bool, style: bool, build: dict,
               donor_style_files: list) -> dict:
    source, donor, out = Path(source), Path(donor), Path(out)
    res = {}

    pages = source_pages(source)
    routes = output_routes(out)
    missing = []
    for pg in pages:
        r = expected_route(pg)
        if r not in routes and not any(x.rsplit("/", 1)[-1] == r.rsplit("/", 1)[-1] and r != "/" for x in routes):
            missing.append(f"{pg} -> {r}")
    res["pages"] = {"label": "Every source page has a route", "total": len(pages), "missing": missing,
                    "value": f"{len(pages) - len(missing)}/{len(pages)}",
                    "pass": None if not pages else not missing}

    corpus = _corpus(out)
    blocks, seen = [], set()
    for pg in pages:
        tp = _Text()
        try:
            tp.feed(read(source / pg))
        except Exception:
            continue
        for part in tp.parts:
            n = _norm(part)
            if len(n.split()) >= 2 and n not in seen:
                seen.add(n)
                blocks.append((pg, part.strip()[:120], n))
    found = [b for b in blocks if b[2] in corpus]
    lost = [f"{b[0]}: “{b[1]}”" for b in blocks if b[2] not in corpus]
    pct = round(100 * len(found) / len(blocks)) if blocks else None
    res["text"] = {"label": "Source text kept", "value": f"{pct}%" if pct is not None else "n/a",
                   "percent": pct, "missing": lost[:40], "missing_count": len(lost),
                   "pass": None if pct is None else pct >= 90}

    if migration:
        left = []
        for p in walk(out, CODE - {".html", ".htm", ".md", ".json"}):
            for m in re.finditer(r"""href\s*=\s*\{?\s*["'`]((?!https?:|//|mailto:|tel:)[^"'`]*\.html?)(?:#[^"'`]*)?["'`]""", read(p)):
                left.append(f"{p.relative_to(out).as_posix()}: {m.group(1)}")
        res["links"] = {"label": "No .html links left", "value": str(len(left)), "missing": left[:30], "pass": not left}

    want = _source_refs(source)
    have = {p.name for p in out.rglob("*") if p.is_file() and "node_modules" not in p.parts}
    gone = sorted(n for n in want if n not in have)
    res["assets"] = {"label": "Source images/fonts present", "value": f"{len(want) - len(gone)}/{len(want)}",
                     "missing": gone, "pass": None if not want else not gone}

    src_rules = _class_rules(source)
    out_rules = _class_rules(out)
    carried = sorted(src_rules & out_rules)
    frac = round(100 * len(carried) / len(src_rules)) if src_rules else None
    if style:
        kept = [f for f in donor_style_files if (out / f).exists()]
        res["style"] = {"label": "Uses donor styles, not source CSS",
                        "value": (f"{frac}% of source CSS rules copied" if frac is not None else "no source CSS")
                                 + (f"; donor style files kept {len(kept)}/{len(donor_style_files)}" if donor_style_files else ""),
                        "missing": carried[:20], "percent_copied": frac,
                        "pass": (frac is None or frac <= 25) and (not donor_style_files or len(kept) == len(donor_style_files))}
    elif migration and frac is not None:
        res["style"] = {"label": "Source look kept (CSS ported)", "value": f"{frac}% of source CSS rules ported",
                        "missing": sorted(src_rules - out_rules)[:20], "percent_copied": frac, "pass": frac >= 75}

    b = build or {}
    res["build"] = {"label": "Builds", "value": "passes" if b.get("ok") else (("fails" if b.get("reason") in (None, "", "build failed")
                                                                               else b.get("reason")) if b.get("ok") is False
                                                                              else b.get("reason") or "not run"),
                    "pass": b.get("ok")}

    applicable = [v["pass"] for v in res.values() if v["pass"] is not None]
    if b.get("ok") is False or (res["pages"]["pass"] is False and len(res["pages"]["missing"]) > len(pages) / 2):
        outcome = "failed"
    elif applicable and all(applicable):
        outcome = "passed" if b.get("ok") else "passed-unbuilt"
    else:
        outcome = "partial"
    passed = sum(1 for v in applicable if v)
    return {"checks": res, "outcome": outcome, "score": f"{passed}/{len(applicable)}"}


# ------------------------------------------------------------------ component checks
_HOOKS = re.compile(r"\b(useState|useEffect|useRef|useContext|useReducer|useLayoutEffect|usePathname|useRouter|"
                    r"useSearchParams)\s*\(|\bon[A-Z][a-zA-Z]+\s*=\s*\{")
_BROWSER = re.compile(r"\b(window\.|document\.|localStorage\b|sessionStorage\b|navigator\.)")
_IMPORT_PKG = re.compile(r"""^\s*import\s[^'"]*?['"]([^'"./][^'"]*)['"]|^\s*import\s+['"]([^'"./][^'"]*)['"]""", re.M)
BUILTIN_PKGS = {"react", "react-dom", "next"}


def _pkg_name(spec: str) -> str:
    parts = spec.split("/")
    return "/".join(parts[:2]) if spec.startswith("@") else parts[0]


def _deps(root: Path) -> dict:
    import json as _j
    try:
        d = _j.loads(read(root / "package.json") or "{}")
    except ValueError:
        return {}
    return {**d.get("devDependencies", {}), **d.get("dependencies", {})}


_EXPORTED = re.compile(r"""export\s+(?:default\s+)?(?:async\s+)?(?:function|const|class)\s+([A-Z]\w*)|"""
                       r"""export\s+default\s+([A-Z]\w*)\s*;?\s*$|^(?:function|class)\s+([A-Z]\w*)""", re.M)


def donor_identifiers(donor: Path, component: str, source: Path) -> list:
    """Names (components, hooks) defined by the donor's component files and what they import, that the source's own
    code does not already define. Seeing one of them in the output is the sign the donor's code came over,
    as opposed to the agent re-creating something the source already had."""
    from . import rag
    donor, source = Path(donor), Path(source)
    mains = rag.find_component_files(donor, component)
    if not mains:
        return []
    files = [rel for rel, _, _ in rag.import_closure(donor, mains, max_files=10, max_depth=1)[0]]
    names = set()
    for rel in files:
        if Path(rel).suffix not in {".js", ".jsx", ".ts", ".tsx", ".vue", ".svelte"}:
            continue
        here = {next(g for g in m.groups() if g) for m in _EXPORTED.finditer(read(donor / rel))}
        if len(here) <= 4 or rel in mains:  # skip icon bundles and barrel files: their names are generic words
            names |= here
        if rel in mains and Path(rel).stem[:1].isalpha():
            names.add(re.sub(r"[-_ ](\w)", lambda x: x.group(1).upper(), Path(rel).stem[:1].upper() + Path(rel).stem[1:]))
    own = " ".join(read(p) for p in walk(source, {".js", ".jsx", ".ts", ".tsx", ".vue", ".svelte"}))
    return sorted(n for n in names if not re.search(r"\b%s\b" % re.escape(n), own))


def component_checks(source: Path, out: Path, component: str, build: dict, framework: str,
                     donor: Path | None = None) -> dict:
    """Checks for 'add the <component> from the donor to my site'. `source` is the user's site before the change."""
    source, out = Path(source), Path(out)
    kw = (component or "").strip().lower()
    code_exts = {".js", ".jsx", ".ts", ".tsx"}
    before = {p.relative_to(source).as_posix(): read(p) for p in walk(source, code_exts | STYLE)}
    after = {p.relative_to(out).as_posix(): read(p) for p in walk(out, code_exts | STYLE)}
    touched = sorted(p for p in after if before.get(p) != after[p])
    added_code = [p for p in touched if p not in before and Path(p).suffix in code_exts]
    touched_code = [p for p in touched if Path(p).suffix in code_exts]
    res = {}

    res["changed"] = {"label": "Something was added", "value": f"{len(added_code)} new, {len(touched) - len(added_code)} changed files",
                      "missing": [], "pass": bool(touched)}

    # the donor's own component (by name) must be in the output, not just any change that makes the build pass
    idents = donor_identifiers(donor, component, source) if donor else []
    found = [n for n in idents if any(re.search(r"\b%s\b" % re.escape(n), after[p]) for p in touched_code)]
    if donor and idents:
        res["donor_code"] = {"label": f"The donor's {component or 'component'} code is in the result",
                             "value": ", ".join(found[:4]) or "none of its parts found",
                             "missing": [] if found else idents[:6], "pass": bool(found)}

    # mounted: a new component is rendered by another file
    names = {Path(p).stem for p in added_code if Path(p).stem[:1].isupper()} | set(found)
    def _defined_in(n):
        return {q for q, t in after.items() if re.search(r"(?:function|const|class)\s+%s\b" % re.escape(n), t)
                or Path(q).stem == n}
    rendered = {n for n in names for q, t in after.items() if q not in _defined_in(n)
                and re.search(r"<%s[\s/>]" % re.escape(n), t)}
    res["mounted"] = {"label": f"The {component or 'component'} is rendered", "value": ", ".join(sorted(rendered)) or "not rendered anywhere",
                      "missing": sorted(names - rendered),
                      "pass": bool(rendered) if names else (False if donor and idents else None)}

    is_next = framework == "next"
    out_deps = _deps(out)
    # foreign router left over
    if "react-router-dom" not in out_deps and "react-router" not in out_deps:
        left = [f"{p}: {m.group(0).strip()[:80]}" for p in touched_code
                for m in re.finditer(r"""from\s+['"]react-router(-dom)?['"]""", after[p])]
        res["router"] = {"label": "No foreign router code left", "value": str(len(left)), "missing": left, "pass": not left}

    if is_next:
        need = [p for p in touched_code if _HOOKS.search(after[p]) and not re.match(r"""\s*(['"])use client\1""", after[p])
                and (p.startswith(("app/", "src/app/", "components/", "src/components/")) or p in added_code)]
        res["use_client"] = {"label": '"use client" where hooks/handlers are used', "value": f"{len(need)} file(s) missing it",
                             "missing": need, "pass": not need}
        bad = []
        for p in touched_code:
            t = after[p]
            if _BROWSER.search(t) and not re.search(r"\buseEffect\s*\(|typeof\s+window", t):
                bad.append(p)
        res["browser_api"] = {"label": "No window/document use while rendering", "value": f"{len(bad)} file(s)",
                              "missing": bad, "pass": not bad}
        glob = []
        for p in touched_code:
            if re.search(r"(^|/)(layout|_app)\.(t|j)sx?$", p):
                continue
            for m in re.finditer(r"""import\s+['"]([^'"]+\.(?:css|scss|sass))['"]""", after[p]):
                if ".module." not in m.group(1):
                    glob.append(f"{p}: {m.group(1)}")
        res["css_import"] = {"label": "Global CSS imported only from the root layout", "value": str(len(glob)),
                             "missing": glob, "pass": not glob}

    # dependencies used by touched code exist
    missing_pkgs = sorted({_pkg_name(m.group(1) or m.group(2)) for p in touched_code for m in _IMPORT_PKG.finditer(after[p])}
                          - set(out_deps) - BUILTIN_PKGS - {"@"})
    missing_pkgs = [m for m in missing_pkgs if not m.startswith(("@/", "~/", "node:")) and m not in ("@",)]
    res["deps"] = {"label": "Imported packages are in package.json", "value": ", ".join(missing_pkgs) or "all present",
                   "missing": missing_pkgs, "pass": not missing_pkgs}

    # assets referenced by new code exist
    gone = []
    for p in touched_code:
        for m in re.finditer(r"""(?:src|href)\s*=\s*\{?\s*["'](/[^"'#?]+\.(?:png|jpe?g|svg|gif|webp|ico|avif))["']""", after[p]):
            if not ((out / "public" / m.group(1).lstrip("/")).exists() or (out / m.group(1).lstrip("/")).exists()):
                gone.append(f"{p}: {m.group(1)}")
        for m in re.finditer(r"""import\s+\w+\s+from\s+['"](\.{1,2}/[^'"]+\.(?:png|jpe?g|svg|gif|webp))['"]""", after[p]):
            if not (out / Path(p).parent / m.group(1)).resolve().exists():
                gone.append(f"{p}: {m.group(1)}")
    res["assets"] = {"label": "Images it uses exist", "value": f"{len(gone)} missing", "missing": gone, "pass": not gone}

    # exactly one: count components named like the keyword rendered in shared layout + home page
    if kw:
        roots_files = [p for p in after if re.search(r"(^|/)(app|src/app)/(layout|page)\.(t|j)sx?$", p)
                       or re.search(r"(^|/)(pages|src/pages)/(_app|index)\.(t|j)sx?$", p)
                       or re.search(r"(^|/)src/(App|main)\.(t|j)sx?$", p)]
        roles = [{"nav", "navbar", "navigation", "header", "topbar", "appbar", "menubar", "menu"},
                 {"footer", "bottombar"}, {"sidebar", "drawer", "sidenav"}, {"hero", "banner", "jumbotron"}]
        words = next((r for r in roles if kw.replace(" ", "") in r), {kw.replace(" ", "")})
        tags = set()
        for p in roots_files:
            tags |= {m.group(1) for m in re.finditer(r"<([A-Z]\w*)", after[p])
                     if any(w in m.group(1).lower() for w in words)}
        res["single"] = {"label": f"Only one {component} on the page", "value": ", ".join(sorted(tags)) or "none in layout/home",
                         "missing": sorted(tags) if len(tags) > 1 else [], "pass": None if not tags else len(tags) == 1}

    b = build or {}
    res["build"] = {"label": "Builds", "value": "passes" if b.get("ok") else (
        ("fails" if b.get("reason") in (None, "", "build failed") else b.get("reason")) if b.get("ok") is False
        else b.get("reason") or "not run"), "pass": b.get("ok")}
    applicable = [v["pass"] for v in res.values() if v["pass"] is not None]
    if b.get("ok") is False or not touched or res.get("donor_code", {}).get("pass") is False:
        outcome = "failed"
    elif applicable and all(applicable):
        outcome = "passed" if b.get("ok") else "passed-unbuilt"
    else:
        outcome = "partial"
    return {"checks": res, "outcome": outcome, "score": f"{sum(1 for v in applicable if v)}/{len(applicable)}",
            "touched": touched}
