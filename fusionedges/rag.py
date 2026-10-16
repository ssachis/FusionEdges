"""Retrieval for the compatibility report (no embeddings, stdlib only).

1. Chunk both repos into ~50-line windows (with file, line range and repo).
2. BM25 lexical search with queries built from the task, the scanner facts and migration topics.
3. Graph expansion: pull in files that retrieved files import / link (one hop).
4. Fill a character budget; record what was retrieved vs. the size of the whole repos, so the
   proposal's offline retrieval experiment can compare strategies later.

`component_context` is the selector for "add one component" tasks. Similarity search is the wrong tool
there: the agent needs the component PLUS everything it imports (completeness), and only a little of
the source site (the wiring points). It is deterministic and does not depend on the compatibility
report, so both experiment conditions receive exactly the same files.
"""
from __future__ import annotations

import json
import math
import os
import re
from collections import Counter, defaultdict
from pathlib import Path

from .scanner import read, walk

TEXT_EXTS = {".html", ".htm", ".css", ".scss", ".sass", ".less", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
             ".vue", ".svelte", ".astro", ".json", ".md", ".mdx"}
SKIP_NAMES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb"}
CHUNK, OVERLAP = 50, 8

APP_TOPICS = [
    "root layout html body font metadata children",
    "tailwind config theme extend colors fontFamily borderRadius",
    "globals css variables :root --background --foreground --primary",
    "navbar header navigation Link menu",
    "footer links copyright",
    "button component variant size className",
    "card component title description",
    "page home hero section",
    "next config images output",
    "use client useState useEffect",
    "font next/font google import",
    "react-router Link NavLink useLocation useNavigate Routes Route",
    "context provider createContext useContext theme",
    "package json dependencies",
]
HTML_TOPICS = [
    "html head title meta description link stylesheet script",
    "nav header menu a href html",
    "footer",
    "script addEventListener querySelector classList toggle click",
    "css :root variables font-family color background",
    "img src alt background url",
    "form input button submit",
    "@media max-width responsive",
]


def _tokens(text: str) -> list:
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    return [t for t in re.split(r"[^A-Za-z0-9]+", text.lower()) if len(t) > 1]


def chunk_repo(root: Path, repo: str) -> list:
    out = []
    for p in walk(Path(root), TEXT_EXTS):
        if p.name in SKIP_NAMES:
            continue
        text = read(p, limit=200_000)
        if not text.strip():
            continue
        lines = text.splitlines()
        rel = p.relative_to(root).as_posix()
        step = CHUNK - OVERLAP
        for start in range(0, max(1, len(lines)), step):
            body = "\n".join(lines[start:start + CHUNK])
            if not body.strip():
                continue
            out.append({"repo": repo, "path": rel, "start": start + 1,
                        "end": min(len(lines), start + CHUNK), "text": body,
                        "tokens": _tokens(rel + " " + body)})
            if start + CHUNK >= len(lines):
                break
    return out


class BM25:
    def __init__(self, docs: list, k1=1.4, b=0.75):
        self.docs, self.k1, self.b = docs, k1, b
        self.tf = [Counter(d["tokens"]) for d in docs]
        self.len = [len(d["tokens"]) or 1 for d in docs]
        self.avg = sum(self.len) / max(1, len(self.len))
        df = defaultdict(int)
        for tf in self.tf:
            for t in tf:
                df[t] += 1
        n = len(docs)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}

    def search(self, query: str, repo: str | None = None, k: int = 5) -> list:
        q = _tokens(query)
        scores = []
        for i, tf in enumerate(self.tf):
            if repo and self.docs[i]["repo"] != repo:
                continue
            s = 0.0
            for t in q:
                if t in tf:
                    f = tf[t]
                    s += self.idf.get(t, 0) * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * self.len[i] / self.avg))
            if s > 0:
                scores.append((s, i))
        scores.sort(reverse=True)
        return [i for _, i in scores[:k]]


_IMPORT = re.compile(r"""(?:import\s[^'"]*?from\s*|import\s*\(?\s*|require\(\s*|@import\s+(?:url\()?\s*)['"]([^'"]+)['"]""")
_HTML_REF = re.compile(r"""<(?:link|script)[^>]+(?:href|src)=["']([^"']+)["']""", re.I)


_CODE_EXTS = (".tsx", ".ts", ".jsx", ".js", ".mjs", ".css", ".scss")


def _alias_dirs(root: Path) -> dict:
    """{'@/': ['', 'src/'], ...} from tsconfig/jsconfig `paths`; '@/' and '~/' always try the root and src/."""
    out = {"@/": ["", "src/"], "~/": ["", "src/"]}
    for name in ("tsconfig.json", "jsconfig.json"):
        raw = read(Path(root) / name)
        if not raw:
            continue
        try:
            conf = json.loads(re.sub(r"/\*.*?\*/|(?<!:)//[^\n]*", "", raw, flags=re.S))
        except ValueError:
            continue
        opts = conf.get("compilerOptions", {}) or {}
        base = (opts.get("baseUrl") or ".").strip("./")
        for pat, targets in (opts.get("paths", {}) or {}).items():
            if not pat.endswith("*"):
                continue
            dirs = []
            for tg in targets or []:
                d = tg.rstrip("*").lstrip("./")
                d = (base + "/" + d).strip("/") if base else d
                dirs.append(d + "/" if d else "")
            if dirs:
                out[pat.rstrip("*")] = dirs
    return out


def resolve_import(root: Path, rel: str, ref: str, aliases=None) -> str | None:
    """Repo-relative path of the local file `ref` (imported from `rel`), or None for packages / missing files."""
    root = Path(root)
    if ref.startswith(("http:", "https:", "//", "data:")):
        return None
    p = root / rel
    bases = []
    if ref.startswith("."):
        bases.append(p.parent / ref)
    else:
        aliases = aliases if aliases is not None else _alias_dirs(root)
        for prefix, dirs in sorted(aliases.items(), key=lambda kv: -len(kv[0])):
            if ref.startswith(prefix):
                bases += [root / d / ref[len(prefix):] for d in dirs]
                break
        else:
            if ref.startswith("/") and p.suffix in (".html", ".htm"):
                bases.append(root / ref.lstrip("/"))
            elif p.suffix in (".html", ".htm"):
                bases.append(p.parent / ref)
    for base in bases:
        for cand in [base] + [base.with_name(base.name + e) for e in _CODE_EXTS] + \
                [base / f"index{e}" for e in _CODE_EXTS]:
            try:
                cand = cand.resolve()
            except OSError:
                continue
            if cand.is_file() and root.resolve() in cand.parents:
                return cand.relative_to(root.resolve()).as_posix()
    return None


def _neighbors(root: Path, rel: str, aliases: list) -> list:
    p = Path(root) / rel
    text = read(p)
    refs = _IMPORT.findall(text) + (_HTML_REF.findall(text) if p.suffix in (".html", ".htm") else [])
    amap = _alias_dirs(root)
    out = []
    for ref in refs:
        hit = resolve_import(root, rel, ref, amap)
        if hit:
            out.append(hit)
    return out


def _topics(prof: dict) -> list:
    return HTML_TOPICS if prof.get("framework") == "html" else APP_TOPICS


def retrieve(source: Path, donor: Path, task: str, scan: dict, keyword: str = "",
             budget_chars: int = 30000) -> dict:
    chunks = chunk_repo(source, "source") + chunk_repo(donor, "donor")
    total_chars = sum(len(c["text"]) for c in chunks)
    if not chunks:
        return {"chunks": [], "stats": {"total_chunks": 0, "total_chars": 0, "selected_chars": 0}}
    index = BM25(chunks)
    picked, why = [], {}

    def take(i, reason):
        if i not in why:
            picked.append(i)
            why[i] = reason

    by_path = defaultdict(list)
    for i, c in enumerate(chunks):
        by_path[(c["repo"], c["path"])].append(i)
    roots = {"source": source, "donor": donor}
    aliases = {r: (scan.get(r) or {}).get("aliases", []) for r in roots}
    kw = (keyword or "").lower().strip()
    # 1. the requested component: every chunk of donor files named after it, plus what they import
    if kw:
        for (repo, path), ids in sorted(by_path.items()):
            if kw in path.lower() and not path.endswith((".md", ".json")):
                for i in (ids if repo == "donor" else ids[:2]):
                    take(i, f"{'component file' if repo == 'donor' else 'existing ' + kw + ' in source'}")
        for i in list(picked):
            c = chunks[i]
            for rel in _neighbors(roots[c["repo"]], c["path"], aliases[c["repo"]]):
                for j in by_path.get((c["repo"], rel), []):
                    take(j, f"imported by {c['path']}")
    # 2. pages of a static source site, and both repos' entry/config files
    for (repo, path), ids in sorted(by_path.items()):
        if (scan.get(repo) or {}).get("framework") == "html" and path.endswith((".html", ".htm")):
            take(ids[0], "page")
    for repo in roots:
        for e in (scan.get(repo) or {}).get("entry_files", []):
            if (repo, e) in by_path:
                take(by_path[(repo, e)][0], "entry/config file")
    # 3. BM25: the task and framework-specific topics
    for repo in roots:
        for q in [task] + _topics(scan.get(repo) or {}):
            for i in index.search(q, repo, k=2):
                take(i, f"query: {q[:40]}")
    # 4. facts with file evidence
    for f in scan.get("facts", []):
        for e in f.get("evidence", []):
            for i in by_path.get((e["repo"], e["path"]), []):
                c = chunks[i]
                if c["start"] <= (e.get("line") or 1) <= c["end"]:
                    take(i, f"fact {f['id']}")
    # 5. one-hop import expansion
    for i in list(picked):
        c = chunks[i]
        for rel in _neighbors(roots[c["repo"]], c["path"], aliases[c["repo"]]):
            for j in by_path.get((c["repo"], rel), [])[:1]:
                take(j, f"imported by {c['path']}")

    out, used = [], 0
    for i in picked:
        c = chunks[i]
        if used + len(c["text"]) > budget_chars:
            continue
        used += len(c["text"])
        out.append({"id": f"R{len(out) + 1}", "repo": c["repo"], "path": c["path"], "start": c["start"],
                    "end": c["end"], "reason": why[i], "text": c["text"]})
    return {"chunks": out, "stats": {"total_chunks": len(chunks), "total_chars": total_chars,
                                     "candidate_chunks": len(picked), "selected_chunks": len(out),
                                     "selected_chars": used,
                                     "context_fraction": round(used / total_chars, 4) if total_chars else 0}}


def render(chunks: list) -> str:
    parts = []
    for c in chunks:
        numbered = "\n".join(f"{c['start'] + k:>5}| {line}" for k, line in enumerate(c["text"].splitlines()))
        parts.append(f"[{c['id']}] {c['repo']}:{c['path']} lines {c['start']}-{c['end']} ({c['reason']})\n{numbered}")
    return "\n\n".join(parts)


# =============================================================== component tasks
SYNONYMS = {
    "navbar": ["navbar", "navigation", "topbar", "header", "nav"],
    "nav": ["nav", "navbar", "navigation", "topbar", "header"],
    "header": ["header", "navbar", "navigation", "topbar", "nav"],
    "footer": ["footer"],
    "sidebar": ["sidebar", "drawer"],
    "hero": ["hero", "banner"],
}
_CODE = {".tsx", ".ts", ".jsx", ".js", ".mjs", ".vue", ".svelte", ".astro", ".html", ".htm", ".css", ".scss"}
_NOISE = (".test.", ".spec.", ".stories.", ".d.ts", ".min.")
_WIRING = ["src/App.tsx", "src/App.jsx", "src/App.js", "src/app/layout.tsx", "src/app/layout.jsx", "app/layout.tsx",
           "app/layout.jsx", "app/layout.js", "pages/_app.tsx", "pages/_app.jsx", "pages/_app.js", "src/main.tsx",
           "src/main.jsx", "src/index.tsx", "src/index.jsx", "src/index.js", "index.html", "public/index.html"]
_GLOBAL_CSS = ["src/index.css", "src/App.css", "src/app/globals.css", "app/globals.css", "styles/globals.css",
               "src/styles/globals.css", "src/styles/global.css", "tailwind.config.js", "tailwind.config.ts",
               "tailwind.config.cjs", "tailwind.config.mjs"]
_IS_PKG = re.compile(r"^(?:@[\w.-]+/[\w.-]+|[\w.-]+)")


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _usable(p: Path, root: Path) -> bool:
    rel = p.relative_to(root).as_posix().lower()
    return (p.suffix.lower() in _CODE and not any(n in rel for n in _NOISE)
            and not rel.startswith("public/") and "/public/" not in rel)


def find_component_files(root: Path, keyword: str, limit: int = 3) -> list:
    """Files that are the named component: file name first, then folder name, then synonyms, then a
    declaration inside the file. Returns repo-relative paths, best first."""
    root = Path(root)
    kw = _norm(keyword)
    words = [kw] + [_norm(w) for w in SYNONYMS.get(keyword.lower().strip(), []) if _norm(w) != kw]
    files = [p for p in walk(root, _CODE) if _usable(p, root)]

    def rank(p):
        # code before styles, component formats before plain .js/.ts, then shallower paths
        return (p.suffix in (".css", ".scss"), p.suffix not in (".tsx", ".jsx", ".vue", ".svelte", ".astro"),
                len(p.relative_to(root).parts), p.relative_to(root).as_posix())

    for w in words:
        if not w:
            continue
        by_stem = [p for p in files if w in _norm(p.stem)]
        if by_stem:
            return [p.relative_to(root).as_posix() for p in sorted(by_stem, key=rank)[:limit]]
        by_dir = [p for p in files if w in _norm("".join(p.relative_to(root).parent.parts[-2:])) and p.stem.lower() in ("index", "main")]
        if by_dir:
            return [p.relative_to(root).as_posix() for p in sorted(by_dir, key=rank)[:limit]]
    for w in words:
        decl = re.compile(r"(?:function|const|class|export\s+default)\s+\w*%s\w*" % re.escape(w), re.I) if w else None
        hits = [p for p in files if p.suffix not in (".css", ".scss", ".html", ".htm") and decl and decl.search(read(p, 60_000))]
        if hits:
            return [p.relative_to(root).as_posix() for p in sorted(hits, key=rank)[:limit]]
    return []


def import_closure(root: Path, starts: list, max_files: int = 10, max_depth: int = 2) -> tuple:
    """Breadth-first over local imports. Returns ([(path, depth, parent)], {package names})."""
    root = Path(root)
    amap = _alias_dirs(root)
    order = [(s, 0, "") for s in starts]
    seen, packages, i = set(starts), set(), 0
    while i < len(order):
        rel, depth, _ = order[i]
        i += 1
        text = read(root / rel, 200_000)
        for ref in _IMPORT.findall(text):
            hit = resolve_import(root, rel, ref, amap)
            if hit:
                if (hit in seen or depth + 1 > max_depth or len(order) >= max_files
                        or not _usable(root / hit, root)):
                    continue
                seen.add(hit)
                order.append((hit, depth + 1, rel))
            elif not ref.startswith((".", "/", "@/", "~/", "http", "data:")) and not any(
                    ref.startswith(a) for a in amap if a not in ("@/", "~/")):
                m = _IS_PKG.match(ref)
                if m:
                    packages.add(m.group(0))
    return order, packages


def _deps(root: Path) -> dict:
    try:
        d = json.loads(read(Path(root) / "package.json") or "{}")
    except ValueError:
        return {}
    return {**d.get("devDependencies", {}), **d.get("dependencies", {})}


def default_budget_chars() -> int:
    """30000 chars, or 7000 when Groq is the first provider: its free 8K tokens-per-minute limit cannot take
    more once the instructions, the report and the answer are added. Fixed per server run, so every
    condition of an experiment gets the same code."""
    try:
        from . import llm
        first = llm.configured()[:1]
    except Exception:
        first = []
    return 7000 if first == ["groq"] else 30000


def context_budget_chars() -> int:
    default = default_budget_chars()
    try:
        return max(6000, int(os.environ.get("FUSIONEDGES_CONTEXT_CHARS", default)))
    except ValueError:
        return default


def component_context(source: Path, donor: Path, scan: dict, keyword: str, budget_chars: int | None = None) -> dict:
    """The files an agent needs to move `keyword` from donor to source, and little else.

    Priority order (earlier = kept first when the budget is tight):
      0 donor component file(s)   1 source package.json   2 donor files they import (depth 1)
      3 source entry/layout/App   4 source's existing component   5 source global CSS / tailwind config
      6 donor files imported two hops away
    Over budget, whole files are dropped in the reverse of that order (but never the component itself) and the
    drop is recorded and shown to the model as NOT SHOWN.
    """
    source, donor = Path(source), Path(donor)
    budget = budget_chars or context_budget_chars()
    cands, dropped = [], []

    d_main = find_component_files(donor, keyword)
    closure, pkgs = import_closure(donor, d_main) if d_main else ([], set())
    for rel, depth, parent in closure:
        cands.append((0 if depth == 0 else 2 if depth == 1 else 6, "donor", rel,
                      "donor component" if depth == 0 else f"imported by {parent}"))
    s_prof = (scan.get("source") or {})
    s_main = find_component_files(source, keyword, limit=2)
    s_css = []
    for rel in s_main:
        for ref in _IMPORT.findall(read(source / rel, 100_000)):
            hit = resolve_import(source, rel, ref)
            if hit and hit.endswith((".css", ".scss")) and _usable(source / hit, source):
                s_css.append(hit)
    if (source / "package.json").is_file():
        cands.append((1, "source", "package.json", "source dependencies"))
    wiring = [r for r in list(s_prof.get("entry_files", [])) + _WIRING
              if (source / r).is_file() and r.endswith((".tsx", ".jsx", ".js", ".ts", ".mjs", ".html", ".htm"))]
    code_wiring = [r for r in wiring if not r.endswith((".html", ".htm"))]
    # an HTML shell only matters when there is no JS/TS entry or layout (plain HTML sites)
    for rel in (code_wiring or wiring):
        cands.append((3, "source", rel, "where the component is mounted"))
    for rel in s_main:
        cands.append((4, "source", rel, f"existing {keyword} in source (to replace)"))
    for rel in s_css:
        cands.append((4, "source", rel, "styles of the existing component"))
    for rel in _GLOBAL_CSS:
        if (source / rel).is_file():
            cands.append((5, "source", rel, "global styles / tailwind config"))

    seen, items = set(), []
    for prio, repo, rel, why in sorted(cands, key=lambda c: c[0]):  # stable: keeps discovery order inside a tier
        if (repo, rel) in seen:
            continue
        seen.add((repo, rel))
        text = read((donor if repo == "donor" else source) / rel)
        if not text.strip():
            continue
        cap = 14000 if prio == 0 else 9000
        truncated = len(text) > cap
        if truncated:
            text = text[:cap] + f"\n... (truncated, {len(text):,} chars total)"
        items.append({"repo": repo, "path": rel, "reason": why, "text": text, "truncated": truncated, "priority": prio})

    # Over budget: drop WHOLE files (a half-file is worse than a named gap), least useful first, never the
    # component itself. Order: two-hop imports, global CSS, then one-hop imports (largest first), then the
    # source's existing component, entry/layout, package.json. Truncating one big file would eat the room
    # that small, essential files (where to mount, what to replace) need.
    DROP_ORDER = {6: 0, 5: 1, 2: 2, 4: 3, 3: 4, 1: 5}
    used = sum(len(i["text"]) for i in items)
    while used > budget:
        pool = [i for i in items if i["priority"] in DROP_ORDER]
        if not pool:
            break
        victim = min(pool, key=lambda i: (DROP_ORDER[i["priority"]], -len(i["text"])))
        items.remove(victim)
        used -= len(victim["text"])
        dropped.append({"repo": victim["repo"], "path": victim["path"], "chars": len(victim["text"]),
                        "why": victim["reason"]})
    if used > budget and items:  # only the component itself is left
        main = items[0]
        main["text"] = main["text"][:budget] + "\n... (truncated to fit the context budget)"
        main["truncated"] = True
        used = len(main["text"])
    files = items

    d_deps, s_deps = _deps(donor), _deps(source)
    packages = [{"name": n, "donor": d_deps.get(n, "?"), "source": s_deps.get(n, "MISSING")} for n in sorted(pkgs)]
    return {"files": files, "dropped": dropped, "packages": packages,
            "found": {"donor": d_main, "source": s_main},
            "stats": {"files": len(files), "chars": used, "est_tokens": round(used / 3.5), "budget_chars": budget,
                      "donor_files": sum(1 for f in files if f["repo"] == "donor"),
                      "source_files": sum(1 for f in files if f["repo"] == "source"),
                      "truncated": sum(1 for f in files if f["truncated"]), "dropped": len(dropped)}}


def context_chunks(ctx: dict) -> list:
    """The selected whole files as numbered 'chunks' (ids R1..Rn), so the report agent can cite them by line."""
    out = []
    for i, f in enumerate(ctx["files"], 1):
        n = max(1, len(f["text"].splitlines()))
        out.append({"id": f"R{i}", "repo": f["repo"], "path": f["path"], "start": 1, "end": n,
                    "text": f["text"], "reason": f["reason"]})
    return out


def render_component_context(ctx: dict) -> str:
    parts = [f"===== {f['repo']}:{f['path']}  ({f['reason']}) =====\n{f['text']}" for f in ctx["files"]]
    if ctx["packages"]:
        rows = "\n".join(f"- {p['name']}: donor {p['donor']}, source {p['source']}" for p in ctx["packages"])
        parts.append("PACKAGES imported by the donor component files (source version or MISSING):\n" + rows)
    if ctx["dropped"]:
        parts.append("NOT SHOWN (over the size budget): " + ", ".join(f"{d['repo']}:{d['path']}" for d in ctx["dropped"]))
    return "\n\n".join(parts)
