#!/usr/bin/env python3
"""
Evidence-degradation diagnostics (`tests/test_diagnostics.py`)

A failed verifier-check-stat build used to be swallowed silently, leaving the
attribution confidence on a weaker scale with no trace in evidence, reports, or
batch summaries. These tests pin that the recovery is recorded as a warning
diagnostic and aggregated into batch statistics.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_DIR = REPO_ROOT / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import analyze_case
from analyze_case import _build_verifier_check_stats, compute_batch_statistics


class TestCheckStatsFailureDiagnostics(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.trial_dir = Path(self._tmp.name)
        (self.trial_dir / "verifier").mkdir()
        (self.trial_dir / "verifier" / "verify.log").write_text(
            "FAIL: results.json missing key 'n_rows'\n", encoding="utf-8"
        )

    def test_build_failure_emits_warning_diagnostic(self) -> None:
        diagnostics = []
        with patch.object(analyze_case, "build_check_stats", side_effect=RuntimeError("boom")):
            stats = _build_verifier_check_stats(self.trial_dir, 120000, diagnostics)
        self.assertIsNone(stats)
        self.assertEqual(len(diagnostics), 1)
        record = diagnostics[0]
        self.assertEqual(record["severity"], "warning")
        self.assertEqual(record["stage"], "verifier_check_stats")
        self.assertEqual(record["code"], "VERIFIER_CHECK_STATS_BUILD_FAILED")
        self.assertEqual(record["error_type"], "RuntimeError")
        self.assertIn(
            "check_localization and counterfactual confidence inputs are unavailable",
            record["impact"],
        )

    def test_invalid_builder_result_emits_warning_diagnostic(self) -> None:
        diagnostics = []
        with patch.object(analyze_case, "build_check_stats", return_value=["not", "a", "dict"]):
            stats = _build_verifier_check_stats(self.trial_dir, 120000, diagnostics)
        self.assertIsNone(stats)
        self.assertEqual(len(diagnostics), 1)
        self.assertEqual(diagnostics[0]["code"], "VERIFIER_CHECK_STATS_INVALID_RESULT")

    def test_unavailable_builder_emits_warning_diagnostic(self) -> None:
        diagnostics = []
        with patch.object(analyze_case, "build_check_stats", None):
            stats = _build_verifier_check_stats(self.trial_dir, 120000, diagnostics)
        self.assertIsNone(stats)
        self.assertEqual(len(diagnostics), 1)
        self.assertEqual(diagnostics[0]["code"], "VERIFIER_CHECK_STATS_BUILDER_UNAVAILABLE")

    def test_missing_verifier_log_is_silent(self) -> None:
        diagnostics = []
        with tempfile.TemporaryDirectory() as empty_dir:
            stats = _build_verifier_check_stats(Path(empty_dir), 120000, diagnostics)
        self.assertIsNone(stats)
        self.assertEqual(diagnostics, [])

    def test_successful_build_adds_no_diagnostic(self) -> None:
        diagnostics = []
        stats = _build_verifier_check_stats(self.trial_dir, 120000, diagnostics)
        self.assertIsInstance(stats, dict)
        self.assertEqual(diagnostics, [])


class TestBatchDiagnosticAggregation(unittest.TestCase):
    def test_compute_batch_statistics_counts_degraded_trials(self) -> None:
        stats = compute_batch_statistics(
            [
                {
                    "trial_name": "t1",
                    "verdict": "failed",
                    "diagnostic_counts": {"warning": 2, "error": 1},
                },
                {
                    "trial_name": "t2",
                    "verdict": "passed",
                    "diagnostic_counts": {"warning": 0, "error": 0},
                },
            ]
        )
        self.assertEqual(stats["degraded_evidence_trial_count"], 1)
        self.assertEqual(stats["diagnostic_severity_counts"], {"warning": 2, "error": 1})

    def test_compute_batch_statistics_without_diagnostic_fields(self) -> None:
        stats = compute_batch_statistics([{"trial_name": "t1", "verdict": "passed"}])
        self.assertEqual(stats["degraded_evidence_trial_count"], 0)
        self.assertEqual(stats["diagnostic_severity_counts"], {"warning": 0, "error": 0})


if __name__ == "__main__":
    unittest.main()
