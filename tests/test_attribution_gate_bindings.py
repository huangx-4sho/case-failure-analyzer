"""Regression tests for attribution boundaries and contextual runtime leads."""

import json
import tempfile
import unittest
from pathlib import Path

from tests.test_attribution_decisions import _agent_event, _evidence, _fail_log, _sig
from attribution.capabilities import reviewed_diagnosis
from attribution.engine import run_attribution_engine
from attribution.runtime_triage import build_runtime_triage
from extract_runtime_errors import extract_runtime_errors


class AttributionGateBindings(unittest.TestCase):
    def _retry_evidence(self, observation):
        retry = _agent_event(event_id="trajectory:step:8:tool:0", cmd="python analysis.py")
        retry.update(observation=observation, exit_code=1)
        return _evidence(
            behavioral_signals=[_sig("iterative_debugging_failure", event_ref=retry["event_id"])],
            timeline=[retry],
            verifier_observations=[_fail_log("FAIL: scientific_report field set differs")],
            verifier_report={
                "failed_checks": [{"name": "scientific_report", "detail": "field set differs"}],
            },
            metadata={"declared_deliverables": ["/app/final"]},
            causal_anchors={
                "deliverable_generation": {
                    "event_id": retry["event_id"],
                    "command": "python analysis.py --out /app/final",
                }
            },
        )

    def _attribute(self, evidence):
        return run_attribution_engine(evidence, {"candidate_hypotheses": []})

    def test_unrelated_debug_retry_does_not_explain_contract_failure(self):
        evidence = self._retry_evidence("TypeError: unsupported operand in experimental fit")
        analysis = self._attribute(evidence)
        self.assertEqual(analysis["primary_root_cause"]["category"], "unknown")

    def test_retry_reproducing_named_check_is_a_candidate_lead(self):
        evidence = self._retry_evidence("FAIL: scientific_report field set differs")
        analysis = self._attribute(evidence)
        self.assertEqual(analysis["causal_assessment"]["level"], "candidate_cause")
        self.assertEqual(analysis["primary_root_cause"]["category"], "agent")

    def test_replay_missing_file_defers_weak_agent_evidence(self):
        evidence = self._retry_evidence("TypeError: unrelated checker")
        evidence["behavioral_signals"] = [_sig("unverified_output")]
        evidence["verifier_report"]["failed_checks"] = [
            {"name": "diagnosis.md", "detail": "diagnosis.md is required"}
        ]
        evidence["contract_review"] = {
            "execution_scope_findings": [
                {
                    "subtype": "captured_deliverable_replay_reconstruction",
                    "status": "review_required",
                    "filename": "diagnosis.md",
                },
            ],
        }
        self.assertEqual(self._attribute(evidence)["primary_root_cause"]["category"], "unknown")


class RuntimeTriageTests(unittest.TestCase):
    def test_terminal_sdk_uses_final_turn_estimate(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = Path(tmp)
            trial = job / "trial"
            (trial / "agent").mkdir(parents=True)
            (trial / "result.json").write_text(
                json.dumps(
                    {
                        "agent_result": {},
                        "exception_info": {
                            "exception_type": "NonZeroAgentExitCodeError",
                            "exception_message": "Agent exited with status 1",
                        },
                    }
                )
            )
            estimates = [
                {"type": "system", "subtype": "thinking_tokens", "estimated_tokens": 29_000},
                {"type": "user"},
                {
                    "type": "system",
                    "subtype": "thinking_tokens",
                    "estimated_tokens": 24_037,
                    "uuid": "final-estimate",
                },
                {"type": "assistant"},
                {
                    "type": "result",
                    "is_error": True,
                    "api_error": "max_output_tokens",
                    "result": "Response exceeded the 32000 output token maximum.",
                },
            ]
            (trial / "agent/claude-code.txt").write_text(
                "\n".join(json.dumps(event) for event in estimates)
            )
            source = extract_runtime_errors(job, trial)["error_observations"][0]
            triage = build_runtime_triage(source, [], [])
        self.assertEqual(triage["terminal_thinking_estimate"]["estimated_tokens"], 24_037)
        self.assertEqual(triage["terminal_thinking_estimate"]["event_uuid"], "final-estimate")
        self.assertEqual(triage["output_token_ceiling"], 32_000)
        self.assertEqual(triage["status"], "review_required")

    def test_image_responses_are_required_for_context_management_lead(self):
        source = {"code": "INFRA_AGENT_AUTOCOMPACT_THRASHING", "error_id": "err:test"}
        actions = [
            {
                "event_id": f"trajectory:step:{i}:tool:0",
                "tool_name": "Read",
                "command": f"/app/figure{i}.png",
                "observation": "file missing",
            }
            for i in range(2)
        ]
        self.assertEqual(build_runtime_triage(source, actions, [])["leads"], [])
        for action in actions:
            action["observation"] = '{"type": "image", "source": {"type": "base64"}}'
        lead = build_runtime_triage(source, actions, [])["leads"][0]
        self.assertEqual(lead["mechanism"], "context_overflow_by_large_read")
        self.assertEqual(lead["evidence_refs"], [a["event_id"] for a in actions])

    def test_short_timeout_does_not_imply_unreasonable_case_budget(self):
        source = {"code": "INFRA_AGENT_TIMEOUT", "error_id": "err:test", "timeout_seconds": 30}
        data = build_runtime_triage(source, [], [])
        self.assertEqual(data["timeout_seconds"], 30)
        self.assertEqual(data["status"], "review_required")
        self.assertEqual(data["leads"][0]["mechanism"], "configured_budget_vs_workload")


class MechanismTagTests(unittest.TestCase):
    def test_reviewed_tag_matches_the_same_decision_and_dimension(self):
        event = "trajectory:step:8:tool:0"
        decision = {
            "kind": "execution",
            "event_ref": event,
            "execution_binding": {"evidence_refs": [event]},
            "basis": {"evidence_refs": ["art:task"]},
            "outcome": {"evidence_refs": ["ver:fail_log"]},
        }
        annotation = {
            "dimension": "observable_definition",
            "mechanism_tag": "norm_or_metric_mismatch",
            "rationale_zh": "代码采用分量绝对值。",
            "rationale_en": "The code applied per-component absolute values.",
            "evidence_refs": [event, "art:task", "ver:fail_log"],
        }
        result = reviewed_diagnosis(annotation, decision, "evidence_supported_cause", {event})
        self.assertEqual(result["status"], "supported_in_trial")
        self.assertEqual(result["mechanism_tag"], "norm_or_metric_mismatch")
        annotation["mechanism_tag"] = "context_overflow_by_large_read"
        with self.assertRaisesRegex(ValueError, "mechanism_tag"):
            reviewed_diagnosis(annotation, decision, "evidence_supported_cause", {event})
