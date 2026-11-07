#!/usr/bin/env python3
"""
Validator integration for verifier recompute-divergence observations
(`tests/test_validator_agent_evidence.py`)

Pins the two collector contracts that make Gate 4's deterministic
recompute-divergence path validatable end to end:

  * `collect_valid_evidence_ids` must register a scientific observation's
    `agent_ref` pointer, so an attribution citing the agent-side step resolves.
  * `collect_positive_agent_evidence_ids` must treat an unrecovered recompute
    divergence with `agent_reported_matches == true` as positive agent evidence
    even though the observation originates in the verifier log — the observation
    itself binds the divergence to the agent's delivered metric. The plain
    trajectory-origin rule stays untouched.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from validate_analysis import (  # noqa: E402
    collect_positive_agent_evidence_ids,
    collect_valid_evidence_ids,
)


def _divergence_observation(**overrides):
    obs = {
        "sci_id": "sci:recompute_divergence:1",
        "software": "verifier",
        "error_family": "verifier_recompute_divergence",
        "matched_text": "FAIL: reported max_force 0.02863 != recomputed 0.03666 (tol 0.001)",
        "source_ref": "verifier:verify.log:L1",
        "metric": "max_force",
        "reported_value": 0.02863,
        "recomputed_value": 0.03666,
        "tolerance": 0.001,
        "check_kind": "recompute_divergence",
        "agent_reported_value": 0.02863,
        "agent_ref": "trajectory:step:12",
        "agent_reported_matches": True,
        "discriminating": True,
        "recovered": False,
    }
    obs.update(overrides)
    return obs


class TestValidEvidenceIdsIncludesAgentRef(unittest.TestCase):
    def test_agent_ref_of_scientific_observation_is_resolvable(self) -> None:
        evidence = {"scientific_observations": [_divergence_observation()]}
        ids = collect_valid_evidence_ids(evidence)
        self.assertIn("sci:recompute_divergence:1", ids)
        self.assertIn("verifier:verify.log:L1", ids)
        self.assertIn("trajectory:step:12", ids)

    def test_agent_ref_absent_is_skipped(self) -> None:
        obs = _divergence_observation(agent_ref=None)
        ids = collect_valid_evidence_ids({"scientific_observations": [obs]})
        self.assertNotIn("None", ids)
        self.assertNotIn("trajectory:step:12", ids)


class TestPositiveAgentEvidenceFromCorroboratedDivergence(unittest.TestCase):
    def test_corroborated_verifier_side_observation_counts_as_positive(self) -> None:
        evidence = {"scientific_observations": [_divergence_observation()]}
        pos = collect_positive_agent_evidence_ids(evidence)
        self.assertIn("sci:recompute_divergence:1", pos)
        self.assertIn("verifier:verify.log:L1", pos)
        self.assertIn("trajectory:step:12", pos)

    def test_uncorroborated_verifier_side_observation_does_not_count(self) -> None:
        obs = _divergence_observation(
            agent_reported_matches=False,
            agent_reported_value=None,
            agent_ref=None,
        )
        pos = collect_positive_agent_evidence_ids({"scientific_observations": [obs]})
        self.assertNotIn("sci:recompute_divergence:1", pos)

    def test_recovered_divergence_does_not_count(self) -> None:
        obs = _divergence_observation(recovered=True)
        pos = collect_positive_agent_evidence_ids({"scientific_observations": [obs]})
        self.assertNotIn("sci:recompute_divergence:1", pos)

    def test_plain_trajectory_origin_rule_unchanged(self) -> None:
        obs = {
            "sci_id": "sci:1",
            "source_ref": "trajectory:step:5",
            "recovered": False,
        }
        pos = collect_positive_agent_evidence_ids({"scientific_observations": [obs]})
        self.assertIn("sci:1", pos)
        self.assertIn("trajectory:step:5", pos)

    def test_artifact_bound_delta_counts_as_positive(self) -> None:
        obs = _divergence_observation(
            is_delta_divergence=True,
            agent_reported_matches=False,
            agent_reported_value=0.898982845013923,
            agent_ref="trajectory:step:55",
            agent_artifact_ref="art:artifacts/app/outputs/charges.csv:L628",
        )
        evidence = {"scientific_observations": [obs]}
        self.assertIn(
            "art:artifacts/app/outputs/charges.csv:L628", collect_valid_evidence_ids(evidence)
        )
        pos = collect_positive_agent_evidence_ids(evidence)
        self.assertIn("sci:recompute_divergence:1", pos)
        self.assertIn("trajectory:step:55", pos)

    def test_delta_without_bound_value_does_not_count(self) -> None:
        obs = _divergence_observation(
            is_delta_divergence=True,
            agent_reported_matches=False,
            agent_reported_value=None,
            agent_ref=None,
            agent_artifact_ref=None,
        )
        pos = collect_positive_agent_evidence_ids({"scientific_observations": [obs]})
        self.assertNotIn("sci:recompute_divergence:1", pos)


def _validator_evidence(**overrides):
    evidence = {
        "case_id": "c",
        "artifacts": [{"artifact_id": "art:trial_result", "exists": True}],
        "runtime": {"agent_started": True, "verifier_started": True, "verdict": "failed"},
        "timeline": [{"event_id": "trajectory:step:1:tool:0"}],
        "error_observations": [],
        "contract_observations": [],
        "verifier_observations": [],
        "scientific_observations": [],
        "missing_artifacts": [],
        "behavioral_signals": [
            {
                "signal_id": "sig:s",
                "signal_type": "unverified_output",
                "event_ref": "trajectory:step:1:tool:0",
            }
        ],
    }
    evidence.update(overrides)
    return evidence


def _validator_analysis(**overrides):
    analysis = {
        "schema_version": "failure-analysis-v1",
        "case_id": "c",
        "verdict": "failed",
        "failure_stage": "agent_execution",
        "detection_stage": "verifier_execution",
        "first_unrecovered_deviation": {
            "status": "identified",
            "event_ref": "trajectory:step:1:tool:0",
            "summary": "delivered value was wrong",
        },
        "failure_manifestation": {"type": "verifier_check_failed", "summary": "check failed"},
        "primary_root_cause": {
            "category": "agent",
            "subtype": "result_validation",
            "code": "AGENT_RESULT_VALIDATION",
            "confidence": 0.8,
            "confidence_band": "high",
        },
        "contributing_factors": [],
        "competing_hypotheses": [
            {
                "hypothesis_id": "H1",
                "category": "agent",
                "claim": "delivered value was wrong",
                "evidence_for": ["sig:s"],
                "evidence_against": [],
            }
        ],
        "evidence_refs": ["sig:s"],
        "excluded_hypotheses": [],
        "recommended_actions": [],
        "skill_prescription": None,
    }
    analysis.update(overrides)
    return analysis


class TestNewValidatorIntegrityRules(unittest.TestCase):
    def test_raw_verifier_json_in_fud_is_rejected(self) -> None:
        from validate_analysis import validate_all

        analysis = _validator_analysis()
        analysis["first_unrecovered_deviation"]["summary"] = (
            '{"schema_version": "qeq-verifier-report-1", "message": "spin_selection: FAIL"}'
        )
        ok, errors = validate_all(_validator_evidence(), analysis)
        self.assertFalse(ok)
        self.assertTrue(any("RAW_VERIFIER_PAYLOAD" in e for e in errors), errors)

    def test_self_check_gap_requires_self_check_evidence(self) -> None:
        from validate_analysis import validate_all

        analysis = _validator_analysis()
        analysis["primary_root_cause"]["subtype"] = "self_check_gap"
        analysis["primary_root_cause"]["code"] = "AGENT_SELF_CHECK_GAP"
        ok, errors = validate_all(_validator_evidence(), analysis)
        self.assertTrue(any("SELF_CHECK_GAP_EVIDENCE" in e for e in errors), errors)

        evidence = _validator_evidence(
            causal_anchors={
                "self_check": {
                    "event_id": "trajectory:step:70:tool:0",
                    "uncovered_failed_checks": ["spin_selection"],
                }
            }
        )
        _, errors = validate_all(evidence, analysis)
        self.assertFalse(any("SELF_CHECK_GAP_EVIDENCE" in e for e in errors), errors)


class TestVerifierNotRunPrematureBlame(unittest.TestCase):
    def test_premature_completion_alone_fails_when_verifier_not_started(self) -> None:
        from validate_analysis import validate_all

        evidence = {
            "case_id": "test_case",
            "artifacts": [{"artifact_id": "art:1", "exists": True}],
            "runtime": {"agent_started": True, "verifier_started": False, "verdict": "unknown"},
            "timeline": [{"event_id": "step:1"}],
            "error_observations": [],
            "contract_observations": [],
            "verifier_observations": [],
            "scientific_observations": [],
            "missing_artifacts": [],
            "behavioral_signals": [
                {"signal_id": "sig:premature_completion", "signal_type": "premature_completion"}
            ],
        }
        analysis = {
            "schema_version": "failure-analysis-v1",
            "case_id": "test_case",
            "verdict": "failed",
            "failure_stage": "agent_execution",
            "detection_stage": "agent_execution",
            "first_unrecovered_deviation": {
                "status": "identified",
                "event_ref": "step:1",
                "summary": "Agent stopped prematurely",
            },
            "failure_manifestation": {"type": "agent_execution_failed", "summary": "premature"},
            "primary_root_cause": {
                "category": "agent",
                "subtype": "premature_termination",
                "code": "AGENT_PREMATURE_TERMINATION",
                "confidence": 0.8,
                "confidence_band": "high",
                "rationale": "Premature exit",
            },
            "contributing_factors": [],
            "competing_hypotheses": [
                {
                    "hypothesis_id": "H1",
                    "category": "agent",
                    "claim": "Agent stopped",
                    "evidence_for": ["sig:premature_completion"],
                    "evidence_against": [],
                }
            ],
            "evidence_refs": ["sig:premature_completion"],
            "excluded_hypotheses": [],
            "recommended_actions": [],
            "skill_prescription": {
                "recommended_skill": {"name": "test-skill"},
                "trigger": ["test"],
                "capability_gap": ["test"],
            },
        }
        is_valid, errors = validate_all(evidence, analysis)
        self.assertTrue(
            any("VERIFIER_NOT_RUN_PREMATURE_BLAME" in e for e in errors),
            f"Expected VERIFIER_NOT_RUN_PREMATURE_BLAME in errors: {errors}",
        )

    def test_premature_completion_allowed_when_verifier_started(self) -> None:
        from validate_analysis import validate_all

        evidence = {
            "case_id": "test_case",
            "artifacts": [{"artifact_id": "art:1", "exists": True}],
            "runtime": {"agent_started": True, "verifier_started": True, "verdict": "failed"},
            "timeline": [{"event_id": "step:1"}],
            "error_observations": [],
            "contract_observations": [],
            "verifier_observations": [],
            "scientific_observations": [],
            "missing_artifacts": [],
            "behavioral_signals": [
                {"signal_id": "sig:premature_completion", "signal_type": "premature_completion"}
            ],
        }
        analysis = {
            "schema_version": "failure-analysis-v1",
            "case_id": "test_case",
            "verdict": "failed",
            "failure_stage": "agent_execution",
            "detection_stage": "verifier_execution",
            "first_unrecovered_deviation": {
                "status": "identified",
                "event_ref": "step:1",
                "summary": "Agent stopped prematurely",
            },
            "failure_manifestation": {"type": "verifier_check_failed", "summary": "premature"},
            "primary_root_cause": {
                "category": "agent",
                "subtype": "premature_termination",
                "code": "AGENT_PREMATURE_TERMINATION",
                "confidence": 0.8,
                "confidence_band": "high",
                "rationale": "Premature exit",
            },
            "contributing_factors": [],
            "competing_hypotheses": [
                {
                    "hypothesis_id": "H1",
                    "category": "agent",
                    "claim": "Agent stopped",
                    "evidence_for": ["sig:premature_completion"],
                    "evidence_against": [],
                }
            ],
            "evidence_refs": ["sig:premature_completion"],
            "excluded_hypotheses": [],
            "recommended_actions": [],
            "skill_prescription": {
                "recommended_skill": {"name": "test-skill"},
                "trigger": ["test"],
                "capability_gap": ["test"],
            },
        }
        is_valid, errors = validate_all(evidence, analysis)
        self.assertFalse(
            any("VERIFIER_NOT_RUN_PREMATURE_BLAME" in e for e in errors),
            f"Unexpected VERIFIER_NOT_RUN_PREMATURE_BLAME in errors: {errors}",
        )


if __name__ == "__main__":
    unittest.main()
