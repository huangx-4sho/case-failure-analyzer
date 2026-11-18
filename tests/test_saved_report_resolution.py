"""Read-only inspect/diff resolution with saved reports and no raw jobs."""

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import cfa


class SavedReportResolutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.bench = Path(self.tmp.name)
        self.reports = self.bench / "failure-report"

    def report(self, path, marker, job="", trial="", flat_prefix=None):
        path.mkdir(parents=True, exist_ok=True)
        evidence = {
            "marker": marker,
            "job_dir": job,
            "trial_name": trial,
            "runtime": {"verdict": "failed"},
        }
        analysis = {
            "verdict": "failed",
            "primary_root_cause": {"category": "agent", "code": marker},
        }
        for kind, payload in (
            ("evidence", evidence),
            ("analysis", analysis),
            ("summary", {"marker": marker}),
        ):
            target = (
                path / f"{flat_prefix}.{kind}.json"
                if flat_prefix
                else path / "data" / f"{kind}.json"
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(payload))
        return path

    def run_cli(self, arguments):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cfa.main(arguments)
        return rc, out.getvalue(), err.getvalue()

    def test_inspect_saved_job_scoped_report_without_raw_jobs(self):
        self.report(self.reports / "jobA/task2", "A")
        rc, out, err = self.run_cli(
            ["inspect", "002-task002", "--jobs-root", str(self.bench), "--raw"]
        )
        self.assertEqual((0, ""), (rc, err))
        self.assertEqual("A", json.loads(out)["marker"])
        self.assertFalse((self.bench / "jobs").exists())

    def test_ambiguous_jobs_require_selection_and_explicit_job_path_works(self):
        self.report(self.reports / "jobA/task2", "A", job="/removed/bench/jobs/jobA")
        self.report(self.reports / "jobB/task2", "B", job="/removed/bench/jobs/jobB")
        rc, _, err = self.run_cli(["inspect", "task2", "--jobs-root", str(self.bench)])
        self.assertEqual(2, rc)
        self.assertIn("multiple saved reports", err)
        for selector in ("jobB", "/removed/bench/jobs/jobB"):
            rc, out, err = self.run_cli(
                ["inspect", "task2", "--jobs-root", str(self.bench), "--job", selector, "--raw"]
            )
            self.assertEqual((0, ""), (rc, err))
            self.assertEqual("B", json.loads(out)["marker"])

    def test_qualified_suite_job_scopes_support_exact_scope_and_metadata(self):
        self.report(self.reports / "suite--jobA/task2", "A", job="/removed/suite/jobs/jobA")
        for selector in ("suite--jobA", "jobA", "/removed/suite/jobs/jobA"):
            rc, out, _ = self.run_cli(
                ["inspect", "task2", "--jobs-root", str(self.bench), "--job", selector, "--raw"]
            )
            self.assertEqual(0, rc)
            self.assertEqual("A", json.loads(out)["marker"])

    def test_exact_saved_trial_selector_disambiguates_jobs(self):
        self.report(self.reports / "jobA/task2", "baseA")
        self.report(self.reports / "jobB/task2", "baseB")
        self.report(self.reports / "jobB/task2/trials/002-task002__b", "trialB")
        for target, flags in (("task2", ["--trial", "002-task002__b"]), ("002-task002__b", [])):
            rc, out, err = self.run_cli(
                ["inspect", target, "--jobs-root", str(self.bench), "--raw", *flags]
            )
            self.assertEqual((0, ""), (rc, err))
            self.assertEqual("trialB", json.loads(out)["marker"])

    def test_single_trial_landing_uses_saved_identity(self):
        self.report(self.reports / "jobA/task2", "A", trial="002-task002__a")
        rc, out, _ = self.run_cli(
            [
                "inspect",
                "task2",
                "--jobs-root",
                str(self.bench),
                "--trial",
                "002-task002__a",
                "--raw",
            ]
        )
        self.assertEqual(0, rc)
        self.assertEqual("A", json.loads(out)["marker"])
        rc, _, err = self.run_cli(
            ["inspect", "task2", "--jobs-root", str(self.bench), "--trial", "missing"]
        )
        self.assertEqual(2, rc)
        self.assertIn("missing", err)

    def test_explicit_inspect_report_dir_is_exact_even_with_other_job_selector(self):
        override = self.report(self.bench / "custom", "EXACT")
        self.report(self.reports / "jobA/task2", "A")
        rc, out, err = self.run_cli(
            ["inspect", "task2", "--report-dir", str(override), "--job", "jobA", "--raw"]
        )
        self.assertEqual((0, ""), (rc, err))
        self.assertEqual("EXACT", json.loads(out)["marker"])
        rc, _, err = self.run_cli(
            [
                "inspect",
                "task2",
                "--jobs-root",
                str(self.bench),
                "--report-dir",
                str(self.bench / "absent"),
            ]
        )
        self.assertEqual(2, rc)
        self.assertIn("does not exist", err)

    def test_legacy_flat_and_hierarchical_fallbacks(self):
        self.report(self.reports, "FLAT", flat_prefix="task2")
        self.report(self.reports, "OTHER", flat_prefix="task3")
        rc, out, _ = self.run_cli(["inspect", "task2", "--jobs-root", str(self.bench), "--raw"])
        self.assertEqual(0, rc)
        self.assertEqual("FLAT", json.loads(out)["marker"])
        self.report(self.reports / "task4", "LEGACY")
        rc, out, _ = self.run_cli(["inspect", "task4", "--jobs-root", str(self.bench), "--raw"])
        self.assertEqual(0, rc)
        self.assertEqual("LEGACY", json.loads(out)["marker"])

    def test_scoped_report_supersedes_legacy_copy_but_explicit_override_reads_old(self):
        old = self.report(self.reports / "task2", "OLD", job="/removed/jobs/jobA")
        self.report(self.reports / "jobA/task2", "NEW")
        rc, out, _ = self.run_cli(
            ["inspect", "task2", "--jobs-root", str(self.bench), "--job", "jobA", "--raw"]
        )
        self.assertEqual(0, rc)
        self.assertEqual("NEW", json.loads(out)["marker"])
        rc, out, _ = self.run_cli(["inspect", "task2", "--report-dir", str(old), "--raw"])
        self.assertEqual(0, rc)
        self.assertEqual("OLD", json.loads(out)["marker"])

    def test_diff_same_task_across_explicit_jobs_and_trial_sides(self):
        self.report(self.reports / "jobA/task2", "A")
        self.report(self.reports / "jobB/task2", "B")
        self.report(self.reports / "jobA/task2/trials/task2__a", "TRIAL_A")
        args = [
            "diff",
            "task2",
            "task2",
            "--jobs-root",
            str(self.bench),
            "--job-a",
            "jobA",
            "--job-b",
            "jobB",
            "--raw",
        ]
        rc, out, err = self.run_cli(args)
        self.assertEqual((0, ""), (rc, err))
        payload = json.loads(out)
        self.assertEqual("A", payload["primary_root_cause"]["a"]["code"])
        self.assertEqual("B", payload["primary_root_cause"]["b"]["code"])
        rc, out, err = self.run_cli(args + ["--trial-a", "task2__a"])
        self.assertEqual((0, ""), (rc, err))
        self.assertEqual("TRIAL_A", json.loads(out)["primary_root_cause"]["a"]["code"])

    def test_diff_single_task_trials_and_invalid_side_selectors(self):
        self.report(self.reports / "jobA/task2/trials/first", "FIRST")
        self.report(self.reports / "jobA/task2/trials/second", "SECOND")
        args = [
            "diff",
            "task2",
            "--jobs-root",
            str(self.bench),
            "--job",
            "jobA",
            "--trials",
            "first",
            "second",
            "--raw",
        ]
        rc, out, err = self.run_cli(args)
        self.assertEqual((0, ""), (rc, err))
        self.assertEqual("FIRST", json.loads(out)["primary_root_cause"]["a"]["code"])
        rc, _, err = self.run_cli(
            ["diff", "task2", "--job-a", "jobA", "--jobs-root", str(self.bench)]
        )
        self.assertEqual(2, rc)
        self.assertIn("require positional", err)

    def test_removed_raw_job_path_can_select_saved_report_metadata(self):
        removed = self.bench / "jobs/jobA"
        self.report(self.reports / "jobA/task2", "A", job=str(removed))
        self.report(self.reports / "jobB/task2", "B", job=str(self.bench / "jobs/jobB"))
        rc, out, err = self.run_cli(["inspect", "task2", "--jobs-root", str(removed), "--raw"])
        self.assertEqual((0, ""), (rc, err))
        self.assertEqual("A", json.loads(out)["marker"])
        self.assertFalse(removed.exists())

    def test_prefixed_legacy_flat_files_resolve_by_normalized_task(self):
        self.report(self.reports, "TWO", flat_prefix="002-task002")
        self.report(self.reports, "THREE", flat_prefix="003-task003")
        rc, out, err = self.run_cli(["inspect", "task2", "--jobs-root", str(self.bench), "--raw"])
        self.assertEqual((0, ""), (rc, err))
        self.assertEqual("TWO", json.loads(out)["marker"])
        rc, out, err = self.run_cli(
            ["diff", "task2", "task3", "--jobs-root", str(self.bench), "--raw"]
        )
        self.assertEqual((0, ""), (rc, err))
        self.assertEqual("THREE", json.loads(out)["primary_root_cause"]["b"]["code"])

    def test_report_root_or_scoped_root_can_be_jobs_root(self):
        self.report(self.reports / "jobA/task2", "A")
        for root in (self.reports, self.reports / "jobA"):
            rc, out, _ = self.run_cli(["inspect", "task2", "--jobs-root", str(root), "--raw"])
            self.assertEqual(0, rc)
            self.assertEqual("A", json.loads(out)["marker"])


if __name__ == "__main__":
    unittest.main()
