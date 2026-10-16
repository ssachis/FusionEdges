"""Deterministic stand-in AI providers for tests (no network).

smart(): report agent reads one file then returns a JSON report. The coding agent behaves like a
weak migration when it gets only the prompt (one page, .html links left, no logo) and like a good
one when the compatibility report is in its first message.
"""
import json
import os
import re
from html.parser import HTMLParser
from pathlib import Path

from fusionedges import llm

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SOURCE = FIXTURES / "source_site"


class _T(HTMLParser):
    def __init__(self):
        super().__init__()
        self.skip, self.out = 0, []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"):
            self.skip += 1
        for k, v in attrs:
            if k == "alt" and v:
                self.out.append(f'<img src="/img/logo.png" alt="{v}" width={{40}} />')

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head"):
            self.skip -= 1

    def handle_data(self, d):
        if not self.skip and d.strip():
            self.out.append(f"<p>{d.strip()}</p>")


def page_jsx(name: str, links_ok: bool) -> str:
    t = _T()
    t.feed((SOURCE / name).read_text())
    body = "\n      ".join(t.out)
    if not links_ok:
        body += '\n      <a href="about.html">About us</a>'
    imp = 'import Link from "next/link";\n' if links_ok else ""
    nav = '<Link href="/about">About us</Link>' if links_ok else ""
    return f"{imp}export default function Page() {{\n  return (\n    <main className=\"mx-auto max-w-5xl px-6 py-24\">\n      {body}\n      {nav}\n    </main>\n  );\n}}\n"


def _first_user(messages):
    return next(m["text"] for m in messages if m["role"] == "user")


def smart(system, messages, tools, max_tokens):
    first = _first_user(messages)
    turns = sum(1 for m in messages if m["role"] == "tool")
    usage = {"in": 1500 + 300 * len(messages), "out": 200}
    if "OUTPUT project" in system and "<<<FILE" in system:  # one-shot editor
        return ({"role": "assistant", "text": _oneshot(first, "COMPATIBILITY REPORT" in first), "tool_calls": []}, usage)
    if "integration analyst" in system:
        rep = {"summary": "React Router navbar into a Next.js app router site.",
               "donor_files": [{"path": "src/components/Navbar.jsx", "role": "component"},
                               {"path": "src/components/Navbar.css", "role": "style"}],
               "mount": {"file": "app/layout.tsx", "replaces": "components/Header.tsx"},
               "dependencies": [{"name": "react-icons", "status": "missing", "action": "use inline icons"}],
               "conflicts": [{"type": "rendering", "title": "needs use client", "severity": "high", "facts": ["F5"],
                              "evidence": [{"repo": "donor", "path": "src/components/Navbar.jsx", "line": 9,
                                            "snippet": "const [scrolled, setScrolled] = useState(window.scrollY > 10);"}],
                              "impact": "window is not defined", "adaptation": "use client + read scroll in useEffect"}],
               "plan": ["rewrite", "mount in layout", "remove Header"]}
        return ({"role": "assistant", "text": "```json\n" + json.dumps(rep) + "\n```", "tool_calls": []}, usage)
    if "migration analyst" in system:
        if turns == 0:
            return ({"role": "assistant", "text": "Checking the layout.", "tool_calls": [
                {"id": "r1", "name": "read_file", "args": {"repo": "donor", "path": "app/layout.tsx"}}]}, usage)
        chunk = re.search(r"\[(R\d+)\] donor:app/layout.tsx", first)
        rep = {"summary": "Three static pages into a Next.js app router project with Tailwind.",
               "page_map": [{"source": "index.html", "target": "app/page.tsx", "route": "/"},
                            {"source": "about.html", "target": "app/about/page.tsx", "route": "/about"},
                            {"source": "contact.html", "target": "app/contact/page.tsx", "route": "/contact"}],
               "layout": {"shared": ["header", "footer"], "donor_files": ["app/layout.tsx", "components/Navbar.tsx"]},
               "style_map": [{"source": ".btn", "target": "rounded-full bg-brand-500 px-6 py-3 text-white"}],
               "behavior": [{"source": "js/menu.js", "target": "client component with useState"}],
               "assets": [{"source": "img/logo.png", "target": "public/img/logo.png"}],
               "conflicts": [
                   {"type": "routing", "title": ".html links must become routes", "severity": "high",
                    "facts": [], "chunks": [chunk.group(1)] if chunk else [],
                    "evidence": [{"repo": "source", "path": "index.html", "line": 15,
                                  "snippet": '<nav class="main-nav"><a href="index.html">Home</a>'}],
                    "impact": "links 404", "adaptation": "use next/link with /about, /contact"},
                   {"type": "other", "title": "invented claim", "severity": "low",
                    "evidence": [{"repo": "donor", "path": "app/nope.tsx", "line": 2, "snippet": "x"}]}],
               "plan": ["map pages", "port layout", "copy assets"]}
        return ({"role": "assistant", "text": "```json\n" + json.dumps(rep) + "\n```", "tool_calls": []}, usage)

    guided = "COMPATIBILITY REPORT" in first
    if turns == 0:
        calls = [{"id": "w1", "name": "write_file", "args": {"path": "app/page.tsx", "content": page_jsx("index.html", guided)}}]
        if guided:
            calls += [
                {"id": "w2", "name": "write_file", "args": {"path": "app/about/page.tsx", "content": page_jsx("about.html", True)}},
                {"id": "w3", "name": "write_file", "args": {"path": "app/contact/page.tsx", "content": page_jsx("contact.html", True)}},
                {"id": "c1", "name": "copy_asset", "args": {"from_repo": "source", "source": "img/logo.png", "destination": "public/img/logo.png"}},
                {"id": "d1", "name": "delete_file", "args": {"path": "app/blog/page.tsx"}},
            ]
        else:
            calls += [{"id": "w4", "name": "write_file", "args": {"path": "app/site.css",
                                                                  "content": (SOURCE / "css/style.css").read_text()}}]
        return ({"role": "assistant", "text": "", "tool_calls": calls}, usage)
    return ({"role": "assistant", "text": "Done. index.html -> /", "tool_calls": []}, usage)


NAIVE_NAVBAR = """import { useState } from "react";
import { Link, NavLink } from "react-router-dom";
import { FiMenu } from "react-icons/fi";
import "./Navbar.css";

export default function Navbar() {
  const [open, setOpen] = useState(false);
  const scrolled = window.scrollY > 10;
  return (
    <header className={scrolled ? "navbar navbar--scrolled" : "navbar"}>
      <Link to="/">Orbit</Link>
      <button onClick={() => setOpen(!open)}><FiMenu /></button>
      <nav><NavLink to="/">Home</NavLink><NavLink to="/about">About</NavLink></nav>
    </header>
  );
}
"""

GOOD_NAVBAR = """"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";

export default function Navbar() {
  const [open, setOpen] = useState(false);
  const [scrolled, setScrolled] = useState(false);
  const pathname = usePathname();
  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 10);
    onScroll();
    window.addEventListener("scroll", onScroll);
    return () => window.removeEventListener("scroll", onScroll);
  }, []);
  useEffect(() => setOpen(false), [pathname]);
  const cls = (href: string) => (pathname === href ? "text-sky-400" : "text-slate-300");
  return (
    <header className={`sticky top-0 z-50 flex items-center justify-between px-8 py-4 ${scrolled ? "bg-slate-900 shadow" : "bg-slate-900/90"}`}>
      <Link href="/" className="flex items-center gap-2 font-bold text-white"><img src="/logo.png" alt="Orbit" className="w-7" /> Orbit</Link>
      <button className="text-2xl text-white md:hidden" onClick={() => setOpen(!open)} aria-label="Menu">{open ? "✕" : "☰"}</button>
      <nav className={`${open ? "flex" : "hidden"} absolute left-0 right-0 top-16 flex-col gap-6 bg-slate-900 px-8 py-4 md:static md:flex md:flex-row md:p-0`}>
        <Link href="/" className={cls("/")}>Home</Link>
        <Link href="/about" className={cls("/about")}>About</Link>
      </nav>
    </header>
  );
}
"""


def _oneshot(first, guided):
    if "Add the navbar" in first:
        if guided:
            layout = (FIXTURES / "next_host/app/layout.tsx").read_text().replace(
                'import Header from "@/components/Header";', 'import Navbar from "@/components/Navbar";').replace("<Header />", "<Navbar />")
            return ("<<<FILE components/Navbar.tsx>>>\n" + GOOD_NAVBAR + "<<<END>>>\n"
                    "<<<FILE app/layout.tsx>>>\n" + layout + "<<<END>>>\n"
                    "<<<DELETE components/Header.tsx>>>\n"
                    "<<<COPY donor:src/assets/logo.png -> public/logo.png>>>\n<<<DONE>>>\nReplaced Header with Navbar.")
        layout = (FIXTURES / "next_host/app/layout.tsx").read_text().replace(
            'import Header from "@/components/Header";', 'import Header from "@/components/Header";\nimport Navbar from "@/components/Navbar";').replace(
            "<Header />", "<Header />\n        <Navbar />")
        return ("<<<FILE components/Navbar.jsx>>>\n" + NAIVE_NAVBAR + "<<<END>>>\n"
                "<<<FILE components/Navbar.css>>>\n" + (FIXTURES / "react_donor/src/components/Navbar.css").read_text() + "<<<END>>>\n"
                "<<<FILE app/layout.tsx>>>\n" + layout + "<<<END>>>\n<<<DONE>>>\nAdded Navbar.")
    # migration
    out = "<<<FILE app/page.tsx>>>\n" + page_jsx("index.html", guided) + "<<<END>>>\n"
    if guided:
        out += ("<<<FILE app/about/page.tsx>>>\n" + page_jsx("about.html", True) + "<<<END>>>\n"
                "<<<FILE app/contact/page.tsx>>>\n" + page_jsx("contact.html", True) + "<<<END>>>\n"
                "<<<COPY source:img/logo.png -> public/img/logo.png>>>\n<<<DELETE app/blog/page.tsx>>>\n")
    else:
        out += "<<<FILE app/site.css>>>\n" + (SOURCE / "css/style.css").read_text() + "<<<END>>>\n"
    return out + "<<<DONE>>>\nDone."


def rate_limited(system, messages, tools, max_tokens):
    raise llm._Retryable("rate limit / quota (HTTP 429): slow down", 60)


def install(*pairs):
    llm.CUSTOM.clear()
    llm.reset_cooldowns()
    for name, fn in pairs:
        llm.CUSTOM[name] = fn
    os.environ["FUSIONEDGES_PROVIDERS"] = ",".join(n for n, _ in pairs)
