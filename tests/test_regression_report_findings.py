#!/usr/bin/env python3
"""
Regression test suite for verification and attribution findings
(`tests/test_regression_report_findings.py`).
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from analyze_case import ReplayNotSupportedError, analyze_single_trial, write_outputs
from discover_artifacts import discover_all
from extract_runtime_errors import extract_runtime_errors
from validate_analysis import validate_all


class TestReportRegressionFindings(unittest.TestCase):
    def test_01_mixed_job_passing_trial_not_polluted_by_job_stats(self) -> None:
        """#1: Job has n_errored_trials=1, but trial with reward=1.0 must be passed."""
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp) / "jobs" / "mixed-job"
            trial_a = job_dir / "trial_a_fail"
            trial_b = job_dir / "trial_b_pass"
            task_dir = Path(tmp) / "tasks" / "sample-task"
            trial_a.mkdir(parents=True)
            (trial_b / "agent").mkdir(parents=True)
            (trial_b / "verifier").mkdir(parents=True)
            task_dir.mkdir(parents=True)

            (job_dir / "result.json").write_text(
                json.dumps({"n_total_trials": 2, "stats": {"n_errored_trials": 1}}),
                encoding="utf-8",
            )
            (trial_a / "exception.txt").write_text(
                "RuntimeError: container failed", encoding="utf-8"
            )
            (trial_b / "agent" / "trajectory.json").write_text(
                json.dumps({"schema_version": "ATIF-v1.7", "steps": []}),
                encoding="utf-8",
            )
            (trial_b / "verifier" / "reward.txt").write_text("1.0", encoding="utf-8")

            disc = discover_all(job_path=job_dir, task_path=task_dir)
            by_name = {t["trial_name"]: t for t in disc["trials"]}
            self.assertEqual(by_name["trial_b_pass"]["runtime"]["verification_status"], "passed")
            self.assertEqual(by_name["trial_b_pass"]["runtime"]["exit_status"], "completed")

            _, an_b, _, _ = analyze_single_trial(by_name["trial_b_pass"], job_dir=job_dir)
            self.assertEqual(an_b["verdict"], "passed")
            self.assertEqual(an_b["primary_root_cause"]["category"], "none")

    def test_02_bare_chaotic_keyword_does_not_trigger_numerical_or_agent(self) -> None:
        """#2: Bare 'chaotic' word in verify.log without trajectory/ensemble data must be unknown."""
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp) / "jobs" / "bare-chaotic"
            trial_dir = job_dir / "trial_1"
            task_dir = Path(tmp) / "tasks" / "md-task"
            (trial_dir / "agent").mkdir(parents=True)
            (trial_dir / "verifier").mkdir(parents=True)
            task_dir.mkdir(parents=True)

            (trial_dir / "agent" / "trajectory.json").write_text(
                json.dumps({"schema_version": "ATIF-v1.7", "steps": []}),
                encoding="utf-8",
            )
            (trial_dir / "verifier" / "verify.log").write_text(
                "FAIL: agent output absent; protocol note mentions chaotic behaviour of the reference run",
                encoding="utf-8",
            )
            (trial_dir / "verifier" / "reward.txt").write_text("0", encoding="utf-8")

            disc = discover_all(job_path=job_dir, task_path=task_dir)
            _, an, _, _ = analyze_single_trial(disc["trials"][0], job_dir=job_dir)
            self.assertNotEqual(an["primary_root_cause"]["category"], "numerical")
            self.assertNotEqual(an["primary_root_cause"]["category"], "agent")
            self.assertEqual(an["primary_root_cause"]["category"], "unknown")

    def test_03_unrelated_d_exponent_in_reference_does_not_blame_verifier(self) -> None:
        """#3: Agent wrote wrong value 9.99E+02 while verify.log prints reference 1.23D+03 -> not verifier."""
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp) / "jobs" / "unrelated-d"
            trial_dir = job_dir / "trial_1"
            task_dir = Path(tmp) / "tasks" / "qe-task"
            (trial_dir / "agent").mkdir(parents=True)
            (trial_dir / "verifier").mkdir(parents=True)
            (task_dir / "tests").mkdir(parents=True)

            (task_dir / "instruction.md").write_text("Compute ENERGY.", encoding="utf-8")
            (task_dir / "tests" / "verify.py").write_text(
                "import re\nVAL_RE = re.compile(r'ENERGY:\\s*([-\\d.E+]+)')\n",
                encoding="utf-8",
            )
            (trial_dir / "agent" / "trajectory.json").write_text(
                json.dumps(
                    {
                        "schema_version": "ATIF-v1.7",
                        "steps": [
                            {
                                "step_id": 1,
                                "source": "agent",
                                "tool_calls": [
                                    {
                                        "tool_call_id": "c1",
                                        "function_name": "Write",
                                        "arguments": {"file_path": "/workspace/results.json"},
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (trial_dir / "verifier" / "verify.log").write_text(
                "FAIL: ENERGY mismatch: got 9.99E+02, expected reference 1.23D+03",
                encoding="utf-8",
            )
            (trial_dir / "verifier" / "reward.txt").write_text("0", encoding="utf-8")

            disc = discover_all(job_path=job_dir, task_path=task_dir)
            ev, an, _, _ = analyze_single_trial(disc["trials"][0], job_dir=job_dir)
            hazards = [v for v in ev["verifier_observations"] if v.get("type") == "parser_hazard"]
            self.assertEqual(hazards[0]["failure_binding"], "none")
            self.assertFalse(hazards[0]["triggered"])
            self.assertNotEqual(an["primary_root_cause"]["category"], "verifier")
            self.assertEqual(an["primary_root_cause"]["category"], "agent")

    def test_04_verifier_internal_crash_blames_verifier_not_agent(self) -> None:
        """#4: Verifier crashing with FileNotFoundError: /tmp/verify_tmp/refs.json must blame verifier."""
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp) / "jobs" / "verifier-crash"
            trial_dir = job_dir / "trial_1"
            task_dir = Path(tmp) / "tasks" / "crash-task"
            (trial_dir / "agent").mkdir(parents=True)
            (trial_dir / "verifier").mkdir(parents=True)
            (task_dir / "tests").mkdir(parents=True)

            (trial_dir / "agent" / "trajectory.json").write_text(
                json.dumps({"schema_version": "ATIF-v1.7", "steps": []}),
                encoding="utf-8",
            )
            (trial_dir / "verifier" / "verify.log").write_text(
                "Traceback (most recent call last):\n"
                "  File '/tests/verify.py', line 14, in <module>\n"
                "FileNotFoundError: [Errno 2] No such file or directory: '/tmp/verify_tmp/refs.json'",
                encoding="utf-8",
            )
            (trial_dir / "verifier" / "reward.txt").write_text("0", encoding="utf-8")

            disc = discover_all(job_path=job_dir, task_path=task_dir)
            _, an, _, _ = analyze_single_trial(disc["trials"][0], job_dir=job_dir)
            self.assertEqual(an["primary_root_cause"]["category"], "verifier")
            self.assertEqual(an["primary_root_cause"]["code"], "VERIFIER_RECOMPUTE_DEFECT")

    def test_05_claude_code_txt_single_source_of_truth_for_agent_started(self) -> None:
        """#5: Trial with only agent/claude-code.txt has agent_started=True in both discover and extract."""
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp) / "jobs" / "claude-txt-job"
            trial_dir = job_dir / "trial_1"
            (trial_dir / "agent").mkdir(parents=True)
            (trial_dir / "agent" / "claude-code.txt").write_text(
                "Agent session log...", encoding="utf-8"
            )

            disc = discover_all(job_path=job_dir)
            ext = extract_runtime_errors(job_dir=job_dir, trial_dir=trial_dir)
            self.assertTrue(disc["trials"][0]["runtime"]["agent_started"])
            self.assertTrue(ext["agent_started"])

    def test_06_null_job_stats_does_not_crash(self) -> None:
        """#6: result.json with {'stats': null} must not raise AttributeError."""
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp) / "jobs" / "null-stats"
            trial_dir = job_dir / "trial_1"
            trial_dir.mkdir(parents=True)
            (job_dir / "result.json").write_text(json.dumps({"stats": None}), encoding="utf-8")
            (trial_dir / "result.json").write_text(
                json.dumps({"trial_name": "trial_1"}), encoding="utf-8"
            )

            disc = discover_all(job_path=job_dir)
            self.assertEqual(disc["trials"][0]["metadata"]["job_stats"]["n_errored_trials"], 0)

    def test_08_validator_rejects_missing_hypotheses_and_deviation(self) -> None:
        """#8: validate_all must fail when competing_hypotheses or first_unrecovered_deviation is missing."""
        ev = {
            "case_id": "c1",
            "artifacts": [
                {"artifact_id": "art:trial_result", "rel_path": "result.json", "scope": "trial"}
            ],
            "runtime": {"agent_started": True, "verifier_started": True},
            "timeline": [],
            "error_observations": [],
            "contract_observations": [],
            "verifier_observations": [],
            "scientific_observations": [],
            "missing_artifacts": [],
        }
        incomplete_analysis = {
            "schema_version": "failure-analysis-v1",
            "case_id": "c1",
            "verdict": "failed",
            "failure_stage": "unknown",
            "detection_stage": "runner",
            "failure_manifestation": {"type": "unknown", "summary": "fail"},
            "primary_root_cause": {
                "category": "unknown",
                "subtype": "insufficient_evidence",
                "code": "UNKNOWN_INSUFFICIENT_EVIDENCE",
                "confidence": 0.3,
            },
            "contributing_factors": [],
            "evidence_refs": ["art:trial_result"],
            "excluded_hypotheses": [],
            "recommended_actions": [],
            "skill_prescription": None,
        }
        ok, errs = validate_all(ev, incomplete_analysis)
        self.assertFalse(ok)
        self.assertTrue(
            any("competing_hypotheses" in e or "first_unrecovered_deviation" in e for e in errs)
        )

    def test_09_validator_rejects_single_line_or_empty_section_report(self) -> None:
        """#9: validate_all must reject single-line section titles or empty bodies in report.md."""
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp) / "jobs" / "empty-case"
            trial_dir = job_dir / "trial_1"
            trial_dir.mkdir(parents=True)
            (trial_dir / "result.json").write_text(
                json.dumps({"trial_name": "trial_1"}), encoding="utf-8"
            )
            disc = discover_all(job_path=job_dir)
            ev, an, _, _ = analyze_single_trial(disc["trials"][0], job_dir=job_dir)

            fake_report = "诊断结论与运行态概览 / 故障现场与因果证据链 / 竞争假设裁决与伴随信号 / 修复行动与处方建议"
            ok, errs = validate_all(ev, an, fake_report)
            self.assertFalse(ok)
            self.assertTrue(any("missing required section header" in e for e in errs))

    def test_10_11_replay_modes_raise_explicit_error(self) -> None:
        """#10 & #11: --replay verifier and --replay safe must fail explicitly rather than faking execution."""
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp) / "jobs" / "replay-job"
            trial_dir = job_dir / "trial_1"
            trial_dir.mkdir(parents=True)
            (trial_dir / "result.json").write_text(
                json.dumps({"trial_name": "trial_1"}), encoding="utf-8"
            )
            disc = discover_all(job_path=job_dir)
            with self.assertRaises(ReplayNotSupportedError):
                analyze_single_trial(disc["trials"][0], job_dir=job_dir, replay_mode="verifier")
            with self.assertRaises(ReplayNotSupportedError):
                analyze_single_trial(disc["trials"][0], job_dir=job_dir, replay_mode="safe")

            proc = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT_DIR / "analyze_case.py"),
                    "--job",
                    str(job_dir),
                    "--output",
                    str(Path(tmp) / "out"),
                    "--replay",
                    "safe",
                ],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(proc.returncode, 0)

    def test_12_bilingual_report_outputs_generated(self) -> None:
        """Verify write_outputs produces bilingual report.md, report.zh.md, and report.en.md."""
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp) / "jobs" / "bi-job"
            trial_dir = job_dir / "trial_1"
            trial_dir.mkdir(parents=True)
            (trial_dir / "result.json").write_text(
                json.dumps({"trial_name": "trial_1"}), encoding="utf-8"
            )
            disc = discover_all(job_path=job_dir)
            ev, an, rep, skill_md = analyze_single_trial(disc["trials"][0], job_dir=job_dir)
            out_dir = Path(tmp) / "out"
            write_outputs(out_dir, ev, an, rep, skill_md)
            self.assertTrue((out_dir / "report.md").is_file())
            self.assertTrue((out_dir / "report.zh.md").is_file())
            self.assertTrue((out_dir / "report.en.md").is_file())
            rep_text = (out_dir / "report.md").read_text(encoding="utf-8")
            self.assertIn("案例失败归因报告 / Case Failure Attribution", rep_text)
            self.assertNotIn("Part II: English Edition", rep_text)

    def test_13_persistent_fixture_tree_and_cli_phases(self) -> None:
        """Verify persistent directory tree fixture under tests/fixtures/regressions/ and CLI --phase/--trial/--format."""
        fixture_job = (
            Path(__file__).resolve().parent
            / "fixtures"
            / "regressions"
            / "mixed-job-success-trial"
            / "job"
        )
        self.assertTrue(fixture_job.is_dir())
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "cli_out"
            proc = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT_DIR / "analyze_case.py"),
                    "--job",
                    str(fixture_job),
                    "--trial",
                    "trial_b_pass",
                    "--output",
                    str(out_dir),
                    "--phase",
                    "all",
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            an = json.loads((out_dir / "analysis.json").read_text(encoding="utf-8"))
            self.assertEqual(an["verdict"], "passed")
            self.assertTrue((out_dir / "candidate-hypotheses.json").is_file())

    def test_14_job_dir_with_stray_agent_folder_not_misclassified_as_single_trial(self) -> None:
        """P2 #12: Job directory containing child trials and a stray agent/ folder must still discover child trials."""
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp) / "jobs" / "stray-agent-job"
            (job_dir / "agent").mkdir(parents=True)
            trial_1 = job_dir / "trial_1"
            trial_1.mkdir(parents=True)
            (trial_1 / "trial.log").write_text("step 1", encoding="utf-8")
            (trial_1 / "result.json").write_text(
                json.dumps({"trial_name": "trial_1"}), encoding="utf-8"
            )

            disc = discover_all(job_path=job_dir)
            self.assertEqual(len(disc["trials"]), 1)
            self.assertEqual(disc["trials"][0]["trial_name"], "trial_1")

    def test_15_co_occurring_case_and_verifier_defects_preserved(self) -> None:
        """§7 #3: When both missing asset (case) and verifier defect occur, verifier defect is preserved in contributing_factors."""
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp) / "jobs" / "co-defect"
            trial_dir = job_dir / "trial_1"
            task_dir = Path(tmp) / "tasks" / "co-task"
            (trial_dir / "agent").mkdir(parents=True)
            (trial_dir / "verifier").mkdir(parents=True)
            (task_dir / "environment" / "assets").mkdir(parents=True)
            (task_dir / "tests").mkdir(parents=True)

            (task_dir / "instruction.md").write_text(
                "Use `/workspace/assets/missing.UPF`.", encoding="utf-8"
            )
            (task_dir / "tests" / "verify.py").write_text(
                "import re\nVAL_RE = re.compile(r'VAL=\\s*([-\\d.E+]+)')\n",
                encoding="utf-8",
            )
            (trial_dir / "agent" / "trajectory.json").write_text(
                json.dumps({"schema_version": "ATIF-v1.7", "steps": []}), encoding="utf-8"
            )
            (trial_dir / "verifier" / "verify.log").write_text(
                "FAIL: could not parse line 'VAL= -0.12345D+03'", encoding="utf-8"
            )
            (trial_dir / "verifier" / "reward.txt").write_text("0", encoding="utf-8")

            disc = discover_all(job_path=job_dir, task_path=task_dir)
            _, an, _, _ = analyze_single_trial(disc["trials"][0], job_dir=job_dir)
            self.assertEqual(an["primary_root_cause"]["category"], "case")
            self.assertTrue(any(cf["category"] == "verifier" for cf in an["contributing_factors"]))
            # Verify candidate_hypotheses from generate_candidate_hypotheses were merged into competing_hypotheses
            self.assertTrue(any(h["category"] == "verifier" for h in an["competing_hypotheses"]))

    def test_16_non_dict_stats_in_extract_runtime_errors_does_not_crash(self) -> None:
        """Residual #1: {'stats': 'x'} with trial_dir=None must not crash with AttributeError."""
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp) / "jobs" / "string-stats"
            job_dir.mkdir(parents=True)
            (job_dir / "result.json").write_text(
                json.dumps({"stats": "x", "task_id": "string-id", "config": "string-cfg"}),
                encoding="utf-8",
            )
            ext = extract_runtime_errors(job_dir=job_dir, trial_dir=None)
            self.assertFalse(ext["agent_started"])
            disc = discover_all(job_path=job_dir)
            self.assertEqual(disc["trials"][0]["metadata"]["job_stats"]["n_errored_trials"], 0)

    def test_17_error_family_registry_missing_or_empty_raises_explicit_error(self) -> None:
        """Residual #4: Missing or empty error-families.json must raise explicit error instead of returning 0 adapters."""
        from extract_scientific_errors import build_scientific_adapters, load_error_family_registry

        with tempfile.TemporaryDirectory() as tmp:
            missing_path = Path(tmp) / "nonexistent.json"
            with self.assertRaises(FileNotFoundError):
                load_error_family_registry(missing_path)
            with self.assertRaises(FileNotFoundError):
                build_scientific_adapters(missing_path)

            empty_path = Path(tmp) / "empty.json"
            empty_path.write_text("{}", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_error_family_registry(empty_path)

    def test_18_confidence_is_pure_evidence_ratio_without_literal_overrides(self) -> None:
        """Residual #3: compute_evidence_confidence uses normalized ratio (points / 6) and no literal overrides exist."""
        from confidence import MAX_EVIDENCE_POINTS, compute_evidence_confidence

        c0, _, s0 = compute_evidence_confidence(ruled_out=True)
        self.assertEqual((c0, s0), (0.0, "low"))
        c_pass, _, s_pass = compute_evidence_confidence(verdict_passed=True)
        self.assertEqual((c_pass, s_pass), (1.0, "high"))
        c3, _, s3 = compute_evidence_confidence(
            direct_causal_evidence=2, cross_source_corroboration=1
        )
        self.assertEqual(c3, round(3 / MAX_EVIDENCE_POINTS, 2))
        self.assertEqual(s3, "medium")

    def test_19_standalone_zh_and_en_reports_and_timeline_windowing(self) -> None:
        """Verify standalone report.zh.md and report.en.md pass 4-section validation and timeline windowing retains tail & deviation."""
        from render_report import select_timeline_events
        from validate_analysis import validate_report_sections

        long_timeline = [
            {"event_id": f"trajectory:step:{i}", "actor": "agent", "event_type": "tool_call"}
            for i in range(1, 45)
        ]
        selected = select_timeline_events(long_timeline, fud_ref="trajectory:step:22", limit=15)
        selected_ids = [e["event_id"] for e in selected]
        self.assertEqual(len(selected), 15)
        self.assertIn("trajectory:step:1", selected_ids)
        self.assertIn("trajectory:step:22", selected_ids)
        self.assertIn("trajectory:step:44", selected_ids)
        # Ensure strict chronological ordering
        step_nums = [int(eid.split(":")[-1]) for eid in selected_ids]
        self.assertEqual(step_nums, sorted(step_nums))

        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp) / "jobs" / "bi-full-job"
            trial_dir = job_dir / "trial_1"
            trial_dir.mkdir(parents=True)
            (trial_dir / "result.json").write_text(
                json.dumps({"trial_name": "trial_1"}), encoding="utf-8"
            )
            disc = discover_all(job_path=job_dir)
            ev, an, rep, skill_md = analyze_single_trial(disc["trials"][0], job_dir=job_dir)
            out_dir = Path(tmp) / "out"
            write_outputs(out_dir, ev, an, rep, skill_md)

            zh_text = (out_dir / "report.zh.md").read_text(encoding="utf-8")
            en_text = (out_dir / "report.en.md").read_text(encoding="utf-8")
            self.assertEqual(validate_report_sections(zh_text), [])
            self.assertEqual(validate_report_sections(en_text), [])
            self.assertTrue((out_dir / "candidate-hypotheses.json").is_file())

    def test_20_verifier_hidden_requirement_and_schema_mismatch_attribution(self) -> None:
        """Verify verifier_hidden_requirement and verifier_schema_mismatch attribute to VERIFIER_* rather than blaming the agent."""
        with tempfile.TemporaryDirectory() as tmp:
            # Case A: Verifier checks extra hidden file secret_metric.json not mentioned in instruction.md
            task_dir_a = Path(tmp) / "task_hidden_file"
            (task_dir_a / "tests").mkdir(parents=True)
            (task_dir_a / "instruction.md").write_text(
                "Compute energy and write `results.json` with key `energy`.",
                encoding="utf-8",
            )
            (task_dir_a / "tests" / "verify.py").write_text(
                "import json\n"
                "data = json.load(open('results.json'))\n"
                "secret = json.load(open('secret_metric.json'))\n"
                "assert 'energy' in data\n",
                encoding="utf-8",
            )
            job_dir_a = Path(tmp) / "jobs" / "hidden-file-job"
            trial_a = job_dir_a / "trial_1"
            (trial_a / "agent").mkdir(parents=True)
            (trial_a / "verifier").mkdir(parents=True)
            (trial_a / "result.json").write_text(
                json.dumps({"trial_name": "trial_1", "reward": 0.0}), encoding="utf-8"
            )
            (trial_a / "results.json").write_text(json.dumps({"energy": -10.5}), encoding="utf-8")
            (trial_a / "verifier" / "reward.txt").write_text("0\n", encoding="utf-8")
            (trial_a / "verifier" / "verify.log").write_text(
                "FileNotFoundError: [Errno 2] No such file or directory: 'secret_metric.json'\n",
                encoding="utf-8",
            )
            (trial_a / "agent" / "trajectory.jsonl").write_text(
                json.dumps({"role": "assistant", "content": "Wrote results.json"}) + "\n",
                encoding="utf-8",
            )

            disc_a = discover_all(job_path=job_dir_a, task_path=task_dir_a)
            # Also verifies trajectory.jsonl is discovered as art:trajectory_json
            art_ids_a = {a["artifact_id"] for a in disc_a["trials"][0]["artifacts"]}
            self.assertIn("art:trajectory_json", art_ids_a)

            ev_a, an_a, rep_a, _ = analyze_single_trial(disc_a["trials"][0], job_dir=job_dir_a)
            self.assertEqual(an_a["primary_root_cause"]["code"], "VERIFIER_HIDDEN_CONTRACT")
            self.assertEqual(an_a["primary_root_cause"]["category"], "verifier")
            self.assertEqual(validate_all(ev_a, an_a, rep_a), (True, []))

            # Case B: Verifier checks extra hidden key 'virial_stress' not mentioned in instruction.md
            task_dir_b = Path(tmp) / "task_hidden_key"
            (task_dir_b / "tests").mkdir(parents=True)
            (task_dir_b / "instruction.md").write_text(
                "Write `results.json` containing `total_energy`.",
                encoding="utf-8",
            )
            (task_dir_b / "tests" / "verify.py").write_text(
                "import json\n"
                "data = json.load(open('results.json'))\n"
                "assert data['virial_stress'] < 0.1\n",
                encoding="utf-8",
            )
            job_dir_b = Path(tmp) / "jobs" / "hidden-key-job"
            trial_b = job_dir_b / "trial_1"
            (trial_b / "agent").mkdir(parents=True)
            (trial_b / "verifier").mkdir(parents=True)
            (trial_b / "result.json").write_text(
                json.dumps({"trial_name": "trial_1", "reward": 0.0}), encoding="utf-8"
            )
            (trial_b / "results.json").write_text(
                json.dumps({"total_energy": -10.5}), encoding="utf-8"
            )
            (trial_b / "verifier" / "reward.txt").write_text("0\n", encoding="utf-8")
            (trial_b / "verifier" / "verify.log").write_text(
                "KeyError: 'virial_stress'\n",
                encoding="utf-8",
            )
            (trial_b / "agent" / "trajectory.json").write_text(
                json.dumps({"steps": [{"role": "assistant", "content": "Done"}]}),
                encoding="utf-8",
            )

            disc_b = discover_all(job_path=job_dir_b, task_path=task_dir_b)
            ev_b, an_b, rep_b, _ = analyze_single_trial(disc_b["trials"][0], job_dir=job_dir_b)
            self.assertEqual(an_b["primary_root_cause"]["code"], "VERIFIER_SCHEMA_MISMATCH")
            self.assertEqual(an_b["primary_root_cause"]["category"], "verifier")
            self.assertEqual(validate_all(ev_b, an_b, rep_b), (True, []))

    def test_21_cli_replay_and_missing_report_exit_codes(self) -> None:
        """Verify --replay safe returns exit code 2 without partial writes and validate_analysis.py fails on missing --report."""
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp) / "jobs" / "cli-job"
            trial_dir = job_dir / "trial_1"
            trial_dir.mkdir(parents=True)
            (trial_dir / "result.json").write_text(
                json.dumps({"trial_name": "trial_1"}), encoding="utf-8"
            )
            out_dir = Path(tmp) / "out_replay"

            proc_replay = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT_DIR / "analyze_case.py"),
                    "--job",
                    str(job_dir),
                    "--replay",
                    "safe",
                    "--output",
                    str(out_dir),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc_replay.returncode, 2)
            self.assertFalse(out_dir.exists())

            # Run normal analysis to get valid evidence.json and analysis.json
            out_valid = Path(tmp) / "out_valid"
            proc_ok = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT_DIR / "analyze_case.py"),
                    "--job",
                    str(job_dir),
                    "--output",
                    str(out_valid),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc_ok.returncode, 0)

            # Passing a non-existent --report path to validate_analysis.py must exit 2
            proc_val = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT_DIR / "validate_analysis.py"),
                    "--evidence",
                    str(out_valid / "evidence.json"),
                    "--analysis",
                    str(out_valid / "analysis.json"),
                    "--report",
                    str(out_valid / "nonexistent_report.md"),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc_val.returncode, 2)

    def test_22_recovered_scf_error_does_not_blame_agent_and_batch_stats(self) -> None:
        """Verify an SCF error recovered in a later trajectory step is NOT blamed as the root cause, and Stage 10 batch_statistics works."""
        from analyze_case import compute_batch_statistics

        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp) / "jobs" / "recovered-scf-job"
            trial_dir = job_dir / "trial_1"
            (trial_dir / "agent").mkdir(parents=True)
            (trial_dir / "verifier").mkdir(parents=True)
            (trial_dir / "result.json").write_text(
                json.dumps({"trial_name": "trial_1", "reward": 0.0}), encoding="utf-8"
            )
            (trial_dir / "verifier" / "reward.txt").write_text("0\n", encoding="utf-8")
            (trial_dir / "verifier" / "verify.log").write_text(
                "Verification failed: unexpected post-processing condition\n", encoding="utf-8"
            )

            # Step 1: pw.x fails with SCF nonconvergence
            # Step 2: Agent adjusts mixing_beta and re-runs pw.x -> exit_code 0, JOB DONE (recovered!)
            traj = {
                "schema_version": "ATIF-v1.7",
                "steps": [
                    {
                        "step_id": 1,
                        "source": "agent",
                        "tool_calls": [
                            {
                                "tool_call_id": "c1",
                                "function_name": "Bash",
                                "arguments": {"command": "pw.x < scf.in > scf.out"},
                            }
                        ],
                        "observation": {
                            "results": [
                                {
                                    "source_call_id": "c1",
                                    "content": "Exit code: 1\nconvergence NOT achieved after 100 iterations: stopping",
                                }
                            ]
                        },
                    },
                    {
                        "step_id": 2,
                        "source": "agent",
                        "tool_calls": [
                            {
                                "tool_call_id": "c2",
                                "function_name": "Bash",
                                "arguments": {"command": "pw.x < scf_fixed.in > scf.out"},
                            }
                        ],
                        "observation": {
                            "results": [
                                {
                                    "source_call_id": "c2",
                                    "content": "Exit code: 0\n     convergence has been achieved in  18 iterations\n   JOB DONE.",
                                }
                            ]
                        },
                    },
                ],
            }
            (trial_dir / "agent" / "trajectory.json").write_text(json.dumps(traj), encoding="utf-8")

            disc = discover_all(job_path=job_dir)
            ev, an, rep, _ = analyze_single_trial(disc["trials"][0], job_dir=job_dir)

            # The SCF error in step 1 must be marked recovered=True and MUST NOT be blamed as AGENT_SCIENTIFIC_PARAMETER_SELECTION
            self.assertEqual(len(ev["scientific_observations"]), 1)
            self.assertTrue(ev["scientific_observations"][0]["recovered"])
            self.assertEqual(
                ev["scientific_observations"][0]["recovery_event_ref"], "trajectory:step:2:tool:0"
            )
            self.assertNotIn(
                an["primary_root_cause"]["code"],
                ("AGENT_SCIENTIFIC_PARAMETER_SELECTION", "AGENT_ERROR_DIAGNOSIS"),
            )
            self.assertEqual(an["primary_root_cause"]["code"], "AGENT_PREMATURE_TERMINATION")
            self.assertEqual(validate_all(ev, an, rep), (True, []))

            # Also verify Stage 10 batch_statistics computation
            stats = compute_batch_statistics(
                [
                    {
                        "trial_name": "trial_1",
                        "verdict": an["verdict"],
                        "agent_started": True,
                        "verifier_started": True,
                        "failure_stage": an["failure_stage"],
                        "category": an["primary_root_cause"]["category"],
                        "code": an["primary_root_cause"]["code"],
                    }
                ]
            )
            self.assertEqual(stats["total_trials"], 1)
            self.assertEqual(stats["agent_started_count"], 1)
            self.assertEqual(stats["verifier_started_count"], 1)
            self.assertEqual(stats["verdict_counts"]["failed"], 1)

    def test_23_numerical_divergence_abstains_when_instruction_missing(self) -> None:
        """P1: Missing instruction.md means incomplete prompt contract; must abstain from numerical defect."""
        from tests.archetype_builders import build_golden6_numerical_md_trajectory_divergence

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job_dir, task_dir = build_golden6_numerical_md_trajectory_divergence(root)
            # Remove instruction.md
            (task_dir / "instruction.md").unlink()

            disc = discover_all(job_path=job_dir, task_path=task_dir)
            ev, an, rep, _ = analyze_single_trial(disc["trials"][0], job_dir=job_dir)
            self.assertNotEqual(an["primary_root_cause"]["code"], "VERIFIER_TOLERANCE_TOO_STRICT")
            self.assertEqual(an["primary_root_cause"]["category"], "unknown")
            self.assertEqual(an["primary_root_cause"]["code"], "UNKNOWN_INSUFFICIENT_EVIDENCE")

    def test_24_numerical_divergence_abstains_when_verifier_only_comments_trajectory(self) -> None:
        """P1: Verifier mentioning trajectory_rmsd only in comments must not trigger numerical defect."""
        from tests.archetype_builders import build_golden6_numerical_md_trajectory_divergence

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job_dir, task_dir = build_golden6_numerical_md_trajectory_divergence(root)
            # Replace verify.py code so trajectory_rmsd is ONLY in comments
            (task_dir / "tests" / "verify.py").write_text(
                "import sys\n"
                "# Note: We do not check trajectory_rmsd here, instantaneous_position or coord_rmsd\n"
                "print('Checking completed')\n",
                encoding="utf-8",
            )

            disc = discover_all(job_path=job_dir, task_path=task_dir)
            ev, an, rep, _ = analyze_single_trial(disc["trials"][0], job_dir=job_dir)
            self.assertNotEqual(an["primary_root_cause"]["code"], "VERIFIER_TOLERANCE_TOO_STRICT")

    def test_25_numerical_divergence_abstains_when_verifier_only_has_trajectory_in_assert_msg(
        self,
    ) -> None:
        """P1: Verifier mentioning trajectory_rmsd only in assert message must not trigger numerical defect."""
        from tests.archetype_builders import build_golden6_numerical_md_trajectory_divergence

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job_dir, task_dir = build_golden6_numerical_md_trajectory_divergence(root)
            # Replace verify.py code: output_exists condition, trajectory_rmsd is ONLY in error message!
            (task_dir / "tests" / "verify.py").write_text(
                "output_exists = False\n"
                "assert output_exists, 'trajectory_rmsd=0.45 exceeds 0.01 tolerance'\n",
                encoding="utf-8",
            )

            disc = discover_all(job_path=job_dir, task_path=task_dir)
            ev, an, rep, _ = analyze_single_trial(disc["trials"][0], job_dir=job_dir)
            self.assertNotEqual(an["primary_root_cause"]["code"], "VERIFIER_TOLERANCE_TOO_STRICT")
            self.assertEqual(an["primary_root_cause"]["category"], "unknown")
            self.assertEqual(an["primary_root_cause"]["code"], "UNKNOWN_INSUFFICIENT_EVIDENCE")

    def test_26_verifier_ast_visitor_ignores_error_strings_and_membership(self) -> None:
        """P1: Unit tests for VerifierASTVisitor condition vs message separation and operator filtering."""
        import ast
        from audit_contract import VerifierASTVisitor

        # 1. Assert message string must NOT trigger trajectory assertion
        code1 = "assert file_exists, 'trajectory_rmsd exceeded threshold'"
        v1 = VerifierASTVisitor()
        v1.visit(ast.parse(code1))
        self.assertFalse(v1.has_instantaneous_trajectory_assertion)

        # 2. String membership check ("..." in log) must NOT trigger trajectory assertion
        code2 = "if 'trajectory_rmsd' in log_output:\n    pass"
        v2 = VerifierASTVisitor()
        v2.visit(ast.parse(code2))
        self.assertFalse(v2.has_instantaneous_trajectory_assertion)

        # 3. Method call error message argument must NOT trigger
        code3 = (
            "self.assertTrue(is_valid, msg='trajectory_rmsd is too large')\n"
            "self.assertEqual(status, 'OK', 'trajectory_rmsd was bad')\n"
        )
        v3 = VerifierASTVisitor()
        v3.visit(ast.parse(code3))
        self.assertFalse(v3.has_instantaneous_trajectory_assertion)

        # 4. Actual trajectory comparison in condition MUST trigger
        code4 = "assert trajectory_rmsd < 0.05"
        v4 = VerifierASTVisitor()
        v4.visit(ast.parse(code4))
        self.assertTrue(v4.has_instantaneous_trajectory_assertion)

        # 5. Attribute in comparison MUST trigger
        code5 = "assert self.coord_rmsd < 0.1"
        v5 = VerifierASTVisitor()
        v5.visit(ast.parse(code5))
        self.assertTrue(v5.has_instantaneous_trajectory_assertion)

        # 7. Dictionary .get() lookup in comparison MUST trigger
        code7 = "assert metrics.get('trajectory_rmsd') < 0.05"
        v7 = VerifierASTVisitor()
        v7.visit(ast.parse(code7))
        self.assertTrue(v7.has_instantaneous_trajectory_assertion)

        # 8. assert_array_equal call MUST trigger
        code8 = "np.testing.assert_array_equal(trajectory_rmsd, expected_rmsd)"
        v8 = VerifierASTVisitor()
        v8.visit(ast.parse(code8))
        self.assertTrue(v8.has_instantaneous_trajectory_assertion)

    def test_27_causally_bound_numerical_failure_rejects_missing_file_pytest_header(self) -> None:
        """P1: Verifier failure that is FileNotFoundError/KeyError must NOT be bound to numerical tolerance defect."""
        from audit_contract import is_causally_bound_numerical_failure

        # 1. Pytest test function name with 'trajectory_rmsd' but failed with FileNotFoundError
        log1 = (
            "FAILED tests/test_md.py::test_trajectory_rmsd - FileNotFoundError: [Errno 2] No such file: 'output.xyz'\n"
            "ensemble average matches reference\n"
        )
        self.assertFalse(is_causally_bound_numerical_failure(log1))

        # 2. Pytest failed with KeyError
        log2 = (
            "FAILED tests/test_md.py::test_trajectory_rmsd - KeyError: 'trajectory_rmsd'\n"
            "ensemble average matches reference\n"
        )
        self.assertFalse(is_causally_bound_numerical_failure(log2))

        # 3. Genuine numerical tolerance check failure MUST bind
        log3 = (
            "FAILED tests/test_md.py::test_trajectory_rmsd - AssertionError: trajectory_rmsd = 0.45 > 0.01 tolerance exceeded\n"
            "ensemble average matches reference\n"
        )
        self.assertTrue(is_causally_bound_numerical_failure(log3))

        # 4. Standard FAIL line with comparison MUST bind
        log4 = "FAIL: instantaneous_position trajectory_rmsd=0.45 > 0.01 (ensemble average matches)"
        self.assertTrue(is_causally_bound_numerical_failure(log4))

    def test_28_causally_bound_numerical_failure_multiline_blocks(self) -> None:
        """P1: Multi-line pytest and structured error messages must be causally bound to numerical defects."""
        from audit_contract import is_causally_bound_numerical_failure

        # 1. Multi-line pytest traceback with E / > markers and where clause
        log_pytest = (
            "________________ test_md_trajectory ________________\n"
            ">       assert trajectory_rmsd < 0.01\n"
            "E       AssertionError: assert 0.45 < 0.01\n"
            "E        +  where 0.45 = trajectory_rmsd\n"
            "ensemble average matches reference\n"
        )
        self.assertTrue(is_causally_bound_numerical_failure(log_pytest))

        # 2. Multi-line structured error report
        log_structured = (
            "AssertionError: Instantaneous coordinate threshold exceeded!\n"
            "  calculated: 0.45\n"
            "  threshold: 0.01\n"
            "conserved quantity matches baseline\n"
        )
        self.assertTrue(is_causally_bound_numerical_failure(log_structured))

        # 3. Multi-line failure that is actually a missing file must NOT bind
        log_missing = (
            "________________ test_md_trajectory ________________\n"
            ">       assert os.path.exists(output_file)\n"
            "E       AssertionError: FileNotFoundError: output.xyz not found\n"
            "ensemble average matches reference\n"
        )
        self.assertFalse(is_causally_bound_numerical_failure(log_missing))


if __name__ == "__main__":
    unittest.main()
