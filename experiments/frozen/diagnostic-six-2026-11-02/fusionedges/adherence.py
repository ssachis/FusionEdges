"""Did the agent act on the compatibility report? Read it off the diff.

For every item the coding agent was given (the report's useful conflicts plus the scanner's must-keep facts),
pull concrete markers out of its instruction (identifiers, packages, class names, paths) and test them against
the lines the run ADDED:
  "Replace usePathname with useLocation"  -> usePathname absent, useLocation present
  "Remove 'use client'"                   -> 'use client' absent
  "Keep classes navbar navbar-default"    -> navbar, navbar-default present
Status per item: addressed (all tests pass), partial, not addressed, or unclear (no testable marker).

The same items are tested against the agent-alone run of the same task. That separates "the agent did it
because the report said so" from "the agent would have done it anyway", which is the report's real effect.

Blast radius: existing files of the starting copy the run modified or deleted, other than the mount file and
the file it replaces. A cheap regression signal before screenshots exist.
"""
from __future__ import annotations

import re

from .agents import useful_conflicts

STOPWORDS = {"the", "and", "with", "into", "from", "this", "that", "component", "components", "file", "files", "class",
             "classes", "import", "imports", "source", "donor", "navbar", "react", "use", "code", "css", "styles",
             "style", "make", "sure", "only", "each", "all", "its", "their", "keep", "replace", "remove", "add",
             # English that also shows up in code comments
             "top-level", "built-in", "client-side", "server-side", "third-party", "re-render", "full-width",
             "mobile-first", "read-only", "e.g", "i.e", "etc"}
QUOTED = re.compile(r"""[`'"]([^`'"\n]{2,60})[`'"]""")
IDENT = re.compile(r"(?<![\w-])(@?[a-zA-Z_][\w-]*(?:[/.][\w@-]+)+|[a-z]+[A-Z]\w*|[a-z][a-z0-9]*-[a-z0-9-]+)(?![\w-])")
SPLIT = re.compile(r"\b(?:replace|convert|swap|change|switch)\s+(.+?)\s+(?:with|to|into|by|for)\s+(.+)", re.I | re.S)
GONE = re.compile(r"\b(?:remove|strip|drop|delete|avoid|eliminate|get rid of)\s+(.+?)(?:[.;]|$| and )", re.I | re.S)


def markers(text: str) -> set:
    out = set()
    for q in QUOTED.findall(text or ""):
        q = q.strip()
        if " " in q and not q.startswith(("use ", "use client")):
            out |= {w for w in q.split() if len(w) > 3 and w.lower() not in STOPWORDS and re.search(r"[-/.A-Z]", w)}
        elif q.lower() not in STOPWORDS:
            out.add(q)
    out |= {m for m in IDENT.findall(text or "") if m.lower() not in STOPWORDS and not m.endswith(".")}
    return {m.strip(" ,()") for m in out if len(m.strip(" ,()")) > 2}


def tests_for(instruction: str, vocab: str | None = None) -> list:
    """[(marker, should_be_present)]. With vocab (the two repos' code), markers that never occur in either
    repo are dropped: those are English ('top-level', 'stub/mock'), not code."""
    t, seen = [], set()
    m = SPLIT.search(instruction or "")
    if m:
        t += [(x, False) for x in markers(m.group(1))] + [(x, True) for x in markers(m.group(2))]
    for g in GONE.findall(instruction or ""):
        t += [(x, False) for x in markers(g)]
    t += [(x, True) for x in markers(instruction or "")]
    out = []
    for mk, want in t:
        if vocab is not None and mk not in vocab:
            continue
        if mk not in seen:
            seen.add(mk)
            out.append((mk, want))
    return out


def added_text(diff: dict) -> str:
    lines = []
    for f in diff.get("files") or []:
        for l in (f.get("patch") or "").splitlines():
            if l.startswith("+") and not l.startswith("+++"):
                lines.append(l[1:])
    return "\n".join(lines)


def check(item: str, added: str, vocab: str | None = None) -> dict:
    ts = tests_for(item, vocab)
    if not ts:
        return {"status": "unclear", "passed": [], "failed": []}
    ok, bad = [], []
    for mk, want in ts:
        (ok if (mk in added) == want else bad).append(("" if want else "no ") + mk)
    status = "addressed" if not bad else "not addressed" if not ok else "partial"
    return {"status": status, "passed": ok, "failed": bad}


def items(report: dict, facts: list | None = None) -> list:
    """What the coding agent was told to do, as (title, instruction)."""
    out = [(c.get("title", ""), c.get("adaptation", "")) for c in useful_conflicts((report or {}).get("conflicts") or [])]
    for f in facts or []:
        if f.get("kind") == "host-script-coupling":
            m = re.search(r"class or id:\s*(.+?)\.\s", f.get("text", ""))
            sels = re.findall(r"[.#]([\w-]+)", m.group(1)) if m else []
            out.append(("Keep the site's script selectors", "Keep classes " + " ".join(f"'{s}'" for s in dict.fromkeys(sels))))
    return out


def repo_vocab(*roots, limit: int = 4_000_000) -> str:
    from .scanner import read, walk
    parts, size = [], 0
    for root in roots:
        if not root:
            continue
        for p in walk(root, {".js", ".jsx", ".ts", ".tsx", ".mjs", ".css", ".scss", ".html", ".json"}):
            if p.name in ("package-lock.json", "yarn.lock"):
                continue
            t = read(p, limit=200_000)
            parts.append(t)
            size += len(t)
            if size > limit:
                return "\n".join(parts)
    return "\n".join(parts)


def adherence(report: dict, facts: list, diff_treated: dict, diff_alone: dict | None = None, vocab: str | None = None) -> dict:
    at, aa = added_text(diff_treated), added_text(diff_alone) if diff_alone else None
    if vocab is not None:
        vocab += "\n" + at + "\n" + (aa or "")   # new APIs the agents wrote count as code too
    rows = []
    for title, instr in items(report, facts):
        r = {"title": title, "instruction": instr, "report_run": check(instr, at, vocab)}
        if aa is not None:
            r["agent_alone"] = check(instr, aa, vocab)
        rows.append(r)
    testable = [r for r in rows if r["report_run"]["status"] != "unclear"]
    n = lambda key, st: sum(1 for r in testable if r.get(key, {}).get("status") == st)  # noqa: E731
    return {"items": rows, "testable": len(testable),
            "addressed": n("report_run", "addressed"), "partial": n("report_run", "partial"),
            "alone_addressed": n("agent_alone", "addressed") if aa is not None else None,
            "only_with_report": sum(1 for r in testable if r["report_run"]["status"] == "addressed"
                                    and r.get("agent_alone", {}).get("status") not in ("addressed", None))}


def blast_radius(diff: dict, report: dict | None) -> list:
    mt = (report or {}).get("mount") or {}
    expected = {mt.get("file"), mt.get("replaces"), "package.json", ".gitignore"}
    return sorted(f["path"] for f in diff.get("files") or []
                  if f["status"] in ("modified", "deleted") and f["kind"] != "generated" and f["path"] not in expected)
