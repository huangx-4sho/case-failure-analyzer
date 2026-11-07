#!/usr/bin/env python3
"""
Normalized verifier report tests (`tests/test_verifier_report.py`)

Harbor/CompChemWorld graders emit at least five incompatible verifier report
schemas. These tests pin the flattening contract used by evidence collection,
Gate 6 (named-check preemption), the validator hard rule, report rendering and
the three-state batch board.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
SCRIPT_DIR = ROOT_DIR / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from normalize_verifier_report import (
    build_check_stats_from_report,
    build_verifier_report,
)


def _write(trial: Path, relpath: str, payload: object) -> None:
    target = trial / "verifier" / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


class VerifierReportNormalizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.trial = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_no_verifier_directory_returns_none(self) -> None:
        self.assertIsNone(build_verifier_report(self.trial))
        self.assertIsNone(build_verifier_report(None))

    def test_layered_report_exposes_named_check_and_deviation(self) -> None:
        """33-wpd-loop trial 215 shape: `core/verify.json` + envelope."""
        _write(
            self.trial,
            "verify.json",
            {"status": "FAIL", "reward": 0.0, "score": 0.0, "core_report": "core/verify.json"},
        )
        _write(
            self.trial,
            "core/verify.json",
            {
                "status": "SCORED",
                "reward": 0.25,
                "core_failures": ["coordination_distances_match_derived_reference"],
                "layers": {
                    "L1": [{"name": "deliverable_present", "ok": True, "detail": "ok"}],
                    "L3": [
                        {"name": "loop_state_consistent", "ok": True},
                        {
                            "name": "coordination_distances_match_derived_reference",
                            "ok": False,
                            "core": True,
                            "detail": "5/25 distances within +/-0.05 A; worst deviation 3.326 A",
                        },
                    ],
                },
            },
        )
        report = build_verifier_report(self.trial)
        self.assertIsNotNone(report)
        assert report is not None
        self.assertEqual(report["verdict"], "fail")
        self.assertEqual(report["primary_report"], "verify.json")
        self.assertEqual(report["failed_check_count"], 1)
        self.assertEqual(report["passed_check_count"], 2)
        self.assertIn("envelope", report["schema_family"])
        self.assertIn("layered", report["schema_family"])
        self.assertIn("3.326", report["summary"])
        self.assertIn("coordination_distances_match_derived_reference", report["summary"])
        self.assertEqual(report["diagnosability"], "normal")
        self.assertTrue(report["scorable"])
        self.assertEqual(
            report["core_failures"], ["coordination_distances_match_derived_reference"]
        )

    def test_envelope_with_embedded_scientific_report_and_veto(self) -> None:
        """35-binding-pose-metad shape: `scientific_report` embedded + veto."""
        layered = {
            "status": "ACADEMIC_INTEGRITY_VETO",
            "core_failures": ["source_paired_effect_and_inference"],
            "layers": {
                "L3": [
                    {"name": "source_paired_effect_and_inference", "ok": False, "core": True},
                ]
            },
        }
        _write(
            self.trial,
            "verify.json",
            {
                "schema_version": "ccw35-verifier-report-1",
                "status": "SCORED",
                "scientific_status": "FAIL",
                "scientific_verifier_status": "ACADEMIC_INTEGRITY_VETO",
                "reward": 0.0,
                "failure_origin": {
                    "role": "candidate",
                    "reason_code": "SCIENTIFIC_CRITERIA_FAILED",
                },
                "scientific_report": layered,
            },
        )
        _write(self.trial, "scientific/verify.json", layered)
        report = build_verifier_report(self.trial)
        assert report is not None
        self.assertEqual(report["verdict"], "fail")
        self.assertEqual(report["failure_origin"]["reason_code"], "SCIENTIFIC_CRITERIA_FAILED")
        # The embedded copy and the standalone file must not double-count.
        self.assertEqual(report["failed_check_count"], 1)
        self.assertEqual(report["core_failures"], ["source_paired_effect_and_inference"])

    def test_criteria_report(self) -> None:
        """32-qeq-electrostatics shape: `criteria` list with level/status."""
        _write(
            self.trial,
            "raw-verifier.json",
            {
                "preflight": "VALID",
                "overall": "FAIL",
                "criteria": [
                    {"criterion": "frozen_input_mapping", "level": "L1", "status": "PASS"},
                    {
                        "criterion": "numeric_setting",
                        "level": "L2",
                        "status": "FAIL",
                        "summary": "invalid numeric setting at primary_01",
                    },
                ],
            },
        )
        report = build_verifier_report(self.trial)
        assert report is not None
        self.assertIn("criteria", report["schema_family"])
        self.assertEqual(report["failed_checks"][0]["layer"], "L2")
        self.assertEqual(report["failed_checks"][0]["name"], "numeric_setting")
        self.assertEqual(report["verdict"], "fail")

    def test_flat_checks_mapping(self) -> None:
        """37-ml-reaction-space / 38-co2 shape: `checks` dict."""
        _write(
            self.trial,
            "native/verify.json",
            {
                "status": "PASS",
                "checks": {
                    "source_and_cycle_contract": "PASS",
                    "submitted_metrics_and_conclusions": "FAIL",
                    "errors": ["counterexample mismatch"],
                },
            },
        )
        report = build_verifier_report(self.trial)
        assert report is not None
        self.assertIn("checks", report["schema_family"])
        self.assertEqual(report["verdict"], "fail")
        names = {c["name"] for c in report["failed_checks"]}
        self.assertIn("submitted_metrics_and_conclusions", names)
        self.assertIn("errors", names)

    def test_primary_envelope_outranks_native_report(self) -> None:
        """`verify.json` is authoritative even though `science.json` sorts first."""
        _write(self.trial, "science.json", {"status": "SCIENTIFIC_FAIL", "reward": None})
        _write(
            self.trial,
            "verification.json",
            {"status": "SCORED", "scientific_status": "FAIL", "reward": 0},
        )
        _write(
            self.trial,
            "verify.json",
            {
                "status": "SCORED",
                "scientific_status": "FAIL",
                "reward": 0,
                "failure_origin": {"reason_code": "scientific_criterion_failed"},
            },
        )
        report = build_verifier_report(self.trial)
        assert report is not None
        self.assertEqual(report["primary_report"], "verify.json")
        self.assertEqual(report["reward"], 0)
        self.assertEqual(report["failure_origin"]["reason_code"], "scientific_criterion_failed")

    def test_terse_shared_message_is_flagged(self) -> None:
        """Task 208 pattern: four criteria, one uninformative message."""
        _write(
            self.trial,
            "verify.json",
            {
                "status": "SCORED",
                "scientific_status": "FAIL",
                "reward": 0.0,
                "criteria": [
                    {
                        "criterion": f"primary_0{i}",
                        "level": "L2",
                        "status": "FAIL",
                        "summary": "invalid numeric setting at primary_01",
                    }
                    for i in range(1, 5)
                ],
            },
        )
        report = build_verifier_report(self.trial)
        assert report is not None
        self.assertEqual(report["diagnosability"], "low")
        self.assertTrue(
            any("terse" in r for r in report["diagnosability_reasons"]),
            report["diagnosability_reasons"],
        )

    def test_malformed_json_is_skipped_without_raising(self) -> None:
        (self.trial / "verifier").mkdir(parents=True, exist_ok=True)
        (self.trial / "verifier" / "broken.json").write_text("{not json", encoding="utf-8")
        _write(self.trial, "verify.json", {"status": "FAIL", "reward": 0.0})
        report = build_verifier_report(self.trial)
        assert report is not None
        self.assertEqual(report["verdict"], "fail")
        self.assertTrue(any("broken.json" in p for p in report["skipped_files"]))

    def test_oversized_file_is_skipped(self) -> None:
        _write(self.trial, "verify.json", {"status": "PASS", "reward": 1.0})
        _write(self.trial, "huge.json", {"status": "FAIL", "padding": "x" * 500})
        report = build_verifier_report(self.trial, max_report_bytes=200)
        assert report is not None
        self.assertEqual(report["verdict"], "pass")
        self.assertTrue(any("huge.json" in p for p in report["skipped_files"]))

    def test_check_stats_bridge_shape(self) -> None:
        report = {
            "passed_check_count": 12,
            "failed_checks": [{"name": "a"}, {"name": "b"}],
            "failed_check_count": 2,
            "failed_check_summaries": ["[L3] a", "[L3] b"],
        }
        stats = build_check_stats_from_report(report)
        self.assertEqual(stats["total_checks"], 14)
        self.assertEqual(stats["failed_checks"], 2)
        self.assertFalse(stats["single_failure"])
        self.assertEqual(stats["source"], "verifier_report")

    def test_raw_verifier_criteria_evidence_preservation(self) -> None:
        """Preserve structured criteria[].evidence dicts (recomputed_eV, selected)."""
        _write(
            self.trial,
            "raw-verifier.json",
            {
                "overall": "FAIL",
                "criteria": [
                    {
                        "criterion": "energy_parameters",
                        "level": "L3",
                        "status": "FAIL",
                        "summary": "parameters differed from recomputed values",
                        "evidence": {
                            "recomputed_eV": [7.482295140671387, 15.02, 11.25, 3.77],
                            "selected": {"0": "cu0_doublet", "1": "cu1_singlet"},
                        },
                    }
                ],
            },
        )
        report = build_verifier_report(self.trial)
        assert report is not None
        self.assertEqual(report["failed_check_count"], 1)
        fc = report["failed_checks"][0]
        self.assertEqual(fc["name"], "energy_parameters")
        self.assertEqual(fc["failure_kind"], "numerical")
        self.assertIn("evidence", fc)
        self.assertEqual(fc["evidence"]["recomputed_eV"][0], 7.482295140671387)
        self.assertEqual(fc["evidence"]["selected"]["0"], "cu0_doublet")
        self.assertEqual(report["diagnosability"], "normal")

    def test_no_false_low_diagnosability_on_schema_or_id(self) -> None:
        """Schema/ID failures without numbers must not get false low diagnosability."""
        _write(
            self.trial,
            "native/verify.json",
            {
                "status": "FAIL",
                "detail": "atom_to_site uses unknown reference IDs",
            },
        )
        report = build_verifier_report(self.trial)
        assert report is not None
        self.assertEqual(report["diagnosability"], "normal")
        self.assertEqual(report["diagnosability_reasons"], [])
        self.assertEqual(report["failed_checks"][0]["failure_kind"], "schema_or_id")

    def test_no_false_low_diagnosability_when_evidence_has_numeric_arrays(self) -> None:
        """Checks without digits in detail but with numbers in evidence are diagnosable."""
        _write(
            self.trial,
            "raw-verifier.json",
            {
                "overall": "FAIL",
                "criteria": [
                    {
                        "criterion": "energy_parameters",
                        "level": "L3",
                        "status": "FAIL",
                        "summary": "energy parameters calculation failed",
                        "evidence": {
                            "recomputed_eV": [7.482295140671387],
                        },
                    }
                ],
            },
        )
        report = build_verifier_report(self.trial)
        assert report is not None
        self.assertEqual(report["diagnosability"], "normal")
        self.assertNotIn(
            "failed check details contain no numerical deviation",
            report["diagnosability_reasons"],
        )

    def test_verification_coverage_and_coverage_limits_extraction(self) -> None:
        """Top-level verification_coverage and coverage_limits extracted from docs."""
        _write(
            self.trial,
            "native/verify.json",
            {
                "status": "FAIL",
                "verification_coverage": "independent_recomputation",
                "coverage_limits": [
                    "no long-range ewald check",
                    {"description": "spin unpolarized reference only"},
                ],
                "checks": {
                    "energy_parameters": "FAIL",
                },
            },
        )
        report = build_verifier_report(self.trial)
        assert report is not None
        self.assertEqual(report["verification_coverage"], "independent_recomputation")
        self.assertEqual(
            report["coverage_limits"],
            ["no long-range ewald check", "spin unpolarized reference only"],
        )

    def test_pair_verifier_evidence_with_delivered_artifacts(self) -> None:
        """Attach artifact_comparison to failed checks from delivered artifacts."""
        from extract_scientific_errors import pair_verifier_evidence_with_artifacts

        outputs = self.trial / "artifacts" / "app" / "outputs"
        outputs.mkdir(parents=True)
        (outputs / "parameters.json").write_text(
            json.dumps(
                {
                    "IP0_eV": 7.482295140652825,
                    "IP1_eV": 15.02,
                    "chi_plus1_eV": 11.25,
                    "J_plus1_eV": 3.77,
                }
            ),
            encoding="utf-8",
        )

        verifier_report = {
            "failed_checks": [
                {
                    "name": "energy_parameters",
                    "layer": "L3",
                    "ok": False,
                    "evidence": {
                        "recomputed_eV": [7.482295140671387, 15.020000000001],
                    },
                }
            ]
        }
        pair_verifier_evidence_with_artifacts(verifier_report, self.trial)
        fc = verifier_report["failed_checks"][0]
        self.assertIn("artifact_comparison", fc)
        self.assertGreaterEqual(len(fc["artifact_comparison"]), 1)
        comp = fc["artifact_comparison"][0]
        self.assertEqual(comp["metric"], "IP0_eV")
        self.assertEqual(comp["delivered"], 7.482295140652825)
        self.assertEqual(comp["recomputed"], 7.482295140671387)
        self.assertAlmostEqual(comp["abs_diff"], 1.8561999920198032e-11)
        self.assertEqual(comp["artifact_ref"], "art:artifacts/app/outputs/parameters.json")


class VerifierReportAttributionTests(unittest.TestCase):
    def test_named_check_failure_from_report_blocks_preemption(self) -> None:
        from attribution.context import build_context
        from attribution.trace import DecisionTrace

        evidence = {
            "runtime": {"verifier_started": True, "agent_started": True},
            "verifier_report": {"failed_checks": [{"name": "L3_check"}]},
            "behavioral_signals": [],
        }
        ctx = build_context(evidence, {}, DecisionTrace(gate_order=("gate6",)))
        self.assertTrue(ctx.named_check_failure)

    def test_enrichment_replaces_premature_termination(self) -> None:
        from analyze_case import _enrich_with_verifier_report

        analysis = {
            "failure_manifestation": {"type": "agent_execution_failed", "summary": "old"},
            "primary_root_cause": {
                "category": "agent",
                "code": "AGENT_PREMATURE_TERMINATION",
                "subtype": "premature_termination",
                "summary": "old",
            },
            "competing_hypotheses": [{"hypothesis_id": "H1", "category": "agent", "claim": "old"}],
            "recommended_actions": [{"action": "premature termination"}],
            "first_unrecovered_deviation": {"summary": "old"},
            "decision_trace": {},
        }
        evidence = {
            "verifier_report": {
                "summary": "[L3] distances: worst deviation 3.326 A",
                "failed_checks": [
                    {"layer": "L3", "name": "distances", "detail": "worst deviation 3.326 A"}
                ],
                "diagnosability": "normal",
            }
        }
        result = _enrich_with_verifier_report(analysis, evidence)
        self.assertEqual(result["primary_root_cause"]["code"], "AGENT_RESULT_VALIDATION")
        self.assertIn("3.326", result["failure_manifestation"]["summary"])
        self.assertEqual(
            result["failure_manifestation"]["named_failed_checks"][0]["name"], "distances"
        )

    def test_validator_rejects_premature_blame_with_named_checks(self) -> None:
        from validate_analysis import validate_all

        evidence = {
            "runtime": {"agent_started": True, "verifier_started": True},
            "verifier_report": {"failed_checks": [{"name": "L3_check"}]},
            "behavioral_signals": [
                {
                    "signal_id": "sig:premature_completion",
                    "signal_type": "premature_completion",
                }
            ],
            "artifacts": [],
            "timeline": [],
            "error_observations": [],
            "contract_observations": [],
            "verifier_observations": [],
            "scientific_observations": [],
            "missing_artifacts": [],
        }
        analysis = {
            "schema_version": "failure-analysis-v1",
            "case_id": "x",
            "verdict": "failed",
            "failure_stage": "agent_execution",
            "detection_stage": "verifier_execution",
            "first_unrecovered_deviation": {
                "status": "identified",
                "event_ref": "sig:premature_completion",
                "summary": "ended early",
            },
            "failure_manifestation": {"summary": "ended early"},
            "primary_root_cause": {
                "category": "agent",
                "code": "AGENT_PREMATURE_TERMINATION",
                "confidence": 0.5,
            },
            "contributing_factors": [],
            "competing_hypotheses": [
                {
                    "hypothesis_id": "H1",
                    "category": "agent",
                    "evidence_for": ["sig:premature_completion"],
                }
            ],
            "evidence_refs": ["sig:premature_completion"],
            "excluded_hypotheses": [],
            "recommended_actions": [],
            "skill_prescription": None,
        }
        ok, errors = validate_all(evidence, analysis)
        self.assertFalse(ok)
        self.assertTrue(any("NAMED_CHECK_PREMATURE_BLAME" in e for e in errors), errors)

    def test_report_renders_named_failed_checks(self) -> None:
        from render_report import _render_verifier_report_block

        evidence = {
            "verifier_report": {
                "schema_family": "envelope+layered",
                "verdict": "fail",
                "reward": 0.0,
                "primary_report": "verify.json",
                "failed_checks": [
                    {
                        "layer": "L3",
                        "name": "distances",
                        "detail": "worst deviation 3.326 A",
                        "core": True,
                        "source": "core/verify.json",
                    }
                ],
                "diagnosability": "normal",
            }
        }
        block = "\n".join(_render_verifier_report_block(evidence, lang="en"))
        self.assertIn("[L3] distances", block)
        self.assertIn("3.326", block)
        self.assertIn("core/verify.json", block)

    def test_check_stats_fallback_when_report_has_checks(self) -> None:
        report = {
            "checks": [
                {"name": "neutral_stationary", "ok": False, "detail": "differs"},
                {"name": "geometry", "ok": True, "detail": "ok"},
            ],
            "failed_checks": [
                {"name": "neutral_stationary", "ok": False, "detail": "differs"},
            ],
            "failed_check_count": 1,
        }
        stats = build_check_stats_from_report(report)
        self.assertIsNotNone(stats)
        self.assertEqual(stats["total_checks"], 2)
        self.assertEqual(stats["failed_checks"], 1)
        self.assertEqual(stats["passed_checks"], 1)
        self.assertTrue(stats["single_failure"])
        self.assertEqual(stats["failed_check_names"], ["neutral_stationary"])


class UnscoredVerifierFaultTests(unittest.TestCase):
    def test_structured_error_becomes_verifier_crash_observation(self) -> None:
        from analyze_case import _attach_unscored_verifier_fault

        report = {
            "schema_version": "verifier-report-1",
            "status": "ERROR",
            "scientific_status": "INCONCLUSIVE",
            "scorable": False,
            "reward": None,
            "eligible_for_success_ranking": False,
            "failure_origin": {"reason_code": "evaluator"},
            "messages": ["TypeError: string indices must be integers, not 'str'"],
            "summary": "TypeError: string indices mus",
        }
        observations = [
            {
                "obs_id": "ver:fail_log",
                "type": "verifier_fail_message",
                "summary": "Warning on use of the timeseries module",
                "matched_text": "Warning on use of the timeseries module ...",
                "source_file": "verifier/verify.log",
            }
        ]
        _attach_unscored_verifier_fault(report, observations)
        self.assertEqual(len(observations), 2)
        crash = observations[1]
        self.assertEqual(crash["obs_id"], "ver:internal_crash")
        self.assertEqual(crash["code"], "VERIFIER_RECOMPUTE_DEFECT")
        self.assertEqual(crash["failure_binding"], "direct")
        self.assertIn("TypeError", observations[0]["matched_text"])

        # Idempotent: never duplicate the crash observation.
        _attach_unscored_verifier_fault(report, observations)
        self.assertEqual(len(observations), 2)

    def test_scored_failure_does_not_create_crash(self) -> None:
        from analyze_case import _attach_unscored_verifier_fault

        report = {
            "status": "SCORED",
            "scientific_status": "FAIL",
            "scorable": True,
            "reward": 0.0,
            "eligible_for_success_ranking": True,
            "failure_origin": {"reason_code": "scientific_criterion_failed"},
            "messages": ["check failed"],
        }
        observations: list = []
        _attach_unscored_verifier_fault(report, observations)
        self.assertEqual(observations, [])


if __name__ == "__main__":
    unittest.main()
