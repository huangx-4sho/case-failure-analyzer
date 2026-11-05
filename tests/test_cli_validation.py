#!/usr/bin/env python3
"""
CLI input-validation & skill-metadata regression tests (`tests/test_cli_validation.py`).

Guards two classes of defects fixed after review:
  1. `analyze_case.py` must fail fast (exit 2) on bad inputs and never emit a
     confident-looking report for a path/trial that does not exist.
  2. Skill metadata must stay discoverable: hyphen-case `SKILL.md` name and a
     standard `agents/openai.yaml` `interface:` block.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ENTRYPOINT = REPO_ROOT / "scripts" / "analyze_case.py"
RUNTIME_ENTRYPOINT = REPO_ROOT / "scripts" / "extract_runtime_errors.py"

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None


def run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(ENTRYPOINT), *args],
        capture_output=True,
        text=True,
    )


def run_runtime_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(RUNTIME_ENTRYPOINT), *args],
        capture_output=True,
        text=True,
    )


class TestCliInputValidation(unittest.TestCase):
    def test_missing_job_path_exits_2_without_writing_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            res = run_cli("--job", str(Path(tmp) / "does_not_exist"), "--output", str(out))
            self.assertEqual(res.returncode, 2, res.stderr)
            self.assertIn("does not exist", res.stderr)
            self.assertFalse(out.exists(), "no output must be written for a bad --job path")

    def test_empty_job_tree_exits_2_without_writing_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job = Path(tmp) / "empty_job"
            job.mkdir()
            out = Path(tmp) / "out"
            res = run_cli("--job", str(job), "--output", str(out))
            self.assertEqual(res.returncode, 2, res.stderr)
            self.assertFalse(out.exists())

    def test_unknown_trial_exits_2_without_writing_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job = Path(tmp) / "jobs" / "j"
            trial = job / "trial_a"
            trial.mkdir(parents=True)
            (trial / "result.json").write_text(
                json.dumps({"trial_name": "trial_a"}), encoding="utf-8"
            )
            out = Path(tmp) / "out"
            res = run_cli("--job", str(job), "--trial", "nope", "--output", str(out))
            self.assertEqual(res.returncode, 2, res.stderr)
            self.assertIn("not found", res.stderr)
            self.assertFalse(out.exists())

    def test_missing_task_path_exits_2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job = Path(tmp) / "jobs" / "j"
            trial = job / "trial_a"
            trial.mkdir(parents=True)
            (trial / "result.json").write_text(
                json.dumps({"trial_name": "trial_a"}), encoding="utf-8"
            )
            out = Path(tmp) / "out"
            res = run_cli(
                "--job", str(job), "--task", str(Path(tmp) / "no_task"), "--output", str(out)
            )
            self.assertEqual(res.returncode, 2, res.stderr)
            self.assertFalse(out.exists())

    def test_undocumented_legacy_draft_phase_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job = Path(tmp) / "jobs" / "j"
            trial = job / "trial_a"
            trial.mkdir(parents=True)
            (trial / "result.json").write_text(
                json.dumps({"trial_name": "trial_a"}), encoding="utf-8"
            )
            res = run_cli(
                "--job", str(job), "--output", str(Path(tmp) / "out"), "--phase", "legacy-draft"
            )
            self.assertEqual(res.returncode, 2, res.stderr)
            self.assertIn("invalid choice", res.stderr)

    def test_non_positive_max_log_bytes_exits_2_without_writing_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job = Path(tmp) / "job"
            job.mkdir()
            (job / "result.json").write_text(json.dumps({"trial_name": "t1"}), encoding="utf-8")
            for bad in ("0", "-1", "-120000"):
                with self.subTest(max_log_bytes=bad):
                    out = Path(tmp) / f"out_{bad.lstrip('-')}"
                    res = run_cli(
                        "--job",
                        str(job),
                        "--output",
                        str(out),
                        "--max-log-bytes",
                        bad,
                    )
                    self.assertEqual(res.returncode, 2, res.stderr)
                    self.assertIn("positive integer", res.stderr)
                    self.assertFalse(out.exists(), "no output may be written for a bad budget")

    def test_non_integer_max_log_bytes_exits_2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job = Path(tmp) / "job"
            job.mkdir()
            (job / "result.json").write_text(json.dumps({"trial_name": "t1"}), encoding="utf-8")
            res = run_cli(
                "--job", str(job), "--output", str(Path(tmp) / "out"), "--max-log-bytes", "1e6"
            )
            self.assertEqual(res.returncode, 2, res.stderr)
            self.assertIn("positive integer", res.stderr)

    def test_runtime_cli_rejects_non_positive_max_log_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job = Path(tmp) / "job"
            job.mkdir()
            for bad in ("0", "-5"):
                with self.subTest(max_log_bytes=bad):
                    out = Path(tmp) / f"runtime_{bad.lstrip('-')}.json"
                    res = run_runtime_cli(
                        "--job",
                        str(job),
                        "--output",
                        str(out),
                        "--max-log-bytes",
                        bad,
                    )
                    self.assertEqual(res.returncode, 2, res.stderr)
                    self.assertIn("positive integer", res.stderr)
                    self.assertFalse(out.exists())

    def test_runtime_cli_rejects_missing_or_non_directory_job(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job_file = Path(tmp) / "job.json"
            job_file.write_text("{}", encoding="utf-8")
            for index, bad_job in enumerate((Path(tmp) / "does_not_exist", job_file)):
                with self.subTest(job=str(bad_job)):
                    out = Path(tmp) / f"runtime_{index}.json"
                    res = run_runtime_cli("--job", str(bad_job), "--output", str(out))
                    self.assertEqual(res.returncode, 2, res.stderr)
                    self.assertIn("--job", res.stderr)
                    self.assertFalse(out.exists())

    def test_runtime_cli_rejects_missing_or_non_directory_trial_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job = Path(tmp) / "job"
            job.mkdir()
            trial_file = Path(tmp) / "trial.json"
            trial_file.write_text("{}", encoding="utf-8")
            for index, bad_trial in enumerate((Path(tmp) / "does_not_exist", trial_file)):
                with self.subTest(trial_dir=str(bad_trial)):
                    out = Path(tmp) / f"runtime_trial_{index}.json"
                    res = run_runtime_cli(
                        "--job",
                        str(job),
                        "--trial-dir",
                        str(bad_trial),
                        "--output",
                        str(out),
                    )
                    self.assertEqual(res.returncode, 2, res.stderr)
                    self.assertIn("--trial-dir", res.stderr)
                    self.assertFalse(out.exists())

    def test_legitimate_job_level_prestartup_failure_still_succeeds(self) -> None:
        """A job dir with result.json but no trial dirs is a valid job-level failure."""
        with tempfile.TemporaryDirectory() as tmp:
            job = Path(tmp) / "jobs" / "job_level_fail"
            job.mkdir(parents=True)
            (job / "result.json").write_text(
                json.dumps({"n_total_trials": 0, "stats": {"n_errored_trials": 1}}),
                encoding="utf-8",
            )
            out = Path(tmp) / "out"
            res = run_cli("--job", str(job), "--output", str(out))
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertTrue((out / "analysis.json").is_file())

    def test_output_same_as_job_exits_2_without_writing_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job = Path(tmp) / "job"
            job.mkdir()
            (job / "result.json").write_text(json.dumps({"trial_name": "t1"}), encoding="utf-8")
            res = run_cli("--job", str(job), "--output", str(job))
            self.assertEqual(res.returncode, 2, res.stderr)
            self.assertIn("cannot be identical to, inside, or a parent of --job", res.stderr)

    def test_output_inside_job_exits_2_without_writing_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job = Path(tmp) / "job"
            job.mkdir()
            (job / "result.json").write_text(json.dumps({"trial_name": "t1"}), encoding="utf-8")
            out = job / "nested_output"
            res = run_cli("--job", str(job), "--output", str(out))
            self.assertEqual(res.returncode, 2, res.stderr)
            self.assertIn("cannot be identical to, inside, or a parent of --job", res.stderr)
            self.assertFalse(out.exists())

    def test_output_parent_of_job_exits_2_without_writing_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp) / "parent"
            job = parent / "job"
            job.mkdir(parents=True)
            (job / "result.json").write_text(json.dumps({"trial_name": "t1"}), encoding="utf-8")
            res = run_cli("--job", str(job), "--output", str(parent))
            self.assertEqual(res.returncode, 2, res.stderr)
            self.assertIn("cannot be identical to, inside, or a parent of --job", res.stderr)

    def test_output_inside_task_exits_2_without_writing_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job = Path(tmp) / "job"
            job.mkdir()
            (job / "result.json").write_text(json.dumps({"trial_name": "t1"}), encoding="utf-8")
            task = Path(tmp) / "task"
            task.mkdir()
            out = task / "nested_out"
            res = run_cli("--job", str(job), "--task", str(task), "--output", str(out))
            self.assertEqual(res.returncode, 2, res.stderr)
            self.assertIn("cannot be identical to, inside, or a parent of --task", res.stderr)
            self.assertFalse(out.exists())

    def test_output_symlink_inside_job_exits_2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job = Path(tmp) / "job"
            job.mkdir()
            (job / "result.json").write_text(json.dumps({"trial_name": "t1"}), encoding="utf-8")
            symlink_to_job = Path(tmp) / "job_symlink"
            symlink_to_job.symlink_to(job)
            out = symlink_to_job / "out"
            res = run_cli("--job", str(job), "--output", str(out))
            self.assertEqual(res.returncode, 2, res.stderr)
            self.assertIn("cannot be identical to, inside, or a parent of --job", res.stderr)
            self.assertFalse(out.exists())

    def test_output_trial_subdirectory_symlinked_to_input_trial_exits_2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            job = tmp_path / "jobs" / "j1"
            trial1 = job / "trial_1"
            trial2 = job / "trial_2"
            trial1.mkdir(parents=True)
            trial2.mkdir(parents=True)
            (trial1 / "result.json").write_text(
                json.dumps({"trial_name": "trial_1"}), encoding="utf-8"
            )
            (trial2 / "result.json").write_text(
                json.dumps({"trial_name": "trial_2"}), encoding="utf-8"
            )

            out = tmp_path / "out"
            out.mkdir()
            (out / "trial_1").symlink_to(trial1)

            res = run_cli("--job", str(job), "--output", str(out))
            self.assertEqual(res.returncode, 2, res.stderr)
            self.assertIn("cannot be identical to, inside, or a parent of", res.stderr)
            self.assertFalse(
                (trial1 / "evidence.json").exists(), "Input trial must remain untouched"
            )

    def test_autodiscovered_task_overlapping_with_output_exits_2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            job = tmp_path / "jobs" / "j1"
            trial1 = job / "trial_1"
            trial1.mkdir(parents=True)
            task = tmp_path / "tasks" / "my_task"
            task.mkdir(parents=True)
            (task / "instruction.md").write_text("Solve the problem", encoding="utf-8")
            (trial1 / "result.json").write_text(
                json.dumps(
                    {
                        "trial_name": "trial_1",
                        "task_id": {"path": "tasks/my_task"},
                    }
                ),
                encoding="utf-8",
            )

            out = task / "nested_out"
            res = run_cli("--job", str(job), "--output", str(out))
            self.assertEqual(res.returncode, 2, res.stderr)
            self.assertIn("cannot be identical to, inside, or a parent of --task", res.stderr)
            self.assertFalse(out.exists(), "Output must not be written inside auto-discovered task")


class TestSkillMetadata(unittest.TestCase):
    @unittest.skipIf(yaml is None, "PyYAML not available")
    def test_skill_name_is_hyphen_case(self) -> None:
        text = (REPO_ROOT / "SKILL.md").read_text(encoding="utf-8")
        match = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
        self.assertIsNotNone(match, "SKILL.md must start with YAML frontmatter")
        frontmatter = yaml.safe_load(match.group(1))
        name = frontmatter["name"]
        self.assertRegex(name, r"^[a-z0-9]+(-[a-z0-9]+)*$", f"non-compliant skill name: {name}")
        self.assertLessEqual(len(name), 64)

    @unittest.skipIf(yaml is None, "PyYAML not available")
    def test_frontmatter_uses_only_allowed_keys(self) -> None:
        text = (REPO_ROOT / "SKILL.md").read_text(encoding="utf-8")
        frontmatter = yaml.safe_load(re.match(r"^---\n(.*?)\n---", text, re.DOTALL).group(1))
        allowed = {"name", "description", "license", "allowed-tools", "metadata"}
        self.assertLessEqual(set(frontmatter), allowed)

    @unittest.skipIf(yaml is None, "PyYAML not available")
    def test_openai_yaml_uses_standard_interface_block(self) -> None:
        data = yaml.safe_load((REPO_ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8"))
        self.assertIn("interface", data, "agents/openai.yaml must use the standard interface block")
        interface = data["interface"]
        self.assertIn("display_name", interface)
        short = interface["short_description"]
        self.assertTrue(
            25 <= len(short) <= 64, f"short_description length out of range: {len(short)}"
        )


if __name__ == "__main__":
    unittest.main()
