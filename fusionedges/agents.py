"""The two LLM agents.

report agent : retrieved code (RAG) + scanner facts + read-only tools -> compatibility report (JSON)
edit agent   : builds the result in output/. Same system prompt, tools and step budget in both tabs;
               'prompt + report' only adds the report to the first message.
"""
from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

from . import rag
from .analyzer import SKIP_DIRS
from .llm import LLMError, Session, short_reason
from .prompts import EDIT_SYSTEM, ONESHOT_SYSTEM, REPORT_SYSTEM, REPORT_SYSTEM_COMPONENT
from .scanner import facts_markdown, read, tree, walk
from .verify import run_build

BINARY_SIG = (b"\x89PNG", b"\xff\xd8\xff", b"GIF87a", b"GIF89a", b"RIFF", b"wOFF", b"wOF2", b"%PDF", b"PK\x03\x04")
HIDDEN = SKIP_DIRS | {".git"}


def _tool(name, desc, props, required):
    return {"name": name, "description": desc,
            "input_schema": {"type": "object", "properties": props, "required": required}}


def _repo_prop(repos):
    return {"type": "string", "enum": list(repos)}


def make_tools(repos: tuple, writable: bool) -> list:
    t = [
        _tool("list_files", "List files under a directory (recursive, max depth 3).",
              {"repo": _repo_prop(repos), "path": {"type": "string", "description": "directory, default '.'"}}, ["repo"]),
        _tool("read_file", "Read a text file with line numbers. Long files: call again with start_line.",
              {"repo": _repo_prop(repos), "path": {"type": "string"}, "start_line": {"type": "integer"}}, ["repo", "path"]),
        _tool("search", "Regex search across a repo's text files. Returns path:line: text.",
              {"repo": _repo_prop(repos), "pattern": {"type": "string"}}, ["repo", "pattern"]),
    ]
    if writable:
        t += [
            _tool("write_file", "Create or overwrite a text file in OUTPUT with its full content.",
                  {"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]),
            _tool("delete_file", "Delete a file in OUTPUT (e.g. donor demo pages the result no longer uses).",
                  {"path": {"type": "string"}}, ["path"]),
            _tool("copy_asset", "Copy a binary/static file (image, font, svg, ico) unchanged from source or donor into OUTPUT.",
                  {"from_repo": {"type": "string", "enum": ["source", "donor"]}, "source": {"type": "string"},
                   "destination": {"type": "string", "description": "path in output, e.g. public/img/logo.png"}},
                  ["from_repo", "source", "destination"]),
            _tool("run_build", "Install dependencies if needed and run OUTPUT's build. Returns BUILD OK or BUILD FAILED with errors.",
                  {}, []),
        ]
    return t


class Workspace:
    def __init__(self, roots: dict, writable: str | None = None, build_enabled: bool = False):
        self.roots = {k: Path(v).resolve() for k, v in roots.items()}
        self.writable, self.build_enabled = writable, build_enabled
        self.builds = self.build_failures = self.tool_calls = 0
        self.writes, self.deletes = [], []

    def _safe(self, repo: str, rel: str) -> Path:
        if repo not in self.roots:
            raise ValueError(f"unknown repo '{repo}'; use one of {', '.join(self.roots)}")
        root = self.roots[repo]
        p = (root / (rel or ".").lstrip("/")).resolve()
        if p != root and root not in p.parents:
            raise ValueError("path escapes the repo")
        if set(p.relative_to(root).parts) & HIDDEN:
            raise ValueError("that directory is not available")
        return p

    def run(self, name: str, a: dict) -> str:
        self.tool_calls += 1
        try:
            if "_invalid_json" in a:
                return "error: your tool arguments were not valid JSON; call the tool again"
            if name == "list_files":
                root = self.roots.get(a.get("repo"))
                base = self._safe(a.get("repo"), a.get("path", "."))
                out = []
                for p in sorted(base.rglob("*")):
                    rel = p.relative_to(root)
                    if set(rel.parts) & HIDDEN or len(p.relative_to(base).parts) > 3:
                        continue
                    out.append(rel.as_posix() + ("/" if p.is_dir() else ""))
                    if len(out) >= 300:
                        out.append("... (truncated; list a subdirectory)")
                        break
                return "\n".join(out) or "(empty)"
            if name == "read_file":
                p = self._safe(a.get("repo"), a["path"])
                if not p.is_file():
                    return f"error: no such file {a['path']}"
                data = p.read_bytes()
                if b"\x00" in data[:4096] or data.startswith(BINARY_SIG):
                    return "error: binary file; use copy_asset"
                lines = data.decode("utf-8", errors="replace").splitlines()
                start = max(1, int(a.get("start_line") or 1))
                chunk, size = [], 0
                for i in range(start - 1, len(lines)):
                    row = f"{i + 1:>5}| {lines[i]}"
                    size += len(row) + 1
                    if size > 12000:
                        chunk.append(f"... (truncated; call again with start_line={i + 1})")
                        break
                    chunk.append(row)
                return "\n".join(chunk) or "(empty file)"
            if name == "search":
                root = self.roots.get(a.get("repo"))
                self._safe(a.get("repo"), ".")
                rx = re.compile(a["pattern"])
                out = []
                for p in sorted(root.rglob("*")):
                    rel = p.relative_to(root)
                    if not p.is_file() or set(rel.parts) & HIDDEN or p.stat().st_size > 300_000:
                        continue
                    for i, line in enumerate(read(p).splitlines(), 1):
                        if rx.search(line):
                            out.append(f"{rel.as_posix()}:{i}: {line.strip()[:160]}")
                            if len(out) >= 60:
                                return "\n".join(out + ["... (more matches)"])
                return "\n".join(out) or "(no matches)"
            if not self.writable:
                return "error: read-only"
            if name == "write_file":
                c = a.get("content", "")
                if "\x00" in c:
                    return "error: binary content; use copy_asset"
                p = self._safe(self.writable, a["path"])
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(c, encoding="utf-8")
                self.writes.append(a["path"])
                return f"wrote {a['path']} ({len(c)} chars)"
            if name == "delete_file":
                p = self._safe(self.writable, a["path"])
                if not p.is_file():
                    return f"error: no such file {a['path']}"
                p.unlink()
                self.deletes.append(a["path"])
                return f"deleted {a['path']}"
            if name == "copy_asset":
                src = self._safe(a.get("from_repo", "source"), a["source"])
                dst = self._safe(self.writable, a["destination"])
                if not src.is_file():
                    return f"error: file not found: {a['source']}"
                if src.stat().st_size > 20 * 1024 * 1024:
                    return "error: file larger than 20MB"
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dst)
                self.writes.append(a["destination"])
                return f"copied {a.get('from_repo')}:{a['source']} -> output:{a['destination']}"
            if name == "run_build":
                if not self.build_enabled:
                    return "build is not available (no build script, or npm not installed)"
                res = run_build(self.roots[self.writable])
                if res["skipped"]:
                    return f"no build available ({res['reason']})"
                self.builds += 1
                if not res["ok"]:
                    self.build_failures += 1
                return ("BUILD OK" if res["ok"] else "BUILD FAILED") + "\n" + res["output"][-3500:]
            return f"error: unknown tool {name}"
        except Exception as e:  # errors go back to the model
            return f"error: {e}"


def run_loop(session: Session, system: str, first: str, ws: Workspace, tools: list, max_steps: int, log) -> str:
    messages = [{"role": "user", "text": first}]
    final = ""
    for _ in range(max_steps):
        msg = session.chat(system, messages, tools, max_tokens=8192)
        messages.append({"role": "assistant", "text": msg.get("text", ""), "tool_calls": msg.get("tool_calls", [])})
        if (msg.get("text") or "").strip():
            final = msg["text"]
            log(f"[{session.label}] {msg['text'].strip()[:300]}")
        if not msg.get("tool_calls"):
            return final
        results = []
        for c in msg["tool_calls"]:
            a = c["args"]
            arg = a.get("path") or a.get("pattern") or a.get("destination") or ""
            log(f"[{session.label}] {c['name']} {a.get('repo', a.get('from_repo', ''))} {str(arg)[:90]}".rstrip())
            results.append({"id": c["id"], "name": c["name"], "content": ws.run(c["name"], a)})
        messages.append({"role": "tool", "results": results})
    log(f"[{session.label}] reached the step limit ({max_steps})")
    messages.append({"role": "user", "text": "Step limit reached. Stop calling tools and give your final answer now."})
    return session.chat(system, messages, None, max_tokens=8192).get("text", "") or final


STATIC_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".woff", ".woff2", ".ttf", ".otf", ".eot",
               ".mp4", ".mp3", ".pdf", ".zip", ".map", ".db"}


def code_tree(root: Path, limit: int = 80) -> list:
    """File list for component tasks: code and config only. Static assets under public/ and images are
    counted, not listed (a one-shot model copies assets by the paths it sees in the code)."""
    out, skipped = [], 0
    for p in walk(Path(root)):
        rel = p.relative_to(root).as_posix()
        if (p.suffix.lower() in STATIC_EXTS or rel.startswith("public/") or p.suffix.lower() in (".md", ".mdx", ".txt")
                or p.name in rag.SKIP_NAMES):
            skipped += 1
            continue
        if len(out) >= limit:
            skipped += 1
            continue
        out.append(rel)
    if skipped:
        out.append(f"... ({skipped} static assets, docs and further files not listed)")
    return out


def _brief(name: str, prof: dict, root: Path, compact: bool = False, files_list: bool = True) -> str:
    keep = {k: prof[k] for k in ("framework", "router", "typescript", "versions", "styling", "tailwind",
                                 "aliases", "entry_files") if k in prof}
    keep["scripts"] = {k: v for k, v in (prof.get("scripts") or {}).items() if k in ("dev", "build", "start")}
    files = (code_tree(root) if compact else tree(root, 200)) if files_list else []
    return f"## {name.upper()} files\nprofile: {json.dumps(keep)}\n" + "\n".join(files)


# ------------------------------------------------------------ report agent
CONFLICT_TYPES = ["framework", "rendering", "routing", "dependency", "version", "styling", "css-collision",
                  "config", "env", "asset", "behavior", "layout", "naming", "data", "other"]


def extract_json(text: str):
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text or "", re.S)
    raw = m.group(1) if m else None
    if raw is None:
        i, j = (text or "").find("{"), (text or "").rfind("}")
        raw = text[i:j + 1] if i >= 0 and j > i else None
    if raw is None:
        return None
    for candidate in (raw, re.sub(r",\s*([}\]])", r"\1", raw)):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return None


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def validate_report(rep: dict, scan: dict, roots: dict, chunks: list) -> dict:
    """Check citations (fact ids, chunk ids, file:line snippets); add high/medium facts the agent skipped."""
    facts = {f["id"]: f for f in scan["facts"]}
    chunk_ids = {c["id"] for c in chunks}
    cited, out = set(), []
    for c in rep.get("conflicts") or []:
        if not isinstance(c, dict):
            continue
        c["type"] = c.get("type") if c.get("type") in CONFLICT_TYPES else "other"
        c["severity"] = c.get("severity") if c.get("severity") in ("high", "medium", "low") else "medium"
        c["confidence"] = c.get("confidence") if c.get("confidence") in ("high", "medium", "low") else "medium"
        good_f = [f for f in c.get("facts") or [] if f in facts]
        good_r = [r for r in c.get("chunks") or [] if r in chunk_ids]
        bad = [x for x in (c.get("facts") or []) + (c.get("chunks") or []) if x not in facts and x not in chunk_ids]
        cited |= set(good_f)
        checked = []
        for e in c.get("evidence") or []:
            if not isinstance(e, dict):
                continue
            root = roots.get(e.get("repo"))
            path = str(e.get("path") or "").lstrip("/")
            ok, why = False, "unknown repo"
            if root is not None:
                p = (Path(root) / path).resolve()
                if Path(root).resolve() not in p.parents or not p.is_file():
                    why = "file not found"
                else:
                    lines = read(p).splitlines()
                    try:
                        ln = int(e.get("line") or 0)
                    except (TypeError, ValueError):
                        ln = 0
                    snip = _norm(e.get("snippet", ""))
                    if ln and not 1 <= ln <= len(lines):
                        why = "line out of range"
                    elif snip:
                        window = lines[max(0, ln - 4):ln + 3] if ln else lines
                        ok = any(snip[:60] in _norm(l) or _norm(l)[:60] in snip for l in window if _norm(l))
                        why = "" if ok else "snippet not found at that line"
                    else:
                        ok, why = True, ""
            checked.append({**e, "path": path, "verified": ok, "problem": why})
        c["evidence"], c["facts"], c["chunks"] = checked, good_f, good_r
        n_ok = len(good_f) + len(good_r) + sum(1 for e in checked if e["verified"])
        n_all = n_ok + len(bad) + sum(1 for e in checked if not e["verified"])
        c["citation"] = "verified" if n_all and n_ok == n_all else ("partial" if n_ok else "unverified")
        if bad:
            c["citation_note"] = "unknown ids: " + ", ".join(bad)
        c["basis"] = "scanner" if good_f else ("retrieved" if good_r else "inferred")
        out.append(c)
    for f in scan["facts"]:
        if f["id"] in cited or f["severity"] == "low":
            continue
        out.append({"type": _fact_type(f["kind"]), "title": f["kind"].replace("-", " "), "severity": f["severity"],
                    "confidence": "high", "basis": "scanner (not discussed by agent)", "facts": [f["id"]], "chunks": [],
                    "evidence": [{**e, "verified": True, "problem": ""} for e in f["evidence"]],
                    "impact": f["text"], "adaptation": "", "citation": "verified"})
    order = {"high": 0, "medium": 1, "low": 2}
    out.sort(key=lambda c: order.get(c["severity"], 1))
    for i, c in enumerate(out, 1):
        c["id"] = f"C{i}"
    rep["conflicts"] = out
    for k, default in (("summary", ""), ("page_map", []), ("layout", {}), ("style_map", []),
                       ("behavior", []), ("assets", []), ("plan", []), ("donor_files", []), ("mount", {}),
                       ("dependencies", [])):
        if not isinstance(rep.get(k), type(default)):
            rep[k] = default
    return rep


def _fact_type(kind: str) -> str:
    return {"framework-mismatch": "framework", "routing-model": "routing", "version-mismatch": "version",
            "language": "config", "path-alias": "config", "styling-system": "styling", "styling-config": "styling",
            "env-var": "env", "env-access": "env", "framework-api": "framework", "server-rendering": "rendering",
            "css-entry": "styling", "behavior-js": "behavior", "external-resource": "dependency",
            "css-assets": "asset", "asset-reference": "asset", "layout-positioning": "layout", "responsive": "layout",
            "css-class-collision": "css-collision", "global-selector-leak": "css-collision",
            "css-variable-conflict": "css-collision"}.get(kind, "other")


def run_report_agent(task: str, source: Path, donor: Path, scan: dict, log, max_steps: int = 6,
                     component: str = "", budget_chars: int | None = None) -> tuple:
    s = Session("report", log)
    system = REPORT_SYSTEM_COMPONENT if component else REPORT_SYSTEM
    ctx = rag.component_context(source, donor, scan, component, budget_chars) if component else None
    extra = ""
    listed, use_tools = True, True  # include each repo's file list and give the agent tools
    if ctx and ctx["found"]["donor"]:
        use_tools = os.environ.get("FUSIONEDGES_REPORT_TOOLS", "") == "1"  # off: the context is already complete
        listed = use_tools  # the exact files are shown; a file list would only repeat them (tools can still list)
        chunks = rag.context_chunks(ctx)
        st = {**ctx["stats"], "selected_chunks": len(chunks), "selected_chars": ctx["stats"]["chars"]}
        retrieved = {"chunks": chunks, "stats": st}
        extra = "\n".join(f"- {p['name']}: donor {p['donor']}, source {p['source']}" for p in ctx["packages"])
        extra = ("PACKAGES imported by the donor component files (source version or MISSING):\n" + extra + "\n\n") if extra else ""
        if ctx["dropped"]:
            extra += "NOT SHOWN (over the size budget): " + ", ".join(f"{d['repo']}:{d['path']}" for d in ctx["dropped"]) + "\n\n"
        log(f"[report] context: {st['files']} files ({st['donor_files']} donor, {st['source_files']} source), "
            f"{st['chars']:,} chars (~{st['est_tokens']:,} tokens) from the {component} and what it imports"
            + (f"; left out for size: {len(ctx['dropped'])}" if ctx["dropped"] else ""))
    else:
        retrieved = rag.retrieve(source, donor, task, scan, keyword=component)
        st = retrieved["stats"]
        log(f"[report] retrieved {st['selected_chunks']} chunks ({st['selected_chars']:,} chars, "
            f"{st.get('context_fraction', 0):.0%} of both repos)")
    roots = {"source": source, "donor": donor}
    ws = Workspace(roots)
    first = (f"TASK:\n{task}\n\nSCANNER FACTS:\n{facts_markdown(scan)}\n\n"
             f"RETRIEVED CODE:\n{rag.render(retrieved['chunks'])}\n\n{extra}"
             f"{_brief('source', scan['source'], source, True, listed)}\n\n{_brief('donor', scan['donor'], donor, True, listed)}\n\n"
             + ("Use the tools only for what the retrieved code does not show, then output the JSON report." if use_tools else
                "No tools are available. Work only from the code above, then output the JSON report."))
    parts = {"system prompt": system, "scanner facts": facts_markdown(scan), "code": rag.render(retrieved["chunks"]),
             "repo descriptions": _brief('source', scan['source'], source, True, listed)
             + _brief('donor', scan['donor'], donor, True, listed)}
    log("[report] first message: " + ", ".join(f"{k} ~{round(len(v) / 3.6):,}" for k, v in parts.items())
        + f" = ~{round(sum(len(v) for v in parts.values()) / 3.6):,} tokens (est.)" + ("; each tool step resends all of it" if use_tools else "; one call, no tools"))
    text = run_loop(s, system, first, ws, make_tools(("source", "donor"), False) if use_tools else [],
                    max_steps if use_tools else 1, log)
    rep = extract_json(text)
    if rep is None:
        log("[report] answer was not valid JSON; asking once more")
        msg = s.chat(system, [{"role": "user", "text": first}, {"role": "assistant", "text": text[:6000]},
                                     {"role": "user", "text": "Output only the JSON object, in a ```json block."}],
                     max_tokens=8192)
        rep = extract_json(msg.get("text", ""))
    if rep is None:
        log("[report] no usable JSON; the report contains scanner facts only")
        rep = {"summary": "The report agent did not return a usable report; scanner facts only.", "agent_failed": True}
    rep = validate_report(rep, scan, roots, retrieved["chunks"])
    rep["retrieval"] = {"stats": st, "chunks": [{k: c[k] for k in ("id", "repo", "path", "start", "end", "reason")}
                                                for c in retrieved["chunks"]]}
    return rep, s.totals()


HEDGE = re.compile(r"none (?:detected|obvious|found)|\bpotential(?:ly)?\b|\bmight\b|\bpossibly\b|\baudit\b|\bif any\b", re.I)
STOP = {"the", "a", "an", "of", "in", "to", "and", "or", "vs", "not", "is", "are", "uses", "use", "source", "donor", "with"}


def _words(s: str) -> set:
    return {w for w in re.findall(r"[a-z0-9@/.-]+", (s or "").lower()) if w not in STOP and len(w) > 2}


TOPICS = [("script-coupling", r"global script|site.s (?:own )?scripts?|main\.js|jquery|offset\(\)|\.navbar\b|runtime error"),
          ("routing", r"usepathname|useroute|useparams|router|routing|navigation api|link component|react-router"),
          ("alias", r"alias|@/"),
          ("typescript", r"typescript|\.tsx?\b|type annotation"),
          ("tailwind", r"tailwind|utility class"),
          ("css-variable", r"css variable|custom propert|--[a-z]"),
          ("css-scope", r"global (?:css|selector)|selectors?|leak|collision|scope"),
          ("react-version", r"react 1[89]|react version"),
          ("dependency", r"missing|librar|dependenc|package"),
          ("framework", r"next\.?js|framework|server|\bnext/")]


def topic_of(c: dict) -> str:
    text = f"{c.get('title', '')} {c.get('adaptation', '')}".lower()
    for name, rx in TOPICS:
        if re.search(rx, text):
            return name
    return ""


def useful_conflicts(conflicts: list, limit: int = 5) -> list:
    """The few conflicts worth sending to the coding agent: verified, concrete, not repeats of each other.
    Drops hedged advice ('none detected', 'potential', 'audit'), unverified claims and near-duplicates."""
    order = {"high": 0, "medium": 1, "low": 2}
    keep = []
    for c in sorted((c for c in conflicts if isinstance(c, dict)), key=lambda c: order.get(c.get("severity"), 3)):
        if c.get("severity") == "low" or c.get("citation") == "unverified" or not c.get("adaptation"):
            continue
        if HEDGE.search(f"{c.get('title', '')} {c.get('adaptation', '')}"):
            continue
        topic = topic_of(c)
        w = _words(c.get("title", "")) | _words(c.get("adaptation", ""))
        if any((topic and topic == k[0]) or len(w & k[1]) / max(1, len(w | k[1])) > 0.6 for _, k in keep):
            continue
        keep.append((c, (topic, w)))
    return [c for c, _ in keep[:limit]]


def report_for_agent(r: dict, packages: list | None = None, contracts: list | None = None) -> str:
    """The report as the coding agent needs it, and no more: where it mounts, what is missing, and the few
    concrete changes that matter. Citations, evidence and hedged or repeated findings stay in the saved
    report.md for people; to a small model they are noise. `packages` is the deterministic list of
    {name, donor, source} the donor component imports (source is 'MISSING' when absent)."""
    out = []
    summary = (r.get("summary") or "").strip()
    if summary:
        out.append(summary.split(". ")[0][:220].rstrip(".") + ".")
    mt = r.get("mount") or {}
    if mt.get("file"):
        out.append(f"Mount: {mt['file']}" + (f"; replace {mt['replaces']}" if mt.get("replaces") else "") + ".")
    missing = [p for p in (packages or []) if p.get("source") == "MISSING"]
    if missing:
        out.append("Imported by the donor files but missing in the source: " +
                   ", ".join(f"{p['name']} ({p['donor']})" for p in missing) +
                   ". Add one only if the component truly needs it, otherwise replace it with plain code.")
    for f in contracts or []:      # scanner-verified facts about the site's own scripts: always sent, never filtered
        out.append("Keep: " + f["text"])
    deps = [d for d in r.get("dependencies") or [] if isinstance(d, dict) and d.get("action")]
    if deps and not missing:
        out.append("Dependencies: " + "; ".join(f"{d.get('name')}: {d['action']}" for d in deps[:5]))
    cs = useful_conflicts(r.get("conflicts", []))
    if cs:
        out.append("Change these:")
        out += [f"- {c.get('title', '').rstrip('.')}: {c['adaptation'].rstrip('.')}." for c in cs]
    return "\n".join(out)


def report_markdown(r: dict) -> str:
    out = ["# Compatibility report", "", r.get("summary", ""), ""]
    if r.get("page_map"):
        out += ["## Page map"] + [f"- {p.get('source')} -> {p.get('target')} ({p.get('route', '')}) {p.get('notes', '')}".rstrip()
                                  for p in r["page_map"] if isinstance(p, dict)] + [""]
    if r.get("donor_files"):
        out += ["## Donor files"] + [f"- {d.get('path')} ({d.get('role', '')})" for d in r["donor_files"]
                                     if isinstance(d, dict)] + [""]
    mt = r.get("mount") or {}
    if mt:
        out += ["## Mount", f"- file: {mt.get('file', '')}", f"- replaces: {mt.get('replaces') or 'nothing'}",
                f"- {mt.get('notes', '')}", ""]
    if r.get("dependencies"):
        out += ["## Dependencies"] + [f"- {d.get('name')}: {d.get('status', '')} -> {d.get('action', '')}"
                                      for d in r["dependencies"] if isinstance(d, dict)] + [""]
    lay = r.get("layout") or {}
    if lay:
        out += ["## Shared layout", f"- shared parts: {', '.join(map(str, lay.get('shared', [])))}",
                f"- donor files: {', '.join(map(str, lay.get('donor_files', [])))}", f"- {lay.get('notes', '')}", ""]
    for key, title in (("style_map", "Style map"), ("behavior", "Behavior"), ("assets", "Assets")):
        rows = [x for x in r.get(key) or [] if isinstance(x, dict)]
        if rows:
            out += [f"## {title}"] + [f"- {x.get('source')} -> {x.get('target')} {x.get('notes', '')}".rstrip() for x in rows] + [""]
    out += ["## Conflicts"]
    for c in r.get("conflicts", []):
        out.append(f"### {c['id']} [{c['severity'].upper()}] {c.get('title', '')} ({c['type']}; {c['basis']}; citation {c.get('citation')})")
        if c.get("impact"):
            out.append(f"Impact: {c['impact']}")
        for e in c.get("evidence", [])[:3]:
            out.append(f"- evidence: {e.get('repo')}:{e.get('path')}:{e.get('line', '')} {_norm(e.get('snippet', ''))[:120]}")
        if c.get("adaptation"):
            out.append(f"Do: {c['adaptation']}")
        out.append("")
    if r.get("plan"):
        out += ["## Plan"] + [f"{i}. {p}" for i, p in enumerate(r["plan"], 1)]
    return "\n".join(out)


# -------------------------------------------------------------- edit agent
def run_edit_agent(task: str, source: Path, donor: Path, output: Path, scan: dict, build_enabled: bool, log,
                   report: dict | None = None, max_steps: int = 45) -> dict:
    s = Session("agent", log)
    ws = Workspace({"source": source, "donor": donor, "output": output}, writable="output", build_enabled=build_enabled)
    first = (f"TASK:\n{task}\n\n"
             f"{_brief('source', scan['source'], source)}\n\n{_brief('donor', scan['donor'], donor)}\n\n"
             f"## OUTPUT (writable) currently contains\n" + "\n".join(tree(output, 200)))
    if report is not None:
        first += ("\n\nCOMPATIBILITY REPORT for this exact task (prepared from both repos; follow its page map, style map "
                  "and adaptations, and resolve every high-severity conflict):\n\n" + report_markdown(report)[:18000])
    summary = run_loop(s, EDIT_SYSTEM, first, ws, make_tools(("source", "donor", "output"), True), max_steps, log)
    t = s.totals()
    t.update({"summary": summary[:3000], "tool_calls": ws.tool_calls, "builds": ws.builds,
              "build_failures": ws.build_failures, "retries": max(0, ws.builds - 1),
              "files_written": sorted(set(ws.writes)), "files_deleted": ws.deletes})
    return t


# ------------------------------------------------------------ one-shot editor
_BLOCK = re.compile(r"<<<FILE\s+(.+?)>>>\n(.*?)\n?<<<END>>>", re.S)
_DELETE = re.compile(r"<<<DELETE\s+(.+?)>>>")
_COPYB = re.compile(r"<<<COPY\s+(source|donor):(.+?)\s*->\s*(.+?)>>>")


def apply_blocks(text: str, ws: Workspace) -> dict:
    """Apply FILE / DELETE / COPY blocks to the output workspace."""
    res = {"written": [], "deleted": [], "copied": [], "errors": []}
    for m in _BLOCK.finditer(text):
        path, body = m.group(1).strip().strip("`"), m.group(2)
        body = re.sub(r"^```[\w-]*\n|\n```\s*$", "", body)  # models sometimes wrap content in fences
        r = ws.run("write_file", {"path": path, "content": body + ("\n" if not body.endswith("\n") else "")})
        (res["written"] if r.startswith("wrote") else res["errors"]).append(path if r.startswith("wrote") else f"{path}: {r}")
    for m in _DELETE.finditer(text):
        r = ws.run("delete_file", {"path": m.group(1).strip()})
        (res["deleted"] if r.startswith("deleted") else res["errors"]).append(m.group(1).strip() if r.startswith("deleted") else r)
    for m in _COPYB.finditer(text):
        r = ws.run("copy_asset", {"from_repo": m.group(1), "source": m.group(2).strip(), "destination": m.group(3).strip()})
        (res["copied"] if r.startswith("copied") else res["errors"]).append(r)
    return res


def files_blocks(retrieved: dict, roots: dict, budget_chars: int = 48000, per_file: int = 14000) -> list:
    """Whole files (not chunks) for the files retrieval selected, in retrieval order: a one-shot model must
    see complete files to rewrite them. One string per file."""
    seen, parts, used = set(), [], 0
    for c in retrieved["chunks"]:
        key = (c["repo"], c["path"])
        if key in seen or c["path"].lower().endswith((".md", ".mdx", ".txt", "license")):
            continue
        seen.add(key)
        text = read(Path(roots[c["repo"]]) / c["path"])
        if len(text) > per_file:
            text = text[:per_file] + f"\n... (truncated, {len(text):,} chars total)"
        block = f"===== {c['repo']}:{c['path']} =====\n{text}"
        if used + len(block) > budget_chars:
            continue
        used += len(block)
        parts.append(block)
    return parts


def files_context(retrieved: dict, roots: dict, budget_chars: int = 48000, per_file: int = 14000) -> str:
    return "\n\n".join(files_blocks(retrieved, roots, budget_chars, per_file))


def run_oneshot(task: str, source: Path, donor: Path, output: Path, scan: dict, build_enabled: bool, log,
                report: dict | None = None, component: str = "", started_from: str = "source",
                max_repairs: int = 2, budget_chars: int | None = None) -> dict:
    """One request returns all files; if the build fails, the errors go back for up to `max_repairs` fixes."""
    s = Session("oneshot", log)
    roots = {"source": source, "donor": donor}
    ws = Workspace({"source": source, "donor": donor, "output": output}, writable="output",
                   build_enabled=build_enabled)
    comp = rag.component_context(source, donor, scan, component, budget_chars) if component else None
    if comp is not None and not comp["found"]["donor"]:
        log(f"[oneshot] no '{component}' file found in the donor; falling back to similarity search")
        comp = None
    if comp is not None:
        # "add one component": the component and everything it imports, plus the source's wiring points.
        # Deterministic and identical with or without a report.
        ctx = rag.render_component_context(comp)
        st = comp["stats"]
        log(f"[oneshot] context: {st['files']} files ({st['donor_files']} donor, {st['source_files']} source), "
            f"{st['chars']:,} chars (~{st['est_tokens']:,} tokens) from the {component} and what it imports; "
            f"budget {st['budget_chars']:,} chars ("
            + ("from FUSIONEDGES_CONTEXT_CHARS" if os.environ.get("FUSIONEDGES_CONTEXT_CHARS") else
               "default: FUSIONEDGES_CONTEXT_CHARS is not set in this process") + ")"
            + (f"; left out for size: {', '.join(d['path'] for d in comp['dropped'])}" if comp["dropped"] else ""))
        context_info = {"mode": "component-closure", **st, "donor_component": comp["found"]["donor"],
                        "left_out": [d["path"] for d in comp["dropped"]]}
    else:
        retrieved = rag.retrieve(source, donor, task, scan, keyword=component)
        blocks = files_blocks(retrieved, roots)
        ctx = "\n\n".join(blocks)
        log(f"[oneshot] context: {len(retrieved['chunks'])} retrieved chunks -> {len(blocks)} whole files, "
            f"{len(ctx):,} chars (~{round(len(ctx) / 3.5):,} tokens)")
        context_info = {"mode": "retrieval", "chunks": len(retrieved["chunks"]), "files": len(blocks),
                        "chars": len(ctx), "est_tokens": round(len(ctx) / 3.5)}
    compact = comp is not None
    first = (f"TASK:\n{task}\n\nThe OUTPUT project currently is a copy of the {started_from.upper()}. "
             f"Paths you write are relative to OUTPUT.\n\n"
             f"{_brief('source', scan['source'], source, compact)}\n\n{_brief('donor', scan['donor'], donor, compact, not compact)}\n\n"
             f"RELEVANT FILES (full content):\n{ctx}")
    base_chars = len(first)
    if report is not None:
        first += ("\n\nCOMPATIBILITY REPORT for this exact task (prepared from both repos; follow its adaptations "
                  "and apply the changes it lists):\n\n" + report_for_agent(report, comp["packages"] if comp else None,
                                                                                      [f for f in scan["facts"] if f["kind"] == "host-script-coupling"])[:4000])
    log(f"[oneshot] first message: {base_chars:,} chars (~{round(base_chars / 3.5):,} tokens)"
        + (f"; with the report {len(first):,} chars (~{round(len(first) / 3.5):,} tokens)" if report is not None else "")
        + ". If a provider answers 413, its limit is below this plus the answer size.")
    messages = [{"role": "user", "text": first}]
    applied = {"written": [], "deleted": [], "copied": [], "errors": []}
    summary, builds, failures, attempts = "", 0, 0, 0

    def ask(msgs):
        """One answer; ask to continue if it was cut off before <<<DONE>>>."""
        text = ""
        for _ in range(4):
            msg = s.chat(ONESHOT_SYSTEM, msgs, None, max_tokens=16000)
            text += msg.get("text", "")
            if "<<<DONE>>>" in text or not msg.get("text"):
                break
            msgs = msgs + [{"role": "assistant", "text": msg.get("text", "")},
                           {"role": "user", "text": "Continue exactly where you stopped (finish the current file "
                                                    "block), then end with <<<DONE>>>."}]
            log("[oneshot] answer was cut off; asking it to continue")
        return text

    run_error = ""
    for attempt in range(1 + max_repairs):
        attempts = attempt + 1
        try:
            text = ask(messages)
        except LLMError as e:
            run_error = str(e)
            log(f"[oneshot] attempt {attempts} could not be completed: {short_reason(run_error)[:200]}")
            if not applied["written"] and not applied["copied"] and not applied["deleted"]:
                raise
            break
        got = apply_blocks(text, ws)
        try:    # keep every raw answer next to the run, so an unparseable one can be inspected afterwards
            with open(output.parent / "answers.txt", "a", encoding="utf-8") as fh:
                fh.write(f"\n\n===== attempt {attempts} ({len(text):,} chars) =====\n{text}")
        except OSError:
            pass
        if not (got["written"] or got["copied"] or got["deleted"]):
            log(f"[oneshot] attempt {attempts}: no <<<FILE>>> block found in an answer of {len(text):,} chars. "
                f"Starts: {text[:200]!r} ... ends: {text[-150:]!r}")
        for k in applied:
            applied[k] += got[k]
        summary = text.split("<<<DONE>>>", 1)[1].strip() if "<<<DONE>>>" in text else summary
        log(f"[oneshot] attempt {attempts}: wrote {len(got['written'])}, deleted {len(got['deleted'])}, "
            f"copied {len(got['copied'])}" + (f", {len(got['errors'])} errors: {'; '.join(str(e)[:120] for e in got['errors'][:3])}"
                                          if got["errors"] else ""))
        if not got["written"] and not got["copied"] and not got["deleted"]:
            if attempt == 0:
                messages += [{"role": "assistant", "text": text[:4000]},
                             {"role": "user", "text": "You did not output any <<<FILE ...>>> blocks. Output the files now "
                                                      "in the required format."}]
                continue
            break
        if not build_enabled:
            break
        res = run_build(output)
        if res.get("skipped"):
            break
        builds += 1
        if res["ok"]:
            log("[oneshot] build passed")
            break
        failures += 1
        if res.get("reason") == "npm install failed":
            log("[oneshot] npm install failed (network or package problem, not the code); not retrying")
            break
        if attempt >= max_repairs:
            log("[oneshot] build still failing; out of repair attempts")
            break
        log(f"[oneshot] build failed; sending the errors back (repair {attempt + 1}/{max_repairs})")
        # A repair is a fresh, small request: the build errors plus the current files, not the whole first
        # message and the earlier answer again (that tripled the size and broke small provider limits).
        files, room = [], 14000
        for p in sorted(set(applied["written"])):
            body = read(output / p)[:min(8000, max(room, 0))]
            if body:
                files.append(f"===== output:{p} =====\n{body}")
                room -= len(body)
        pkg = read(output / "package.json")[:1500]
        messages = [{"role": "user", "text": (
            f"The OUTPUT project is a React/web project. You just added the {component or 'requested change'} "
            f"from a donor repo, and the build FAILED.\n\nBUILD OUTPUT (end):\n{res['output'][-3000:]}\n\n"
            f"CURRENT FILES YOU WROTE:\n" + "\n\n".join(files) + f"\n\nOUTPUT package.json:\n{pkg}\n\n"
            "Fix the errors. Output only the files that change, in the same format, then <<<DONE>>>.")}]
    t = s.totals()
    t.update({"summary": summary[:3000], "attempts": attempts, "builds": builds, "build_failures": failures,
              "retries": max(0, builds - 1), "files_written": sorted(set(applied["written"])),
              "files_deleted": applied["deleted"], "files_copied": applied["copied"],
              "apply_errors": applied["errors"], "tool_calls": 0,
              "context": context_info})
    if run_error:
        t["error" if not applied["written"] else "repair_error"] = run_error[:600]
    return t


SIZE_ERROR = re.compile(r"limit\D{0,4}(\d[\d,]*)\D{0,40}requested\D{0,4}(\d[\d,]*)", re.I)


def size_error(msg: str):
    """(limit, requested) when a provider refused the request as too large (HTTP 413), else None."""
    m = SIZE_ERROR.search(msg or "")
    if m and "413" in msg:
        a, b = (int(x.replace(",", "")) for x in m.groups())
        if b > a > 0:
            return a, b
    return None


def fit_to_limit(call, log, start: int | None = None, floor: int = 6000, tries: int = 3, used: dict | None = None):
    """Run `call(budget_chars)`. If a provider answers 413 (request larger than its per-minute limit), cut the
    code budget in proportion to how far over it was and run again. Only the amount of code shown changes,
    never what the experiment compares, and the budget used is logged."""
    from . import llm
    budget = start or rag.context_budget_chars()
    for i in range(tries):
        try:
            result = call(budget)
            if used is not None:
                used["budget"] = budget
            return result
        except LLMError as e:
            over = size_error(str(e))
            if not over or budget <= floor or i == tries - 1:
                raise
            limit, asked = over
            new = max(floor, int(budget * min(0.9, 0.8 * limit / asked)))
            log(f"[fit] provider limit {limit:,}, request was {asked:,}; cutting the code budget "
                f"{budget:,} -> {new:,} chars and trying again")
            llm.reset_cooldowns()
            budget = new
