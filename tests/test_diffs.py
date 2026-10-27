import json
import subprocess
import tempfile
from pathlib import Path

from fusionedges import diffs, store
from fusionedges.pipeline import run
from fusionedges.prompts import default_prompt
from tests import fake_llm
from tests.test_flow import FIX, Base


class DiffTest(Base):
    def _run(self, mode):
        fake_llm.install(("second", fake_llm.smart))
        return run({"mode": mode, "source": str(FIX / "next_host"), "donor": str(FIX / "react_donor"),
                    "add_component": True, "component": "navbar",
                    "prompt": default_prompt(False, False, "navbar"), "build": False}, lambda m: None)

    def test_run_records_baseline_and_diff(self):
        rec = self._run("report")
        project = Path(rec["project"])
        log = subprocess.run(["git", "log", "--format=%s"], cwd=project, capture_output=True, text=True).stdout
        self.assertEqual(len(log.strip().splitlines()), 2)
        self.assertIn(diffs.BASELINE_MSG, log.strip().splitlines()[-1])   # first commit = starting copy
        self.assertTrue(diffs.baseline_commit(project))
        d = diffs.for_run(store.load(rec["id"]))
        self.assertTrue(d["ok"], d.get("error"))
        self.assertEqual(d["baseline"], "git")
        files = {f["path"]: f for f in d["files"]}
        self.assertEqual(files["components/Header.tsx"]["status"], "deleted")   # replaced the old navbar
        self.assertEqual(files["app/layout.tsx"]["status"], "modified")
        self.assertIn("+++ b/app/layout.tsx", files["app/layout.tsx"]["patch"])
        self.assertEqual(files["public/logo.png"]["kind"], "binary")
        self.assertNotIn(".gitignore", files)                  # our own .gitignore lines are not the agent's work
        self.assertEqual(rec["changes"], d["totals"])
        self.assertTrue((Path(rec["folder"]) / "changes.diff").read_text().strip())
        self.assertEqual(json.loads((Path(rec["folder"]) / "changes.json").read_text())["totals"], d["totals"])

    def test_both_conditions_differ(self):
        a, b = self._run("prompt"), self._run("report")
        da, db = diffs.for_run(a), diffs.for_run(b)
        self.assertNotEqual({f["path"] for f in da["files"]}, {f["path"] for f in db["files"]})

    def test_old_run_without_baseline_uses_starting_repo(self):
        rec = self._run("prompt")
        project = Path(rec["project"])
        subprocess.run(["rm", "-rf", str(project / ".git")], check=True)   # like runs made before this change
        for p in Path(rec["folder"]).glob("changes.*"):
            p.unlink()
        d = diffs.for_run(rec)
        self.assertTrue(d["ok"], d.get("error"))
        self.assertEqual(d["baseline"], "folder")
        self.assertTrue(d["files"])

    def test_compare_flags_generated_and_skips_build_output(self):
        with tempfile.TemporaryDirectory() as t:
            a, b = Path(t, "a"), Path(t, "b")
            for root in (a, b):
                (root / "src").mkdir(parents=True)
                (root / "src/x.js").write_text("one\ntwo\n")
            (b / "src/x.js").write_text("one\nTWO\nthree\n")
            (b / "package-lock.json").write_text("{}")
            (b / "node_modules/pkg").mkdir(parents=True)
            (b / "node_modules/pkg/i.js").write_text("x")
            (b / "build").mkdir()
            (b / "build/main.js").write_text("x")
            out = {f["path"]: f for f in diffs.compare(a, b)}
            self.assertEqual(set(out), {"src/x.js", "package-lock.json"})
            self.assertEqual((out["src/x.js"]["added"], out["src/x.js"]["removed"]), (2, 1))
            self.assertEqual(out["package-lock.json"]["kind"], "generated")
            self.assertEqual(out["package-lock.json"]["patch"], "")
