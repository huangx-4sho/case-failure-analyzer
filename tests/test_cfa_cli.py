#!/usr/bin/env python3
"""
Tests for unified CFA CLI (`scripts/cfa.py`).
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
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

    def test_cfa_inspect_artifacts_and_step_sections(self) -> None:
        home_dir = os.path.expanduser("~")
        secret_tok = "sk-123456789012345678901234"
        with tempfile.TemporaryDirectory() as tmp:
            rep_dir = Path(tmp) / REPORT_DIR_NAME / "task310"
            data_dir = rep_dir / "data"
            data_dir.mkdir(parents=True)

            timeline = []
            for s in (1, 2, 3, 4, 43, 430, 44):
                timeline.append(
                    {
                        "event_id": f"trajectory:step:{s}:thought",
                        "actor": "agent",
                        "event_type": "agent_thought",
                        "tool_name": None,
                        "command": None,
                        "observation": f"Thinking at step {s}",
                        "exit_code": None,
                    }
                )
                timeline.append(
                    {
                        "event_id": f"trajectory:step:{s}:tool:0",
                        "actor": "agent",
                        "event_type": "tool_call" if s != 43 else "file_write",
                        "tool_name": "Write" if s == 43 else "Bash",
                        "command": (
                            f"{home_dir}/work/calc.py"
                            if s == 43
                            else f"python3 run_{s}.py --key {secret_tok}"
                        ),
                        "code_payload": "energy = -10.5 * 27.211386\n" if s == 43 else None,
                        "observation": f"step {s} output with token {secret_tok} at {home_dir}/out",
                        "exit_code": 0 if s != 44 else 1,
                    }
                )

            ev_content = {
                "runtime": {
                    "execution_status": "completed",
                    "verification_status": "failed",
                    "verdict": "failed",
                    "reward": 0.0,
                },
                "metadata": {"declared_deliverables": ["/app/results.json", "/app/traj.xyz"]},
                "artifacts": [
                    {
                        "artifact_id": "art:results",
                        "rel_path": "artifacts/app/results.json",
                        "scope": "trial",
                        "exists": True,
                        "size_bytes": 512,
                    }
                ],
                "missing_deliverables": ["/app/traj.xyz"],
                "contract_observations": [
                    {
                        "contract_id": "contract:1",
                        "item": "results.json",
                        "alignment": "agent_schema_mismatch",
                        "agent_output_status": "present",
                        "details": "energy unit in Hartree instead of eV",
                    }
                ],
                "verifier_report": {
                    "failed_checks": [
                        {
                            "layer": "L3",
                            "name": "total_energy",
                            "detail": "delivered -10.5 vs recomputed -285.72",
                            "artifact_comparison": [
                                {
                                    "metric": "energy_eV",
                                    "delivered": -10.5,
                                    "recomputed": -285.72,
                                    "abs_diff": 275.22,
                                    "artifact_ref": "art:artifacts/app/results.json",
                                    "scale_or_sign_hint": {
                                        "kind": "unit_conversion",
                                        "factor": 27.211386,
                                        "label": "Hartree -> eV",
                                    },
                                }
                            ],
                        }
                    ]
                },
                "scientific_observations": [
                    {
                        "sci_id": "sci:recompute_divergence:1",
                        "metric": "energy_eV",
                        "agent_artifact_ref": "art:artifacts/app/results.json",
                        "scale_or_sign_hint": {
                            "kind": "unit_conversion",
                            "factor": 27.211386,
                            "label": "Hartree -> eV",
                        },
                        "stale_deliverable_sync": True,
                        "recomputed_match_in_trajectory_ref": "trajectory:step:42",
                    }
                ],
                "timeline": timeline,
            }
            (data_dir / "evidence.json").write_text(json.dumps(ev_content), encoding="utf-8")

            # 1. --section artifacts (Markdown)
            f = io.StringIO()
            with redirect_stdout(f):
                rc = cfa.main(
                    ["inspect", "task310", "--report-dir", str(rep_dir), "--section", "artifacts"]
                )
            self.assertEqual(rc, 0)
            out = f.getvalue()
            self.assertIn("# Artifacts & Deliverables: `task310`", out)
            self.assertIn("/app/traj.xyz", out)
            self.assertIn("artifacts/app/results.json", out)
            self.assertIn("agent_schema_mismatch", out)
            self.assertIn("total_energy", out)
            self.assertIn("Hartree -> eV", out)
            self.assertIn("stale_deliverable_sync=True", out)
            self.assertIn("trajectory:step:42", out)

            # 2. --section artifacts --raw (JSON)
            f = io.StringIO()
            with redirect_stdout(f):
                rc = cfa.main(
                    [
                        "inspect",
                        "task310",
                        "--report-dir",
                        str(rep_dir),
                        "--section",
                        "artifacts",
                        "--raw",
                    ]
                )
            self.assertEqual(rc, 0)
            art_json = json.loads(f.getvalue())
            self.assertEqual(art_json["missing_deliverables"], ["/app/traj.xyz"])
            self.assertEqual(len(art_json["check_artifact_comparisons"]), 1)
            self.assertTrue(len(art_json["scale_or_sign_hints"]) >= 1)
            self.assertEqual(len(art_json["stale_deliverable_syncs"]), 1)

            # 3. --section step without --step (index + last 5 steps)
            f = io.StringIO()
            with redirect_stdout(f):
                rc = cfa.main(
                    ["inspect", "task310", "--report-dir", str(rep_dir), "--section", "step"]
                )
            self.assertEqual(rc, 0)
            out = f.getvalue()
            self.assertIn("# Timeline Step Index: `task310`", out)
            self.assertIn("**Total Steps**: `7` (`14` events)", out)
            self.assertIn("Last 5 Step(s)", out)
            self.assertIn("Step `430`", out)
            self.assertNotIn(secret_tok, out)

            # 4. --section step --step 43 (exact step boundary: must NOT include step 4 or 430)
            f = io.StringIO()
            with redirect_stdout(f):
                rc = cfa.main(
                    [
                        "inspect",
                        "task310",
                        "--report-dir",
                        str(rep_dir),
                        "--section",
                        "step",
                        "--step",
                        "43",
                    ]
                )
            self.assertEqual(rc, 0)
            out = f.getvalue()
            self.assertIn("trajectory:step:43:thought", out)
            self.assertIn("trajectory:step:43:tool:0", out)
            self.assertNotIn("trajectory:step:430", out)
            self.assertNotIn("trajectory:step:4:", out)
            self.assertIn("Thinking at step 43", out)
            self.assertIn("energy = -10.5 * 27.211386", out)
            if home_dir != "/":
                self.assertNotIn(home_dir, out)
            self.assertNotIn(secret_tok, out)

            # 5. --section step --step trajectory:step:43 --raw --no-redact
            f = io.StringIO()
            with redirect_stdout(f):
                rc = cfa.main(
                    [
                        "inspect",
                        "task310",
                        "--report-dir",
                        str(rep_dir),
                        "--section",
                        "step",
                        "--step",
                        "trajectory:step:43",
                        "--raw",
                        "--no-redact",
                    ]
                )
            self.assertEqual(rc, 0)
            step_json = json.loads(f.getvalue())
            self.assertEqual(step_json["matched_count"], 2)
            self.assertEqual(
                [e["event_id"] for e in step_json["events"]],
                ["trajectory:step:43:thought", "trajectory:step:43:tool:0"],
            )
            self.assertIn(secret_tok, step_json["events"][1]["observation"])

    def _write_audited_report(
        self,
        report_dir: Path,
        *,
        verdict: str,
        reward: float,
        exec_status: str,
        verif_status: str,
        category: str,
        subtype: str,
        code: str,
        confidence: float,
        failed_checks: list,
        causal_anchors: dict,
    ) -> None:
        data_dir = report_dir / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        ev = {
            "runtime": {
                "verdict": verdict,
                "reward": reward,
                "execution_status": exec_status,
                "verification_status": verif_status,
            },
            "verifier_report": {"failed_checks": failed_checks},
            "causal_anchors": causal_anchors,
        }
        an = {
            "verdict": verdict,
            "primary_root_cause": {
                "category": category,
                "subtype": subtype,
                "code": code,
                "confidence": confidence,
            },
            "failure_manifestation": {"named_failed_checks": failed_checks},
        }
        (data_dir / "evidence.json").write_text(json.dumps(ev), encoding="utf-8")
        (data_dir / "analysis.json").write_text(json.dumps(an), encoding="utf-8")

    def test_cfa_diff_two_tasks_markdown_and_raw(self) -> None:
        secret_tok = "sk-abcdefghijklmnop123456"
        with tempfile.TemporaryDirectory() as tmp:
            bench = Path(tmp)
            rep_root = bench / REPORT_DIR_NAME
            dir_a = rep_root / "task101"
            dir_b = rep_root / "task102"

            self._write_audited_report(
                dir_a,
                verdict="failed",
                reward=0.0,
                exec_status="completed",
                verif_status="failed",
                category="agent",
                subtype="numerical_accuracy",
                code="AGENT_NUMERICAL_ERROR",
                confidence=0.70,
                failed_checks=[
                    {
                        "layer": "L3",
                        "name": "energy_match",
                        "detail": f"energy off by 0.50 (tok {secret_tok})",
                        "artifact_comparison": [
                            {
                                "metric": "energy",
                                "delivered": -10.5,
                                "recomputed": -10.0,
                                "abs_diff": 0.5,
                            }
                        ],
                    },
                    {
                        "layer": "L2",
                        "name": "only_a_check",
                        "detail": "missing header",
                    },
                ],
                causal_anchors={
                    "algorithm_or_param_definition": {
                        "event_id": "trajectory:step:3:tool:0",
                        "step_id": 3,
                        "tool_name": "Write",
                        "target_path": "run.py",
                        "parameter_evolution": [
                            {
                                "param": "cutoff",
                                "initial": "30",
                                "final": "50",
                                "from_event": "trajectory:step:2:tool:0",
                                "to_event": "trajectory:step:3:tool:0",
                            }
                        ],
                    },
                    "deliverable_generation": {
                        "event_id": "trajectory:step:5:tool:0",
                        "step_id": 5,
                        "tool_name": "Bash",
                        "command": "python3 run.py",
                    },
                    "self_check": {
                        "event_id": "trajectory:step:6:tool:0",
                        "step_id": 6,
                        "tool_name": "Bash",
                        "command": "python3 check.py",
                        "self_check_passed": True,
                        "checked_failed_checks": [],
                        "uncovered_failed_checks": ["energy_match"],
                    },
                },
            )
            self._write_audited_report(
                dir_b,
                verdict="failed",
                reward=0.25,
                exec_status="completed",
                verif_status="failed",
                category="agent",
                subtype="result_validation",
                code="AGENT_RESULT_VALIDATION",
                confidence=0.85,
                failed_checks=[
                    {
                        "layer": "L3",
                        "name": "energy_match",
                        "detail": "energy off by 0.20",
                        "artifact_comparison": [
                            {
                                "metric": "energy",
                                "delivered": -10.2,
                                "recomputed": -10.0,
                                "abs_diff": 0.2,
                            }
                        ],
                    },
                    {
                        "layer": "L3",
                        "name": "only_b_check",
                        "detail": "force deviation",
                    },
                ],
                causal_anchors={
                    "algorithm_or_param_definition": {
                        "event_id": "trajectory:step:4:tool:0",
                        "step_id": 4,
                        "tool_name": "Write",
                        "target_path": "run.py",
                        "parameter_evolution": [
                            {
                                "param": "cutoff",
                                "initial": "30",
                                "final": "80",
                                "from_event": "trajectory:step:2:tool:0",
                                "to_event": "trajectory:step:4:tool:0",
                            }
                        ],
                    },
                    "deliverable_generation": {
                        "event_id": "trajectory:step:5:tool:0",
                        "step_id": 5,
                        "tool_name": "Bash",
                        "command": "python3 run.py",
                    },
                    "self_check": {
                        "event_id": "trajectory:step:7:tool:0",
                        "step_id": 7,
                        "tool_name": "Bash",
                        "command": "python3 check.py",
                        "self_check_passed": False,
                        "checked_failed_checks": ["energy_match"],
                        "uncovered_failed_checks": [],
                    },
                },
            )

            # Markdown diff using --jobs-root
            f = io.StringIO()
            with redirect_stdout(f):
                rc = cfa.main(["diff", "task101", "task102", "--jobs-root", str(bench)])
            self.assertEqual(rc, 0)
            md = f.getvalue()
            self.assertIn("# CFA Diff: `task101` vs `task102`", md)
            self.assertIn("## 1. Runtime & Verdict", md)
            self.assertIn("## 2. Primary Root Cause", md)
            self.assertIn("+0.15", md)
            self.assertIn("only_a_check", md)
            self.assertIn("only_b_check", md)
            self.assertIn("energy_match", md)
            self.assertIn("Numeric Delta (`energy`)", md)
            self.assertIn("Parameter Evolution", md)
            self.assertIn("cutoff", md)
            self.assertNotIn(secret_tok, md)

            # Raw JSON diff using direct directory paths + --no-redact
            f = io.StringIO()
            with redirect_stdout(f):
                rc = cfa.main(["diff", str(dir_a), str(dir_b), "--raw", "--no-redact"])
            self.assertEqual(rc, 0)
            payload = json.loads(f.getvalue())
            self.assertEqual(payload["runtime_and_verdict"]["a"]["reward"], 0.0)
            self.assertEqual(payload["runtime_and_verdict"]["b"]["reward"], 0.25)
            self.assertAlmostEqual(payload["primary_root_cause"]["confidence_delta"], 0.15)
            self.assertEqual(
                [c["name"] for c in payload["failed_checks_diff"]["only_in_a"]],
                ["only_a_check"],
            )
            self.assertEqual(
                [c["name"] for c in payload["failed_checks_diff"]["only_in_b"]],
                ["only_b_check"],
            )
            shared = payload["failed_checks_diff"]["shared"]
            self.assertEqual(len(shared), 1)
            self.assertAlmostEqual(shared[0]["numeric_deltas"][0]["abs_diff_delta"], -0.3)
            self.assertIn(secret_tok, shared[0]["detail_a"])
            algo_diff = payload["causal_anchors_diff"]["algorithm_or_param_definition"]
            self.assertTrue(algo_diff["changed"])
            self.assertEqual(algo_diff["parameter_evolution_diff"][0]["a_final"], "50")
            self.assertEqual(algo_diff["parameter_evolution_diff"][0]["b_final"], "80")

    def test_cfa_diff_trials_and_auto_two_latest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bench = Path(tmp)
            task_rep = bench / REPORT_DIR_NAME / "task208"
            trials_dir = task_rep / "trials"

            t1 = trials_dir / "task 208__t1"
            t2 = trials_dir / "task 208__t2"
            t3 = trials_dir / "task 208__t3"

            for idx, (tdir, code, reward, mtime) in enumerate(
                [
                    (t1, "AGENT_CODE_1", 0.0, 1_700_000_100),
                    (t2, "AGENT_CODE_2", 0.5, 1_700_000_200),
                    (t3, "AGENT_CODE_3", 1.0, 1_700_000_300),
                ]
            ):
                self._write_audited_report(
                    tdir,
                    verdict="passed" if reward == 1.0 else "failed",
                    reward=reward,
                    exec_status="completed",
                    verif_status="passed" if reward == 1.0 else "failed",
                    category="none" if reward == 1.0 else "agent",
                    subtype="none" if reward == 1.0 else "result_validation",
                    code=code,
                    confidence=0.8 + idx * 0.05,
                    failed_checks=[]
                    if reward == 1.0
                    else [{"layer": "L3", "name": f"check_{idx}", "detail": "fail"}],
                    causal_anchors={},
                )
                for p in (
                    tdir / "data" / "analysis.json",
                    tdir / "data" / "evidence.json",
                    tdir,
                ):
                    os.utime(p, (mtime, mtime))

            # 1. Explicit --trials t1 t3
            f = io.StringIO()
            with redirect_stdout(f):
                rc = cfa.main(
                    [
                        "diff",
                        "task208",
                        "--trials",
                        "task 208__t1",
                        "task 208__t3",
                        "--jobs-root",
                        str(bench),
                        "--raw",
                    ]
                )
            self.assertEqual(rc, 0)
            payload = json.loads(f.getvalue())
            self.assertEqual(payload["target_a"]["label"], "task 208__t1")
            self.assertEqual(payload["target_b"]["label"], "task 208__t3")
            self.assertEqual(payload["primary_root_cause"]["a"]["code"], "AGENT_CODE_1")
            self.assertEqual(payload["primary_root_cause"]["b"]["code"], "AGENT_CODE_3")

            # 2. Auto two-latest trials (should pick t2 and t3, excluding t1)
            f = io.StringIO()
            with redirect_stdout(f):
                rc = cfa.main(["diff", "task208", "--jobs-root", str(bench), "--raw"])
            self.assertEqual(rc, 0)
            payload = json.loads(f.getvalue())
            self.assertEqual(payload["target_a"]["label"], "task 208__t2")
            self.assertEqual(payload["target_b"]["label"], "task 208__t3")
            self.assertEqual(payload["primary_root_cause"]["a"]["code"], "AGENT_CODE_2")
            self.assertEqual(payload["primary_root_cause"]["b"]["code"], "AGENT_CODE_3")

            # 3. Error when fewer than 2 trials exist
            single_rep = bench / REPORT_DIR_NAME / "task209"
            self._write_audited_report(
                single_rep / "trials" / "only_one",
                verdict="failed",
                reward=0.0,
                exec_status="completed",
                verif_status="failed",
                category="agent",
                subtype="result_validation",
                code="AGENT_ONE",
                confidence=0.7,
                failed_checks=[],
                causal_anchors={},
            )
            err = io.StringIO()
            with redirect_stderr(err):
                rc = cfa.main(["diff", "task209", "--jobs-root", str(bench)])
            self.assertEqual(rc, 2)
            self.assertIn("fewer than 2 audited trials", err.getvalue())


if __name__ == "__main__":
    unittest.main()
