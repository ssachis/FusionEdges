import csv
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from fusionedges import bench, benchreport, paths


def fake_rec(success=True, tokens=1000, report_tokens=0, cost=0.01, retries=0, model="m1"):
    return {"status": "done", "checks": {"outcome": "passed" if success else "partial", "score": "7/7" if success else "5/7"},
            "build": {"ok": True}, "agent": {"input_tokens": tokens, "output_tokens": 0, "est_cost_usd": cost, "retries": retries},
            "report_usage": {"input_tokens": report_tokens, "output_tokens": 0, "est_cost_usd": 0.005} if report_tokens else None,
            "models": [model], "seconds": 3.0, "source_framework": "react-cra", "donor_framework": "next"}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._old = (paths.HOME, paths.OUTPUT, paths.DB)
        paths.HOME, paths.OUTPUT = self.tmp, self.tmp / "output"
        paths.DB = paths.OUTPUT / "runs.db"
        self.manifest = self.tmp / "tasks.jsonl"

    def tearDown(self):
        paths.HOME, paths.OUTPUT, paths.DB = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_manifest(self, n=3):
        lines = ["# comment"] + [json.dumps({"id": f"t{i}", "source": f"s{i}", "donor": f"d{i}", "component": "navbar",
                                              "category": "react->next" if i % 2 else "html->react"}) for i in range(1, n + 1)]
        self.manifest.write_text("\n".join(lines))


class ManifestTest(Base):
    def test_loads_tasks_and_skips_comments(self):
        self.write_manifest(3)
        tasks = bench.load_tasks(self.manifest)
        self.assertEqual([t["id"] for t in tasks], ["t1", "t2", "t3"])

    def test_rejects_bad_manifests(self):
        for text, msg in (('{"id": "a", "source": "s", "donor": "d"}', "missing component"),
                          ('{"id": "a b", "source": "s", "donor": "d", "component": "x"}', "id may only"),
                          ("not json", "not valid JSON"),
                          ('{"id": "a", "source": "s", "donor": "d", "component": "x"}\n'
                           '{"id": "a", "source": "s", "donor": "d", "component": "x"}', "duplicate id"),
                          ("", "no tasks")):
            self.manifest.write_text(text)
            with self.assertRaises(bench.ManifestError) as cm:
                bench.load_tasks(self.manifest)
            self.assertIn(msg, str(cm.exception))

    def test_plan_covers_every_job_in_a_repeatable_shuffled_order(self):
        self.write_manifest(4)
        tasks = bench.load_tasks(self.manifest)
        a = bench.plan(tasks, ["A", "D"], 3, seed=7)
        b = bench.plan(tasks, ["A", "D"], 3, seed=7)
        c = bench.plan(tasks, ["A", "D"], 3, seed=8)
        key = lambda jobs: [bench.job_key(j["task"]["id"], j["condition"], j["rep"]) for j in jobs]  # noqa: E731
        self.assertEqual(len(a), 4 * 2 * 3)
        self.assertEqual(len(set(key(a))), 24)
        self.assertEqual(key(a), key(b))
        self.assertNotEqual(key(a), key(c))
        self.assertNotEqual(key(a), sorted(key(a)))  # shuffled, not grouped
        with self.assertRaises(bench.ManifestError):
            bench.plan(tasks, ["A", "Z"], 1, 1)


class RunnerTest(Base):
    def test_runs_resumes_and_records_failures(self):
        self.write_manifest(2)
        calls = []

        def run_fn(params, log=None, run_id=None):
            calls.append((params["component"], params["mode"], run_id))
            if run_id.endswith("t2-D-r1"):
                raise RuntimeError("provider down")
            return fake_rec(report_tokens=500 if params["mode"] == "report" else 0)

        rows = bench.run_bench("p", self.manifest, ["A", "D"], 1, 1, run_fn=run_fn, limit=3, log=lambda *_: None)
        self.assertEqual(len(rows), 3)
        rows = bench.run_bench("p", self.manifest, ["A", "D"], 1, 1, run_fn=run_fn, log=lambda *_: None)
        self.assertEqual(len(rows), 4)
        self.assertEqual(len(calls), 4)  # the first three jobs were not repeated
        self.assertEqual({r["condition"] for r in rows}, {"A", "D"})
        failed = [r for r in rows if r["status"] != "done"]
        self.assertEqual(len(failed), 1)
        self.assertIn("provider down", failed[0]["error"])
        self.assertFalse(failed[0]["success"])
        modes = {(r["condition"]) for r in rows}
        self.assertTrue(all(m in ("prompt", "report") for _, m, _ in calls))
        self.assertTrue((paths.OUTPUT / "bench" / "p" / "config.json").exists())

    def test_result_row_fields(self):
        job = {"task": {"id": "t1", "category": "react->next"}, "condition": "D", "rep": 2}
        row = bench.result_row(job, fake_rec(tokens=1000, report_tokens=400, cost=0.02), None, "p", "rid")
        self.assertTrue(row["success"])
        self.assertEqual((row["checks_passed"], row["checks_total"]), (7, 7))
        self.assertEqual(row["tokens_total"], 1400)
        self.assertAlmostEqual(row["cost"], 0.025)
        self.assertIsNone(row["runtime_errors"])
        partial = bench.result_row(job, fake_rec(success=False), None, "p", "rid")
        self.assertFalse(partial["success"])

    def test_bad_bench_name_is_rejected(self):
        with self.assertRaises(bench.ManifestError):
            bench.bench_dir("../x")


def row(task, cond, rep, success, tokens=1000, cost=0.01, retries=0, model="m1", category="c"):
    return {"task": task, "category": category, "condition": cond, "rep": rep, "status": "done",
            "outcome": "passed" if success else "partial", "success": success, "build_ok": True, "runtime_errors": None,
            "agent_tokens": tokens, "report_tokens": 0, "tokens_total": tokens, "cost": cost, "retries": retries,
            "seconds": 1.0, "models": [model], "run_id": f"{task}{cond}{rep}", "error": None, "checks_passed": 7, "checks_total": 7}


class StatsTest(unittest.TestCase):
    def test_sign_test_exact_values(self):
        self.assertEqual(benchreport.sign_test([1, 1, 1, 1, 1])["p"], 0.0625)
        s = benchreport.sign_test([1, 1, 1, 1, 1, 1, 1, 1, 1, -1, 0])
        self.assertEqual((s["wins"], s["losses"], s["ties"]), (9, 1, 1))
        self.assertAlmostEqual(s["p"], 0.021484375)
        self.assertIsNone(benchreport.sign_test([0, 0])["p"])
        self.assertEqual(benchreport.sign_test([1, -1])["p"], 1.0)

    def test_bootstrap_interval_brackets_the_mean_and_is_repeatable(self):
        diffs = [1.0, 1.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0]
        ci = benchreport.bootstrap_ci(diffs, seed=3)
        self.assertEqual(ci, benchreport.bootstrap_ci(diffs, seed=3))
        self.assertLessEqual(ci[0], sum(diffs) / len(diffs))
        self.assertGreaterEqual(ci[1], sum(diffs) / len(diffs))
        self.assertIsNone(benchreport.bootstrap_ci([1.0]))

    def test_paired_uses_task_means_across_reps(self):
        rows = [row("a", "A", 1, False), row("a", "A", 2, True), row("a", "D", 1, True), row("a", "D", 2, True),
                row("b", "A", 1, True), row("b", "D", 1, True)]
        p = benchreport.paired(rows, "success", "D", "A")
        self.assertEqual(p["tasks"], 2)
        self.assertAlmostEqual(p["mean_diff"], 0.25)  # task a: 0.5 -> 1.0, task b: 1.0 -> 1.0
        self.assertEqual((p["sign"]["wins"], p["sign"]["ties"]), (1, 1))

    def test_paired_ignores_tasks_missing_a_condition(self):
        rows = [row("a", "A", 1, True), row("b", "A", 1, True), row("a", "D", 1, True)]
        self.assertEqual(benchreport.paired(rows, "success", "D", "A")["tasks"], 1)
        self.assertIsNone(benchreport.paired([row("a", "A", 1, True)], "success", "D", "A"))


class ReportTest(Base):
    def make_rows(self):
        rows = []
        for i in range(1, 7):
            for rep in (1, 2, 3):
                rows.append(row(f"t{i}", "A", rep, success=(i <= 3), tokens=24000, cost=0.022, category="react->next" if i % 2 else "html->react"))
                rows.append(row(f"t{i}", "D", rep, success=(i <= 5), tokens=41000, cost=0.032, category="react->next" if i % 2 else "html->react"))
        return rows

    def test_summary_numbers(self):
        s = benchreport.summarize(self.make_rows())
        self.assertAlmostEqual(s["A"]["success_rate"], 0.5)
        self.assertAlmostEqual(s["D"]["success_rate"], 5 / 6)
        self.assertEqual(s["D"]["mean_tokens"], 41000)
        self.assertAlmostEqual(s["A"]["cost_per_success"], 0.022 * 18 / 9)

    def test_markdown_has_the_sections_and_the_right_signs(self):
        md = benchreport.markdown(self.make_rows(), "pilot", baseline="A")
        for text in ("# Benchmark report: pilot", "## Results by condition", "## Paired differences against A",
                     "## Success by category", "## Per task", "## Runs that did not succeed", "## How to read this",
                     "Small sample", "+33 pts", "Better / worse / tied"):
            self.assertIn(text, md)
        # Tokens went up with the report, so the sign test row for tokens counts 'better' as lower: 0 better, 6 worse.
        self.assertIn("| 0 / 6 / 0 |", md)

    def test_warns_when_runs_used_different_models(self):
        rows = self.make_rows()
        rows[0]["models"] = ["other"]
        self.assertIn("different models", benchreport.markdown(rows, "x"))

    def test_empty_results(self):
        self.assertIn("No results yet", benchreport.markdown([], "x"))

    def test_generate_writes_report_csv_and_summary(self):
        results = self.tmp / "results.jsonl"
        results.write_text("\n".join(json.dumps(r) for r in self.make_rows()))
        out = benchreport.generate("pilot", results, self.tmp / "out")
        self.assertEqual(out["runs"], 36)
        self.assertTrue((self.tmp / "out" / "report.md").read_text().startswith("# Benchmark report"))
        with open(self.tmp / "out" / "results.csv") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 36)
        self.assertEqual(rows[0]["task"], "t1")
        self.assertIn("A", json.loads((self.tmp / "out" / "summary.json").read_text()))


if __name__ == "__main__":
    unittest.main()
