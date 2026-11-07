#!/usr/bin/env python3
"""
Tests for unified CFA CLI (`scripts/cfa.py`).
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
SCRIPT_DIR = ROOT_DIR / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import cfa
from cfa import redact_sensitive
from task_ref import REPORT_DIR_NAME, discover_all_task_ids


class TestCfaCli(unittest.TestCase):
    def test_redact_sensitive(self) -> None:
        raw = "User /Users/alice/repo has token sk-12345678901234567890 and secret='supersecret'"
        redacted = redact_sensitive(raw)
        self.assertNotIn("sk-12345678901234567890", redacted)
        self.assertIn("[REDACTED_KEY]", redacted)
        self.assertIn('secret: "[REDACTED]"', redacted)

    def test_cfa_help(self) -> None:
        f = io.StringIO()
        with redirect_stdout(f):
            rc = cfa.main(["--help"])
        self.assertEqual(rc, 0)
        self.assertIn("Case Failure Analyzer", f.getvalue())

    def test_cfa_index_missing_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rc = cfa.main(["index", "--report-root", str(Path(tmp) / "reports")])
            self.assertEqual(rc, 0)
            self.assertTrue((Path(tmp) / "reports" / "INDEX.md").is_file())

    def test_cfa_inspect_sections_and_raw(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bench = Path(tmp)
            # Create a mock task failure-report layout
            rep_dir = bench / REPORT_DIR_NAME / "task208"
            data_dir = rep_dir / "data"
            data_dir.mkdir(parents=True)

            (rep_dir / "summary.md").write_text(
                "# task208 Summary\nVerdict: failed\n", encoding="utf-8"
            )
            (rep_dir / "task208.md").write_text(
                "# task208 Report\n## 1. Verdict\n", encoding="utf-8"
            )

            ev_content = {
                "runtime": {
                    "execution_status": "completed",
                    "verification_status": "failed",
                    "verdict": "failed",
                    "reward": 0.0,
                    "agent_started": True,
                    "verifier_started": True,
                },
                "verifier_report": {
                    "diagnosability": "normal",
                    "failed_checks": [
                        {
                            "layer": "L3",
                            "name": "spin_selection",
                            "failure_kind": "reference_mismatch",
                            "detail": "reported 0.02 != recomputed 0.03",
                            "artifact_comparison": {"reported": 0.02, "expected": 0.03},
                        }
                    ],
                },
                "causal_anchors": {"self_check": {"checked": ["spin_selection"]}},
                "scientific_observations": [
                    {
                        "sci_id": "sci:recompute:1",
                        "software": "cp2k",
                        "error_family": "verifier_recompute_divergence",
                        "matched_text": "spin divergence delta 0.01",
                    }
                ],
                "behavioral_signals": [{"signal_id": "sig:1", "description": "repeated fail"}],
            }
            (data_dir / "evidence.json").write_text(json.dumps(ev_content), encoding="utf-8")

            an_content = {
                "verdict": "failed",
                "primary_root_cause": {
                    "category": "agent",
                    "subtype": "result_validation",
                    "code": "AGENT_RESULT_VALIDATION",
                    "summary": "Agent wrote result without validation",
                    "confidence": 0.75,
                    "evidence_strength": "high",
                    "confidence_breakdown": {
                        "points": 6,
                        "denominator": 8,
                        "factors": [
                            {
                                "key": "direct_causal_evidence",
                                "delta": 2,
                                "label_en": "Direct causal evidence",
                                "label_zh": "直接因果证据",
                            },
                            {
                                "key": "counterfactual_supported",
                                "delta": 2,
                                "label_en": "Counterfactual supported",
                                "label_zh": "反事实闭环",
                            },
                        ],
                        "missing_to_raise": ["Need secondary corroboration"],
                    },
                },
                "competing_hypotheses": [
                    {
                        "hypothesis_id": "H1",
                        "category": "agent",
                        "subtype": "result_validation",
                        "confidence": 0.75,
                        "evidence_strength": "high",
                        "claim": "Primary hypothesis",
                    },
                    {
                        "hypothesis_id": "H2",
                        "category": "verifier",
                        "subtype": "defect",
                        "confidence": 0.15,
                        "evidence_strength": "low",
                        "claim": "Excluded",
                    },
                ],
                "failure_manifestation": {
                    "verifier_diagnosability": "normal",
                    "named_failed_checks": ev_content["verifier_report"]["failed_checks"],
                },
                "evidence_refs": ["ver:fail_log", "sci:recompute:1"],
            }
            (data_dir / "analysis.json").write_text(json.dumps(an_content), encoding="utf-8")

            # 1. Test --section errors (Markdown)
            f = io.StringIO()
            with redirect_stdout(f):
                rc = cfa.main(
                    ["inspect", "task208", "--report-dir", str(rep_dir), "--section", "errors"]
                )
            self.assertEqual(rc, 0)
            out = f.getvalue()
            self.assertIn("# Error & Verifier Summary: `task208`", out)
            self.assertIn("AGENT_RESULT_VALIDATION", out)
            self.assertIn("spin_selection", out)
            self.assertIn("reference_mismatch", out)
            self.assertIn("Artifact Comparison", out)

            # 2. Test --section errors --raw (JSON)
            f = io.StringIO()
            with redirect_stdout(f):
                rc = cfa.main(
                    [
                        "inspect",
                        "task208",
                        "--report-dir",
                        str(rep_dir),
                        "--section",
                        "errors",
                        "--raw",
                    ]
                )
            self.assertEqual(rc, 0)
            data = json.loads(f.getvalue())
            self.assertEqual(data["task_id"], "task208")
            self.assertEqual(data["primary_root_cause"]["code"], "AGENT_RESULT_VALIDATION")
            self.assertEqual(data["failed_checks"][0]["name"], "spin_selection")

            # 3. Test --section confidence (Markdown)
            f = io.StringIO()
            with redirect_stdout(f):
                rc = cfa.main(
                    ["inspect", "task208", "--report-dir", str(rep_dir), "--section", "confidence"]
                )
            self.assertEqual(rc, 0)
            out = f.getvalue()
            self.assertIn("# Confidence & Evidence Breakdown: `task208`", out)
            self.assertIn("0.75", out)
            self.assertIn("6 / 8", out)
            self.assertIn("Direct causal evidence", out)
            self.assertIn("Need secondary corroboration", out)
            self.assertIn("H1", out)

            # 4. Test --section confidence --raw (JSON)
            f = io.StringIO()
            with redirect_stdout(f):
                rc = cfa.main(
                    [
                        "inspect",
                        "task208",
                        "--report-dir",
                        str(rep_dir),
                        "--section",
                        "confidence",
                        "--raw",
                    ]
                )
            self.assertEqual(rc, 0)
            data = json.loads(f.getvalue())
            self.assertEqual(data["confidence"], 0.75)
            self.assertEqual(data["confidence_breakdown"]["points"], 6)

            # 5. Test --section evidence (Markdown digest)
            f = io.StringIO()
            with redirect_stdout(f):
                rc = cfa.main(
                    ["inspect", "task208", "--report-dir", str(rep_dir), "--section", "evidence"]
                )
            self.assertEqual(rc, 0)
            out = f.getvalue()
            self.assertIn("# Evidence Digest: `task208`", out)
            self.assertIn("Failed Verifier Checks", out)
            self.assertIn("Causal Anchors", out)

            # 6. Test --section evidence --raw (raw evidence.json)
            f = io.StringIO()
            with redirect_stdout(f):
                rc = cfa.main(
                    [
                        "inspect",
                        "task208",
                        "--report-dir",
                        str(rep_dir),
                        "--section",
                        "evidence",
                        "--raw",
                    ]
                )
            self.assertEqual(rc, 0)
            data = json.loads(f.getvalue())
            self.assertIn("verifier_report", data)

    def test_cfa_audit_all_and_discover_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bench = Path(tmp)
            # Create a case with two tasks (CompChemWorld layout)
            case = bench / "suite-demo"
            for num in ("194", "98"):
                task_dir = case / f"task {num}"
                task_dir.mkdir(parents=True)
                (task_dir / "task.toml").write_text(
                    'schema_version = "1.4"\nartifacts = ["/app/out"]\n', encoding="utf-8"
                )
                trial_dir = case / "jobs" / "job1" / f"task {num}__trial1"
                (trial_dir / "agent").mkdir(parents=True)
                (trial_dir / "verifier").mkdir(parents=True)
                (trial_dir / "result.json").write_text(
                    json.dumps(
                        {
                            "trial_name": f"task {num}__trial1",
                            "verifier_result": {"rewards": {"reward": 1.0}},
                        }
                    ),
                    encoding="utf-8",
                )
                (trial_dir / "agent" / "trajectory.json").write_text(
                    json.dumps({"steps": []}), encoding="utf-8"
                )
                (trial_dir / "trial.log").write_text("trial ok\n", encoding="utf-8")
            (case / "jobs" / "job1" / "job.log").write_text("job ok\n", encoding="utf-8")

            # Check discover_all_task_ids natural order
            tasks = discover_all_task_ids(bench)
            self.assertEqual(tasks, ["task98", "task194"])

            # Run cfa audit --all
            f = io.StringIO()
            with redirect_stdout(f):
                rc = cfa.main(["audit", "--all", "--jobs-root", str(bench), "--no-verdict-stdout"])
            self.assertEqual(rc, 0)
            out = f.getvalue()
            self.assertIn("Auditing task98...", out)
            self.assertIn("Auditing task194...", out)
            self.assertIn("Batch audit complete (2 tasks)", out)

            # Check INDEX.md was rendered
            index_path = bench / REPORT_DIR_NAME / "INDEX.md"
            self.assertTrue(index_path.is_file())
            content = index_path.read_text(encoding="utf-8")
            self.assertIn("task98", content)
            self.assertIn("task194", content)


if __name__ == "__main__":
    unittest.main()
