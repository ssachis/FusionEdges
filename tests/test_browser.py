"""Browser check (browser.py). Needs Python Playwright + Chromium; skipped where they are missing."""
import shutil
import tempfile
import unittest
from pathlib import Path

from fusionedges import browser, llm
from fusionedges.agents import run_oneshot
from fusionedges.pipeline import scan_for
from tests import fake_llm
from tests.test_flow import FIX

OK, WHY = browser.available()
TEXT = "<main><h1>My site</h1><p>" + "Some words about this site. " * 20 + "</p></main>"
GOOD = ('<!doctype html><html><head><meta name="viewport" content="width=device-width"></head><body>'
        '<header class="navbar"><a href="/">Home</a><a href="/about">About</a><a href="/blog">Blog</a></header>' + TEXT + '</body></html>')
HIDDEN = ('<!doctype html><html><head><meta name="viewport" content="width=device-width"></head><body>'
          '<header class="navbar"><a href="/">Logo</a><nav style="display:none"><a href="/about">About</a>'
          '<a href="/blog">Blog</a></nav></header>' + TEXT + '</body></html>')
CRASH = GOOD.replace("</body>", "<script>addEventListener('scroll', () => document.querySelector('.gone').offsetTop)</script>"
                     + "<div style='height:3000px'></div></body>")


def site(html):
    d = Path(tempfile.mkdtemp())
    (d / "index.html").write_text(html)
    return d


@unittest.skipUnless(OK, WHY)
class BrowserCheckTest(unittest.TestCase):
    def tearDown(self):
        for d in getattr(self, "_dirs", []):
            shutil.rmtree(d, ignore_errors=True)

    def check(self, html, base=None):
        d = site(html)
        self._dirs = getattr(self, "_dirs", []) + [d]
        return browser.check(d, "navbar", shots=d / "shots", base=base)

    def test_a_working_page_passes_and_is_photographed(self):
        r = self.check(GOOD)
        self.assertTrue(r["pass"], r["problems"])
        self.assertEqual(r["viewports"]["desktop"]["links"], ["Home", "About", "Blog"])
        self.assertTrue(Path(r["viewports"]["mobile"]["screenshot"]).exists())

    def test_hidden_links_are_reported(self):
        r = self.check(HIDDEN)
        self.assertFalse(r["pass"])
        self.assertTrue(any("links are probably hidden" in p for p in r["problems"]), r["problems"])

    def test_a_crash_on_scroll_is_reported_unless_the_original_site_had_it(self):
        r = self.check(CRASH)
        self.assertTrue(any("while scrolling" in p for p in r["problems"]), r["problems"])
        self.assertEqual(sum("while scrolling" in p for p in r["problems"]), 1)     # same error at both widths: once
        base = {"viewports": {"desktop": {"errors_scroll": r["viewports"]["desktop"]["errors_scroll"]}}}
        self.assertTrue(self.check(CRASH, base=base)["pass"])

    def test_the_agent_repairs_what_the_browser_saw(self):
        calls = []

        def agent(system, messages, tools, max_tokens):
            first = next(m["text"] for m in messages if m["role"] == "user")
            calls.append(first)
            fixed = "real browser" in first          # the repair message describes what a user saw
            html = GOOD if fixed else HIDDEN
            return {"role": "assistant", "text": f"<<<FILE index.html>>>\n{html}\n<<<END>>>\n<<<DONE>>>\nok", "tool_calls": []}, \
                {"in": 100, "out": 50}

        fake_llm.install(("fake", agent))
        self.addCleanup(llm.CUSTOM.clear)
        out = Path(tempfile.mkdtemp()) / "project"
        out.mkdir()
        self._dirs = [out.parent]
        sc = scan_for(FIX / "host_react", FIX / "react_donor", False, "navbar")
        res = run_oneshot("add the navbar", FIX / "host_react", FIX / "react_donor", out, sc, False, lambda m: None,
                          component="navbar", after_build=lambda o: browser.check(o, "navbar")["problems"])
        self.assertEqual(res["browser_repairs"], 1)
        self.assertEqual(len(calls), 2)
        self.assertIn("links are probably hidden", calls[1])
        self.assertEqual(res["browser_log"][-1], [])                   # fixed on the second try
        self.assertIn("/about", (out / "index.html").read_text())


class BrowserProblemsTest(unittest.TestCase):
    """problems() is plain logic: runs everywhere, Playwright or not."""

    def test_network_noise_and_blank_pages(self):
        res = {"component": "navbar", "viewports": {"desktop": {"text": 5, "found": True, "links": ["a", "b"]}}}
        self.assertIn("blank", browser.problems(res)[0])

    def test_unstyled_navbar_is_flagged(self):
        res = {"component": "navbar", "viewports": {"desktop": {"text": 500, "found": True, "links": ["a", "b", "c"], "height": 212}}}
        self.assertTrue(any("unstyled" in p for p in browser.problems(res)))

    def test_skipped_check_has_no_problems(self):
        self.assertEqual(browser.problems({"skipped": True, "reason": "x"}), [])


MENU_ONLY = ('<!doctype html><html><head><meta name="viewport" content="width=device-width"></head><body>'
             '<header class="navbar"><a href="/">~/ Me</a><a href="/gh">Star</a><button>☰</button>'
             '<nav style="display:none"><a href="/">Home</a><a href="/about">About</a><a href="/p">Projects</a></nav></header>'
             + TEXT + '</body></html>')


@unittest.skipUnless(OK, WHY)
class PilotBrowserRegressionTest(BrowserCheckTest):
    """Found on the 27 Oct pilot: a desktop navbar with a logo, a button and a menu icon, its real links hidden."""

    def test_links_hidden_behind_a_menu_on_desktop_fail(self):
        r = self.check(MENU_ONLY)
        self.assertTrue(any("of the navbar's links are hidden" in p for p in r["problems"]), r["problems"])


class SidewaysBaselineTest(unittest.TestCase):
    def test_sideways_scroll_the_original_site_already_had_is_ignored(self):
        res = {"component": "navbar", "viewports": {"mobile": {"text": 500, "found": True, "links": ["menu"], "sideways": True}}}
        self.assertTrue(any("sideways" in p for p in browser.problems(res)))
        base = {"viewports": {"mobile": {"sideways": True}}}
        self.assertFalse(any("sideways" in p for p in browser.problems(res, base)))
