import json
import unittest
from pathlib import Path

from fusionedges import analyze, render_markdown

FIX = Path(__file__).resolve().parent / "fixtures"
HOST, DONOR = FIX / "host_react", FIX / "donor_html"


class AnalyzerTest(unittest.TestCase):
    def setUp(self):
        self.rep = analyze(HOST, DONOR, "footer")
        self.cats = {f["category"] for f in self.rep["findings"]}

    def test_profiles(self):
        self.assertEqual(self.rep["host"]["framework"], "react-vite")
        self.assertEqual(self.rep["donor"]["framework"], "html")

    def test_section_located(self):
        s = self.rep["donor_section"]
        self.assertTrue(s["found"])
        self.assertEqual(s["file"], "index.html")
        self.assertIn("footer-links", s["classes"])
        self.assertEqual(s["css_files"], ["css/style.css"])

    def test_expected_findings(self):
        for c in ["framework-mismatch", "css-class-collision", "global-selector-leak",
                  "css-variable-conflict", "css-variable-undefined", "id-collision",
                  "asset-reference", "external-resource"]:
            self.assertIn(c, self.cats)

    def test_jsx_conversion_evidence(self):
        fm = next(f for f in self.rep["findings"] if f["category"] == "framework-mismatch")
        text = " ".join(fm["evidence"])
        for needle in ["className", "htmlFor", "style", "void tags", "event handlers"]:
            self.assertIn(needle, text)

    def test_class_collision_names_footer(self):
        cc = next(f for f in self.rep["findings"] if f["category"] == "css-class-collision")
        self.assertTrue(any(e.startswith(".footer ") for e in cc["evidence"]))

    def test_slot_occupied_when_donor_is_not_component(self):
        # donor is plain HTML so no component-name collision; host Footer => slot-occupied
        self.assertIn("slot-occupied", self.cats)

    def test_deterministic(self):
        a = json.dumps(analyze(HOST, DONOR, "footer"), sort_keys=True)
        b = json.dumps(analyze(HOST, DONOR, "footer"), sort_keys=True)
        self.assertEqual(a, b)

    def test_missing_section(self):
        r = analyze(HOST, DONOR, "carousel")
        self.assertFalse(r["donor_section"]["found"])

    def test_react_donor_into_html_host(self):
        r = analyze(DONOR, HOST, "footer")
        self.assertEqual(r["donor"]["framework"], "react-vite")
        cats = {f["category"] for f in r["findings"]}
        self.assertIn("framework-mismatch", cats)
        self.assertIn("slot-occupied", cats)

    def test_markdown_renders(self):
        md = render_markdown(self.rep)
        self.assertIn("# Compatibility report", md)
        self.assertIn("css-class-collision", md)


if __name__ == "__main__":
    unittest.main()
