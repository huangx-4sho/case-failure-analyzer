#!/usr/bin/env python3
"""Unit tests for Phase 3 (`scripts/extract_runtime_errors.py`)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from extract_runtime_errors import extract_runtime_errors, read_text_head_tail


class TestRuntimeExtraction(unittest.TestCase):
    def test_docker_apt_network_failure_is_causal_when_agent_not_started(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp) / "job"
            trial_dir = job_dir / "trial_1"
            trial_dir.mkdir(parents=True)
            (trial_dir / "result.json").write_text(
                json.dumps(
                    {
                        "agent_result": None,
                        "verifier_result": None,
                        "exception_info": {
                            "exception_type": "RuntimeError",
                            "exception_message": "Docker compose command failed for environment. apt-get Failed to fetch http://archive.ubuntu.com",
                        },
                    }
                ),
                encoding="utf-8",
            )
            res = extract_runtime_errors(job_dir=job_dir, trial_dir=trial_dir)
            self.assertFalse(res["agent_started"])
            codes = {e["code"] for e in res["error_observations"] if e["causal_candidate"]}
            self.assertIn("INFRA_EXTERNAL_NETWORK", codes)

    def test_transient_network_warning_when_agent_and_verifier_completed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp) / "job"
            trial_dir = job_dir / "trial_1"
            (trial_dir / "agent").mkdir(parents=True)
            (trial_dir / "verifier").mkdir(parents=True)
            (trial_dir / "agent" / "trajectory.json").write_text("{}", encoding="utf-8")
            (trial_dir / "verifier" / "verify.log").write_text("PASS", encoding="utf-8")
            (trial_dir / "result.json").write_text(
                json.dumps(
                    {
                        "agent_result": {"cost_usd": 0.1},
                        "verifier_result": {"rewards": {"reward": 1.0}},
                        "exception_info": None,
                    }
                ),
                encoding="utf-8",
            )
            (trial_dir / "trial.log").write_text(
                "WARNING: Temporary failure resolving mirror, retrying and succeeded.",
                encoding="utf-8",
            )
            res = extract_runtime_errors(job_dir=job_dir, trial_dir=trial_dir)
            self.assertTrue(res["agent_started"])
            for err in res["error_observations"]:
                if err["code"] == "INFRA_EXTERNAL_NETWORK":
                    self.assertTrue(err["transient"])
                    self.assertFalse(err["causal_candidate"])


class TestLogBudgetValidation(unittest.TestCase):
    """A non-positive byte budget must never silently read an entire log."""

    def test_head_tail_rejects_non_positive_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "verify.log"
            log.write_text("FAIL: boom\n", encoding="utf-8")
            for bad in (0, -1, -120000):
                with self.subTest(bad=bad):
                    with self.assertRaises(ValueError):
                        read_text_head_tail(log, bad)

    def test_extract_runtime_errors_rejects_non_positive_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                extract_runtime_errors(job_dir=Path(tmp), max_log_bytes=0)

    def test_positive_budget_still_truncates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "verify.log"
            log.write_text("A" * 400 + "MIDDLE" + "B" * 400, encoding="utf-8")
            text = read_text_head_tail(log, 100)
            self.assertIn("... [TRUNCATED] ...", text)
            self.assertLess(len(text), 400)


if __name__ == "__main__":
    unittest.main()
