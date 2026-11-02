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
        self.assertEqual(rl[0]["trust"], "unverified")
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


class RecordsTest(Base):
    def test_browser_checks_experiment_and_export(self):
        from fusionedges import paths, records
        a = CompareTest._run(self, "prompt")
        d = CompareTest._run(self, "report")
        compare.record(a["id"], d["id"], experiment="exp1")
        bdir = paths.HOME / "experiments" / "browser" / "x"
        bdir.mkdir(parents=True)
        (bdir / "labels.json").write_text(json.dumps({"checked_at": "2026-10-27T10:40:00", "checker": "t",
                                                     "labels": {"A": a["id"], "D": d["id"]}}))
        nav = {"navbar": {"tag": "header", "classes": "x", "height": 80}, "visibleLinks": ["Home", "About"],
               "inlineHidden": [], "hasNavbarClass": False, "hasNavbarDefault": False}
        res = {lab: {vp: {"atLoad": nav, "afterScroll": nav,
                          "errors": {"load": ["console: Failed to load resource: net::ERR_X", "pageerror: boom"],
                                     "scroll": ["pageerror: top"] if lab == "A" else []}}
                     for vp in ("desktop", "mobile")} for lab in ("A", "D")}
        (bdir / "results.json").write_text(json.dumps(res))
        (bdir / "A-desktop.png").write_bytes(b"png")
        self.assertEqual(records.import_browser(str(bdir), log=lambda m: None), 4)
        rows = records.browser_for(a["id"])
        self.assertEqual(len(rows), 2)
        desk = [r for r in rows if r["viewport"] == "desktop"][0]
        self.assertEqual(desk["link_count"], 2)
        self.assertEqual(desk["scroll_errors"], ["pageerror: top"])
        self.assertEqual(desk["load_errors"], ["pageerror: boom"])          # the checker's own network noise is dropped
        self.assertEqual(desk["screenshot"], "experiments/browser/x/A-desktop.png")
        self.assertEqual(len(desk["screenshot_sha256"]), 64)
        records.record_experiment("exp1", "2026-10-27", writeup="experiments/x.md")
        out = records.export("exp1", "2026-10-27")
        data = json.loads(out.read_text())
        self.assertEqual(data["experiment"]["writeup"], "experiments/x.md")
        self.assertEqual(len(data["comparisons"]), 1)
        self.assertEqual(len(data["runs"][a["id"]]["browser"]), 2)
        self.assertTrue(data["runs"][d["id"]]["diff"])


class HelpedTest(Base):
    def test_yes_no_is_stored_per_comparison_and_per_experiment(self):
        from fusionedges import records
        a = CompareTest._run(self, "prompt")
        d = CompareTest._run(self, "report")
        c = compare.get(d["comparison"])
        self.assertEqual((c["helped"], c["helped_basis"], c["fair"]), ("yes", "automatic", 1))   # fake agent improves
        compare.add_review(c["id"], "Sachi", "worse", "broke the page")
        c = compare.get(c["id"])
        self.assertEqual((c["helped"], c["helped_basis"]), ("no", "review"))       # the review wins and is stored
        compare.record(a["id"], d["id"])                                            # re-recording keeps the review's answer
        self.assertEqual(compare.get(c["id"])["helped"], "no")
        with store.connect() as db:
            db.execute("UPDATE comparisons SET experiment='e1' WHERE id=?", (c["id"],))
        records.record_experiment("e1", "2026-10-27")
        with store.connect() as db:
            ans, why = db.execute("SELECT answer, answer_reason FROM experiments WHERE name='e1'").fetchone()
        self.assertEqual(ans, "no")
        self.assertIn("worse", why)

    def test_answer_rules(self):
        from fusionedges.records import answer
        self.assertEqual(answer([])["answer"], "not yet")
        self.assertEqual(answer([{"fair": 0, "helped": "yes", "final": "improved"}])["answer"], "not yet")   # no fair pair
        rows = [{"fair": 1, "helped": "yes", "final": "improved"}, {"fair": 1, "helped": "yes", "final": "improved"},
                {"fair": 0, "helped": "no", "final": "worse"}]
        self.assertEqual(answer(rows)["answer"], "yes")
        self.assertEqual(answer([{"fair": 1, "helped": "no", "final": "no change"}])["answer"], "not clear yet")


class FairTextTest(Base):
    def test_fair_stored_as_text_is_read_correctly(self):
        from fusionedges.records import answer
        rows = [{"fair": "0", "helped": "no", "final": "worse"}, {"fair": "1", "helped": "yes", "final": "improved"}]
        self.assertIn("1 of 1 fair comparison", answer(rows)["reason"])


class FeedbackTest(Base):
    def test_a_persons_answer_outranks_and_trains(self):
        from fusionedges import records
        a = CompareTest._run(self, "prompt")
        d = CompareTest._run(self, "report")
        cid = d["comparison"]
        self.assertEqual(compare.get(cid)["helped"], "yes")                     # automatic
        an = records.analysis()
        self.assertEqual([w["id"] for w in an["waiting"]], [cid])               # waiting for a person
        compare.add_feedback(cid, "no", "the links vanish on desktop", as_lesson=True, user="Sachi")
        c = compare.get(cid)
        self.assertEqual((c["helped"], c["helped_basis"]), ("no", "user"))
        self.assertEqual(c["feedback"]["note"], "the links vanish on desktop")
        self.assertEqual(records.analysis()["waiting"], [])
        compare.add_review(cid, "Claude", "improved", "later review")           # a review does not overwrite the person
        self.assertEqual(compare.get(cid)["helped"], "no")
        compare.record(a["id"], d["id"])                                        # neither does re-recording
        self.assertEqual(compare.get(cid)["helped"], "no")
        ls = compare.lessons()                                                  # the note became a human-reviewed lesson
        mine = [l for l in ls if l["text"] == "the links vanish on desktop"]
        self.assertEqual(len(mine), 1)                        # still used after a later AI review
        self.assertEqual((mine[0]["trust"], mine[0]["kind"]), ("human-reviewed", "avoid"))
        compare.add_feedback(cid, "yes")                                        # changing your mind keeps history
        self.assertEqual(compare.get(cid)["helped"], "yes")
        self.assertEqual([f["helped"] for f in compare.feedback_for(cid)], ["no", "yes"])
        with self.assertRaises(ValueError):
            compare.add_feedback(cid, "maybe")


class WaitingOrderTest(Base):
    def test_fair_pairs_are_asked_first_and_lessons_add_up(self):
        from fusionedges import records
        CompareTest._run(self, "prompt")
        d = CompareTest._run(self, "report")
        cid = d["comparison"]
        compare.add_review(cid, "Claude", "improved", "r", lessons=[{"kind": "do", "text": "AI lesson"}])
        compare.add_feedback(cid, "yes", "my lesson", as_lesson=True)
        texts = {l["text"] for l in records.analysis()["lessons"]}
        self.assertEqual(texts, {"AI lesson", "my lesson"})      # the person's lesson is added, not swapped in


class ProposedLessonsTest(Base):
    def test_drafts_causes_and_keep(self):
        from fusionedges import lessons_auto, memory
        rep_rec = {"id": "r1", "mode": "report", "source": {"label": "me/site"}, "donor": {"label": "you/donor"},
                   "options": {"component": "navbar"}, "source_framework": "react-cra", "donor_framework": "next",
                   "facts": [{"kind": "styling-system"}],
                   "report": {"conflicts": [{"title": "Tailwind missing", "adaptation": "Convert the classes to inline styles."}]}}
        d = lessons_auto.draft(rep_rec, ["On desktop (1280px) 5 of the navbar's links are hidden (Home, About); only ☰ is visible."])
        self.assertEqual((d[0]["topic"], d[0]["cause"]), ("tailwind-responsive", "report advice"))   # the report misled
        alone = {**rep_rec, "id": "a1", "mode": "prompt", "report": None}
        d2 = lessons_auto.draft(alone, ["On desktop (1280px) the navbar is 212px tall: it is probably unstyled.",
                                        "On phone (390px) the navbar is 212px tall: it is probably unstyled."])
        self.assertEqual(len(d2), 1)                                                   # one lesson per pattern
        self.assertEqual(d2[0]["cause"], "agent alone")
        crash = lessons_auto.draft({**alone, "facts": []}, ["On desktop the page throws while scrolling: pageerror: x is undefined"])
        self.assertEqual(crash[0]["topic"], "runtime-error")                           # no script fact: generic lesson
        lessons_auto.store_drafts(rep_rec, ["On desktop (1280px) 5 of the navbar's links are hidden (Home); only ☰ is visible."])
        lessons_auto.store_drafts(rep_rec, ["On desktop (1280px) 5 of the navbar's links are hidden (Home); only ☰ is visible."])
        rows = lessons_auto.for_runs(["r1"])
        self.assertEqual(len(rows), 1)                                                 # not drafted twice
        self.assertEqual(rows[0]["status"], "proposed")
        self.assertEqual(compare.lessons(), [])                                        # proposed: not used yet
        lessons_auto.decide(rows[0]["id"], "kept")
        got = compare.lessons()
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["trust"], "human-reviewed")
        self.assertEqual(compare.lessons(exclude_task=("me/site", "you/donor", "navbar")), [])   # leakage guard
        sc = {"source": {"framework": "react-cra"}, "donor": {"framework": "next"}, "facts": [{"kind": "styling-system", "text": "tailwind"}]}
        self.assertTrue(memory.review_lessons(sc, "navbar", ("other/site", "you/donor", "navbar")))  # reaches condition E
        lessons_auto.decide(rows[0]["id"], "discarded")
        self.assertEqual(compare.lessons(), [])
        with self.assertRaises(ValueError):
            lessons_auto.decide(rows[0]["id"], "maybe")


class KeptInAnalysisTest(Base):
    def test_kept_browser_lessons_show_in_the_analysis(self):
        from fusionedges import lessons_auto, records
        CompareTest._run(self, "prompt")
        d = CompareTest._run(self, "report")
        rec = store.load(d["id"])
        lessons_auto.store_drafts(rec, ["On desktop (1280px) the navbar is 212px tall: it is probably unstyled."])
        lid = lessons_auto.for_runs([d["id"]])[0]["id"]
        self.assertNotIn("unstyled", " ".join(l["text"] for l in records.analysis()["lessons"]))
        lessons_auto.decide(lid, "kept")
        self.assertIn("unstyled", " ".join(l["text"] for l in records.analysis()["lessons"]))


class RunnerConditionsTest(Base):
    def test_env_is_pinned_and_restored_and_screening_table(self):
        import os
        from fusionedges import experiment
        fake_llm.install(("second", fake_llm.smart))
        os.environ.pop("FUSIONEDGES_TEST_PIN", None)
        seen = []
        orig = experiment.run

        def spy(params, log, run_id):
            seen.append((params["mode"], params["edit"], os.environ.get("FUSIONEDGES_TEST_PIN")))
            return orig(params, log=log, run_id=run_id)
        experiment.run = spy
        try:
            spec = {"name": "t", "env": {"FUSIONEDGES_TEST_PIN": "on"},
                    "tasks": [{"source": str(HOST), "donor": str(DONOR), "component": "navbar"}]}
            logs = []
            experiment.run_experiment(spec, log=logs.append, conditions=["prompt"], repeats=2, build=False)
        finally:
            experiment.run = orig
        self.assertEqual({x[2] for x in seen}, {"on"})                    # pinned during the runs
        self.assertIsNone(os.environ.get("FUSIONEDGES_TEST_PIN"))         # restored after
        self.assertIn("SCREENING", "\n".join(logs))
        self.assertEqual(experiment.CONDITIONS["agent+report"], ("report", "agent"))


class ResumeTest(Base):
    def test_quota_stops_cleanly_and_the_next_day_resumes(self):
        from fusionedges import experiment, llm
        spec = {"name": "multi-day", "tasks": [{"source": str(HOST), "donor": str(DONOR), "component": "navbar"},
                                               {"source": str(FIX / "host_react"), "donor": str(DONOR), "component": "navbar"}]}
        calls = {"n": 0}

        def day_one(system, messages, tools, max_tokens):    # works for 3 calls, then the daily quota is gone
            calls["n"] += 1
            if calls["n"] > 3:
                raise llm._Retryable("rate limit / quota (HTTP 429): You exceeded your current quota", 86400)
            return fake_llm.smart(system, messages, tools, max_tokens)

        fake_llm.install(("day1", day_one))
        os.environ["FUSIONEDGES_WAIT_MAX"] = "0"
        self.addCleanup(os.environ.pop, "FUSIONEDGES_WAIT_MAX", None)
        logs = []
        experiment.run_experiment(spec, log=logs.append, build=False)
        text = "\n".join(logs)
        self.assertIn("QUOTA REACHED", text)
        prog = json.loads(experiment._progress_path("multi-day").read_text())
        done1 = [k for k in prog if not k.startswith("pair|")]
        self.assertTrue(0 < len(done1) < 4)                               # some runs, not all
        fake_llm.install(("day2", fake_llm.smart))                        # quota reset
        logs2 = []
        experiment.run_experiment(spec, log=logs2.append, build=False)
        self.assertIn(f"{len(done1)} already done (resuming)", logs2[0])
        prog2 = json.loads(experiment._progress_path("multi-day").read_text())
        self.assertEqual(len([k for k in prog2 if not k.startswith("pair|")]), 4)   # 2 tasks x 2 conditions
        for k in done1:
            self.assertEqual(prog2[k], prog[k])                           # nothing redone
        self.assertEqual(len([c for c in compare.all_rows() if c["experiment"] == "multi-day"]), 2)
        logs3 = []
        experiment.run_experiment(spec, log=logs3.append, build=False)    # a third call has nothing left to do
        self.assertTrue(any("All 4 runs done" in l for l in logs3))
        self.assertFalse(any("=== multi-day" in l for l in logs3))
