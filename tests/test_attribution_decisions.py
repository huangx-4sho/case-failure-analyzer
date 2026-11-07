#!/usr/bin/env python3
"""
Attribution decision table (`tests/test_attribution_decisions.py`)

Drives `run_attribution_engine` directly with hand-built evidence — no filesystem,
no fixture tree — and pins which gate each evidence shape selects, plus the
subcase within Gate 6. This is the payoff of splitting the gates out: the
decision is now a pure function of one dict.

The rows encode the documented Hard Rules, not merely current behavior:

  * rule 3  - `agent_started == false` must yield `infra` (with infra evidence) or
              `unknown` (without), and must NEVER yield `agent`
  * rule 4  - `agent` requires positive evidence; `reward == 0` alone is not enough
  * rule 5  - a verifier hazard needs `failure_binding == "direct"` to blame `verifier`
  * rule 1  - a bare keyword must not become a root cause

It also closes three branches that had no test coverage at all before: rule 3b
(agent never started, no infra evidence), and the validator's FALSE_AGENT_BLAME
and POSITIVE_AGENT_EVIDENCE violation paths.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_DIR = REPO_ROOT / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from attribution.engine import GATE_ORDER, run_attribution_engine
from attribution.schema import OUTCOME_KEYS, make_attribution
from confidence import compute_evidence_confidence
from discover_artifacts import discover_all
from verify_check_stats import build_check_stats
from analyze_case import analyze_single_trial
from tests.archetype_builders import ARCHETYPES
from validate_analysis import validate_all


# ── evidence factories ───────────────────────────────────────────────────────


def _runtime(**kw: Any) -> Dict[str, Any]:
    base = {
        "agent_started": True,
        "verifier_started": True,
        "verification_status": "failed",
        "reward": 0.0,
        "started_at": "2026-01-01T00:00:00Z",
        "finished_at": "2026-01-01T00:10:00Z",
        "execution_status": "completed",
        "exit_status": "completed",
    }
    base.update(kw)
    return base


def _evidence(**kw: Any) -> Dict[str, Any]:
    base = {
        "case_id": "case-1",
        "trial_name": "trial_1",
        "runtime": _runtime(),
        "artifacts": [{"artifact_id": "art:trial_result", "exists": True}],
        "error_observations": [],
        "contract_observations": [],
        "verifier_observations": [],
        "scientific_observations": [],
        "behavioral_signals": [],
        "timeline": [],
    }
    base.update(kw)
    return base


def _fail_log(text: str) -> Dict[str, Any]:
    return {
        "obs_id": "ver:fail_log",
        "type": "verifier_fail_message",
        "matched_text": text,
        "summary": text,
    }


def _infra_error(code: str, **kw: Any) -> Dict[str, Any]:
    base = {
        "error_id": f"err:{code}",
        "code": code,
        "subtype": "container_runtime",
        "stage": "environment_build",
        "matched_text": f"fatal infra: {code}",
        "causal_candidate": True,
    }
    base.update(kw)
    return base


def _sci(family: str, **kw: Any) -> Dict[str, Any]:
    base = {
        "sci_id": f"sci:{family}",
        "software": "quantum-espresso",
        "error_family": family,
        "aliases": [],
        "matched_text": f"{family} reported by solver",
        "source_ref": "trajectory:step:1:tool:0",
        "recovered": False,
        "recovery_event_ref": None,
        "candidate_causes": ["cause_a", "cause_b"],
        "required_discriminating_evidence": ["check_a", "check_b"],
    }
    base.update(kw)
    return base


def _sig(signal_type: str, **kw: Any) -> Dict[str, Any]:
    base = {
        "signal_id": f"sig:{signal_type}",
        "signal_type": signal_type,
        "description": signal_type,
        "event_ref": "trajectory:step:1:tool:0",
    }
    base.update(kw)
    return base


def _contract(alignment: str, **kw: Any) -> Dict[str, Any]:
    base = {
        "contract_id": f"contract:{alignment}",
        "alignment": alignment,
        "item": "Ge.UPF",
        "details": f"contract observation ({alignment})",
    }
    base.update(kw)
    return base


def _recompute_divergence(**kw: Any) -> Dict[str, Any]:
    """
    Minimal `verifier_recompute_divergence` scientific observation per the
    extraction-layer contract: the verifier independently recomputed a metric
    the agent delivered and the values disagree, with an optional agent-side
    trajectory self-report for corroboration.
    """
    base = _sci(
        "verifier_recompute_divergence",
        sci_id="sci:recompute_divergence:1",
        matched_text="FAIL: reported max_force 0.02863 != recomputed 0.03666 (tol 0.001)",
        metric="max_force",
        reported_value=0.02863,
        recomputed_value=0.03666,
        tolerance=0.001,
        check_kind="recompute_divergence",
        agent_reported_value=0.02863,
        agent_ref="trajectory:step:7",
        agent_reported_matches=True,
        source_ref="trajectory:step:7:tool:0",
        discriminating=True,
    )
    base.update(kw)
    return base


def _agent_event(**kw: Any) -> Dict[str, Any]:
    base = {"event_id": "trajectory:step:9:tool:0", "actor": "agent", "action": "write"}
    base.update(kw)
    return base


# ── decision table ───────────────────────────────────────────────────────────
#
# row = (label, evidence, expected) where expected carries the fields that the
# documented rules actually constrain.

DECISION_TABLE: List[Tuple[str, Dict[str, Any], Dict[str, Any]]] = [
    (
        "gate0 via verification_status",
        _evidence(runtime=_runtime(verification_status="passed")),
        {
            "gate_id": "gate0_passed",
            "category": "none",
            "code": "NONE",
            "verdict": "passed",
            "failure_stage": "none",
        },
    ),
    (
        "gate0 via reward >= 1.0",
        _evidence(runtime=_runtime(verification_status=None, reward=1.0)),
        {
            "gate_id": "gate0_passed",
            "category": "none",
            "code": "NONE",
            "verdict": "passed",
            "failure_stage": "none",
        },
    ),
    (
        "gate1a network failure before agent start (rule 3a)",
        _evidence(
            runtime=_runtime(agent_started=False, verifier_started=False),
            error_observations=[_infra_error("INFRA_EXTERNAL_NETWORK")],
        ),
        {
            "gate_id": "gate1a_infra_prestartup",
            "category": "infra",
            "code": "INFRA_EXTERNAL_NETWORK",
            "verdict": "errored",
            "failure_stage": "environment_build",
        },
    ),
    (
        "gate1a generic container build failure before agent start",
        _evidence(
            runtime=_runtime(agent_started=False, verifier_started=False),
            error_observations=[_infra_error("INFRA_IMAGE_PULL_FAILED")],
        ),
        {
            "gate_id": "gate1a_infra_prestartup",
            "category": "infra",
            "code": "INFRA_IMAGE_PULL_FAILED",
            "verdict": "errored",
        },
    ),
    (
        "gate1b pre-startup with no infra evidence (rule 3b)",
        _evidence(runtime=_runtime(agent_started=False, verifier_started=False)),
        {
            "gate_id": "gate1b_unknown_no_evidence",
            "category": "unknown",
            "code": "UNKNOWN_INSUFFICIENT_EVIDENCE",
            "verdict": "errored",
            "failure_stage": "unknown",
        },
    ),
    (
        "gate1c runner artifact collection crash after agent execution",
        _evidence(
            runtime=_runtime(
                agent_started=True,
                verifier_started=False,
                verification_status="not_run",
                reward=None,
            ),
            error_observations=[
                {
                    "error_id": "err:collection",
                    "code": "INFRA_ARTIFACT_COLLECTION_CRASH",
                    "subtype": "artifact_collection_crash",
                    "stage": "runner",
                    "matched_text": (
                        "RuntimeError: Engine cp failed: Could not find the file "
                        "/app/final in container abc123"
                    ),
                    "causal_candidate": True,
                    "missing_deliverable": "/app/final",
                }
            ],
            missing_deliverables=["/app/final"],
            behavioral_signals=[_sig("premature_completion")],
        ),
        {
            "gate_id": "gate1c_runner_artifact_collection",
            "category": "infra",
            "code": "INFRA_ARTIFACT_COLLECTION_CRASH",
            "verdict": "errored",
            "failure_stage": "runner",
        },
    ),
    (
        "gate2 case contract defect",
        _evidence(
            contract_observations=[_contract("case_defect")],
            verifier_observations=[_fail_log("FAIL: missing asset")],
        ),
        {
            "gate_id": "gate2_case_definition",
            "category": "case",
            "code": "CASE_MISSING_ASSET",
            "verdict": "failed",
            "failure_stage": "agent_execution",
        },
    ),
    (
        "gate3 verifier internal crash (rule 5b)",
        _evidence(
            verifier_observations=[
                {
                    "obs_id": "ver:crash",
                    "type": "verifier_internal_crash",
                    "summary": "FileNotFoundError: /tmp/verify_tmp/refs.json",
                },
                _fail_log("FAIL: verifier crashed"),
            ],
        ),
        {
            "gate_id": "gate3_verifier_defect",
            "category": "verifier",
            "verdict": "failed",
            "failure_stage": "verifier_execution",
        },
    ),
    (
        "gate3 direct-bound parser hazard",
        _evidence(
            verifier_observations=[
                _fail_log("FAIL: could not parse 'VAL= -0.12345D+03'"),
                {
                    "obs_id": "ver:hazard:1",
                    "type": "parser_hazard",
                    "triggered": True,
                    "failure_binding": "direct",
                    "summary": "missing D exponent",
                },
            ],
        ),
        {
            "gate_id": "gate3_verifier_defect",
            "category": "verifier",
            "code": "VERIFIER_REGEX_OR_PARSER_DEFECT",
            "verdict": "failed",
        },
    ),
    (
        "gate3 abstains when a hazard is not causally bound (rule 5)",
        _evidence(
            verifier_observations=[
                _fail_log("FAIL: ENERGY mismatch: got 9.99E+02, expected reference 1.23D+03"),
                {
                    "obs_id": "ver:hazard:1",
                    "type": "parser_hazard",
                    "triggered": False,
                    "failure_binding": "none",
                    "summary": "unrelated D exponent",
                },
            ],
            timeline=[_agent_event()],
        ),
        {"not_gate_id": "gate3_verifier_defect", "not_category": "verifier"},
    ),
    (
        "gate4 structured numerical divergence",
        _evidence(
            contract_observations=[
                {
                    "contract_id": "contract:1",
                    "item": "trajectory:instantaneous_rmsd",
                    "alignment": "verifier_defect",
                    "prompt_requirement": "ensemble_consistency",
                    "verifier_requirement": "instantaneous_trajectory_rmsd",
                    "agent_output_status": "ensemble_matches_trajectory_diverges",
                    "details": "Verifier checked instantaneous trajectory rather than ensemble average",
                }
            ],
            verifier_observations=[
                _fail_log(
                    "FAIL: instantaneous_position trajectory_rmsd=0.45 > 0.01 "
                    "(ensemble average matches)"
                ),
                {
                    "obs_id": "ver:hazard:1",
                    "type": "tolerance_hazard",
                    "matched_text": "instantaneous_trajectory_rmsd",
                    "triggered": True,
                    "failure_binding": "direct",
                    "summary": "instantaneous trajectory comparison",
                },
            ],
        ),
        {
            "gate_id": "gate4_numerical_divergence",
            "category": "verifier",
            "code": "VERIFIER_TOLERANCE_TOO_STRICT",
            "verdict": "failed",
        },
    ),
    (
        "gate4 abstains when prompt specifies pointwise trajectory",
        _evidence(
            contract_observations=[
                {
                    "contract_id": "contract:1",
                    "item": "trajectory:instantaneous_rmsd",
                    "alignment": "verifier_defect",
                    "prompt_requirement": "pointwise_exact",
                    "verifier_requirement": "instantaneous_trajectory_rmsd",
                    "agent_output_status": "ensemble_matches_trajectory_diverges",
                    "details": "Prompt mandated pointwise trajectory",
                }
            ],
            verifier_observations=[
                _fail_log(
                    "FAIL: instantaneous_position trajectory_rmsd=0.45 > 0.01 "
                    "(ensemble average matches)"
                ),
            ],
        ),
        {"not_gate_id": "gate4_numerical_divergence", "not_category": "verifier"},
    ),
    (
        "gate4 abstains when instruction.md is missing (instruction_exists=False)",
        _evidence(
            prompt_contract={"instruction_exists": False, "specifies_pointwise_trajectory": None},
            verifier_observations=[
                _fail_log(
                    "FAIL: instantaneous_position trajectory_rmsd=0.45 > 0.01 "
                    "(ensemble average matches)"
                ),
                {
                    "obs_id": "ver:hazard:1",
                    "matched_text": "instantaneous_trajectory_rmsd",
                    "triggered": True,
                    "failure_binding": "direct",
                    "summary": "instantaneous trajectory comparison",
                },
            ],
        ),
        {"not_gate_id": "gate4_numerical_divergence", "not_category": "verifier"},
    ),
    (
        "gate4 abstains when contract loop is unproven",
        _evidence(
            verifier_observations=[
                _fail_log(
                    "FAIL: instantaneous_position trajectory_rmsd=0.45 > 0.01 "
                    "(ensemble average matches)"
                ),
            ],
        ),
        {"not_gate_id": "gate4_numerical_divergence", "not_category": "verifier"},
    ),
    (
        "gate4 abstains on a bare keyword (rule 1)",
        _evidence(verifier_observations=[_fail_log("FAIL: the run looks chaotic")]),
        {"not_gate_id": "gate4_numerical_divergence", "not_category": "verifier"},
    ),
    (
        "gate4 abstains when numerical metrics exist in log but failure check is unrelated",
        _evidence(
            verifier_observations=[
                _fail_log(
                    "INFO: instantaneous_position trajectory_rmsd=0.45 (ensemble average matches)\n"
                    "FAIL: results.json missing key 'energy'"
                )
            ]
        ),
        {
            "not_gate_id": "gate4_numerical_divergence",
            "category": "unknown",
            "code": "UNKNOWN_INSUFFICIENT_EVIDENCE",
        },
    ),
    (
        "gate4 abstains when trajectory diverges but ensemble statistics also fail",
        _evidence(
            verifier_observations=[
                _fail_log(
                    "FAIL: instantaneous_position trajectory_rmsd=0.45 > 0.01 (ensemble average diverged by 50%)"
                )
            ]
        ),
        {
            "not_gate_id": "gate4_numerical_divergence",
            "category": "unknown",
            "code": "UNKNOWN_INSUFFICIENT_EVIDENCE",
        },
    ),
    (
        "gate4 deterministic recompute divergence with agent corroboration and contract binding",
        _evidence(
            contract_observations=[
                _contract(
                    "consistent",
                    contract_id="contract:max_force",
                    item="results.json:values.max_force",
                )
            ],
            scientific_observations=[_recompute_divergence()],
            verifier_observations=[
                _fail_log("FAIL: reported max_force 0.02863 != recomputed 0.03666 (tol 0.001)")
            ],
            timeline=[_agent_event(event_id="trajectory:step:7")],
        ),
        {
            "gate_id": "gate4_numerical_divergence",
            "category": "agent",
            "code": "AGENT_RESULT_VALIDATION",
            "verdict": "failed",
            "failure_stage": "agent_execution",
        },
    ),
    (
        "gate4 abstains on recompute divergence without agent corroboration (gate5 takes over)",
        _evidence(
            scientific_observations=[
                _recompute_divergence(
                    agent_reported_value=None,
                    agent_ref=None,
                    agent_reported_matches=False,
                )
            ],
            verifier_observations=[
                _fail_log("FAIL: reported max_force 0.02863 != recomputed 0.03666 (tol 0.001)")
            ],
        ),
        {
            "gate_id": "gate5_insufficient_positive_evidence",
            "category": "unknown",
            "code": "UNKNOWN_INSUFFICIENT_EVIDENCE",
            "verdict": "failed",
            "failure_stage": "unknown",
        },
    ),
    (
        "gate4 abstains on a corroborated count_mismatch divergence",
        _evidence(
            contract_observations=[
                _contract(
                    "consistent",
                    contract_id="contract:n_molecules",
                    item="results.json:values.n_molecules",
                )
            ],
            scientific_observations=[
                _recompute_divergence(
                    sci_id="sci:recompute_divergence:2",
                    matched_text="FAIL: reported n_molecules 245 != recomputed 244 (tol 0)",
                    metric="n_molecules",
                    reported_value=245.0,
                    recomputed_value=244.0,
                    tolerance=0.0,
                    check_kind="count_mismatch",
                    source_ref="trajectory:step:5:tool:0",
                )
            ],
            verifier_observations=[
                _fail_log("FAIL: reported n_molecules 245 != recomputed 244 (tol 0)")
            ],
        ),
        {
            "gate_id": "gate5_insufficient_positive_evidence",
            "category": "unknown",
            "code": "UNKNOWN_INSUFFICIENT_EVIDENCE",
            "verdict": "failed",
        },
    ),
    (
        "gate4 abstains when the diverging metric cannot be bound to the task contract",
        _evidence(
            prompt_contract={"instruction_exists": True, "json_keys": ["energy"]},
            scientific_observations=[_recompute_divergence()],
            verifier_observations=[
                _fail_log("FAIL: reported max_force 0.02863 != recomputed 0.03666 (tol 0.001)")
            ],
        ),
        {
            "gate_id": "gate5_insufficient_positive_evidence",
            "category": "unknown",
            "code": "UNKNOWN_INSUFFICIENT_EVIDENCE",
            "verdict": "failed",
        },
    ),
    (
        "gate4 recompute divergence binds via verifier_selfcheck when no instruction text is available",
        _evidence(
            scientific_observations=[
                _recompute_divergence(
                    sci_id="sci:recompute_divergence:3",
                    matched_text="FAIL: fit_window reported 50 != reference 100",
                    metric="fit_window",
                    reported_value=50.0,
                    recomputed_value=100.0,
                    tolerance=0.0,
                    check_kind="reference_mismatch",
                    agent_ref="trajectory:step:4",
                    source_ref="trajectory:step:4:tool:0",
                )
            ],
            verifier_observations=[_fail_log("FAIL: fit_window reported 50 != reference 100")],
            timeline=[_agent_event(event_id="trajectory:step:4")],
        ),
        {
            "gate_id": "gate4_numerical_divergence",
            "category": "agent",
            "code": "AGENT_SCIENTIFIC_PARAMETER_SELECTION",
            "verdict": "failed",
            "failure_stage": "agent_execution",
        },
    ),
    (
        "gate4 ensemble path still matches when an uncorroborated recompute divergence is present",
        _evidence(
            contract_observations=[
                {
                    "contract_id": "contract:1",
                    "item": "trajectory:instantaneous_rmsd",
                    "alignment": "verifier_defect",
                    "prompt_requirement": "ensemble_consistency",
                    "verifier_requirement": "instantaneous_trajectory_rmsd",
                    "agent_output_status": "ensemble_matches_trajectory_diverges",
                    "details": "Verifier checked instantaneous trajectory rather than ensemble average",
                }
            ],
            scientific_observations=[
                _recompute_divergence(
                    agent_reported_value=None,
                    agent_ref=None,
                    agent_reported_matches=False,
                )
            ],
            verifier_observations=[
                _fail_log(
                    "FAIL: instantaneous_position trajectory_rmsd=0.45 > 0.01 "
                    "(ensemble average matches)"
                ),
                {
                    "obs_id": "ver:hazard:1",
                    "type": "tolerance_hazard",
                    "matched_text": "instantaneous_trajectory_rmsd",
                    "triggered": True,
                    "failure_binding": "direct",
                    "summary": "instantaneous trajectory comparison",
                },
            ],
        ),
        {
            "gate_id": "gate4_numerical_divergence",
            "category": "verifier",
            "code": "VERIFIER_TOLERANCE_TOO_STRICT",
            "verdict": "failed",
        },
    ),
    (
        "precedence: corroborated recompute divergence wins over the ensemble path",
        _evidence(
            contract_observations=[
                {
                    "contract_id": "contract:1",
                    "item": "trajectory:instantaneous_rmsd",
                    "alignment": "verifier_defect",
                    "prompt_requirement": "ensemble_consistency",
                    "verifier_requirement": "instantaneous_trajectory_rmsd",
                    "agent_output_status": "ensemble_matches_trajectory_diverges",
                    "details": "Verifier checked instantaneous trajectory rather than ensemble average",
                }
            ],
            scientific_observations=[_recompute_divergence()],
            verifier_observations=[
                _fail_log(
                    "FAIL: instantaneous_position trajectory_rmsd=0.45 > 0.01 "
                    "(ensemble average matches)"
                ),
                {
                    "obs_id": "ver:hazard:1",
                    "type": "tolerance_hazard",
                    "matched_text": "instantaneous_trajectory_rmsd",
                    "triggered": True,
                    "failure_binding": "direct",
                    "summary": "instantaneous trajectory comparison",
                },
            ],
            timeline=[_agent_event(event_id="trajectory:step:7")],
        ),
        {
            "gate_id": "gate4_numerical_divergence",
            "category": "agent",
            "code": "AGENT_RESULT_VALIDATION",
            "verdict": "failed",
        },
    ),
    (
        "gate5 failed with no positive agent evidence (rule 4)",
        _evidence(verifier_observations=[_fail_log("FAIL: agent output absent")]),
        {
            "gate_id": "gate5_insufficient_positive_evidence",
            "category": "unknown",
            "code": "UNKNOWN_INSUFFICIENT_EVIDENCE",
            "verdict": "failed",
            "failure_stage": "unknown",
        },
    ),
    (
        "gate6 subcase 5a unrecovered solver error + repeated action",
        _evidence(
            scientific_observations=[_sci("scf_convergence_not_achieved")],
            behavioral_signals=[_sig("repeated_failed_action")],
            verifier_observations=[_fail_log("FAIL: SCF not converged")],
        ),
        {
            "gate_id": "gate6_agent_primary",
            "subcase_id": "5a",
            "category": "agent",
            "code": "AGENT_ERROR_DIAGNOSIS",
            "failure_stage": "agent_execution",
        },
    ),
    (
        "gate6 subcase 5b missing potential, no dependency search",
        _evidence(
            scientific_observations=[_sci("basis_or_potential_missing")],
            verifier_observations=[_fail_log("FAIL: basis not found")],
        ),
        {
            "gate_id": "gate6_agent_primary",
            "subcase_id": "5b",
            "category": "agent",
            "code": "AGENT_PATH_OR_DEPENDENCY_DISCOVERY",
        },
    ),
    (
        "gate6 subcase 5c continuation mismatch",
        _evidence(
            scientific_observations=[_sci("restart_or_timestep_continuation_mismatch")],
            verifier_observations=[_fail_log("FAIL: continuation diverged")],
        ),
        {
            "gate_id": "gate6_agent_primary",
            "subcase_id": "5c",
            "category": "agent",
            "code": "AGENT_SCIENTIFIC_PARAMETER_SELECTION",
        },
    ),
    (
        "gate6 abstains on isolated other unrecovered solver family (rule 4)",
        _evidence(
            scientific_observations=[_sci("some_other_solver_family")],
            verifier_observations=[_fail_log("FAIL: solver error")],
        ),
        {
            "gate_id": "gate5_insufficient_positive_evidence",
            "category": "unknown",
            "code": "UNKNOWN_INSUFFICIENT_EVIDENCE",
        },
    ),
    (
        "gate6 subcase 5d premature completion",
        _evidence(
            behavioral_signals=[_sig("premature_completion")],
            verifier_observations=[_fail_log("FAIL: required output missing")],
        ),
        {
            "gate_id": "gate6_agent_primary",
            "subcase_id": "5d",
            "category": "agent",
            "code": "AGENT_PREMATURE_TERMINATION",
        },
    ),
    (
        "gate6 subcase 5d agent_mismatch contract",
        _evidence(
            contract_observations=[_contract("agent_mismatch")],
            verifier_observations=[_fail_log("FAIL: schema mismatch")],
        ),
        {
            "gate_id": "gate6_agent_primary",
            "subcase_id": "5d",
            "category": "agent",
            "code": "AGENT_TASK_UNDERSTANDING",
        },
    ),
    (
        "gate6 abstains when agent acted but no causal signals exist (rule 4)",
        _evidence(
            timeline=[_agent_event()],
            verifier_observations=[_fail_log("FAIL: ENERGY mismatch: got 1.0 expected 2.0")],
        ),
        {
            "gate_id": "gate5_insufficient_positive_evidence",
            "category": "unknown",
            "code": "UNKNOWN_INSUFFICIENT_EVIDENCE",
        },
    ),
    (
        "precedence: 5a wins over 5b when both apply",
        _evidence(
            scientific_observations=[_sci("basis_or_potential_missing")],
            behavioral_signals=[_sig("repeated_failed_action")],
            verifier_observations=[_fail_log("FAIL: basis not found")],
        ),
        {"gate_id": "gate6_agent_primary", "subcase_id": "5a", "code": "AGENT_ERROR_DIAGNOSIS"},
    ),
    (
        "precedence: 5b wins over 5c when both families present",
        _evidence(
            scientific_observations=[
                _sci("basis_or_potential_missing", sci_id="sci:basis"),
                _sci("restart_or_timestep_continuation_mismatch", sci_id="sci:restart"),
            ],
            verifier_observations=[_fail_log("FAIL: both")],
        ),
        {
            "gate_id": "gate6_agent_primary",
            "subcase_id": "5b",
            "code": "AGENT_PATH_OR_DEPENDENCY_DISCOVERY",
        },
    ),
]


class TestAttributionDecisionTable(unittest.TestCase):
    def test_decision_table(self) -> None:
        for label, evidence, expected in DECISION_TABLE:
            with self.subTest(row=label):
                analysis = run_attribution_engine(evidence, {"candidate_hypotheses": []})
                trace = analysis["decision_trace"]
                selected = trace["selected"]
                prc = analysis["primary_root_cause"]

                for key in (
                    "gate_id",
                    "subcase_id",
                    "category",
                    "code",
                    "verdict",
                    "failure_stage",
                ):
                    if key in expected:
                        actual = (
                            selected.get(key)
                            if key in ("gate_id", "subcase_id")
                            else (
                                prc.get(key) if key in ("category", "code") else analysis.get(key)
                            )
                        )
                        self.assertEqual(actual, expected[key], f"{label}: {key}")

                # negative assertions: the gate/category must NOT be selected
                if "not_gate_id" in expected:
                    self.assertNotEqual(selected["gate_id"], expected["not_gate_id"], label)
                if "not_category" in expected:
                    self.assertNotEqual(prc["category"], expected["not_category"], label)

    def test_rule_3_forbids_agent_when_agent_never_started(self) -> None:
        """The single most important invariant: never blame an agent that never ran."""
        for label, evidence, _ in DECISION_TABLE:
            if evidence["runtime"].get("agent_started"):
                continue
            with self.subTest(row=label):
                analysis = run_attribution_engine(evidence, {"candidate_hypotheses": []})
                self.assertNotEqual(
                    analysis["primary_root_cause"]["category"],
                    "agent",
                    f"{label}: blamed the agent although agent_started == false",
                )

    def test_output_contract_on_every_row(self) -> None:
        for label, evidence, _ in DECISION_TABLE:
            with self.subTest(row=label):
                analysis = run_attribution_engine(evidence, {"candidate_hypotheses": []})
                expected_keys = list(OUTCOME_KEYS) + ["decision_trace"]
                self.assertEqual(list(analysis.keys()), expected_keys, label)

                trace = analysis["decision_trace"]
                ids = [e["gate_id"] for e in trace["evaluated"]]
                self.assertEqual(
                    ids,
                    list(GATE_ORDER)[: len(ids)],
                    f"{label}: evaluated must be a GATE_ORDER prefix",
                )
                self.assertTrue(
                    trace["evaluated"][-1]["matched"],
                    f"{label}: last evaluated gate must be the match",
                )
                self.assertEqual(trace["selected"]["gate_id"], ids[-1], label)
                self.assertEqual(
                    trace["selected"]["code"], analysis["primary_root_cause"]["code"], label
                )

    def test_trace_is_json_serializable_and_bounded(self) -> None:
        for label, evidence, _ in DECISION_TABLE:
            with self.subTest(row=label):
                analysis = run_attribution_engine(evidence, {"candidate_hypotheses": []})
                text = json.dumps(analysis["decision_trace"])
                self.assertLess(len(text), 4000, f"{label}: trace is unexpectedly large")
                for entry in analysis["decision_trace"]["evaluated"]:
                    for value in entry["checks"].values():
                        self.assertIsInstance(value, (int, float, bool, str, type(None)), label)


class TestAttributionSchemaContract(unittest.TestCase):
    def test_missing_key_is_rejected(self) -> None:
        with self.assertRaises(TypeError):
            make_attribution(**{k: None for k in OUTCOME_KEYS[:-1]})

    def test_extra_key_is_rejected(self) -> None:
        with self.assertRaises(TypeError):
            make_attribution(**{k: None for k in OUTCOME_KEYS}, unexpected_key=1)

    def test_key_order_is_the_contract(self) -> None:
        built = make_attribution(**{k: k for k in OUTCOME_KEYS})
        self.assertEqual(list(built.keys()), list(OUTCOME_KEYS))

    def test_values_are_placed_by_reference(self) -> None:
        """Gates share one list object between `evidence_refs` and hypothesis evidence."""
        shared = ["a"]
        built = make_attribution(**{**{k: None for k in OUTCOME_KEYS}, "evidence_refs": shared})
        self.assertIs(built["evidence_refs"], shared)

    def test_all_gates_emit_the_contract(self) -> None:
        seen = set()
        for _, evidence, _ in DECISION_TABLE:
            analysis = run_attribution_engine(evidence, {"candidate_hypotheses": []})
            seen.add(analysis["decision_trace"]["selected"]["gate_id"])
            self.assertEqual(list(analysis.keys())[:15], list(OUTCOME_KEYS))
            self.assertEqual(analysis["schema_version"], "failure-analysis-v1")
        self.assertEqual(seen, set(GATE_ORDER), "decision table must exercise every gate")


class TestEngineRobustness(unittest.TestCase):
    def test_malformed_signal_entry_does_not_crash_on_a_passing_trial(self) -> None:
        """Hoisted derivations use .get(): a malformed signal must not break gate 0."""
        evidence = _evidence(
            runtime=_runtime(verification_status="passed"),
            behavioral_signals=[{"signal_id": "sig:broken"}],  # no signal_type
            verifier_observations=[{"matched_text": "no obs_id"}],
        )
        analysis = run_attribution_engine(evidence, {"candidate_hypotheses": []})
        self.assertEqual(analysis["verdict"], "passed")

    def test_scientific_recovered_error_is_not_blamed(self) -> None:
        evidence = _evidence(
            scientific_observations=[_sci("scf_convergence_not_achieved", recovered=True)],
            verifier_observations=[_fail_log("FAIL: post-processing condition")],
        )
        analysis = run_attribution_engine(evidence, {"candidate_hypotheses": []})
        self.assertNotEqual(analysis["primary_root_cause"]["category"], "agent")


class TestImportGraph(unittest.TestCase):
    def test_attribution_package_never_imports_analyze_case(self) -> None:
        """The dependency direction is one-way: analyze_case -> attribution."""
        offenders = []
        for path in sorted((SCRIPT_DIR / "attribution").rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            for lineno, line in enumerate(text.splitlines(), start=1):
                stripped = line.strip()
                if stripped.startswith(("import ", "from ")) and "analyze_case" in stripped:
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}")
        self.assertEqual(offenders, [], f"attribution modules importing analyze_case: {offenders}")


class TestValidatorHardRuleBranches(unittest.TestCase):
    """
    The validator's rule-violation branches for FALSE_AGENT_BLAME and
    POSITIVE_AGENT_EVIDENCE had no test that actually triggered them: the pipeline
    cannot produce those states, only a hand-authored analysis.json can.
    """

    def _base_pair(self) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        with tempfile.TemporaryDirectory() as tmp:
            job_dir, task_dir = ARCHETYPES["golden7_unknown_missing_logs"](Path(tmp))
            disc = discover_all(job_path=job_dir, task_path=task_dir)
            evidence, analysis, _, _ = analyze_single_trial(disc["trials"][0], job_dir=job_dir)
        return evidence, analysis

    def test_false_agent_blame_branch_fires(self) -> None:
        evidence, analysis = self._base_pair()
        self.assertFalse(evidence["runtime"]["agent_started"])
        analysis["primary_root_cause"]["category"] = "agent"
        analysis["primary_root_cause"]["code"] = "AGENT_ERROR_DIAGNOSIS"
        analysis["evidence_refs"] = ["sig:whatever"]
        ok, errors = validate_all(evidence, analysis, None)
        self.assertFalse(ok)
        self.assertTrue(any("FALSE_AGENT_BLAME" in e for e in errors), errors)

    def test_positive_agent_evidence_branch_fires(self) -> None:
        evidence, analysis = self._base_pair()
        evidence["runtime"]["agent_started"] = True
        analysis["primary_root_cause"]["category"] = "agent"
        analysis["primary_root_cause"]["code"] = "AGENT_RESULT_VALIDATION"
        analysis["evidence_refs"] = ["art:trial_result"]
        analysis["competing_hypotheses"] = [
            {
                "hypothesis_id": "H1",
                "category": "agent",
                "subtype": "result_validation",
                "claim": "x",
                "evidence_for": ["art:trial_result"],
                "evidence_against": [],
                "missing_evidence": [],
                "counterfactual_test": "y",
            },
        ]
        ok, errors = validate_all(evidence, analysis, None)
        self.assertFalse(ok)
        self.assertTrue(any("POSITIVE_AGENT_EVIDENCE" in e for e in errors), errors)

    def test_malformed_decision_trace_is_rejected_but_absence_is_allowed(self) -> None:
        evidence, analysis = self._base_pair()
        analysis.pop("decision_trace", None)
        ok, errors = validate_all(evidence, analysis, None)
        self.assertTrue(ok, f"an analysis without decision_trace must still validate: {errors}")

        analysis["decision_trace"] = {"evaluated": "not-a-list"}
        ok, errors = validate_all(evidence, analysis, None)
        self.assertFalse(ok)
        self.assertTrue(any("decision_trace.evaluated" in e for e in errors), errors)


class TestAgentAttributionTightening(unittest.TestCase):
    """
    Negative tests verifying that tightening the positive agent evidence gate
    prevents false agent blame when causal links are missing.
    """

    def test_isolated_solver_error_without_signals_does_not_blame_agent(self) -> None:
        ev = _evidence(
            scientific_observations=[_sci("generic_linear_solver_divergence")],
            verifier_observations=[_fail_log("FAIL: solver error")],
        )
        an = run_attribution_engine(ev, {"candidate_hypotheses": []})
        self.assertEqual(an["primary_root_cause"]["category"], "unknown")
        self.assertEqual(an["primary_root_cause"]["code"], "UNKNOWN_INSUFFICIENT_EVIDENCE")

    def test_agent_command_with_value_mismatch_without_signal_does_not_blame_agent(self) -> None:
        ev = _evidence(
            timeline=[_agent_event(cmd="python3 run.py")],
            verifier_observations=[_fail_log("FAIL: value mismatch: got 1.0 expected 2.0")],
        )
        an = run_attribution_engine(ev, {"candidate_hypotheses": []})
        self.assertEqual(an["primary_root_cause"]["category"], "unknown")
        self.assertEqual(an["primary_root_cause"]["code"], "UNKNOWN_INSUFFICIENT_EVIDENCE")

    def test_agent_with_unverified_output_and_value_mismatch_attributes_to_agent(self) -> None:
        ev = _evidence(
            behavioral_signals=[
                {
                    "signal_id": "sig:unverified_output",
                    "signal_type": "unverified_output",
                    "description": "Agent wrote results.json without verification",
                    "event_ref": "traj:write_step",
                    "source_pointer": "/steps/0",
                }
            ],
            verifier_observations=[_fail_log("FAIL: value mismatch: got 1.0 expected 2.0")],
        )
        an = run_attribution_engine(ev, {"candidate_hypotheses": []})
        self.assertEqual(an["primary_root_cause"]["category"], "agent")
        self.assertEqual(an["primary_root_cause"]["code"], "AGENT_RESULT_VALIDATION")
        self.assertEqual(an["first_unrecovered_deviation"]["event_ref"], "traj:write_step")

    def test_validator_rejects_generic_agent_timeline_event_as_positive_evidence(self) -> None:
        ev = _evidence(
            timeline=[_agent_event(event_id="traj:step:1", cmd="ls -la")],
            verifier_observations=[_fail_log("FAIL: value mismatch: got 1.0 expected 2.0")],
        )
        an = {
            "schema_version": "failure-analysis-v1",
            "case_id": "test_case",
            "trial_name": "trial_1",
            "verdict": "failed",
            "failure_stage": "agent_execution",
            "detection_stage": "verifier_execution",
            "first_unrecovered_deviation": {
                "status": "identified",
                "event_ref": "traj:step:1",
                "summary": "Agent ran ls",
            },
            "failure_manifestation": {"type": "wrong_value", "summary": "fail"},
            "primary_root_cause": {
                "category": "agent",
                "subtype": "result_validation",
                "code": "AGENT_RESULT_VALIDATION",
                "confidence": 0.5,
                "confidence_kind": "heuristic_evidence_score",
                "evidence_strength": "medium",
                "summary": "Agent produced wrong values",
            },
            "contributing_factors": [],
            "competing_hypotheses": [
                {
                    "hypothesis_id": "H1",
                    "category": "agent",
                    "subtype": "result_validation",
                    "claim": "Agent produced wrong values",
                    "evidence_for": ["traj:step:1"],
                    "evidence_against": [],
                    "missing_evidence": [],
                    "counterfactual_test": "Fix calculation",
                }
            ],
            "evidence_refs": ["traj:step:1"],
            "excluded_hypotheses": [],
            "recommended_actions": [{"owner": "Agent", "action": "Fix calculation"}],
            "skill_prescription": None,
        }
        ok, errors = validate_all(ev, an, None)
        self.assertFalse(ok)
        self.assertTrue(any("POSITIVE_AGENT_EVIDENCE" in e for e in errors), errors)


class TestEnhancedAttributionGates(unittest.TestCase):
    """Verify enhanced attribution for case ambiguity, verifier rules, and agent decisions."""

    def test_case_ambiguous_contract_drift_sign(self) -> None:
        ev = _evidence(
            verifier_observations=[
                _fail_log("FAIL: results.json cons_qty_drift -2.769e-06 != .ener drift 2.769e-06")
            ],
            artifacts=[{"artifact_id": "art:trial_result", "exists": True}],
        )
        an = run_attribution_engine(ev, {"candidate_hypotheses": []})
        self.assertEqual(an["primary_root_cause"]["category"], "case")
        self.assertEqual(an["primary_root_cause"]["code"], "CASE_AMBIGUOUS_CONTRACT")
        self.assertGreater(an["primary_root_cause"]["confidence"], 0.0)

    def test_case_ambiguous_contract_clustering_and_rotatable_bonds(self) -> None:
        for fail_msg in (
            "FAIL: reported n_clusters=14 != re-clustered 16 from the submitted conformers",
            "FAIL: n_rotatable_bonds reported 4 != recomputed 7",
            "FAIL: results.json n_msd_rows=51 != log production rows 101",
            "FAIL: final_pe=-5.1862444 differs from ref -5.2225305 by >0.001",
        ):
            with self.subTest(msg=fail_msg):
                ev = _evidence(
                    verifier_observations=[_fail_log(fail_msg)],
                    artifacts=[{"artifact_id": "art:trial_result", "exists": True}],
                )
                an = run_attribution_engine(ev, {"candidate_hypotheses": []})
                self.assertEqual(an["primary_root_cause"]["category"], "case")
                self.assertEqual(an["primary_root_cause"]["code"], "CASE_AMBIGUOUS_CONTRACT")
                self.assertGreater(an["primary_root_cause"]["confidence"], 0.0)

    def test_verifier_ultra_strict_tolerance(self) -> None:
        ev = _evidence(
            verifier_observations=[
                _fail_log(
                    "FAIL: final energy -0.02125869 eV differs from ref -0.02104561 by > 1.00e-06"
                )
            ],
            artifacts=[{"artifact_id": "art:trial_result", "exists": True}],
        )
        an = run_attribution_engine(ev, {"candidate_hypotheses": []})
        self.assertEqual(an["primary_root_cause"]["category"], "verifier")
        self.assertEqual(an["primary_root_cause"]["code"], "VERIFIER_TOLERANCE_TOO_STRICT")
        self.assertGreater(an["primary_root_cause"]["confidence"], 0.0)

    def test_verifier_schema_mismatch_string_int(self) -> None:
        for fail_msg in (
            "FAIL: results.json n_rdf_bins must be 100, got 100",
            "FAIL: n_input=12 != replayed 12",
        ):
            with self.subTest(msg=fail_msg):
                ev = _evidence(
                    verifier_observations=[_fail_log(fail_msg)],
                    artifacts=[{"artifact_id": "art:trial_result", "exists": True}],
                )
                an = run_attribution_engine(ev, {"candidate_hypotheses": []})
                self.assertEqual(an["primary_root_cause"]["category"], "verifier")
                self.assertEqual(an["primary_root_cause"]["code"], "VERIFIER_SCHEMA_MISMATCH")
                self.assertGreater(an["primary_root_cause"]["confidence"], 0.0)

    def test_agent_method_and_parameter_selection(self) -> None:
        sig = {
            "signal_id": "sig:asset_mod:1",
            "signal_type": "asset_modified",
            "description": "Agent modified input",
            "event_ref": "traj:step:1",
        }
        cases = [
            (
                "FAIL: final.inp does not enable a spin-polarized (UKS/LSD) calculation",
                "AGENT_SCIENTIFIC_METHOD_SELECTION",
                True,
            ),
            (
                "FAIL: fixed.in timestep 0.015 is too large for LJ fluid",
                "AGENT_SCIENTIFIC_PARAMETER_SELECTION",
                True,
            ),
            (
                "FAIL: phi=0: pw.in missing celldm(1)",
                "AGENT_TOOL_USE",
                True,
            ),
            (
                "FAIL: D = 5.7080e-05 cm^2/s matches a correct-factor fit over the colleague's 100-500 ps window",
                "AGENT_TASK_UNDERSTANDING",
                True,
            ),
            (
                "FAIL: methanol_szv.out geometry does not match the pinned asset geometry",
                "AGENT_PLANNING",
                True,
            ),
        ]
        for fail_msg, expected_code, has_prescription in cases:
            with self.subTest(code=expected_code):
                ev = _evidence(
                    behavioral_signals=[sig],
                    verifier_observations=[_fail_log(fail_msg)],
                    artifacts=[{"artifact_id": "art:trial_result", "exists": True}],
                )
                an = run_attribution_engine(ev, {"candidate_hypotheses": []})
                self.assertEqual(an["primary_root_cause"]["category"], "agent")
                self.assertEqual(an["primary_root_cause"]["code"], expected_code)
                self.assertGreater(an["primary_root_cause"]["confidence"], 0.0)
                if has_prescription:
                    self.assertIsNotNone(an["skill_prescription"])


class TestGate4RecomputeDivergencePath(unittest.TestCase):
    """
    Pins the deterministic verifier-recompute-divergence path added to Gate 4:
    the verifier independently recomputed a metric the agent delivered, the
    agent's own trajectory self-report matches the reported value, and the
    metric binds to the task contract.
    """

    def _matched_evidence(self) -> Dict[str, Any]:
        # `missing_artifacts` is only needed so the fixture is complete enough
        # for `validate_all`; no gate consumes it.
        return _evidence(
            missing_artifacts=[],
            contract_observations=[
                _contract(
                    "consistent",
                    contract_id="contract:max_force",
                    item="results.json:values.max_force",
                )
            ],
            scientific_observations=[_recompute_divergence()],
            verifier_observations=[
                _fail_log("FAIL: reported max_force 0.02863 != recomputed 0.03666 (tol 0.001)")
            ],
            timeline=[_agent_event(event_id="trajectory:step:7")],
        )

    def test_confidence_is_computed_by_compute_evidence_confidence(self) -> None:
        an = run_attribution_engine(self._matched_evidence(), {"candidate_hypotheses": []})
        prc = an["primary_root_cause"]
        # The fixture's divergence margin: |0.02863 - 0.03666| / 0.001 ≈ 8.03,
        # which crosses the ≥4 band (+2 points) and expands the denominator.
        margin_ratio = abs(0.02863 - 0.03666) / 0.001
        expected, kind, _strength = compute_evidence_confidence(
            direct_causal_evidence=3,
            cross_source_corroboration=1,
            counterfactual_supported=False,
            single_keyword_only=False,
            missing_discriminating_evidence=0,
            contradicting_evidence=0,
            margin_ratio=margin_ratio,
        )
        self.assertEqual(prc["confidence"], expected)
        self.assertEqual(prc["confidence"], 0.75)
        self.assertEqual(prc["confidence_kind"], kind)

    def test_evidence_refs_cite_divergence_fail_log_and_agent_ref(self) -> None:
        an = run_attribution_engine(self._matched_evidence(), {"candidate_hypotheses": []})
        self.assertEqual(
            an["evidence_refs"],
            ["sci:recompute_divergence:1", "ver:fail_log", "trajectory:step:7"],
        )

    def test_trace_checks_carry_divergence_counts_and_binding(self) -> None:
        an = run_attribution_engine(self._matched_evidence(), {"candidate_hypotheses": []})
        g4 = next(
            e
            for e in an["decision_trace"]["evaluated"]
            if e["gate_id"] == "gate4_numerical_divergence"
        )
        self.assertTrue(g4["matched"])
        self.assertEqual(g4["checks"]["has_recompute_divergence"], True)
        self.assertEqual(g4["checks"]["agent_corroborated_count"], 1)
        self.assertEqual(g4["checks"]["contract_binding"], "contract_observations")

    def test_abstain_trace_carries_divergence_counts(self) -> None:
        ev = _evidence(
            scientific_observations=[
                _recompute_divergence(
                    agent_reported_value=None,
                    agent_ref=None,
                    agent_reported_matches=False,
                )
            ],
            verifier_observations=[
                _fail_log("FAIL: reported max_force 0.02863 != recomputed 0.03666 (tol 0.001)")
            ],
        )
        an = run_attribution_engine(ev, {"candidate_hypotheses": []})
        self.assertEqual(
            an["decision_trace"]["selected"]["gate_id"],
            "gate5_insufficient_positive_evidence",
        )
        g4 = next(
            e
            for e in an["decision_trace"]["evaluated"]
            if e["gate_id"] == "gate4_numerical_divergence"
        )
        self.assertFalse(g4["matched"])
        self.assertEqual(g4["checks"]["recompute_divergence_count"], 1)
        self.assertEqual(g4["checks"]["agent_corroborated_count"], 0)

    def test_validator_accepts_the_new_agent_attribution(self) -> None:
        ev = self._matched_evidence()
        an = run_attribution_engine(ev, {"candidate_hypotheses": []})
        ok, errors = validate_all(ev, an, None)
        self.assertTrue(ok, errors)


class TestConfidenceUpgradeWiring(unittest.TestCase):
    """
    Pins the confidence-upgrade wiring: the case-specific measurements
    (`margin_ratio`, `check_localization`, `hypothesis_separation`) must flow
    from the evidence / candidate hypotheses through `AttributionContext` into
    the Gate 4 agent-path and Gate 6 confidence calls, and their absence must
    reproduce the legacy scores exactly.
    """

    #: |0.02863 - 0.03666| / 0.001 ≈ 8.03 → crosses the ≥4 band (+2 points).
    FIXTURE_MARGIN_RATIO = abs(0.02863 - 0.03666) / 0.001

    def _gate4_agent_evidence(self) -> Dict[str, Any]:
        return _evidence(
            missing_artifacts=[],
            contract_observations=[
                _contract(
                    "consistent",
                    contract_id="contract:max_force",
                    item="results.json:values.max_force",
                )
            ],
            scientific_observations=[_recompute_divergence()],
            verifier_observations=[
                _fail_log("FAIL: reported max_force 0.02863 != recomputed 0.03666 (tol 0.001)")
            ],
            timeline=[_agent_event(event_id="trajectory:step:7")],
        )

    # (i) margin_ratio ≥ 4 lifts the Gate 4 agent score above the no-margin
    # score of the same fixture (base 4 points + margin 2 → 6/8 = 0.75).
    def test_gate4_margin_ratio_raises_confidence(self) -> None:
        an = run_attribution_engine(self._gate4_agent_evidence(), {"candidate_hypotheses": []})
        prc = an["primary_root_cause"]
        self.assertEqual(prc["code"], "AGENT_RESULT_VALIDATION")
        expected, kind, strength = compute_evidence_confidence(
            direct_causal_evidence=3,
            cross_source_corroboration=1,
            margin_ratio=self.FIXTURE_MARGIN_RATIO,
        )
        self.assertEqual(prc["confidence"], expected)
        self.assertEqual(prc["confidence"], 0.75)
        self.assertEqual(prc["confidence_kind"], kind)
        self.assertEqual(prc["evidence_strength"], strength)
        legacy, _, _ = compute_evidence_confidence(
            direct_causal_evidence=3, cross_source_corroboration=1
        )
        self.assertGreater(prc["confidence"], legacy)

    # (ii) check_localization="single" whose unique failing check contains the
    # diverging metric name turns the counterfactual on and lifts the score
    # above the margin-only variant. The stats dict is built by the real
    # `build_check_stats` from a log with per-check pass markers (a fail-fast
    # log with a lone FAIL line never sets `single_failure`).
    def test_gate4_single_failure_counterfactual_raises_confidence_further(self) -> None:
        ev = self._gate4_agent_evidence()
        ev["verifier_check_stats"] = build_check_stats(
            "PASS: results.json exists\n"
            "FAIL: reported max_force 0.02863 != recomputed 0.03666 (tol 0.001)\n"
        )
        self.assertTrue(ev["verifier_check_stats"]["single_failure"])
        an = run_attribution_engine(ev, {"candidate_hypotheses": []})
        prc = an["primary_root_cause"]
        expected, _, _ = compute_evidence_confidence(
            direct_causal_evidence=3,
            cross_source_corroboration=1,
            counterfactual_supported=True,
            margin_ratio=self.FIXTURE_MARGIN_RATIO,
            check_localization="single",
        )
        self.assertEqual(prc["confidence"], expected)
        self.assertEqual(prc["evidence_strength"], "high")
        self.assertGreater(prc["confidence"], 0.75)

    def test_gate4_counterfactual_stays_off_when_single_failure_is_unrelated(self) -> None:
        """A single failing check that does not name the diverging metric must
        NOT enable the counterfactual bonus (replacing the value would not fix
        that check)."""
        ev = self._gate4_agent_evidence()
        ev["verifier_check_stats"] = build_check_stats(
            "PASS: results.json exists\nFAIL: results.json missing key 'n_rows'\n"
        )
        an = run_attribution_engine(ev, {"candidate_hypotheses": []})
        prc = an["primary_root_cause"]
        expected, _, _ = compute_evidence_confidence(
            direct_causal_evidence=3,
            cross_source_corroboration=1,
            counterfactual_supported=False,
            margin_ratio=self.FIXTURE_MARGIN_RATIO,
            check_localization="single",
        )
        self.assertEqual(prc["confidence"], expected)

    # (ii-b) A fail-fast verifier log carries one FAIL line and no PASS lines:
    # that is a `counterfactual_candidate`, not a proven single-check failure.
    # It must not add the localization point nor the +2 counterfactual bonus.
    def test_gate4_lone_fail_fast_log_does_not_enable_counterfactual(self) -> None:
        ev = self._gate4_agent_evidence()
        ev["verifier_check_stats"] = build_check_stats(
            "FAIL: reported max_force 0.02863 != recomputed 0.03666 (tol 0.001)\n"
        )
        self.assertFalse(ev["verifier_check_stats"]["single_failure"])
        an = run_attribution_engine(ev, {"candidate_hypotheses": []})
        prc = an["primary_root_cause"]
        expected, _, _ = compute_evidence_confidence(
            direct_causal_evidence=3,
            cross_source_corroboration=1,
            counterfactual_supported=False,
            margin_ratio=self.FIXTURE_MARGIN_RATIO,
        )
        self.assertEqual(prc["confidence"], expected)
        self.assertEqual(prc["confidence"], 0.75)

    def test_gate2_single_fail_line_does_not_add_counterfactual_bonus(self) -> None:
        ev = _evidence(
            contract_observations=[
                _contract(
                    "case_defect",
                    contract_id="contract:missing_upf",
                    item="Ge.UPF",
                    details="Prompt references asset `Ge.UPF`, which is absent.",
                )
            ],
            verifier_observations=[_fail_log("FAIL: Ge.UPF not found")],
            verifier_check_stats=build_check_stats("FAIL: Ge.UPF not found\n"),
        )
        an = run_attribution_engine(ev, {"candidate_hypotheses": []})
        self.assertEqual(an["decision_trace"]["selected"]["gate_id"], "gate2_case_definition")
        prc = an["primary_root_cause"]
        self.assertEqual(prc["code"], "CASE_MISSING_ASSET")
        expected, _, _ = compute_evidence_confidence(
            direct_causal_evidence=2, cross_source_corroboration=1
        )
        self.assertEqual(prc["confidence"], expected)
        self.assertEqual(prc["confidence"], 0.5)

    def test_gate3_single_fail_line_does_not_add_counterfactual_bonus(self) -> None:
        fail_line = "FAIL: Expected 5 thermo rows in scf.out, got 32"
        ev = _evidence(
            verifier_observations=[_fail_log(fail_line)],
            verifier_check_stats=build_check_stats(fail_line + "\n"),
        )
        an = run_attribution_engine(ev, {"candidate_hypotheses": []})
        self.assertEqual(an["decision_trace"]["selected"]["gate_id"], "gate3_verifier_defect")
        self.assertEqual(an["primary_root_cause"]["code"], "VERIFIER_REGEX_OR_PARSER_DEFECT")
        expected, _, _ = compute_evidence_confidence(
            direct_causal_evidence=2, cross_source_corroboration=1
        )
        self.assertEqual(an["primary_root_cause"]["confidence"], expected)
        self.assertEqual(an["primary_root_cause"]["confidence"], 0.5)

    def test_gate6_single_fail_line_does_not_add_counterfactual_bonus(self) -> None:
        ev = _evidence(
            scientific_observations=[_sci("scf_convergence_not_achieved")],
            behavioral_signals=[_sig("repeated_failed_action")],
            verifier_observations=[_fail_log("FAIL: SCF not converged")],
            verifier_check_stats=build_check_stats("FAIL: SCF not converged\n"),
        )
        an = run_attribution_engine(ev, {"candidate_hypotheses": []})
        self.assertEqual(an["decision_trace"]["selected"]["gate_id"], "gate6_agent_primary")
        expected, _, _ = compute_evidence_confidence(
            direct_causal_evidence=3, cross_source_corroboration=1
        )
        self.assertEqual(an["primary_root_cause"]["confidence"], expected)
        self.assertEqual(an["primary_root_cause"]["confidence"], 0.67)

    # (iii) hypothesis_separation ≥ 0.6 lifts the Gate 6 agent score.
    def test_gate6_hypothesis_separation_raises_confidence(self) -> None:
        ev = _evidence(
            scientific_observations=[_sci("scf_convergence_not_achieved")],
            behavioral_signals=[_sig("repeated_failed_action")],
            verifier_observations=[_fail_log("FAIL: SCF not converged")],
        )
        legacy = run_attribution_engine(ev, {"candidate_hypotheses": []})
        self.assertEqual(legacy["primary_root_cause"]["confidence"], 0.67)

        top1, top2 = 0.83, 0.17
        separation = (top1 - top2) / top1  # ≈ 0.795 ≥ 0.6 → +1 point
        hyps = {
            "candidate_hypotheses": [
                {"hypothesis_id": "H1", "category": "agent", "confidence": top1},
                {"hypothesis_id": "H2", "category": "verifier", "confidence": top2},
            ]
        }
        an = run_attribution_engine(ev, hyps)
        self.assertEqual(an["decision_trace"]["selected"]["gate_id"], "gate6_agent_primary")
        expected, kind, _ = compute_evidence_confidence(
            direct_causal_evidence=3,
            cross_source_corroboration=1,
            hypothesis_separation=separation,
        )
        self.assertEqual(an["primary_root_cause"]["confidence"], expected)
        self.assertEqual(an["primary_root_cause"]["confidence_kind"], kind)
        self.assertGreater(
            an["primary_root_cause"]["confidence"],
            legacy["primary_root_cause"]["confidence"],
        )

    # (iv) regression: with all three new signals absent the scores are
    # byte-identical to the legacy algorithm (4/6 = 0.67).
    def test_absent_new_signals_reproduce_legacy_scores(self) -> None:
        # Gate 4 agent path where margin_ratio is unmeasurable (tolerance 0),
        # no `verifier_check_stats`, and no candidate hypotheses.
        ev4 = _evidence(
            scientific_observations=[
                _recompute_divergence(
                    sci_id="sci:recompute_divergence:3",
                    matched_text="FAIL: fit_window reported 50 != reference 100",
                    metric="fit_window",
                    reported_value=50.0,
                    recomputed_value=100.0,
                    tolerance=0.0,
                    check_kind="reference_mismatch",
                    agent_ref="trajectory:step:4",
                    source_ref="trajectory:step:4:tool:0",
                )
            ],
            verifier_observations=[_fail_log("FAIL: fit_window reported 50 != reference 100")],
            timeline=[_agent_event(event_id="trajectory:step:4")],
        )
        an4 = run_attribution_engine(ev4, {"candidate_hypotheses": []})
        expected4, _, _ = compute_evidence_confidence(
            direct_causal_evidence=3, cross_source_corroboration=1
        )
        self.assertEqual(an4["primary_root_cause"]["code"], "AGENT_SCIENTIFIC_PARAMETER_SELECTION")
        self.assertEqual(an4["primary_root_cause"]["confidence"], expected4)
        self.assertEqual(an4["primary_root_cause"]["confidence"], 0.67)

        ev6 = _evidence(
            scientific_observations=[_sci("scf_convergence_not_achieved")],
            behavioral_signals=[_sig("repeated_failed_action")],
            verifier_observations=[_fail_log("FAIL: SCF not converged")],
        )
        an6 = run_attribution_engine(ev6, {"candidate_hypotheses": []})
        self.assertEqual(an6["primary_root_cause"]["confidence"], 0.67)

    def test_gate6_dynamic_deliverable_name(self) -> None:
        ev = _evidence(
            behavioral_signals=[_sig("unverified_output")],
            verifier_observations=[_fail_log("FAIL: mismatch")],
            metadata={"declared_deliverables": ["charges.csv"]},
        )
        an = run_attribution_engine(ev, {"candidate_hypotheses": []})
        prc = an["primary_root_cause"]
        self.assertEqual(prc["code"], "AGENT_RESULT_VALIDATION")
        self.assertIn("`charges.csv`", prc["summary"])

    def test_gate4_delta_divergence_attribution(self) -> None:
        ev = _evidence(
            contract_observations=[
                _contract(
                    "consistent",
                    contract_id="contract:charge",
                    item="results.json:values.charge",
                )
            ],
            scientific_observations=[
                _sci(
                    "verifier_recompute_divergence",
                    sci_id="sci:recompute_divergence:1",
                    metric="charge",
                    reported_value=1.59869e-06,
                    recomputed_value=0.0,
                    tolerance=1.00001e-10,
                    check_kind="reference_mismatch",
                    is_delta_divergence=True,
                    agent_reported_value=0.898982845013923,
                    agent_ref="trajectory:step:55",
                    agent_artifact_ref="art:artifacts/app/outputs/charges.csv:L628",
                    agent_reported_matches=False,
                    source_ref="verifier:raw-verifier.json:neutral_stationary",
                    discriminating=True,
                )
            ],
            verifier_observations=[
                _fail_log(
                    "neutral_stationary: R02-M01/site000003 charge differs from independent frozen EQeq replay by 1.59869e-06 e beyond its decimal serialization bound 1.00001e-10 e"
                )
            ],
            timeline=[_agent_event(event_id="trajectory:step:55")],
        )
        ev["verifier_check_stats"] = {
            "total_checks": 2,
            "failed_checks": 1,
            "single_failure": True,
            "widespread_failure": False,
            "failed_check_summaries": [
                "neutral_stationary: R02-M01/site000003 charge differs from independent frozen EQeq replay by 1.59869e-06 e"
            ],
        }
        an = run_attribution_engine(ev, {"candidate_hypotheses": []})
        prc = an["primary_root_cause"]
        self.assertEqual(prc["category"], "agent")
        self.assertEqual(prc["code"], "AGENT_RESULT_VALIDATION")
        self.assertEqual(prc["subtype"], "result_validation")
        self.assertGreaterEqual(prc["confidence"], 0.75)
        self.assertIn("0.898982845013923", prc["summary"])
        self.assertNotIn("self-report matches", json.dumps(an["excluded_hypotheses"]))
        self.assertEqual(an["first_unrecovered_deviation"]["status"], "identified")
        self.assertEqual(an["first_unrecovered_deviation"]["event_ref"], "trajectory:step:55")

    def test_gate4_unbound_delta_divergence_does_not_blame_agent(self) -> None:
        ev = _evidence(
            scientific_observations=[
                _sci(
                    "verifier_recompute_divergence",
                    sci_id="sci:recompute_divergence:1",
                    metric="charge",
                    reported_value=1.59869e-06,
                    recomputed_value=0.0,
                    tolerance=1.00001e-10,
                    check_kind="reference_mismatch",
                    is_delta_divergence=True,
                    agent_reported_value=None,
                    agent_ref=None,
                    agent_artifact_ref=None,
                    agent_reported_matches=False,
                    source_ref="verifier:raw-verifier.json:neutral_stationary",
                    discriminating=True,
                )
            ],
            verifier_observations=[
                _fail_log(
                    "neutral_stationary: R02-M01/site000003 charge differs from independent frozen EQeq replay by 1.59869e-06 e"
                )
            ],
        )
        an = run_attribution_engine(ev, {"candidate_hypotheses": []})
        self.assertEqual(an["primary_root_cause"]["category"], "unknown")

    def test_gate6_self_verification_routes_to_self_check_gap(self) -> None:
        ev = _evidence(
            behavioral_signals=[_sig("self_verification_executed", event_ref="trajectory:step:70")],
            verifier_observations=[
                _fail_log(
                    "spin_selection: atomic energy/spin differs from independent native calculation"
                )
            ],
            verifier_check_stats={
                "total_checks": 2,
                "failed_checks": 1,
                "single_failure": True,
                "failed_check_names": ["spin_selection"],
                "failed_check_summaries": [
                    "spin_selection: atomic energy/spin differs from independent native calculation"
                ],
            },
            causal_anchors={
                "algorithm_or_param_definition": {
                    "event_id": "trajectory:step:6:tool:0",
                    "step_id": "trajectory:step:6",
                },
                "self_check": {
                    "event_id": "trajectory:step:70:tool:0",
                    "script_path": "/app/outputs/native/verify_outputs.py",
                    "script_event_ref": "trajectory:step:62:tool:0",
                    "self_check_passed": True,
                    "checked_failed_checks": [],
                    "uncovered_failed_checks": ["spin_selection"],
                },
            },
            timeline=[
                _agent_event(event_id="trajectory:step:6:tool:0"),
                _agent_event(event_id="trajectory:step:70:tool:0"),
                _agent_event(event_id="trajectory:step:62:tool:0"),
            ],
        )
        an = run_attribution_engine(ev, {"candidate_hypotheses": []})
        prc = an["primary_root_cause"]
        self.assertEqual(prc["category"], "agent")
        self.assertEqual(prc["code"], "AGENT_SELF_CHECK_GAP")
        self.assertEqual(prc["subtype"], "self_check_gap")
        self.assertIn("spin_selection", prc["summary"])
        self.assertIn("verify_outputs.py", prc["summary"])
        self.assertEqual(an["first_unrecovered_deviation"]["event_ref"], "trajectory:step:6:tool:0")

    def test_gate5_unlinked_deliverable_anchor_stays_unknown(self) -> None:
        ev = _evidence(
            verifier_observations=[_fail_log("FAIL: verifier rejected outputs")],
            metadata={"declared_deliverables": ["/app/outputs"]},
            causal_anchors={
                "deliverable_generation": {
                    "event_id": "trajectory:step:9:tool:0",
                    "command": "python prepare.py --input /opt/data",
                    "observation_snippet": "prepared inputs",
                }
            },
        )
        an = run_attribution_engine(ev, {"candidate_hypotheses": []})
        self.assertEqual(an["primary_root_cause"]["category"], "unknown")
        self.assertEqual(an["primary_root_cause"]["code"], "UNKNOWN_INSUFFICIENT_EVIDENCE")

    def test_gate5_linked_deliverable_anchor_continues_to_gate6(self) -> None:
        ev = _evidence(
            behavioral_signals=[_sig("unverified_output")],
            verifier_observations=[_fail_log("FAIL: wrong value")],
            metadata={"declared_deliverables": ["/app/outputs"]},
            causal_anchors={
                "deliverable_generation": {
                    "event_id": "trajectory:step:9:tool:0",
                    "command": "python analysis.py --output /app/outputs",
                }
            },
            timeline=[_agent_event(event_id="trajectory:step:9:tool:0")],
        )
        an = run_attribution_engine(ev, {"candidate_hypotheses": []})
        self.assertEqual(an["primary_root_cause"]["category"], "agent")

    def test_gate6_self_check_gap_without_anchor_does_not_fabricate_coverage(self) -> None:
        ev = _evidence(
            behavioral_signals=[_sig("self_verification_executed", event_ref="trajectory:step:70")],
            verifier_observations=[
                _fail_log(
                    "spin_selection: atomic energy/spin differs from independent native calculation"
                )
            ],
            verifier_check_stats={
                "total_checks": 2,
                "failed_checks": 1,
                "single_failure": True,
                "failed_check_names": ["spin_selection"],
                "failed_check_summaries": [
                    "spin_selection: atomic energy/spin differs from independent native calculation"
                ],
            },
            timeline=[_agent_event(event_id="trajectory:step:70")],
        )
        an = run_attribution_engine(ev, {"candidate_hypotheses": []})
        summary = an["primary_root_cause"]["summary"]
        self.assertEqual(an["primary_root_cause"]["code"], "AGENT_SELF_CHECK_GAP")
        self.assertIn("no self-check script source was captured", summary)
        self.assertNotIn("it passed", summary)
        self.assertNotIn("omits verifier-required checks", summary)

    def test_gate6_self_check_gap_respects_missing_pass_marker(self) -> None:
        ev = _evidence(
            behavioral_signals=[_sig("self_verification_executed", event_ref="trajectory:step:70")],
            verifier_observations=[_fail_log("FAIL: verifier rejected outputs")],
            verifier_check_stats={
                "total_checks": 2,
                "failed_checks": 1,
                "single_failure": True,
                "failed_check_names": ["spin_selection"],
                "failed_check_summaries": ["spin_selection: FAIL"],
            },
            causal_anchors={
                "self_check": {
                    "event_id": "trajectory:step:70:tool:0",
                    "script_path": "/app/outputs/native/verify_outputs.py",
                    "script_event_ref": "trajectory:step:62:tool:0",
                    "self_check_passed": False,
                    "checked_failed_checks": [],
                    "uncovered_failed_checks": ["spin_selection"],
                }
            },
            timeline=[
                _agent_event(event_id="trajectory:step:70:tool:0"),
                _agent_event(event_id="trajectory:step:62:tool:0"),
            ],
        )
        an = run_attribution_engine(ev, {"candidate_hypotheses": []})
        summary = an["primary_root_cause"]["summary"]
        self.assertIn("without a pass marker", summary)
        self.assertNotIn("and reported success", summary)

    def test_unscored_verifier_error_routes_to_verifier_defect(self) -> None:
        ev = _evidence(
            missing_artifacts=[],
            verifier_observations=[
                {
                    "obs_id": "ver:fail_log",
                    "type": "verifier_fail_message",
                    "summary": "TypeError: string indices must be integers, not 'str'",
                    "matched_text": "TypeError: string indices must be integers, not 'str'",
                    "source_file": "verifier/verify.json",
                },
                {
                    "obs_id": "ver:internal_crash",
                    "type": "verifier_internal_crash",
                    "subtype": "recompute_defect",
                    "code": "VERIFIER_RECOMPUTE_DEFECT",
                    "summary": "Verifier crashed internally: TypeError: string indices must be integers, not 'str'",
                    "matched_text": "TypeError: string indices must be integers, not 'str'",
                    "hazard_detected": True,
                    "failure_binding": "direct",
                    "triggered": True,
                    "source_file": "verifier/verify.json",
                },
            ],
            verifier_report={
                "status": "ERROR",
                "scientific_status": "INCONCLUSIVE",
                "scorable": False,
                "reward": None,
                "failure_origin": {"reason_code": "evaluator"},
                "messages": ["TypeError: string indices must be integers, not 'str'"],
                "failed_checks": [],
                "summary": "TypeError: string indices must be integers, not 'str'",
            },
        )
        an = run_attribution_engine(ev, {"candidate_hypotheses": []})
        prc = an["primary_root_cause"]
        self.assertEqual(prc["category"], "verifier")
        self.assertEqual(prc["code"], "VERIFIER_RECOMPUTE_DEFECT")
        self.assertEqual(an["first_unrecovered_deviation"]["event_ref"], "ver:internal_crash")
        ok, errors = validate_all(ev, an)
        self.assertTrue(ok, errors)


if __name__ == "__main__":
    unittest.main()
