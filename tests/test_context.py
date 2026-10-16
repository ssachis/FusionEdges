"""Context selection for "add a component" tasks: dependency closure, budget, honest logging."""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from fusionedges import agents, llm, paths, rag
from fusionedges.pipeline import scan_for
from tests import fake_llm


def write(root: Path, files: dict):
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)


def make_repos(tmp: Path):
    donor, source = tmp / "donor", tmp / "source"
    write(donor, {
        "package.json": json.dumps({"dependencies": {"next": "16.0.0", "framer-motion": "^13.0.0", "clsx": "^2.0.0"}}),
        "tsconfig.json": '{ "compilerOptions": { "paths": { "@/*": ["./src/*"] } } /* comment */ }',
        "src/components/layout/navbar.tsx":
            'import { motion } from "framer-motion";\nimport Link from "next/link";\n'
            'import { ThemeToggle } from "@/components/layout/theme-toggle";\n'
            'import { cn } from "@/lib/utils";\nexport function Navbar() { return null }\n',
        "src/components/layout/theme-toggle.tsx": 'import { Icon } from "../ui/icon";\nexport function ThemeToggle() {}\n',
        "src/components/ui/icon.tsx": 'import { deep } from "@/lib/deep";\nexport const Icon = 1;\n',
        "src/lib/deep.ts": 'import { deeper } from "./deeper";\nexport const deep = 1;\n',
        "src/lib/deeper.ts": "export const deeper = 1;\n",
        "src/lib/utils.ts": 'import clsx from "clsx";\nexport const cn = clsx;\n',
        "src/components/layout/footer.tsx": "export function Footer() {}\n",
        "src/components/unrelated/Chart.tsx": "export const Chart = 1;\n",
        "public/big.html": "x" * 50000,
    })
    write(source, {
        "package.json": json.dumps({"dependencies": {"react": "^18.0.0", "clsx": "^2.0.0"}}),
        "src/index.js": "import App from './App';\n",
        "src/App.js": "import Header from './component/Header';\nexport default function App() {}\n",
        "src/component/Header.js": "import './Header.css';\nexport default function Header() {}\n",
        "src/component/Header.css": ".header{color:red}\n",
        "src/component/Footer.js": "export default function Footer() {}\n",
        "src/index.css": "body{margin:0}\n",
        "public/index.html": "<html>" + "y" * 9000 + "</html>",
    })
    return source, donor


class ComponentContextTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.source, self.donor = make_repos(self.tmp)
        self.sc = {"source": {"entry_files": ["src/index.js"]}, "donor": {}}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def paths(self, ctx, repo):
        return [f["path"] for f in ctx["files"] if f["repo"] == repo]

    def test_alias_imports_are_followed_through_tsconfig_paths(self):
        # '@/lib/utils' lives in src/lib; the old resolver looked in the repo root and found nothing
        self.assertEqual(rag.resolve_import(self.donor, "src/components/layout/navbar.tsx", "@/lib/utils"), "src/lib/utils.ts")
        self.assertIsNone(rag.resolve_import(self.donor, "src/components/layout/navbar.tsx", "framer-motion"))

    def test_closure_has_component_and_everything_it_imports(self):
        ctx = rag.component_context(self.source, self.donor, self.sc, "navbar")
        d = self.paths(ctx, "donor")
        self.assertEqual(d[0], "src/components/layout/navbar.tsx")
        for need in ("src/components/layout/theme-toggle.tsx", "src/lib/utils.ts", "src/components/ui/icon.tsx"):
            self.assertIn(need, d)
        self.assertNotIn("src/lib/deeper.ts", d)                      # three hops away
        self.assertNotIn("src/components/layout/footer.tsx", d)       # same folder, not imported
        self.assertNotIn("src/components/unrelated/Chart.tsx", d)
        self.assertFalse(any(p.startswith("public/") for p in d))

    def test_source_gets_only_wiring_and_the_existing_component(self):
        ctx = rag.component_context(self.source, self.donor, self.sc, "navbar")
        s = self.paths(ctx, "source")
        for need in ("package.json", "src/index.js", "src/App.js", "src/component/Header.js",
                     "src/component/Header.css", "src/index.css"):
            self.assertIn(need, s)
        self.assertNotIn("src/component/Footer.js", s)
        self.assertNotIn("public/index.html", s)  # HTML shell is skipped when a JS entry exists

    def test_packages_are_listed_with_source_versions(self):
        ctx = rag.component_context(self.source, self.donor, self.sc, "navbar")
        pk = {p["name"]: p for p in ctx["packages"]}
        self.assertEqual(pk["framer-motion"]["source"], "MISSING")
        self.assertEqual(pk["clsx"]["source"], "^2.0.0")
        self.assertEqual(pk["next"]["donor"], "16.0.0")
        self.assertIn("framer-motion: donor ^13.0.0, source MISSING", rag.render_component_context(ctx))

    def test_budget_drops_low_priority_files_first_and_says_so(self):
        ctx = rag.component_context(self.source, self.donor, self.sc, "navbar", budget_chars=6000)
        files = self.paths(ctx, "donor")
        self.assertEqual(files[0], "src/components/layout/navbar.tsx")  # the component itself is never dropped
        self.assertLessEqual(ctx["stats"]["chars"], 6000 + 200)
        text = rag.render_component_context(ctx)
        if ctx["dropped"]:
            self.assertIn("NOT SHOWN", text)

    def test_tight_budget_drops_in_order_and_keeps_the_wiring(self):
        # utils.ts is a ONE-hop import (made big); icon.tsx is a TWO-hop import (small)
        write(self.donor, {"src/lib/utils.ts": 'import clsx from "clsx";\n' + "export const cn = clsx;\n" * 300})
        full = rag.component_context(self.source, self.donor, self.sc, "navbar", budget_chars=100000)
        self.assertIn("src/components/ui/icon.tsx", [f["path"] for f in full["files"]])
        tight = rag.component_context(self.source, self.donor, self.sc, "navbar", budget_chars=full["stats"]["chars"] - 3000)
        kept = [(f["repo"], f["path"]) for f in tight["files"]]
        gone = [d["path"] for d in tight["dropped"]]
        self.assertEqual(gone[0], "src/components/ui/icon.tsx")        # two-hop imports go first
        self.assertIn("src/lib/utils.ts", gone)                        # then the largest one-hop import
        for need in ("src/App.js", "src/component/Header.js", "src/index.js", "package.json"):
            self.assertIn(("source", need), kept)                      # where to mount / what to replace stays
        self.assertIn(("donor", "src/components/layout/navbar.tsx"), kept)
        self.assertIn(("donor", "src/components/layout/theme-toggle.tsx"), kept)
        self.assertLessEqual(tight["stats"]["chars"], full["stats"]["chars"] - 3000)
        self.assertIn("NOT SHOWN", rag.render_component_context(tight))

    def test_tiny_budget_keeps_only_a_cut_component(self):
        ctx = rag.component_context(self.source, self.donor, self.sc, "navbar", budget_chars=100)
        self.assertEqual([f["path"] for f in ctx["files"]], ["src/components/layout/navbar.tsx"])
        self.assertTrue(ctx["files"][0]["truncated"])
        self.assertLess(ctx["stats"]["chars"], 200)

    def test_selection_is_deterministic(self):
        a = rag.component_context(self.source, self.donor, self.sc, "navbar")
        b = rag.component_context(self.source, self.donor, self.sc, "navbar")
        self.assertEqual(rag.render_component_context(a), rag.render_component_context(b))

    def test_synonym_finds_header_in_source(self):
        got = rag.find_component_files(self.source, "navbar", 2)
        self.assertEqual(got[0], "src/component/Header.js")   # code ranks ahead of its stylesheet
        self.assertLessEqual(set(got), {"src/component/Header.js", "src/component/Header.css"})

    def test_unknown_component_finds_nothing(self):
        self.assertEqual(rag.find_component_files(self.donor, "carousel"), [])

    def test_code_tree_hides_static_assets(self):
        write(self.source, {"public/img/a.png": "x", "src/logo.svg": "<svg/>", "package-lock.json": "{}"})
        tree = agents.code_tree(self.source)
        self.assertNotIn("public/img/a.png", tree)
        self.assertNotIn("package-lock.json", tree)
        self.assertIn("src/App.js", tree)
        self.assertTrue(tree[-1].startswith("..."))


class OneshotContextTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.source, self.donor = make_repos(self.tmp)
        self.seen = []

        def spy(system, messages, tools, max_tokens):
            self.seen.append(messages[0]["text"])
            return ({"role": "assistant", "text": "<<<FILE src/x.js>>>\nexport {}\n<<<END>>>\n<<<DONE>>> ok", "tool_calls": []},
                    {"in": 10, "out": 5})
        llm.CUSTOM["spy"] = spy
        os.environ["FUSIONEDGES_PROVIDERS"] = "spy"

    def tearDown(self):
        llm.CUSTOM.clear()
        os.environ.pop("FUSIONEDGES_PROVIDERS", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_one(self, report=None, comp="navbar"):
        out = self.tmp / "out"
        shutil.rmtree(out, ignore_errors=True)
        shutil.copytree(self.source, out)
        sc = scan_for(self.source, self.donor, False, comp)
        logs = []
        res = agents.run_oneshot("Add the navbar", self.source, self.donor, out, sc, False, logs.append,
                                 report=report, component=comp)
        return res, logs

    def test_log_counts_real_files_and_reports_size(self):
        res, logs = self.run_one()
        line = next(l for l in logs if "context:" in l)
        self.assertIn("files (", line)
        self.assertIn("tokens", line)
        self.assertEqual(res["context"]["mode"], "component-closure")
        self.assertEqual(res["context"]["donor_component"], ["src/components/layout/navbar.tsx"])
        self.assertTrue(any("first message" in l for l in logs))

    def test_log_says_where_the_budget_came_from(self):
        os.environ.pop("FUSIONEDGES_CONTEXT_CHARS", None)
        _, logs = self.run_one()
        self.assertIn("is not set in this process", next(l for l in logs if "context:" in l))
        os.environ["FUSIONEDGES_CONTEXT_CHARS"] = "8000"
        try:
            _, logs = self.run_one()
        finally:
            os.environ.pop("FUSIONEDGES_CONTEXT_CHARS", None)
        line = next(l for l in logs if "context:" in l)
        self.assertIn("budget 8,000 chars (from FUSIONEDGES_CONTEXT_CHARS)", line)

    def test_same_files_with_and_without_report(self):
        self.run_one()
        self.run_one(report={"summary": "s", "conflicts": [], "plan": []})
        plain, with_report = self.seen
        marker = "RELEVANT FILES (full content):"
        self.assertEqual(plain.split(marker)[1].split("COMPATIBILITY REPORT")[0].strip(),
                         with_report.split(marker)[1].split("COMPATIBILITY REPORT")[0].strip())
        self.assertIn("COMPATIBILITY REPORT", with_report)
        self.assertNotIn("COMPATIBILITY REPORT", plain)

    def test_falls_back_to_retrieval_when_component_missing(self):
        res, logs = self.run_one(comp="carousel")
        self.assertEqual(res["context"]["mode"], "retrieval")
        self.assertTrue(any("falling back" in l for l in logs))


class ShortReasonTest(unittest.TestCase):
    def test_keeps_the_numbers_a_90_char_cut_hides(self):
        msg = ('request too large or no credit (HTTP 413): {"error":{"message":"Request too large for model '
               '`openai/gpt-oss-120b` in organization `org_x` service tier `on_demand` on tokens per minute (TPM): '
               'Limit 8000, Requested 21345, please reduce your message size"}}')
        out = llm.short_reason(msg)
        self.assertIn("limit 8000 per-minute tokens, requested 21345", out)
        self.assertEqual(llm.short_reason("plain failure " * 20), ("plain failure " * 20)[:160])


class MaxTokensTest(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("FUSIONEDGES_GROQ_MAX_TOKENS", None)

    def test_cap_applies_only_when_set_and_never_raises_the_request(self):
        self.assertEqual(llm.max_tokens_for("groq", 16000), 16000)
        os.environ["FUSIONEDGES_GROQ_MAX_TOKENS"] = "4000"
        self.assertEqual(llm.max_tokens_for("groq", 16000), 4000)
        self.assertEqual(llm.max_tokens_for("groq", 2000), 2000)
        self.assertEqual(llm.max_tokens_for("gemini", 16000), 16000)
        os.environ["FUSIONEDGES_GROQ_MAX_TOKENS"] = "oops"
        self.assertEqual(llm.max_tokens_for("groq", 16000), 16000)


if __name__ == "__main__":
    unittest.main()


class ScopedEnvFactsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.donor, self.source = make_repos(self.tmp)
        write(self.donor, {
            "src/components/layout/navbar.tsx":
                'import Link from "next/link";\nconst k = process.env.NAVBAR_KEY;\nexport function Navbar() { return null }\n',
            "src/lib/analytics.ts": "export const a = process.env.ANALYTICS_ID;\n",
            "src/app/api/route.ts": "export const s = process.env.STRIPE_SECRET;\n",
            ".env.example": "DATABASE_URL=\nSTRIPE_SECRET=\n",
        })

    def env_facts(self, component):
        sc = scan_for(self.source, self.donor, False, component)
        return {f["text"] for f in sc["facts"] if f.get("kind") == "env-var" or "env" in f.get("kind", "")}, sc

    def test_component_only_reports_its_own_env_vars(self):
        _, sc = self.env_facts("navbar")
        text = json.dumps(sc["facts"])
        self.assertIn("NAVBAR_KEY", text)
        for other in ("ANALYTICS_ID", "STRIPE_SECRET", "DATABASE_URL"):
            self.assertNotIn(other, text)

    def test_without_a_component_the_whole_repo_is_scanned(self):
        sc = scan_for(self.source, self.donor, False, "")
        text = json.dumps(sc["facts"])
        for name in ("NAVBAR_KEY", "ANALYTICS_ID", "STRIPE_SECRET"):
            self.assertIn(name, text)


class MarkInterruptedTest(unittest.TestCase):
    def test_running_rows_become_interrupted(self):
        from fusionedges import store
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        old = paths.DB, paths.OUTPUT
        paths.DB, paths.OUTPUT = tmp / "runs.db", tmp
        self.addCleanup(lambda: setattr(paths, "DB", old[0]) or setattr(paths, "OUTPUT", old[1]))
        base = {"created": store.now_iso(), "mode": "prompt", "options": {}, "prompt": "x",
                "source": {}, "donor": {}, "folder": str(tmp)}
        store.save({**base, "id": "a", "status": "running"})
        store.save({**base, "id": "b", "status": "done"})
        self.assertEqual(store.mark_interrupted(), 1)
        st = {r["id"]: r["status"] for r in store.history()}
        self.assertEqual(st, {"a": "interrupted", "b": "done"})


class ReportContextTest(unittest.TestCase):
    def test_report_agent_sees_the_component_closure_not_bm25_junk(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        source, donor = make_repos(tmp)
        seen = []

        def spy(system, messages, tools, max_tokens):
            seen.append(messages[0]["text"])
            return {"role": "assistant", "text": '```json\n{"summary": "s", "conflicts": []}\n```', "tool_calls": []}, \
                {"in": 10, "out": 5}
        llm.CUSTOM["spy"] = spy
        os.environ["FUSIONEDGES_PROVIDERS"] = "spy"
        self.addCleanup(llm.CUSTOM.clear)
        self.addCleanup(os.environ.pop, "FUSIONEDGES_PROVIDERS", None)
        sc = scan_for(source, donor, False, "navbar")
        logs = []
        rep, _ = agents.run_report_agent("Add the navbar", source, donor, sc, logs.append, component="navbar")
        msg = seen[0]
        self.assertIn("donor:src/components/layout/navbar.tsx", msg)
        self.assertIn("theme-toggle.tsx", msg)
        self.assertNotIn("Chart = 1", msg)  # its name may appear in the file tree, its code must not
        self.assertNotIn("xxxxxxxxxx", msg)
        self.assertTrue(any("[report] context:" in l for l in logs))
        self.assertTrue(all(c["id"].startswith("R") for c in rep["retrieval"]["chunks"]))


class RateLimitWaitTest(unittest.TestCase):
    def setUp(self):
        self.slept = []
        self._orig = llm._sleep
        llm._sleep = self.slept.append
        llm.reset_cooldowns()
        os.environ["FUSIONEDGES_PROVIDERS"] = "flaky"

    def tearDown(self):
        llm._sleep = self._orig
        llm.CUSTOM.clear()
        llm.reset_cooldowns()
        os.environ.pop("FUSIONEDGES_PROVIDERS", None)

    def provider(self, fail_times, err):
        n = {"i": 0}

        def f(system, messages, tools, max_tokens):
            n["i"] += 1
            if n["i"] <= fail_times:
                raise err
            return {"role": "assistant", "text": "ok", "tool_calls": []}, {"in": 1, "out": 1}
        llm.CUSTOM["flaky"] = f

    def test_waits_out_a_per_minute_limit_then_succeeds(self):
        self.provider(1, llm._Retryable("rate limit / quota (HTTP 429): TPM", 20))
        msg = llm.Session("t", lambda *_: None).chat("s", [{"role": "user", "text": "x"}])
        self.assertEqual(msg["text"], "ok")
        self.assertEqual(self.slept, [21])

    def test_does_not_wait_for_a_size_error(self):
        self.provider(5, llm._Retryable("request too large or no credit (HTTP 413)", 30))
        with self.assertRaises(llm.LLMError):
            llm.Session("t", lambda *_: None).chat("s", [{"role": "user", "text": "x"}])
        self.assertEqual(self.slept, [])

    def test_does_not_wait_for_a_long_quota(self):
        self.provider(5, llm._Retryable("rate limit / quota (HTTP 429): daily", 600))
        with self.assertRaises(llm.LLMError):
            llm.Session("t", lambda *_: None).chat("s", [{"role": "user", "text": "x"}])
        self.assertEqual(self.slept, [])


class GroqUsedTest(unittest.TestCase):
    def test_keeps_used_and_retry_hint(self):
        msg = ('rate limit / quota (HTTP 429): Rate limit reached for model `x` on tokens per minute (TPM): '
               'Limit 8000, Used 6500, Requested 3000. Please try again in 4.2s.')
        out = llm.short_reason(msg)
        for bit in ("limit 8000", "already used 6500", "requested 3000", "retry in 4.2s"):
            self.assertIn(bit, out)


class ReportForAgentTest(unittest.TestCase):
    REPORT = {
        "summary": "Rewrite the Next navbar for CRA.",
        "donor_files": [{"path": "src/components/layout/navbar.tsx", "role": "component"}],
        "mount": {"file": "src/App.js", "replaces": "Header", "notes": "one navbar only"},
        "conflicts": [
            {"id": "C2", "severity": "medium", "title": "CSS variable", "type": "styling", "basis": "scanner",
             "citation": "verified", "impact": "falls back", "adaptation": "define --font",
             "evidence": [{"repo": "donor", "path": "a.css", "line": 3, "snippet": "x" * 300, "verified": True}]},
            {"id": "C1", "severity": "high", "title": "Next Link", "type": "routing", "basis": "scanner",
             "citation": "verified", "impact": "no nav", "adaptation": "use react-router Link",
             "evidence": [{"repo": "donor", "path": "b.tsx", "line": 2, "snippet": "y" * 300, "verified": True}]}],
        "plan": ["strip types", "mount it"]}

    def test_keeps_the_instructions_and_drops_the_bookkeeping(self):
        out = agents.report_for_agent(self.REPORT)
        for bit in ("src/App.js", "use react-router Link", "define --font"):
            self.assertIn(bit, out)
        self.assertNotIn("xxxx", out)
        self.assertNotIn("citation", out)
        self.assertLess(out.index("Next Link"), out.index("CSS variable"))  # high severity first
        self.assertLess(len(out), len(agents.report_markdown(self.REPORT)) * 0.7)


class DefaultBudgetTest(unittest.TestCase):
    def test_groq_first_gets_the_smaller_default(self):
        env = {k: os.environ.get(k) for k in ("FUSIONEDGES_PROVIDERS", "GROQ_API_KEY", "GEMINI_API_KEY",
                                              "FUSIONEDGES_CONTEXT_CHARS")}
        self.addCleanup(lambda: [os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
                                 for k, v in env.items()])
        os.environ.pop("FUSIONEDGES_CONTEXT_CHARS", None)
        os.environ["GROQ_API_KEY"], os.environ["GEMINI_API_KEY"] = "g", "k"
        os.environ["FUSIONEDGES_PROVIDERS"] = "groq,gemini"
        self.assertEqual(rag.context_budget_chars(), 7000)
        os.environ["FUSIONEDGES_PROVIDERS"] = "gemini,groq"
        self.assertEqual(rag.context_budget_chars(), 30000)
        os.environ["FUSIONEDGES_CONTEXT_CHARS"] = "9000"
        self.assertEqual(rag.context_budget_chars(), 9000)


class RepairTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.source, self.donor = make_repos(self.tmp)
        self.out = self.tmp / "out"
        shutil.copytree(self.source, self.out)
        self.calls = []
        self._build = agents.run_build
        self.addCleanup(setattr, agents, "run_build", self._build)
        agents.run_build = lambda folder: {"ok": False, "output": "Module not found: Can't resolve './missing'"}
        os.environ["FUSIONEDGES_PROVIDERS"] = "spy"
        self.addCleanup(os.environ.pop, "FUSIONEDGES_PROVIDERS", None)
        self.addCleanup(llm.CUSTOM.clear)

    def run_one(self, second):
        def spy(system, messages, tools, max_tokens):
            self.calls.append(messages)
            if len(self.calls) == 1:
                return ({"role": "assistant", "text": "<<<FILE src/Nav.js>>>\nimport './missing';\n<<<END>>>\n<<<DONE>>> ok",
                         "tool_calls": []}, {"in": 100, "out": 50})
            return second()
        llm.CUSTOM["spy"] = spy
        sc = scan_for(self.source, self.donor, False, "navbar")
        return agents.run_oneshot("Add the navbar", self.source, self.donor, self.out, sc, True, lambda *_: None,
                                  component="navbar", max_repairs=1)

    def test_repair_is_a_small_fresh_request(self):
        res = self.run_one(lambda: ({"role": "assistant", "text": "<<<DONE>>> nothing", "tool_calls": []}, {"in": 1, "out": 1}))
        repair = self.calls[1]
        self.assertEqual(len(repair), 1)  # not the first message + the earlier answer + more
        text = repair[0]["text"]
        self.assertIn("Can't resolve './missing'", text)
        self.assertIn("import './missing'", text)
        self.assertNotIn("RELEVANT FILES", text)
        self.assertNotIn("theme-toggle", text)  # donor context is not sent again

    def test_a_failed_repair_keeps_the_work_and_the_token_count(self):
        def boom():
            raise llm.LLMError("All providers failed. groq: too large")
        res = self.run_one(boom)
        self.assertEqual(res["files_written"], ["src/Nav.js"])
        self.assertIn("repair_error", res)
        self.assertNotIn("error", res)
        self.assertEqual(res["input_tokens"], 100)


class UsefulConflictsTest(unittest.TestCase):
    def conflict(self, sev, title, fix, cite="verified", typ="other"):
        return {"severity": sev, "title": title, "adaptation": fix, "citation": cite, "type": typ}

    def test_drops_hedges_unverified_repeats_and_caps(self):
        cs = [
            self.conflict("high", "Donor uses Next.js, source uses CRA", "Rewrite as a plain React function."),
            self.conflict("high", "Next.js specific modules imported", "Remove the next/* imports."),  # repeat
            self.conflict("high", "React 19 vs 18", "Audit for React 19 hooks (none detected)."),  # hedge
            self.conflict("high", "Alias @/ not configured", "Use relative paths."),
            self.conflict("high", "Made-up claim", "Do something.", cite="unverified"),
            self.conflict("low", "Minor thing", "Whatever."),
            self.conflict("medium", "TypeScript vs JavaScript", "Strip the types."),
        ]
        titles = [c["title"] for c in agents.useful_conflicts(cs)]
        self.assertEqual(titles, ["Donor uses Next.js, source uses CRA", "Alias @/ not configured",
                                  "TypeScript vs JavaScript"])
        many = [self.conflict("high", f"{a} problem", f"Change the {b} file") for a, b in
                (("Zebra", "red"), ("Quartz", "green"), ("Mango", "blue"), ("Ivory", "pink"), ("Cobalt", "grey"),
                 ("Saffron", "teal"), ("Walnut", "gold"))]
        self.assertEqual(len(agents.useful_conflicts(many)), 5)

    def test_missing_packages_come_from_the_scan_not_the_model(self):
        out = agents.report_for_agent({"summary": "S.", "conflicts": []},
                                      [{"name": "framer-motion", "donor": "^12", "source": "MISSING"},
                                       {"name": "clsx", "donor": "^2", "source": "^2"}])
        self.assertIn("framer-motion (^12)", out)
        self.assertNotIn("clsx", out)


class FitToLimitTest(unittest.TestCase):
    MSG = ("All providers failed. limit 8000 per-minute tokens, requested 8372: groq: request too large or no "
           "credit (HTTP 413): {}")

    def test_size_error_reads_the_numbers(self):
        self.assertEqual(agents.size_error(self.MSG), (8000, 8372))
        self.assertIsNone(agents.size_error("limit 8000 per-minute tokens, requested 3000: rate limit (HTTP 429)"))
        self.assertIsNone(agents.size_error("network error"))

    def test_budget_shrinks_until_it_fits(self):
        seen = []

        def call(budget):
            seen.append(budget)
            if budget > 20000:
                raise llm.LLMError(self.MSG)
            return "ok"
        logs = []
        self.assertEqual(agents.fit_to_limit(call, logs.append, start=30000), "ok")
        self.assertEqual(seen, sorted(seen, reverse=True))  # each try is smaller than the last
        self.assertLessEqual(seen[-1], 20000)
        self.assertLessEqual(len(seen), 3)
        self.assertTrue(any("[fit]" in l for l in logs))

    def test_other_errors_and_the_floor_are_not_retried(self):
        def other(budget):
            raise llm.LLMError("All providers failed. gemini: rate limit (HTTP 429)")
        with self.assertRaises(llm.LLMError):
            agents.fit_to_limit(other, lambda *_: None, start=30000)
        calls = []

        def always(budget):
            calls.append(budget)
            raise llm.LLMError(self.MSG)
        with self.assertRaises(llm.LLMError):
            agents.fit_to_limit(always, lambda *_: None, start=30000)
        self.assertEqual(len(calls), 3)  # gives up after three tries


class PacingTest(unittest.TestCase):
    def setUp(self):
        llm._USAGE.clear()
        self.addCleanup(llm._USAGE.clear)

    def test_no_wait_when_idle_or_unlimited(self):
        self.assertEqual(llm.pacing_wait("groq", 3000, now=1000), 0)
        self.assertEqual(llm.pacing_wait("gemini", 99999, now=1000), 0)  # no known per-minute limit

    def test_waits_until_enough_of_the_minute_has_passed(self):
        llm.record_usage("groq", 6500, now=1000)
        gap = llm.pacing_wait("groq", 3000, now=1010)
        self.assertAlmostEqual(gap, 51, delta=1.5)  # call finished at t=1000, frees up at t=1060
        self.assertEqual(llm.pacing_wait("groq", 3000, now=1061), 0)

    def test_a_request_too_big_for_any_minute_is_not_delayed(self):
        llm.record_usage("groq", 6500, now=1000)
        self.assertEqual(llm.pacing_wait("groq", 9000, now=1010), 0)  # waiting cannot fix it; the 413 path handles it

    def test_env_overrides_the_limit(self):
        os.environ["FUSIONEDGES_GROQ_TPM"] = "0"
        self.addCleanup(os.environ.pop, "FUSIONEDGES_GROQ_TPM", None)
        llm.record_usage("groq", 7900, now=1000)
        self.assertEqual(llm.pacing_wait("groq", 3000, now=1010), 0)


class DonorCodeCheckTest(unittest.TestCase):
    """A run must not pass just because the build passes: the donor's component has to be in the result."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.source, self.donor = make_repos(self.tmp)
        write(self.source, {"src/App.js": "import Header from './component/Header';\nexport default function App(){return <Header/>}\n"})

    def out(self, files):
        out = self.tmp / "out"
        shutil.rmtree(out, ignore_errors=True)
        shutil.copytree(self.source, out)
        write(out, files)
        return out

    def check(self, out):
        from fusionedges.checks import component_checks
        return component_checks(self.source, out, "navbar", {"ok": True}, "react-cra", donor=self.donor)

    def test_recreating_the_existing_header_fails(self):
        out = self.out({"src/component/Header.js": "export default function Header(){return <header/>}\n"})
        res = self.check(out)
        self.assertEqual(res["outcome"], "failed")
        self.assertFalse(res["checks"]["donor_code"]["pass"])
        self.assertFalse(res["checks"]["mounted"]["pass"])

    def test_bringing_the_donor_component_over_passes(self):
        out = self.out({"src/component/Navbar.js": "export function Navbar(){return <nav/>}\n",
                        "src/App.js": "import {Navbar} from './component/Navbar';\nexport default function App(){return <Navbar/>}\n"})
        res = self.check(out)
        self.assertEqual(res["outcome"], "passed")
        self.assertTrue(res["checks"]["donor_code"]["pass"])
        self.assertTrue(res["checks"]["mounted"]["pass"])


class NvidiaProviderTest(unittest.TestCase):
    def test_listed_and_used_only_with_a_key(self):
        env = {k: os.environ.get(k) for k in ("NVIDIA_API_KEY", "FUSIONEDGES_PROVIDERS", "FUSIONEDGES_NVIDIA_MODEL")}
        self.addCleanup(lambda: [os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
                                 for k, v in env.items()])
        os.environ.pop("FUSIONEDGES_PROVIDERS", None)
        os.environ.pop("NVIDIA_API_KEY", None)
        self.assertNotIn("nvidia", llm.configured())
        os.environ["NVIDIA_API_KEY"] = "k"
        self.assertIn("nvidia", llm.configured())
        os.environ["FUSIONEDGES_NVIDIA_MODEL"] = "deepseek-ai/some-model"
        self.assertEqual(llm.model_for("nvidia"), "deepseek-ai/some-model")
        self.assertEqual(llm.SPECS["nvidia"].url, "https://integrate.api.nvidia.com/v1/chat/completions")


class ShortReasonJson(unittest.TestCase):
    def test_json_error_message_is_kept(self):
        from fusionedges import llm
        msg = 'HTTP 404: [{\n  "error": {\n    "code": 404,\n    "message": "This model models/gemini-2.5-flash is no longer available to new users.",\n  }}]'
        out = llm.short_reason(msg)
        self.assertIn("no longer available to new users", out)
        self.assertNotIn("\n", out)


class BusyProviderRetry(unittest.TestCase):
    def test_503_is_retried_then_succeeds(self):
        from fusionedges import llm
        calls = {"n": 0}

        def flaky(system, messages, tools, max_tokens):
            calls["n"] += 1
            if calls["n"] < 3:
                raise llm._Retryable("provider error (HTTP 503): high demand", 1)
            return {"text": "ok", "tool_calls": []}, {"in": 1, "out": 1}

        llm.CUSTOM["flaky"] = flaky
        old = os.environ.get("FUSIONEDGES_PROVIDERS")
        os.environ["FUSIONEDGES_PROVIDERS"] = "flaky"
        try:
            llm._COOLDOWN.clear()
            out = llm.Session("t").chat("s", [{"role": "user", "text": "hi"}])
            self.assertEqual(out["text"], "ok")
            self.assertEqual(calls["n"], 3)
        finally:
            llm.CUSTOM.pop("flaky", None)
            if old is None:
                os.environ.pop("FUSIONEDGES_PROVIDERS", None)
            else:
                os.environ["FUSIONEDGES_PROVIDERS"] = old


class ModelFallback(unittest.TestCase):
    def _run(self, behaviour):
        from fusionedges import llm
        seen = []

        def fake_openai(spec, model, key, system, messages, tools, max_tokens):
            seen.append(model)
            behaviour(model)
            return {"text": model, "tool_calls": []}, {"in": 1, "out": 1}

        keep = (llm._openai, dict(os.environ))
        llm._openai = fake_openai
        os.environ["GEMINI_API_KEY"] = "k"
        os.environ["FUSIONEDGES_PROVIDERS"] = "gemini"
        os.environ["FUSIONEDGES_GEMINI_MODEL"] = "m-new,m-old"
        try:
            llm._COOLDOWN.clear()
            return llm.Session("t").chat("s", [{"role": "user", "text": "hi"}]), seen
        finally:
            llm._openai = keep[0]
            os.environ.clear()
            os.environ.update(keep[1])

    def test_busy_model_falls_back_to_older(self):
        from fusionedges import llm

        def behaviour(model):
            if model == "m-new":
                raise llm._Retryable("provider error (HTTP 503): high demand", 1)
        out, seen = self._run(behaviour)
        self.assertEqual(out["text"], "m-old")
        self.assertEqual(seen[-1], "m-old")
        self.assertGreaterEqual(seen.count("m-new"), 3)

    def test_retired_model_falls_back_at_once(self):
        from fusionedges import llm

        def behaviour(model):
            if model == "m-new":
                raise llm.LLMError("HTTP 404: This model is no longer available to new users")
        out, seen = self._run(behaviour)
        self.assertEqual(out["text"], "m-old")
        self.assertEqual(seen, ["m-new", "m-old"])


class HostScriptCoupling(unittest.TestCase):
    def make(self):
        root = Path(tempfile.mkdtemp())
        (root / "public/assets/js").mkdir(parents=True)
        (root / "src/component").mkdir(parents=True)
        (root / "public/index.html").write_text('<script src="assets/js/main.js"></script>')
        (root / "public/assets/js/main.js").write_text(
            '$(window).on("scroll", function () {\n  if ($(".navbar").offset().top > 25) {}\n});\n$("#contactForm").x();')
        (root / "src/component/Header.js").write_text('export default () => <nav className="navbar navbar-default" />;')
        (root / "src/component/Contact.js").write_text('export default () => <form id="contactForm" />;')
        return root

    def test_finds_selectors_the_site_scripts_need_for_the_component(self):
        from fusionedges import scanner
        found = scanner.script_selectors(self.make(), "navbar")
        self.assertEqual(sorted(found), [".navbar"])
        self.assertEqual(found[".navbar"][0], "public/assets/js/main.js")
        self.assertEqual(found[".navbar"][1], 2)

    def test_no_keyword_returns_all_shared_selectors(self):
        from fusionedges import scanner
        self.assertEqual(sorted(scanner.script_selectors(self.make())), ["#contactForm", ".navbar"])

    def test_sites_without_global_scripts_have_none(self):
        from fusionedges import scanner
        self.assertEqual(scanner.script_selectors(Path(tempfile.mkdtemp()), "navbar"), {})

    def test_report_for_agent_always_includes_the_contract(self):
        fact = {"kind": "host-script-coupling", "text": "The site's own scripts look up .navbar."}
        out = agents.report_for_agent({"summary": "S.", "conflicts": []}, None, [fact])
        self.assertIn("Keep: The site's own scripts look up .navbar.", out)


class QuotaFallback(ModelFallback):
    def test_out_of_quota_model_falls_back_at_once(self):
        from fusionedges import llm

        def behaviour(model):
            if model == "m-new":
                raise llm._Retryable("rate limit (HTTP 429): You exceeded your current quota", 60)
        out, seen = self._run(behaviour)
        self.assertEqual(out["text"], "m-old")
        self.assertEqual(seen, ["m-new", "m-old"])


class UnparseableAnswer(OneshotContextTest):
    def test_answer_without_file_blocks_is_logged_and_saved(self):
        def prose(system, messages, tools, max_tokens):
            return ({"role": "assistant", "text": "### Summary\nI created Header.js and more.\n<<<DONE>>> done", "tool_calls": []},
                    {"in": 10, "out": 5})
        llm.CUSTOM["spy"] = prose
        res, logs = self.run_one()
        self.assertEqual(res["files_written"], [])
        self.assertTrue(any("no <<<FILE>>> block found" in l and "Summary" in l for l in logs))
        saved = (self.tmp / "answers.txt").read_text()
        self.assertIn("I created Header.js", saved)
