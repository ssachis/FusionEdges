import json
import os
from pathlib import Path

from fusionedges import adherence, compare, memory, store
from fusionedges.experiment import run_experiment, table
from fusionedges.pipeline import run
from fusionedges.prompts import default_prompt
from tests import fake_llm
from tests.test_flow import FIX, Base

HOST, DONOR = FIX / "next_host", FIX / "react_donor"


class CompareTest(Base):
    def _run(self, mode, source=HOST, **kw):
        fake_llm.install(("second", fake_llm.smart))
        return run({"mode": mode, "source": str(source), "donor": str(DONOR), "add_component": True, "component": "navbar",
                    "prompt": default_prompt(False, False, "navbar"), "build": False, **kw}, lambda m: None)

    def test_auto_pair_records_verdict_and_note(self):
        a = self._run("prompt")
        self.assertNotIn("comparison", a)                 # nothing to pair with yet
        d = self._run("report")
        c = compare.get(d["comparison"])
        self.assertEqual((c["prompt_run"], c["report_run"], c["condition"]), (a["id"], d["id"], "D"))
        self.assertEqual(c["verdict"], "improved")         # fake agent makes the classic mistakes without the report
        self.assertIn("The report run fixed", c["note"])
        self.assertTrue(c["fixed"])
        self.assertGreater(c["adherence"]["testable"], 0)
        compare.set_note(c["id"], "looks right")
        self.assertEqual(compare.record(a["id"], d["id"])["human_note"], "looks right")   # re-recording keeps the note
        self.assertEqual(compare.summary()["improved"], 1)

    def test_history_condition_never_sees_its_own_task(self):
        self._run("prompt")
        self._run("report")
        e = self._run("history")
        self.assertEqual(e["report"]["memory"]["pool"], 0)     # only this task's runs exist -> excluded
        other = self._run("history", source=FIX / "host_react")
        mem = other["report"]["memory"]
        self.assertGreater(mem["pool"], 0)
        self.assertTrue(mem["cases"])
        c = compare.get(e["comparison"])
        self.assertEqual(c["condition"], "E")

    def test_same_task_allowed_only_when_asked(self):
        self._run("report")
        os.environ["FUSIONEDGES_MEMORY_SAME_TASK"] = "1"
        try:
            e = self._run("history", auto_compare=False)
        finally:
            os.environ.pop("FUSIONEDGES_MEMORY_SAME_TASK")
        self.assertGreater(e["report"]["memory"]["pool"], 0)

    def test_experiment_runner(self):
        fake_llm.install(("second", fake_llm.smart))
        spec = {"name": "t", "tasks": [{"source": str(HOST), "donor": str(DONOR), "component": "navbar"}]}
        rows = run_experiment(spec, log=lambda m: None, build=False)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["experiment"], "t")
        self.assertIn("1 comparisons: 1 improved", table(compare.all_rows()))


class AdherenceTest(Base):
    def test_markers(self):
        t = dict(adherence.tests_for("Replace usePathname from next/navigation with useLocation from react-router-dom."))
        self.assertFalse(t["usePathname"])
        self.assertFalse(t["next/navigation"])
        self.assertTrue(t["useLocation"])
        self.assertTrue(t["react-router-dom"])
        self.assertFalse(dict(adherence.tests_for("Remove the 'use client' directive"))["use client"])
        # English words are dropped when they do not occur in the repos' code
        self.assertNotIn("stub/mock", dict(adherence.tests_for("stub/mock the auth client", vocab="authClient")))

    def test_check_against_added_lines(self):
        diff = {"files": [{"patch": "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-import { usePathname } from 'next/navigation'\n"
                                    "+import { useLocation } from 'react-router-dom'"}]}
        added = adherence.added_text(diff)
        self.assertEqual(adherence.check("Replace usePathname with useLocation", added)["status"], "addressed")
        self.assertEqual(adherence.check("Keep classes 'navbar-default'", added)["status"], "not addressed")

    def test_blast_radius(self):
        diff = {"files": [{"path": "app/layout.tsx", "status": "modified", "kind": "code"},
                          {"path": "app/page.tsx", "status": "modified", "kind": "code"},
                          {"path": "components/Nav.tsx", "status": "added", "kind": "code"},
                          {"path": "package-lock.json", "status": "modified", "kind": "generated"}]}
        self.assertEqual(adherence.blast_radius(diff, {"mount": {"file": "app/layout.tsx"}}), ["app/page.tsx"])


class VerdictRulesTest(Base):
    def _rec(self, mode, models, score, error=None):
        return {"id": mode, "mode": mode, "status": "done", "models": models, "agent": {"error": error} if error else {},
                "checks": {"checks": {"a": {"label": "A", "pass": True}, "b": {"label": "B", "pass": score > 1}}}}

    def test_different_models_are_inconclusive(self):
        j = compare.judge(self._rec("prompt", ["gemini:lite"], 1), self._rec("report", ["gemini:flash"], 2), {}, {})
        self.assertEqual(j["verdict"], "inconclusive")
        self.assertIn("different models", j["note"])

    def test_failed_ai_call_is_inconclusive(self):
        j = compare.judge(self._rec("prompt", ["m"], 1, error="All providers failed"), self._rec("report", ["m"], 2), {}, {})
        self.assertEqual(j["verdict"], "inconclusive")

    def test_same_model_is_judged(self):
        j = compare.judge(self._rec("prompt", ["m"], 1), self._rec("report", ["m"], 2), {}, {})
        self.assertEqual(j["verdict"], "improved")

    def test_reviews_and_stored_diffs(self):
        a = CompareTest._run(self, "prompt")
        d = CompareTest._run(self, "report")
        self.assertTrue(store.load_diff(d["id"]))                     # the patch itself is in runs.db
        cid = d["comparison"]
        compare.add_review(cid, "tester", "no change", "read the code")
        compare.add_review(cid, "tester", "improved", "second look")
        self.assertEqual([r["verdict"] for r in compare.reviews_for(cid)], ["no change", "improved"])   # history kept
        self.assertEqual(compare.get(cid)["review"]["verdict"], "improved")


class ReviewLessonsTest(Base):
    def _task(self, source):
        a = CompareTest._run(self, "prompt", source=source)
        d = CompareTest._run(self, "report", source=source)
        return d["comparison"]

    def test_lessons_reach_a_later_report_on_another_task_only(self):
        cid = self._task(HOST)
        compare.add_review(cid, "Sachi", "worse", "inline styles hid the nav",
                           lessons=[{"kind": "avoid", "topic": "tailwind-responsive",
                                     "text": "Do not express Tailwind responsive classes as inline styles."}])
        # same task: the leakage guard keeps its own lesson out
        e_same = CompareTest._run(self, "history", auto_compare=False)
        self.assertEqual(e_same["report"]["memory"]["review_lessons"], [])
        # another task: the lesson is retrieved, marked human-reviewed, and reaches the report agent's prompt
        e_other = CompareTest._run(self, "history", source=FIX / "host_react", auto_compare=False)
        rl = e_other["report"]["memory"]["review_lessons"]
        self.assertEqual(len(rl), 1)
        self.assertEqual(rl[0]["trust"], "human-reviewed")
        self.assertIn("inline styles", e_other["report"]["memory"]["lessons"][0])

    def test_ai_reviews_are_labelled_as_such_and_repeats_are_merged(self):
        lesson = {"kind": "do", "topic": "host-script-coupling", "text": "Keep the classes the site's scripts use."}
        compare.add_review(self._task(HOST), "Claude (read every diff)", "improved", "kept classes", lessons=[lesson])
        sc = {"source": {"framework": "next"}, "donor": {"framework": "react-vite"},
              "facts": [{"kind": "host-script-coupling", "text": "scripts look up .navbar"}]}
        got = memory.review_lessons(sc, "navbar", ("other/site", "other/donor", "navbar"))
        self.assertEqual(got[0]["trust"], "AI-reviewed")
        self.assertEqual(got[0]["seen"], 1)

    def test_import_adds_lessons_to_an_existing_review(self):
        import tempfile
        a = CompareTest._run(self, "prompt")
        d = CompareTest._run(self, "report")
        spec = {"reviewer": "R", "reviews": [{"prompt_run": a["id"], "report_run": d["id"], "verdict": "no change", "summary": "s"}]}
        f = Path(tempfile.mkdtemp()) / "r.json"
        f.write_text(json.dumps(spec))
        self.assertEqual(compare.import_reviews(str(f)), 1)
        spec["reviews"][0]["lessons"] = [{"text": "L"}]
        f.write_text(json.dumps(spec))
        self.assertEqual(compare.import_reviews(str(f)), 1)            # updated in place, not a second review
        self.assertEqual(len(compare.reviews_for(d["comparison"])), 1)
        self.assertEqual(compare.reviews_for(d["comparison"])[0]["lessons"], [{"text": "L"}])
