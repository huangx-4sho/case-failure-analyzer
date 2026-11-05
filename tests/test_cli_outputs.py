#!/usr/bin/env python3
"""
CLI output-shape tests (`tests/test_cli_outputs.py`)

Locks in the parts of the `analyze_case.py` CLI contract that the existing suite
did not exercise: the `--format` filter, the multi-trial output layout (per-trial
subdirectories plus `job_summary.json`), and `--phase collect`.

Every subprocess test in the suite before this file ran a single-trial job, so the
multi-trial branch in `main()` was entirely untested.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from report_style import section_title  # noqa: E402

ENTRYPOINT = REPO_ROOT / "scripts" / "analyze_case.py"
FIXTURE_JOB = REPO_ROOT / "tests" / "fixtures" / "regressions" / "mixed-job-success-trial" / "job"

JSON_ARTIFACTS = ("evidence.json", "analysis.json", "candidate-hypotheses.json")
MARKDOWN_ARTIFACTS = ("report.md", "report.zh.md", "report.en.md")


def run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(ENTRYPOINT), *args],
        capture_output=True,
        text=True,
    )


class TestFormatFilter(unittest.TestCase):
    def _run(self, fmt: str) -> Path:
        tmp = Path(tempfile.mkdtemp())
        out = tmp / "out"
        proc = run_cli(
            "--job",
            str(FIXTURE_JOB),
            "--trial",
            "trial_b_pass",
            "--output",
            str(out),
            "--format",
            fmt,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return out

    def test_json_format_suppresses_markdown(self) -> None:
        out = self._run("json")
        for name in JSON_ARTIFACTS:
            self.assertTrue((out / name).is_file(), f"{name} should exist")
        for name in MARKDOWN_ARTIFACTS:
            self.assertFalse((out / name).exists(), f"{name} should be suppressed")

    def test_markdown_format_suppresses_json(self) -> None:
        out = self._run("markdown")
        for name in MARKDOWN_ARTIFACTS:
            self.assertTrue((out / name).is_file(), f"{name} should exist")
        for name in JSON_ARTIFACTS:
            self.assertFalse((out / name).exists(), f"{name} should be suppressed")

    def test_both_format_writes_everything(self) -> None:
        out = self._run("both")
        for name in JSON_ARTIFACTS + MARKDOWN_ARTIFACTS:
            self.assertTrue((out / name).is_file(), f"{name} should exist")


class TestMultiTrialLayout(unittest.TestCase):
    """The committed fixture has one failing and one passing trial."""

    def test_per_trial_subdirectories_and_job_summary(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        out = tmp / "out"
        proc = run_cli("--job", str(FIXTURE_JOB), "--output", str(out), "--trial", "all")
        self.assertEqual(proc.returncode, 0, proc.stderr)

        for trial in ("trial_a_fail", "trial_b_pass"):
            self.assertTrue((out / trial / "analysis.json").is_file(), trial)

        # The failing trial must not be polluted by the job-level errored count,
        # and the passing trial must stay passed.
        fail = json.loads((out / "trial_a_fail" / "analysis.json").read_text(encoding="utf-8"))
        passed = json.loads((out / "trial_b_pass" / "analysis.json").read_text(encoding="utf-8"))
        self.assertEqual(passed["verdict"], "passed")
        self.assertEqual(passed["primary_root_cause"]["category"], "none")
        self.assertNotEqual(fail["verdict"], "passed")

        summary = json.loads((out / "job_summary.json").read_text(encoding="utf-8"))
        stats = summary["batch_statistics"]
        self.assertEqual(stats["total_trials"], 2)
        verdicts = {row["trial_name"]: row["verdict"] for row in summary["trials"]}
        self.assertEqual(verdicts, {"trial_a_fail": fail["verdict"], "trial_b_pass": "passed"})

        # Assert job_summary.md and companions are created and contain critical batch summary metrics
        self.assertTrue((out / "job_summary.md").is_file())
        self.assertTrue((out / "job_summary.zh.md").is_file())
        self.assertTrue((out / "job_summary.en.md").is_file())

        job_md = (out / "job_summary.md").read_text(encoding="utf-8")
        self.assertIn("Total Trials", job_md)
        self.assertIn("Pass Rate", job_md)
        self.assertIn("50.0%", job_md)
        self.assertIn("trial_a_fail", job_md)
        self.assertIn("trial_b_pass", job_md)
        self.assertIn("trial_a_fail/report.md", job_md)
        self.assertIn("trial_b_pass/report.md", job_md)
        self.assertIn("agent", job_md)

    def test_multi_trial_format_markdown_generates_md_and_suppresses_json(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        out = tmp / "out"
        proc = run_cli(
            "--job",
            str(FIXTURE_JOB),
            "--output",
            str(out),
            "--trial",
            "all",
            "--format",
            "markdown",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue((out / "job_summary.md").is_file())
        self.assertTrue((out / "job_summary.zh.md").is_file())
        self.assertTrue((out / "job_summary.en.md").is_file())
        self.assertFalse((out / "job_summary.json").exists())

    def test_multi_trial_format_json_generates_json_and_suppresses_md(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        out = tmp / "out"
        proc = run_cli(
            "--job", str(FIXTURE_JOB), "--output", str(out), "--trial", "all", "--format", "json"
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue((out / "job_summary.json").is_file())
        self.assertFalse((out / "job_summary.md").exists())
        self.assertFalse((out / "job_summary.zh.md").exists())
        self.assertFalse((out / "job_summary.en.md").exists())

    def test_phase_collect_writes_no_analysis(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        out = tmp / "out"
        proc = run_cli(
            "--job", str(FIXTURE_JOB), "--output", str(out), "--trial", "all", "--phase", "collect"
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue((out / "trial_a_fail" / "evidence.json").is_file())
        self.assertTrue((out / "trial_a_fail" / "candidate-hypotheses.json").is_file())
        self.assertFalse((out / "trial_a_fail" / "analysis.json").exists())
        self.assertFalse((out / "trial_a_fail" / "report.md").exists())


class TestLifecycleAndRerun(unittest.TestCase):
    """Lifecycle tests: empty parameter combinations fail, and reruns purge stale artifacts."""

    def test_collect_phase_with_markdown_format_exits_2(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        out = tmp / "out"
        proc = run_cli(
            "--job",
            str(FIXTURE_JOB),
            "--trial",
            "trial_b_pass",
            "--output",
            str(out),
            "--phase",
            "collect",
            "--format",
            "markdown",
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("does not produce markdown reports", proc.stderr)
        self.assertFalse(out.exists())

    def test_rerun_collect_purges_stale_markdown_and_analysis(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        out = tmp / "out"

        # 1. Initial run with all artifacts
        proc1 = run_cli(
            "--job",
            str(FIXTURE_JOB),
            "--trial",
            "trial_b_pass",
            "--output",
            str(out),
            "--phase",
            "all",
            "--format",
            "both",
        )
        self.assertEqual(proc1.returncode, 0, proc1.stderr)
        for name in JSON_ARTIFACTS + MARKDOWN_ARTIFACTS:
            self.assertTrue((out / name).is_file(), f"{name} must exist after run 1")

        # 2. Subsequent rerun with phase collect & format json into the SAME directory
        proc2 = run_cli(
            "--job",
            str(FIXTURE_JOB),
            "--trial",
            "trial_b_pass",
            "--output",
            str(out),
            "--phase",
            "collect",
            "--format",
            "json",
        )
        self.assertEqual(proc2.returncode, 0, proc2.stderr)

        # Collect artifacts must exist
        self.assertTrue((out / "evidence.json").is_file())
        self.assertTrue((out / "candidate-hypotheses.json").is_file())

        # Old analysis and markdown reports must be purged
        self.assertFalse((out / "analysis.json").exists())
        for name in MARKDOWN_ARTIFACTS:
            self.assertFalse((out / name).exists(), f"stale {name} must be purged on collect rerun")

    def test_rerun_markdown_purges_stale_json(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        out = tmp / "out"

        # 1. Initial run with all artifacts
        proc1 = run_cli(
            "--job",
            str(FIXTURE_JOB),
            "--trial",
            "trial_b_pass",
            "--output",
            str(out),
            "--format",
            "both",
        )
        self.assertEqual(proc1.returncode, 0, proc1.stderr)

        # 2. Subsequent rerun with format markdown only
        proc2 = run_cli(
            "--job",
            str(FIXTURE_JOB),
            "--trial",
            "trial_b_pass",
            "--output",
            str(out),
            "--format",
            "markdown",
        )
        self.assertEqual(proc2.returncode, 0, proc2.stderr)

        for name in MARKDOWN_ARTIFACTS:
            self.assertTrue((out / name).is_file())
        for name in JSON_ARTIFACTS:
            self.assertFalse(
                (out / name).exists(), f"stale {name} must be purged on markdown-only rerun"
            )

    def test_single_trial_purges_stale_job_summary(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        out = tmp / "out"

        # 1. Multi-trial run produces job_summary.json and job_summary.md
        proc1 = run_cli(
            "--job",
            str(FIXTURE_JOB),
            "--output",
            str(out),
            "--trial",
            "all",
        )
        self.assertEqual(proc1.returncode, 0, proc1.stderr)
        self.assertTrue((out / "job_summary.json").is_file())
        self.assertTrue((out / "job_summary.md").is_file())
        self.assertTrue((out / "job_summary.zh.md").is_file())
        self.assertTrue((out / "job_summary.en.md").is_file())

        # 2. Single trial run targeting the same output root purges stale job_summary.*
        proc2 = run_cli(
            "--job",
            str(FIXTURE_JOB),
            "--output",
            str(out),
            "--trial",
            "trial_b_pass",
        )
        self.assertEqual(proc2.returncode, 0, proc2.stderr)
        self.assertFalse((out / "job_summary.json").exists())
        self.assertFalse((out / "job_summary.md").exists())
        self.assertFalse((out / "job_summary.zh.md").exists())
        self.assertFalse((out / "job_summary.en.md").exists())

    def test_switching_multi_to_single_preserves_user_files_and_cleans_managed(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        out = tmp / "out"

        # 1. Multi-trial run
        proc1 = run_cli("--job", str(FIXTURE_JOB), "--output", str(out), "--trial", "all")
        self.assertEqual(proc1.returncode, 0, proc1.stderr)
        self.assertTrue((out / "trial_a_fail" / "report.md").is_file())
        self.assertTrue((out / "job_summary.json").is_file())
        self.assertTrue((out / "job_summary.md").is_file())

        # 2. Add user-authored custom files
        user_root_note = out / "custom_notes.txt"
        user_root_note.write_text("important user notes", encoding="utf-8")
        user_trial_calc = out / "trial_a_fail" / "user_calc.dat"
        user_trial_calc.write_text("some custom calculation", encoding="utf-8")

        # 3. Rerun single trial in same out dir
        proc2 = run_cli("--job", str(FIXTURE_JOB), "--output", str(out), "--trial", "trial_b_pass")
        self.assertEqual(proc2.returncode, 0, proc2.stderr)

        # Single-trial outputs exist at root
        self.assertTrue((out / "evidence.json").is_file())
        self.assertTrue((out / "analysis.json").is_file())
        self.assertFalse((out / "job_summary.json").exists())
        self.assertFalse((out / "job_summary.md").exists())

        # User files are untouched
        self.assertTrue(user_root_note.is_file())
        self.assertEqual(user_root_note.read_text(encoding="utf-8"), "important user notes")
        self.assertTrue(user_trial_calc.is_file())
        self.assertEqual(user_trial_calc.read_text(encoding="utf-8"), "some custom calculation")

        # Managed trial artifacts inside trial_a_fail were cleaned
        self.assertFalse((out / "trial_a_fail" / "report.md").exists())

    def test_switching_single_to_multi_preserves_user_files_and_cleans_managed(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        out = tmp / "out"

        # 1. Single trial run
        proc1 = run_cli("--job", str(FIXTURE_JOB), "--output", str(out), "--trial", "trial_b_pass")
        self.assertEqual(proc1.returncode, 0, proc1.stderr)
        self.assertTrue((out / "evidence.json").is_file())
        self.assertTrue((out / "report.md").is_file())

        # 2. Add user file in root
        user_root_note = out / "custom_notes.txt"
        user_root_note.write_text("keep this", encoding="utf-8")

        # 3. Rerun multi-trial in same out dir
        proc2 = run_cli("--job", str(FIXTURE_JOB), "--output", str(out), "--trial", "all")
        self.assertEqual(proc2.returncode, 0, proc2.stderr)

        # Root managed trial artifacts must be purged
        self.assertFalse((out / "report.md").exists())
        self.assertFalse((out / "evidence.json").exists())
        self.assertTrue((out / "job_summary.json").is_file())
        self.assertTrue((out / "job_summary.md").is_file())

        # Multi-trial subdirs exist
        self.assertTrue((out / "trial_a_fail" / "report.md").is_file())
        self.assertTrue((out / "trial_b_pass" / "report.md").is_file())

        # User file is preserved
        self.assertTrue(user_root_note.is_file())
        self.assertEqual(user_root_note.read_text(encoding="utf-8"), "keep this")

    def test_atomic_publish_rollback_leaves_output_untouched_on_failure(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        out = tmp / "out"
        out.mkdir()
        user_file = out / "existing_work.txt"
        user_file.write_text("do not touch", encoding="utf-8")

        # Point CLI to a non-existent trial to fail early
        proc = run_cli(
            "--job", str(FIXTURE_JOB), "--output", str(out), "--trial", "non_existent_trial"
        )
        self.assertEqual(proc.returncode, 2)

        # out directory contains only the original user file, nothing was written or deleted
        children = list(out.iterdir())
        self.assertEqual(children, [user_file])
        self.assertEqual(user_file.read_text(encoding="utf-8"), "do not touch")

    def test_user_nested_subdirectory_and_report_preserved(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        out = tmp / "out"
        user_sub = out / "user_dir"
        user_sub.mkdir(parents=True)
        user_report = user_sub / "report.md"
        user_report.write_text("# My User Report", encoding="utf-8")

        proc = run_cli("--job", str(FIXTURE_JOB), "--output", str(out), "--trial", "trial_b_pass")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(user_report.is_file(), "User report in user_dir must not be deleted")
        self.assertEqual(user_report.read_text(encoding="utf-8"), "# My User Report")

    def test_transactional_rollback_restores_pre_publish_state_on_copy_error(self) -> None:
        import shutil
        from unittest.mock import patch

        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        from analyze_case import _publish_staging_to_output

        tmp = Path(tempfile.mkdtemp())
        out = tmp / "out"
        out.mkdir()
        initial_file = out / "report.md"
        initial_file.write_text("INITIAL_REPORT_V1", encoding="utf-8")
        manifest_file = out / ".cfa_manifest.json"
        manifest_file.write_text(
            json.dumps(
                {
                    "schema_version": "cfa-manifest-v1",
                    "layout": "single",
                    "managed_files": ["report.md", ".cfa_manifest.json"],
                    "managed_dirs": [],
                }
            ),
            encoding="utf-8",
        )

        staging = Path(tempfile.mkdtemp())
        (staging / "report.md").write_text("NEW_REPORT_V2", encoding="utf-8")
        (staging / "analysis.json").write_text('{"new": true}', encoding="utf-8")

        original_copy2 = shutil.copy2
        call_count = 0

        def fail_on_second_copy(src, dst):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise OSError("Simulated disk error during publish")
            return original_copy2(src, dst)

        with patch("shutil.copy2", side_effect=fail_on_second_copy):
            with self.assertRaises(OSError):
                _publish_staging_to_output(staging, out, is_multi_trial=False)

        # Rollback check: out must be restored to its exact pre-publish state
        self.assertEqual(initial_file.read_text(encoding="utf-8"), "INITIAL_REPORT_V1")
        self.assertFalse((out / "analysis.json").exists())

    def test_manifest_path_traversal_does_not_delete_outside_files(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        out = tmp / "out"
        out.mkdir()
        victim_sibling = tmp / "victim.txt"
        victim_sibling.write_text("precious data", encoding="utf-8")

        # Malicious manifest trying path traversal outside out_dir
        malicious_manifest = out / ".cfa_manifest.json"
        malicious_manifest.write_text(
            json.dumps(
                {
                    "schema_version": "cfa-manifest-v1",
                    "layout": "single",
                    "managed_files": [
                        "../victim.txt",
                        "../../victim.txt",
                        "/tmp/victim.txt",
                        "report.md",
                    ],
                    "managed_dirs": ["../victim_dir", "/tmp/victim_dir"],
                }
            ),
            encoding="utf-8",
        )

        proc = run_cli(
            "--job",
            str(FIXTURE_JOB),
            "--output",
            str(out),
            "--trial",
            "trial_b_pass",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)

        # Outside file must remain untouched despite malicious manifest entries
        self.assertTrue(victim_sibling.is_file())
        self.assertEqual(victim_sibling.read_text(encoding="utf-8"), "precious data")

    def test_no_manifest_does_not_delete_existing_user_files(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        out = tmp / "out"
        out.mkdir()
        user_report = out / "report.md"
        user_report.write_text("# User Pre-existing Report\nDo not delete!", encoding="utf-8")

        # Run CLI with format json on a fresh dir without .cfa_manifest.json
        proc = run_cli(
            "--job",
            str(FIXTURE_JOB),
            "--output",
            str(out),
            "--trial",
            "trial_b_pass",
            "--format",
            "json",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # Pre-existing user report must NOT be deleted
        self.assertTrue(user_report.is_file())
        self.assertEqual(
            user_report.read_text(encoding="utf-8"), "# User Pre-existing Report\nDo not delete!"
        )
        self.assertTrue((out / "evidence.json").is_file())

    def test_atomic_replacement_via_temp_files(self) -> None:
        from unittest.mock import patch

        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        from analyze_case import _publish_staging_to_output

        tmp = Path(tempfile.mkdtemp())
        out = tmp / "out"
        out.mkdir()
        staging = Path(tempfile.mkdtemp())
        (staging / "report.md").write_text("STAGED CONTENT", encoding="utf-8")

        replaced_destinations = []
        original_replace = os.replace

        def track_replace(src, dst):
            replaced_destinations.append((Path(src).name, Path(dst).name))
            return original_replace(src, dst)

        with patch("os.replace", side_effect=track_replace):
            _publish_staging_to_output(staging, out, is_multi_trial=False)

        self.assertTrue(any(dst == "report.md" for src, dst in replaced_destinations))
        self.assertTrue(
            any(src.startswith(".report.md.cfa_tmp_") for src, dst in replaced_destinations)
        )
        self.assertEqual((out / "report.md").read_text(encoding="utf-8"), "STAGED CONTENT")

    def test_rollback_catches_secondary_io_errors_and_reraises_primary(self) -> None:
        from unittest.mock import patch

        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        from analyze_case import _publish_staging_to_output

        tmp = Path(tempfile.mkdtemp())
        out = tmp / "out"
        out.mkdir()
        (out / "existing.txt").write_text("PRE-EXISTING", encoding="utf-8")
        manifest = out / ".cfa_manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "schema_version": "cfa-manifest-v1",
                    "layout": "single",
                    "managed_files": ["existing.txt", ".cfa_manifest.json"],
                    "managed_dirs": [],
                }
            ),
            encoding="utf-8",
        )

        staging = Path(tempfile.mkdtemp())
        (staging / "existing.txt").write_text("NEW_CONTENT", encoding="utf-8")

        def fail_replace(src, dst):
            raise OSError("Primary replacement failed")

        with patch("os.replace", side_effect=fail_replace):
            with patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
                with self.assertRaises(OSError) as cm:
                    _publish_staging_to_output(staging, out, is_multi_trial=False)
                self.assertIn("Primary replacement failed", str(cm.exception))
                self.assertIn(
                    "WARNING: Rollback encountered secondary I/O errors", mock_stderr.getvalue()
                )

    def test_orphaned_cfa_tmp_files_cleanup_removes_dead_pid_files(self) -> None:
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        from analyze_case import _cleanup_orphaned_cfa_tmp_files

        tmp = Path(tempfile.mkdtemp())
        out = tmp / "out"
        out.mkdir()

        # Dead PID file (999999 is dead)
        dead_orphan = out / ".report.md.cfa_tmp_999999_deadbeef"
        dead_orphan.write_text("abandoned data", encoding="utf-8")

        # Current PID file (alive)
        live_tmp = out / f".report.md.cfa_tmp_{os.getpid()}_livefeed"
        live_tmp.write_text("active data", encoding="utf-8")

        _cleanup_orphaned_cfa_tmp_files(out)

        # Dead orphan was unlinked
        self.assertFalse(dead_orphan.exists())
        # Live file for active process remains
        self.assertTrue(live_tmp.exists())

    def test_acquire_publish_lock_keeps_out_dir_clean(self) -> None:
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        from analyze_case import _acquire_publish_lock, _get_publish_lock_path

        tmp = Path(tempfile.mkdtemp())
        out = tmp / "out"
        out.mkdir()

        lock_path = _get_publish_lock_path(out)

        with _acquire_publish_lock(out):
            # Lock file is located in temp dir, NOT polluting out_dir
            self.assertTrue(lock_path.is_file())
            self.assertEqual(list(out.iterdir()), [])

        # out_dir remains completely clean
        self.assertEqual(list(out.iterdir()), [])

    def test_acquire_publish_lock_tier2_directory_fallback(self) -> None:
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        from analyze_case import _acquire_publish_lock, _get_publish_lock_path
        from unittest.mock import patch

        tmp = Path(tempfile.mkdtemp())
        out = tmp / "out"
        out.mkdir()

        lock_path = _get_publish_lock_path(out)
        lock_dir = lock_path.with_suffix(".lockdir")

        with patch("fcntl.flock", side_effect=OSError("NFS lock unsupported")):
            with _acquire_publish_lock(out):
                # Fallback to atomic lockdir engaged!
                self.assertTrue(lock_dir.is_dir())
                self.assertTrue((lock_dir / "pid").is_file())
                self.assertEqual(
                    (lock_dir / "pid").read_text(encoding="utf-8").strip(), str(os.getpid())
                )
                self.assertEqual(list(out.iterdir()), [])

        # Lockdir must be cleanly released
        self.assertFalse(lock_dir.exists())
        self.assertEqual(list(out.iterdir()), [])

    def test_job_as_file_fails_with_exit_code_2(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        fake_job_file = tmp / "job.txt"
        fake_job_file.write_text("not a directory", encoding="utf-8")
        out = tmp / "out"

        proc = run_cli("--job", str(fake_job_file), "--output", str(out), "--trial", "trial_1")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("not a directory", proc.stderr)

    def test_task_as_file_fails_with_exit_code_2(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        fake_task_file = tmp / "task.txt"
        fake_task_file.write_text("not a directory", encoding="utf-8")
        out = tmp / "out"

        proc = run_cli(
            "--job",
            str(FIXTURE_JOB),
            "--task",
            str(fake_task_file),
            "--output",
            str(out),
            "--trial",
            "trial_b_pass",
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("not a directory", proc.stderr)

    def test_output_as_file_fails_with_exit_code_2(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        out_file = tmp / "out_file.txt"
        out_file.write_text("not a directory", encoding="utf-8")

        proc = run_cli(
            "--job",
            str(FIXTURE_JOB),
            "--output",
            str(out_file),
            "--trial",
            "trial_b_pass",
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("not a directory", proc.stderr)

    def test_output_non_writable_fails_with_exit_code_2(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        read_only_dir = tmp / "readonly_root"
        read_only_dir.mkdir()
        os.chmod(read_only_dir, 0o555)
        out = read_only_dir / "target_out"

        try:
            proc = run_cli(
                "--job",
                str(FIXTURE_JOB),
                "--output",
                str(out),
                "--trial",
                "trial_b_pass",
            )
            self.assertEqual(proc.returncode, 2)
            self.assertIn("not writable", proc.stderr)
        finally:
            os.chmod(read_only_dir, 0o755)

    def test_output_symlink_to_directory_succeeds(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        target_dir = tmp / "actual_output_dir"
        symlink_out = tmp / "symlink_output"
        symlink_out.symlink_to(target_dir)

        proc = run_cli(
            "--job",
            str(FIXTURE_JOB),
            "--output",
            str(symlink_out),
            "--trial",
            "trial_b_pass",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue((target_dir / "report.md").is_file())
        self.assertTrue((target_dir / "evidence.json").is_file())

    def test_output_symlink_to_file_fails_with_exit_code_2(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        target_file = tmp / "a_file.txt"
        target_file.write_text("i am a file", encoding="utf-8")
        symlink_out = tmp / "symlink_output"
        symlink_out.symlink_to(target_file)

        proc = run_cli(
            "--job",
            str(FIXTURE_JOB),
            "--output",
            str(symlink_out),
            "--trial",
            "trial_b_pass",
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("not a directory", proc.stderr)

    def test_is_safe_manifest_relpath_rejects_identity_paths(self) -> None:
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        from analyze_case import _is_safe_manifest_relpath

        tmp = Path(tempfile.mkdtemp())
        out = tmp / "out"
        out.mkdir()

        self.assertFalse(_is_safe_manifest_relpath(".", out))
        self.assertFalse(_is_safe_manifest_relpath("./", out))
        self.assertFalse(_is_safe_manifest_relpath("..", out))
        self.assertFalse(_is_safe_manifest_relpath("", out))
        self.assertTrue(_is_safe_manifest_relpath("report.md", out))

    def test_rollback_restores_preexisting_file_without_manifest(self) -> None:
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        from analyze_case import _publish_staging_to_output
        from unittest.mock import patch

        tmp = Path(tempfile.mkdtemp())
        out = tmp / "out"
        out.mkdir()

        # Pre-existing file without manifest
        user_file = out / "report.md"
        user_file.write_text("ORIGINAL_USER_CONTENT", encoding="utf-8")

        staging = Path(tempfile.mkdtemp())
        (staging / "report.md").write_text("NEW_CONTENT", encoding="utf-8")
        (staging / "second_file.txt").write_text("SECOND_NEW", encoding="utf-8")

        real_replace = os.replace

        def failing_replace(src, dst):
            if "second_file" in str(dst):
                raise OSError("Disk full during publish")
            return real_replace(src, dst)

        with patch("os.replace", side_effect=failing_replace):
            with self.assertRaises(OSError):
                _publish_staging_to_output(staging, out, is_multi_trial=False)

        # Rollback must restore original content!
        self.assertEqual(user_file.read_text(encoding="utf-8"), "ORIGINAL_USER_CONTENT")
        # Newly created files must not be present
        self.assertFalse((out / "second_file.txt").exists())

    def test_causally_bound_numerical_failure_namespaced_assertion(self) -> None:
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        from audit_contract import is_causally_bound_numerical_failure

        log = (
            "FAILED tests/test_md.py::test_run\n"
            "E   numpy.testing._private.utils.AssertionError:\n"
            "E   trajectory_rmsd = 0.45 > 0.05 tolerance\n"
        )
        self.assertTrue(is_causally_bound_numerical_failure(log))


class TestLanguageCliOption(unittest.TestCase):
    def test_lang_zh_generates_pure_chinese_report(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        out = tmp / "out"
        proc = run_cli(
            "--job",
            str(FIXTURE_JOB),
            "--trial",
            "trial_b_pass",
            "--output",
            str(out),
            "--lang",
            "zh",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue((out / "report.md").is_file())
        self.assertTrue((out / "report.zh.md").is_file())
        self.assertFalse((out / "report.en.md").exists())

        rep_text = (out / "report.md").read_text(encoding="utf-8")
        self.assertIn(f"## {section_title(1, 'zh', passed=True)}", rep_text)
        self.assertIn(f"## {section_title(2, 'zh', passed=True)}", rep_text)
        self.assertNotIn("Verdict & Pass Summary", rep_text)

        # Also test on a failed trial
        out_fail = tmp / "out_fail"
        proc_fail = run_cli(
            "--job",
            str(FIXTURE_JOB),
            "--trial",
            "trial_a_fail",
            "--output",
            str(out_fail),
            "--lang",
            "zh",
        )
        self.assertEqual(proc_fail.returncode, 0, proc_fail.stderr)
        fail_rep = (out_fail / "report.md").read_text(encoding="utf-8")
        self.assertIn(f"## {section_title(1, 'zh')}", fail_rep)
        self.assertNotIn("Root Cause Verdict", fail_rep)

    def test_lang_en_generates_pure_english_report(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        out = tmp / "out"
        proc = run_cli(
            "--job",
            str(FIXTURE_JOB),
            "--trial",
            "trial_b_pass",
            "--output",
            str(out),
            "--lang",
            "en",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue((out / "report.md").is_file())
        self.assertTrue((out / "report.en.md").is_file())
        self.assertFalse((out / "report.zh.md").exists())

        rep_text = (out / "report.md").read_text(encoding="utf-8")
        self.assertIn(f"## {section_title(1, 'en', passed=True)}", rep_text)
        self.assertIn(f"## {section_title(2, 'en', passed=True)}", rep_text)
        self.assertNotIn("通过结论", rep_text)

        # Also test on a failed trial
        out_fail = tmp / "out_fail"
        proc_fail = run_cli(
            "--job",
            str(FIXTURE_JOB),
            "--trial",
            "trial_a_fail",
            "--output",
            str(out_fail),
            "--lang",
            "en",
        )
        self.assertEqual(proc_fail.returncode, 0, proc_fail.stderr)
        fail_rep = (out_fail / "report.md").read_text(encoding="utf-8")
        self.assertIn(f"## {section_title(1, 'en')}", fail_rep)
        self.assertNotIn("归因结论", fail_rep)

    def test_lang_bilingual_default_and_explicit(self) -> None:
        # Default without --lang flag
        tmp1 = Path(tempfile.mkdtemp())
        out1 = tmp1 / "out"
        proc1 = run_cli("--job", str(FIXTURE_JOB), "--trial", "trial_b_pass", "--output", str(out1))
        self.assertEqual(proc1.returncode, 0, proc1.stderr)
        self.assertTrue((out1 / "report.md").is_file())
        self.assertTrue((out1 / "report.zh.md").is_file())
        self.assertTrue((out1 / "report.en.md").is_file())

        rep1_text = (out1 / "report.md").read_text(encoding="utf-8")
        self.assertIn(f"## {section_title(1, 'bilingual', passed=True)}", rep1_text)

        # Explicit --lang bilingual on multi-trial batch
        tmp2 = Path(tempfile.mkdtemp())
        out2 = tmp2 / "out"
        proc2 = run_cli(
            "--job",
            str(FIXTURE_JOB),
            "--trial",
            "all",
            "--output",
            str(out2),
            "--lang",
            "bilingual",
        )
        self.assertEqual(proc2.returncode, 0, proc2.stderr)
        self.assertTrue((out2 / "job_summary.md").is_file())
        self.assertTrue((out2 / "job_summary.zh.md").is_file())
        self.assertTrue((out2 / "job_summary.en.md").is_file())

        job_md = (out2 / "job_summary.md").read_text(encoding="utf-8")
        self.assertIn("Multi-Trial Batch Execution Summary / 多 Trial 批处理执行汇总", job_md)

    def test_lang_multi_trial_batch_zh_and_en(self) -> None:
        # Batch with --lang zh
        tmp_zh = Path(tempfile.mkdtemp())
        out_zh = tmp_zh / "out"
        proc_zh = run_cli(
            "--job", str(FIXTURE_JOB), "--trial", "all", "--output", str(out_zh), "--lang", "zh"
        )
        self.assertEqual(proc_zh.returncode, 0, proc_zh.stderr)
        self.assertTrue((out_zh / "job_summary.md").is_file())
        self.assertTrue((out_zh / "job_summary.zh.md").is_file())
        self.assertFalse((out_zh / "job_summary.en.md").exists())
        self.assertIn(
            "多 Trial 评测任务汇总报告", (out_zh / "job_summary.md").read_text(encoding="utf-8")
        )
        self.assertTrue((out_zh / "trial_a_fail" / "report.zh.md").is_file())
        self.assertFalse((out_zh / "trial_a_fail" / "report.en.md").exists())

        # Batch with --lang en
        tmp_en = Path(tempfile.mkdtemp())
        out_en = tmp_en / "out"
        proc_en = run_cli(
            "--job", str(FIXTURE_JOB), "--trial", "all", "--output", str(out_en), "--lang", "en"
        )
        self.assertEqual(proc_en.returncode, 0, proc_en.stderr)
        self.assertTrue((out_en / "job_summary.md").is_file())
        self.assertTrue((out_en / "job_summary.en.md").is_file())
        self.assertFalse((out_en / "job_summary.zh.md").exists())
        self.assertIn(
            "Multi-Trial Batch Execution Summary",
            (out_en / "job_summary.md").read_text(encoding="utf-8"),
        )
        self.assertTrue((out_en / "trial_a_fail" / "report.en.md").is_file())
        self.assertFalse((out_en / "trial_a_fail" / "report.zh.md").exists())

    def test_lang_rerun_purges_stale_language_editions(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        out = tmp / "out"

        # 1. Run default bilingual: produces report.md, report.zh.md, report.en.md
        proc1 = run_cli("--job", str(FIXTURE_JOB), "--trial", "trial_b_pass", "--output", str(out))
        self.assertEqual(proc1.returncode, 0, proc1.stderr)
        self.assertTrue((out / "report.zh.md").is_file())
        self.assertTrue((out / "report.en.md").is_file())

        # 2. Rerun with --lang zh: report.en.md must be cleanly purged
        proc2 = run_cli(
            "--job",
            str(FIXTURE_JOB),
            "--trial",
            "trial_b_pass",
            "--output",
            str(out),
            "--lang",
            "zh",
        )
        self.assertEqual(proc2.returncode, 0, proc2.stderr)
        self.assertTrue((out / "report.zh.md").is_file())
        self.assertFalse((out / "report.en.md").exists())

        # 3. Rerun with --lang en: report.zh.md must be cleanly purged
        proc3 = run_cli(
            "--job",
            str(FIXTURE_JOB),
            "--trial",
            "trial_b_pass",
            "--output",
            str(out),
            "--lang",
            "en",
        )
        self.assertEqual(proc3.returncode, 0, proc3.stderr)
        self.assertTrue((out / "report.en.md").is_file())
        self.assertFalse((out / "report.zh.md").exists())


class TestStagingModuleDecoupling(unittest.TestCase):
    def test_direct_staging_import_and_reexports(self) -> None:
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        import staging
        from analyze_case import (
            MANIFEST_FILENAME,
            acquire_advisory_process_lock,
            clean_tier2_lock_dir,
            cleanup_orphaned_cfa_tmp_files,
            is_pid_alive,
            is_safe_manifest_relpath,
            paths_overlap,
            publish_staging_to_output,
            publish_staging_to_output_locked,
            release_advisory_process_lock,
            validate_input_output_isolation,
            validate_output_writable,
        )

        self.assertEqual(MANIFEST_FILENAME, staging.MANIFEST_FILENAME)
        self.assertIs(publish_staging_to_output, staging.publish_staging_to_output)
        self.assertIs(publish_staging_to_output_locked, staging.publish_staging_to_output_locked)
        self.assertIs(validate_input_output_isolation, staging.validate_input_output_isolation)
        self.assertIs(validate_output_writable, staging.validate_output_writable)
        self.assertIs(acquire_advisory_process_lock, staging.acquire_advisory_process_lock)
        self.assertIs(release_advisory_process_lock, staging.release_advisory_process_lock)
        self.assertIs(clean_tier2_lock_dir, staging.clean_tier2_lock_dir)
        self.assertIs(paths_overlap, staging.paths_overlap)
        self.assertIs(is_safe_manifest_relpath, staging.is_safe_manifest_relpath)
        self.assertIs(is_pid_alive, staging.is_pid_alive)
        self.assertIs(cleanup_orphaned_cfa_tmp_files, staging.cleanup_orphaned_cfa_tmp_files)


if __name__ == "__main__":
    unittest.main()
