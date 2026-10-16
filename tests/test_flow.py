import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from fusionedges import llm, paths, rag, store
from fusionedges.agents import Workspace, extract_json, validate_report
from fusionedges.checks import expected_route, output_routes, run_checks
from fusionedges.pipeline import run, scan_for
from fusionedges.prompts import default_prompt
from fusionedges.repos import parse
from fusionedges.scanner import scan
from tests import fake_llm

FIX = Path(__file__).resolve().parent / "fixtures"
SRC, DONOR = FIX / "source_site", FIX / "donor_next"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._old = (paths.HOME, paths.EXAMPLES, paths.OUTPUT, paths.DB)
        paths.HOME, paths.EXAMPLES, paths.OUTPUT = self.tmp, self.tmp / "examples", self.tmp / "output"
        paths.DB = paths.OUTPUT / "runs.db"

    def tearDown(self):
        paths.HOME, paths.EXAMPLES, paths.OUTPUT, paths.DB = self._old
        llm.CUSTOM.clear()
        os.environ.pop("FUSIONEDGES_PROVIDERS", None)
        shutil.rmtree(self.tmp, ignore_errors=True)


class FallbackTest(Base):
    def test_switches_provider_on_rate_limit(self):
        fake_llm.install(("first", fake_llm.rate_limited), ("second", fake_llm.smart))
        logs = []
        s = llm.Session("t", logs.append)
        msg = s.chat("migration analyst", [{"role": "user", "text": "hi"}], [])
        self.assertEqual(msg["provider"], "second")
        self.assertTrue(any("switching provider" in l for l in logs))
        s.chat("migration analyst", [{"role": "user", "text": "hi"}], [])
        self.assertEqual(len(s.fallbacks), 1)  # first provider is cooling down, not retried

    def test_all_fail(self):
        fake_llm.install(("a", fake_llm.rate_limited))
        with self.assertRaises(llm.LLMError):
            llm.Session("t", lambda m: None).chat("x", [{"role": "user", "text": "hi"}], [])

    def test_anthropic_ids_sanitized(self):
        conv = [{"role": "user", "text": "go"},
                {"role": "assistant", "text": "", "tool_calls": [{"id": "call:1", "name": "read_file", "args": {}}]},
                {"role": "tool", "results": [{"id": "call:1", "name": "read_file", "content": "x"}]}]
        cap = {}

        def fake_post(url, headers, payload, timeout=180):
            cap["p"] = payload
            return {"content": [{"type": "text", "text": "ok"}], "usage": {"input_tokens": 1, "output_tokens": 1}}
        with unittest.mock.patch.object(llm, "_post", fake_post):
            llm._anthropic(llm.SPECS["anthropic"], "m", "k", "sys", conv, [], 10)
        self.assertEqual(cap["p"]["messages"][-1]["content"][0]["tool_use_id"], "call_1")


class PromptTest(unittest.TestCase):
    def test_defaults(self):
        both, mig, sty = default_prompt(True, True), default_prompt(True, False), default_prompt(False, True)
        self.assertIn("DONOR's design system", both)
        self.assertIn("keep the SOURCE's look", mig)
        self.assertIn("Restyle", sty)
        self.assertEqual(default_prompt(False, False), "")


class ScanRagTest(unittest.TestCase):
    def test_scan_roles(self):
        sc = scan_for(SRC, DONOR, True)
        self.assertEqual(sc["source"]["framework"], "html")
        self.assertEqual(sc["donor"]["framework"], "next")
        self.assertEqual(sc["donor"]["aliases"], ["@/*"])
        kinds = {f["kind"] for f in sc["facts"]}
        self.assertIn("framework-mismatch", kinds)
        self.assertIn("behavior-js", kinds)
        self.assertTrue(all(e["repo"] in ("source", "donor") for f in sc["facts"] for e in f["evidence"]))

    def test_retrieval_covers_pages_and_imports(self):
        sc = scan_for(SRC, DONOR, True)
        r = rag.retrieve(SRC, DONOR, default_prompt(True, True), sc)
        got = {(c["repo"], c["path"]) for c in r["chunks"]}
        for p in ("index.html", "about.html", "contact.html"):
            self.assertIn(("source", p), got)
        self.assertIn(("donor", "app/layout.tsx"), got)
        self.assertIn(("donor", "components/Navbar.tsx"), got)  # via @/ alias import from the layout
        self.assertIn(("donor", "tailwind.config.js"), got)
        self.assertLessEqual(r["stats"]["selected_chars"], 26000)

    def test_parse_github(self):
        self.assertEqual(parse("https://github.com/a/b/tree/main/site"), ("a", "b", "main", "site"))


class ChecksTest(unittest.TestCase):
    def test_routes(self):
        self.assertEqual(expected_route("index.html"), "/")
        self.assertEqual(expected_route("blog/post.html"), "/blog/post")
        self.assertEqual(output_routes(DONOR), {"/", "/blog"})

    def test_donor_copy_fails_checks(self):
        res = run_checks(SRC, DONOR, DONOR, True, True, {"ok": None}, ["tailwind.config.js"])
        self.assertEqual(len(res["checks"]["pages"]["missing"]), 2)  # /about, /contact
        self.assertLess(res["checks"]["text"]["percent"], 20)
        self.assertEqual(res["outcome"], "failed")  # lost 2 of 3 pages


class ReportTest(unittest.TestCase):
    def test_citations(self):
        sc = scan_for(SRC, DONOR, True)
        rep = validate_report({"conflicts": [
            {"title": "ok", "severity": "high", "evidence": [{"repo": "source", "path": "index.html", "line": 15,
                                                              "snippet": '<nav class="main-nav"><a href="index.html">Home</a>'}]},
            {"title": "bad", "severity": "low", "chunks": ["R999"], "evidence": [{"repo": "donor", "path": "x.tsx", "line": 1}]}]},
            sc, {"source": SRC, "donor": DONOR}, [])
        by = {c["title"]: c for c in rep["conflicts"]}
        self.assertEqual(by["ok"]["citation"], "verified")
        self.assertEqual(by["bad"]["citation"], "unverified")
        self.assertEqual(extract_json('x ```json\n{"a": [1,],}\n```'), {"a": [1]})


class WorkspaceTest(unittest.TestCase):
    def test_permissions(self):
        with tempfile.TemporaryDirectory() as t:
            out = Path(t) / "out"
            out.mkdir()
            ws = Workspace({"source": SRC, "donor": DONOR, "output": out}, writable="output")
            self.assertIn("escapes", ws.run("write_file", {"path": "../x", "content": "y"}))
            self.assertIn("binary", ws.run("read_file", {"repo": "source", "path": "img/logo.png"}))
            self.assertIn("copied", ws.run("copy_asset", {"from_repo": "source", "source": "img/logo.png", "destination": "public/l.png"}))
            ws.run("write_file", {"path": "a.txt", "content": "x"})
            self.assertIn("deleted", ws.run("delete_file", {"path": "a.txt"}))
            self.assertIn("error", ws.run("delete_file", {"path": "../../etc/passwd"}))
            ro = Workspace({"source": SRC, "donor": DONOR})
            self.assertIn("read-only", ro.run("write_file", {"path": "a", "content": "b"}))


class RepairTest(unittest.TestCase):
    def test_build_error_goes_back_for_a_fix(self):
        from fusionedges import agents
        fake_llm.install(("second", fake_llm.smart))
        builds = iter([{"skipped": False, "ok": False, "reason": "build failed", "output": "Type error: x"},
                       {"skipped": False, "ok": True, "reason": "", "output": ""}])
        seen = []
        orig = fake_llm.smart

        def spy(system, messages, tools, mt):
            seen.append(messages[-1]["text"][:60])
            return orig(system, messages, tools, mt)
        llm.CUSTOM["second"] = spy
        sc = scan_for(FIX / "next_host", FIX / "react_donor", False, "navbar")
        with tempfile.TemporaryDirectory() as t, unittest.mock.patch.object(agents, "run_build", lambda root: next(builds)):
            out = Path(t) / "p"
            shutil.copytree(FIX / "next_host", out)
            res = agents.run_oneshot(default_prompt(False, False, "navbar"), FIX / "next_host", FIX / "react_donor",
                                     out, sc, True, lambda m: None, component="navbar")
        self.assertEqual(res["attempts"], 2)
        self.assertEqual(res["builds"], 2)
        self.assertTrue(seen[1].startswith("The OUTPUT project is"))  # a fresh, small repair request
        llm.CUSTOM.clear()


class PipelineTest(Base):
    def _run(self, mode):
        fake_llm.install(("first", fake_llm.rate_limited), ("second", fake_llm.smart))
        return run({"mode": mode, "source": str(SRC), "donor": str(DONOR), "migration": True, "style": True,
                    "prompt": default_prompt(True, True), "build": False}, lambda m: None)

    def test_prompt_only_vs_report(self):
        plain = self._run("prompt")
        guided = self._run("report")
        for rec in (plain, guided):
            folder = Path(rec["folder"])
            for f in ("run.json", "facts.json", "checks.json", "project.zip", "project/package.json"):
                self.assertTrue((folder / f).exists(), f)
            self.assertTrue((folder / "project/.git").exists())  # ready to push to GitHub later
            self.assertFalse((DONOR / "app/about").exists())    # originals untouched
        self.assertTrue((Path(guided["folder"]) / "report.md").exists())
        pc, gc = plain["checks"]["checks"], guided["checks"]["checks"]
        self.assertTrue(pc["pages"]["missing"])
        self.assertTrue(pc["links"]["missing"])
        self.assertTrue(pc["assets"]["missing"])
        self.assertFalse(pc["style"]["pass"])           # copied the source CSS
        self.assertEqual(gc["pages"]["missing"], [])
        self.assertEqual(gc["links"]["missing"], [])
        self.assertEqual(gc["assets"]["missing"], [])
        self.assertGreaterEqual(gc["text"]["percent"], 90)
        self.assertEqual(guided["checks"]["outcome"], "passed-unbuilt")
        self.assertEqual(plain["checks"]["outcome"], "failed")
        rep = guided["report"]
        self.assertEqual(len(rep["page_map"]), 3)
        self.assertEqual({c["title"]: c["citation"] for c in rep["conflicts"]}["invented claim"], "unverified")
        self.assertGreater(rep["retrieval"]["stats"]["selected_chunks"], 5)
        with sqlite3.connect(paths.DB) as db:
            self.assertEqual(db.execute("select count(*) from runs").fetchone()[0], 2)
            self.assertGreater(db.execute("select count(*) from conflicts").fetchone()[0], 1)
        log = subprocess.run(["git", "log", "--oneline"], cwd=Path(guided["folder"]) / "project",
                             capture_output=True, text=True).stdout
        self.assertIn("FusionEdges report run", log)
        self.assertEqual(store.history()[0]["mode"], "report")

    def test_agent_mode_still_works(self):
        fake_llm.install(("second", fake_llm.smart))
        rec = run({"mode": "report", "source": str(SRC), "donor": str(DONOR), "migration": True, "style": True,
                   "edit": "agent", "prompt": default_prompt(True, True), "build": False}, lambda m: None)
        self.assertEqual(rec["checks"]["outcome"], "passed-unbuilt")

    def test_add_component_prompt_vs_report(self):
        fake_llm.install(("second", fake_llm.smart))
        host, donor = FIX / "next_host", FIX / "react_donor"
        res = {}
        for mode in ("prompt", "report"):
            res[mode] = run({"mode": mode, "source": str(host), "donor": str(donor), "add_component": True,
                             "component": "navbar", "prompt": default_prompt(False, False, "navbar"), "build": False},
                            lambda m: None)
        p, g = res["prompt"]["checks"]["checks"], res["report"]["checks"]["checks"]
        # the naive one-shot makes the classic mistakes
        self.assertFalse(p["router"]["pass"])
        self.assertFalse(p["use_client"]["pass"])
        self.assertFalse(p["browser_api"]["pass"])
        self.assertFalse(p["css_import"]["pass"])
        self.assertFalse(p["deps"]["pass"])
        self.assertIn("react-icons", p["deps"]["missing"])
        self.assertFalse(p["single"]["pass"])           # Header and Navbar both in the layout
        self.assertEqual(res["prompt"]["checks"]["outcome"], "partial")
        # the report-guided one gets them right
        for k in ("router", "use_client", "browser_api", "css_import", "deps", "assets", "single", "mounted"):
            self.assertTrue(g[k]["pass"], k)
        self.assertEqual(res["report"]["checks"]["outcome"], "passed-unbuilt")
        self.assertFalse((Path(res["report"]["project"]) / "components/Header.tsx").exists())
        self.assertTrue((Path(res["report"]["project"]) / "public/logo.png").exists())
        self.assertTrue((host / "components/Header.tsx").exists())  # original untouched
        self.assertEqual(res["report"]["run_script"], "dev")  # the UI shows the right start command
        rep = res["report"]["report"]
        self.assertEqual(rep["mount"]["file"], "app/layout.tsx")
        self.assertEqual(res["report"]["agent"]["attempts"], 1)
        self.assertEqual(store.history()[0]["component"], "navbar")

    def test_needs_an_option(self):
        with self.assertRaisesRegex(ValueError, "Pick"):  # noqa
            run({"source": str(SRC), "donor": str(DONOR), "migration": False, "style": False, "prompt": "x"}, lambda m: None)


if __name__ == "__main__":
    unittest.main()
