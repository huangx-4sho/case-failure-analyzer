"""Audit destinations retain independent histories for the same task in jobs."""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import cfa
from task_ref import discover_job_task_refs, job_report_key, resolve_task_ref


class JobScopedAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        task = self.root / "tasks/001-task1"
        task.mkdir(parents=True)
        (task / "task.toml").write_text('schema_version="1.4"')

    def trial(self, job, suffix, timestamp):
        path = self.root / "jobs" / job / ("001-task1__" + suffix)
        path.mkdir(parents=True)
        (path / "agent").mkdir()
        (path / "agent/trajectory.json").write_text(json.dumps({"steps": []}))
        (path / "verifier").mkdir()
        (path / "trial.log").write_text("trial complete")
        (path / "result.json").write_text(
            json.dumps({"verifier_result": {"rewards": {"reward": 1}}})
        )
        os.utime(path / "result.json", (timestamp, timestamp))
        return path

    def test_latest_is_scoped_to_selected_job_and_all_keeps_both_jobs(self):
        self.trial("older-job", "old", 10)
        selected = self.trial("older-job", "retry", 20)
        newest = self.trial("newer-job", "new", 30)
        latest = resolve_task_ref("task1", self.root)
        self.assertEqual(latest.trial_dir, newest.resolve())
        self.assertEqual(latest.report_dir, self.root.resolve() / "failure-report/newer-job/task1")
        refs = discover_job_task_refs(self.root)
        self.assertEqual(len(refs), 2)
        by_job = {r.job_dir.name: r for r in refs}
        self.assertEqual(by_job["older-job"].trial_dir, selected.resolve())
        self.assertEqual(
            resolve_task_ref("task1", self.root, job="older-job").trial_dir, selected.resolve()
        )
        self.assertNotEqual(by_job["older-job"].report_dir, by_job["newer-job"].report_dir)

    def test_task_id_override_remains_exact(self):
        self.trial("run", "trial", 10)
        override = self.root / "custom"
        ref = resolve_task_ref("task1", self.root, report_dir_override=override)
        self.assertEqual(ref.report_dir, override.resolve())
        self.assertEqual(ref.report_root, self.root.resolve() / "failure-report")

    def test_suite_keys_stay_stable_before_and_after_sibling_jobs_exist(self):
        a = self.root / "suite-a/jobs/same-name"
        b = self.root / "suite-b/jobs/same-name"
        a.mkdir(parents=True)
        self.assertEqual(job_report_key(a, self.root), "suite-a--same-name")
        b.mkdir(parents=True)
        self.assertEqual(job_report_key(a, self.root), "suite-a--same-name")
        self.assertEqual(job_report_key(b, self.root), "suite-b--same-name")

    def test_all_dispatches_each_job_task_and_override_is_batch_root(self):
        self.trial("job-a", "old", 10)
        self.trial("job-a", "retry", 20)
        self.trial("job-b", "new", 30)
        called = []
        output = self.root / "custom-batch"
        with (
            patch(
                "analyze_case.main", side_effect=lambda **kwargs: called.append(list(sys.argv[1:]))
            ),
            patch("cfa.update_failure_report_index", return_value=output / "INDEX.md") as index,
            redirect_stdout(io.StringIO()),
        ):
            rc = cfa.cmd_audit(["--all", "--jobs-root", str(self.root), "--output", str(output)])
        self.assertEqual(rc, 0)
        self.assertEqual(len(called), 2)
        destinations = {Path(args[args.index("--output") + 1]) for args in called}
        self.assertEqual(
            destinations, {output.resolve() / "job-a/task1", output.resolve() / "job-b/task1"}
        )
        index.assert_called_once_with(output.resolve())

    def test_all_default_updates_global_index(self):
        self.trial("job-a", "one", 10)
        self.trial("job-b", "two", 20)
        with (
            patch("analyze_case.main"),
            patch("cfa.update_failure_report_index") as index,
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(cfa.cmd_audit(["--all", "--jobs-root", str(self.root)]), 0)
        index.assert_called_once_with(self.root.resolve() / "failure-report")

    def test_real_all_keeps_same_task_in_two_jobs_and_manual_overview(self):
        self.trial("job-a", "one", 10)
        self.trial("job-b", "two", 20)
        report_root = self.root / "failure-report"
        report_root.mkdir()
        manual = report_root / "manual-overview.md"
        manual.write_text("Preserve this manual overview.")
        with redirect_stdout(io.StringIO()):
            rc = cfa.cmd_audit(["--all", "--jobs-root", str(self.root), "--no-verdict-stdout"])
        self.assertEqual(rc, 0)
        for job in ("job-a", "job-b"):
            data = report_root / job / "task1/data"
            self.assertTrue((data / "analysis.json").is_file())
            evidence = json.loads((data / "evidence.json").read_text())
            self.assertEqual(Path(evidence["job_dir"]).name, job)
        self.assertTrue((report_root / "INDEX.md").is_file())
        self.assertEqual(manual.read_text(), "Preserve this manual overview.")

    def test_all_equals_report_override_is_scoped_batch_root(self):
        self.trial("job-a", "one", 10)
        self.trial("job-b", "two", 20)
        called = []
        output = self.root / "custom-report-batch"
        with (
            patch(
                "analyze_case.main", side_effect=lambda **kwargs: called.append(list(sys.argv[1:]))
            ),
            patch("cfa.update_failure_report_index") as index,
            redirect_stdout(io.StringIO()),
        ):
            rc = cfa.cmd_audit(
                ["--all", "--jobs-root=" + str(self.root), "--report-dir=" + str(output)]
            )
        self.assertEqual(rc, 0)
        self.assertEqual(len(called), 2)
        for args in called:
            self.assertTrue(Path(args[args.index("--output") + 1]).is_relative_to(output.resolve()))
        index.assert_called_once_with(output.resolve())
