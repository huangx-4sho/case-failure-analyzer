#!/usr/bin/env python3
"""
Verifier recompute-divergence extraction tests (`tests/test_verifier_divergence.py`)

Covers the deterministic verifier-log -> `scientific_observations` parser in
`scripts/extract_scientific_errors.py`: patterns P1-P4, metric extraction,
agent-side trajectory corroboration (hit / miss / unavailable), coexistence
with the knowledge-base family adapters, and an end-to-end run against the
minimal fixture copied from the real `ase-custom-calculator` failure case.
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

from attribution.engine import run_attribution_engine
from extract_scientific_errors import (
    _load_trajectory_steps,
    detect_scale_or_sign_factor,
    extract_scientific_errors,
    extract_verifier_divergences,
    pair_verifier_evidence_with_artifacts,
)
from validate_analysis import validate_all


FIXTURE_TRIAL_DIR = (
    ROOT_DIR / "tests" / "fixtures" / "verifier_divergence" / "ase-custom-calculator"
)

# Real verifier line from
# ccb-l2-ase-custom-calculator/ase-custom-calculator__KaimrKM/verifier/verify.log
P1_LINE = "FAIL: reported max_force 0.02863 != recomputed 0.03666 (tol 0.001)"
P1_LINE_NO_TOL = "FAIL: reported 0.02863 != recomputed 0.03666"
P2_LINE = "FAIL: final_energy -0.02125869 differs from the reference -0.02125870"
P2_UNIT_LINE = "FAIL: final energy -0.02125869 eV differs from ref -0.02125870 eV"
P3_MUST_BE_LINE = "FAIL: results.json n_msd_rows must be 100, got 51"
P3_EXPECTED_LINE = "FAIL: expected 10 irreducible k-points, got 11"
P4_LINE = "FAIL: overlap 0.42 != 0.37"


def _one_line(line: str) -> list:
    return [(1, line)]


def _steps(*steps: dict) -> list:
    """Serialize execution-only ATIF steps the way `_load_trajectory_steps` does.

    `message` / `reasoning_content` are deliberately dropped: corroboration
    must never bind a value that only appeared in an agent message or thought.
    """
    return [
        (
            step.get("step_id"),
            json.dumps(
                {
                    "tool_calls": step.get("tool_calls") or [],
                    "observation": step.get("observation") or "",
                },
                ensure_ascii=False,
                default=str,
            ),
        )
        for step in steps
    ]


class TestVerifierDivergencePatterns(unittest.TestCase):
    """P1-P4 pattern matching on real verifier FAIL-line fixtures."""

    def test_p1_recompute_divergence_with_tolerance(self) -> None:
        obs = extract_verifier_divergences(_one_line(P1_LINE))
        self.assertEqual(len(obs), 1)
        o = obs[0]
        self.assertEqual(o["software"], "verifier")
        self.assertEqual(o["error_family"], "verifier_recompute_divergence")
        self.assertEqual(o["aliases"], ["numerical_divergence"])
        self.assertIsNone(o["reference_anchor"])
        self.assertEqual(o["matched_text"], P1_LINE)
        self.assertEqual(o["source_ref"], "verifier:verify.log:L1")
        self.assertEqual(o["check_kind"], "recompute_divergence")
        self.assertEqual(o["metric"], "max_force")
        self.assertEqual(o["reported_value"], 0.02863)
        self.assertEqual(o["recomputed_value"], 0.03666)
        self.assertEqual(o["tolerance"], 0.001)
        self.assertTrue(o["discriminating"])

    def test_p1_without_metric_label_and_tolerance(self) -> None:
        obs = extract_verifier_divergences(_one_line(P1_LINE_NO_TOL))
        self.assertEqual(len(obs), 1)
        o = obs[0]
        self.assertEqual(o["check_kind"], "recompute_divergence")
        self.assertEqual(o["reported_value"], 0.02863)
        self.assertEqual(o["recomputed_value"], 0.03666)
        self.assertIsNone(o["tolerance"])

    def test_p2_reference_mismatch_spec_form(self) -> None:
        obs = extract_verifier_divergences(_one_line(P2_LINE))
        self.assertEqual(len(obs), 1)
        o = obs[0]
        self.assertEqual(o["check_kind"], "reference_mismatch")
        self.assertEqual(o["metric"], "final_energy")
        self.assertEqual(o["reported_value"], -0.02125869)
        self.assertEqual(o["recomputed_value"], -0.02125870)
        self.assertIsNone(o["tolerance"])
        self.assertTrue(o["discriminating"])

    def test_p2_reference_mismatch_with_unit_token(self) -> None:
        obs = extract_verifier_divergences(_one_line(P2_UNIT_LINE))
        self.assertEqual(len(obs), 1)
        o = obs[0]
        self.assertEqual(o["check_kind"], "reference_mismatch")
        self.assertEqual(o["metric"], "energy")
        self.assertEqual(o["reported_value"], -0.02125869)
        self.assertEqual(o["recomputed_value"], -0.02125870)

    def test_p3_count_mismatch_must_be_got(self) -> None:
        obs = extract_verifier_divergences(_one_line(P3_MUST_BE_LINE))
        self.assertEqual(len(obs), 1)
        o = obs[0]
        self.assertEqual(o["check_kind"], "count_mismatch")
        self.assertEqual(o["metric"], "n_msd_rows")
        self.assertEqual(o["reported_value"], 51)
        self.assertEqual(o["recomputed_value"], 100)
        self.assertIsInstance(o["reported_value"], int)
        self.assertIsInstance(o["recomputed_value"], int)
        self.assertIsNone(o["tolerance"])

    def test_p3_count_mismatch_expected_got(self) -> None:
        obs = extract_verifier_divergences(_one_line(P3_EXPECTED_LINE))
        self.assertEqual(len(obs), 1)
        o = obs[0]
        self.assertEqual(o["check_kind"], "count_mismatch")
        self.assertEqual(o["reported_value"], 11)
        self.assertEqual(o["recomputed_value"], 10)

    def test_p4_fail_inequality_fallback(self) -> None:
        obs = extract_verifier_divergences(_one_line(P4_LINE))
        self.assertEqual(len(obs), 1)
        o = obs[0]
        self.assertEqual(o["check_kind"], "recompute_divergence")
        self.assertEqual(o["metric"], "overlap")
        self.assertEqual(o["reported_value"], 0.42)
        self.assertEqual(o["recomputed_value"], 0.37)
        self.assertIsNone(o["tolerance"])
        self.assertTrue(o["discriminating"])

    def test_p1_takes_precedence_and_yields_single_observation_per_line(self) -> None:
        # P1_LINE also contains a bare `!=` (P4 shape); only one observation
        # must be produced for the line.
        obs = extract_verifier_divergences(_one_line(P1_LINE))
        self.assertEqual(len(obs), 1)
        self.assertEqual(obs[0]["check_kind"], "recompute_divergence")
        self.assertEqual(obs[0]["tolerance"], 0.001)

    def test_p4_ignored_without_fail_marker(self) -> None:
        obs = extract_verifier_divergences(_one_line("comparison result: 0.42 != 0.37"))
        self.assertEqual(obs, [])

    def test_unparseable_number_yields_null_and_not_discriminating(self) -> None:
        obs = extract_verifier_divergences(
            _one_line("FAIL: reported max_force 1.2.3 != recomputed 0.5")
        )
        self.assertEqual(len(obs), 1)
        o = obs[0]
        self.assertIsNone(o["reported_value"])
        self.assertEqual(o["recomputed_value"], 0.5)
        self.assertFalse(o["discriminating"])
        self.assertFalse(o["agent_reported_matches"])
        self.assertIsNone(o["agent_reported_value"])
        self.assertIsNone(o["agent_ref"])

    def test_non_matching_fail_lines_produce_nothing(self) -> None:
        for line in (
            "FAIL: could not parse line 'VAL= -0.12345D+03'",
            "FAIL: instantaneous_position trajectory_rmsd=0.45 > 0.01",
            "FAIL: agent output absent; protocol note mentions chaotic behaviour",
            "Traceback (most recent call last):",
        ):
            self.assertEqual(extract_verifier_divergences(_one_line(line)), [], line)

    def test_line_numbers_preserved_across_multiple_lines(self) -> None:
        log = "PASS: everything fine\n" + P1_LINE + "\n" + P4_LINE + "\n"
        numbered = [(i + 1, ln) for i, ln in enumerate(log.splitlines())]
        obs = extract_verifier_divergences(numbered)
        self.assertEqual(len(obs), 2)
        self.assertEqual(obs[0]["source_ref"], "verifier:verify.log:L2")
        self.assertEqual(obs[1]["source_ref"], "verifier:verify.log:L3")


class TestAgentCorroboration(unittest.TestCase):
    """Agent-side cross-corroboration against execution-only trajectory steps."""

    def test_corroboration_hit_same_step(self) -> None:
        steps = _steps(
            {"step_id": 11, "observation": "running relaxation"},
            {"step_id": 12, "observation": "BFGS step: max_force = 0.02863 eV/A"},
            {"step_id": 13, "observation": "done"},
        )
        obs = extract_verifier_divergences(_one_line(P1_LINE), trajectory_steps=steps)
        self.assertEqual(len(obs), 1)
        o = obs[0]
        self.assertTrue(o["agent_reported_matches"])
        self.assertEqual(o["agent_reported_value"], 0.02863)
        self.assertEqual(o["agent_ref"], "trajectory:step:12")

    def test_corroboration_miss_when_value_absent(self) -> None:
        steps = _steps({"step_id": 12, "observation": "BFGS step: max_force = 0.05 eV/A"})
        obs = extract_verifier_divergences(_one_line(P1_LINE), trajectory_steps=steps)
        self.assertEqual(len(obs), 1)
        o = obs[0]
        self.assertFalse(o["agent_reported_matches"])
        self.assertIsNone(o["agent_reported_value"])
        self.assertIsNone(o["agent_ref"])

    def test_corroboration_requires_metric_in_same_step(self) -> None:
        # Value string appears but the metric name is in a different step.
        steps = _steps(
            {"step_id": 5, "observation": "printed 0.02863 somewhere"},
            {"step_id": 6, "observation": "max_force convergence check"},
        )
        obs = extract_verifier_divergences(_one_line(P1_LINE), trajectory_steps=steps)
        self.assertEqual(len(obs), 1)
        self.assertFalse(obs[0]["agent_reported_matches"])
        self.assertIsNone(obs[0]["agent_ref"])

    def test_corroboration_handles_unicode_minus(self) -> None:
        steps = _steps(
            {"step_id": 7, "observation": "final_energy = \u22120.02125869 eV after relaxation"}
        )
        obs = extract_verifier_divergences(_one_line(P2_LINE), trajectory_steps=steps)
        self.assertEqual(len(obs), 1)
        self.assertTrue(obs[0]["agent_reported_matches"])
        self.assertEqual(obs[0]["agent_reported_value"], -0.02125869)
        self.assertEqual(obs[0]["agent_ref"], "trajectory:step:7")

    def test_corroboration_falls_back_to_step_ordinal_without_step_id(self) -> None:
        steps = _steps({"observation": "BFGS step: max_force = 0.02863 eV/A"})
        obs = extract_verifier_divergences(_one_line(P1_LINE), trajectory_steps=steps)
        self.assertEqual(len(obs), 1)
        self.assertTrue(obs[0]["agent_reported_matches"])
        self.assertEqual(obs[0]["agent_ref"], "trajectory:step:1")

    def test_final_response_and_thought_are_not_execution_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            trial_dir = Path(tmp)
            (trial_dir / "agent").mkdir()
            (trial_dir / "agent" / "trajectory.json").write_text(
                json.dumps(
                    {
                        "schema_version": "ATIF-v1.7",
                        "steps": [
                            {
                                "step_id": 60,
                                "message": "almost done",
                                "reasoning_content": "max_force = 0.02863 eV/A in my head",
                            },
                            {
                                "step_id": 61,
                                "message": "Done. max_force = 0.02863 eV/A, deliverables complete.",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            steps = _load_trajectory_steps(trial_dir)
        self.assertEqual(steps, [])
        obs = extract_verifier_divergences(_one_line(P1_LINE), trajectory_steps=steps)
        self.assertFalse(obs[0]["agent_reported_matches"])
        self.assertIsNone(obs[0]["agent_ref"])


class TestExtractionIntegration(unittest.TestCase):
    """Wiring through `extract_scientific_errors` incl. coexistence and fixtures."""

    def test_historical_notes_and_log_inspection_do_not_create_execution_errors(self) -> None:
        error = "SCF run NOT converged"
        events = [
            {"event_id": "trajectory:step:1", "event_type": "agent_thought", "observation": error},
            {"event_id": "trajectory:step:2", "event_type": "file_read", "observation": error},
            {
                "event_id": "trajectory:step:3",
                "event_type": "tool_call",
                "exit_code": 0,
                "command": f'grep "{error}|SCF run converged" archive/cp2k.out',
                "observation": "SCF run converged in 13 iterations",
            },
            {
                "event_id": "trajectory:step:4",
                "event_type": "tool_call",
                "exit_code": 0,
                "command": 'cd /app && head -30 archive/cp2k.out; grep "SCF" archive/cp2k.out',
                "observation": error,
            },
        ]
        extraction = extract_scientific_errors(None, {"events": events})
        self.assertEqual(extraction["scientific_observations"], [])

        events.extend(
            [
                {
                    "event_id": "trajectory:step:5",
                    "event_type": "shell_error",
                    "exit_code": 1,
                    "command": "cd /app && mpirun cp2k.psmp -i run.inp",
                    "observation": error,
                },
                {
                    "event_id": "trajectory:step:6",
                    "event_type": "tool_call",
                    "exit_code": 0,
                    "command": "cat archive/cp2k_success.out",
                    "observation": "SCF run converged",
                },
            ]
        )
        observations = extract_scientific_errors(None, {"events": events})[
            "scientific_observations"
        ]
        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0]["source_ref"], "trajectory:step:5")
        self.assertFalse(observations[0]["recovered"])

        events.append(
            {
                "event_id": "trajectory:step:7",
                "event_type": "tool_call",
                "exit_code": 0,
                "command": "mpirun cp2k.psmp -i corrected.inp",
                "observation": "SCF run converged",
            }
        )
        observations = extract_scientific_errors(None, {"events": events})[
            "scientific_observations"
        ]
        self.assertTrue(observations[0]["recovered"])
        self.assertEqual(observations[0]["recovery_event_ref"], "trajectory:step:7")

    def test_coexistence_with_trajectory_family_extraction(self) -> None:
        fake_traj = {
            "events": [
                {
                    "event_id": "trajectory:step:1",
                    "command": "python run_mace_md.py",
                    "observation": "RuntimeError: NaN detected in predicted forces during MACE rollout",
                }
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            trial_dir = Path(tmp)
            (trial_dir / "verifier").mkdir()
            (trial_dir / "agent").mkdir()
            (trial_dir / "verifier" / "verify.log").write_text(P1_LINE + "\n", encoding="utf-8")
            (trial_dir / "agent" / "trajectory.json").write_text(
                json.dumps({"schema_version": "ATIF-v1.7", "steps": []}), encoding="utf-8"
            )
            res = extract_scientific_errors(trial_dir=trial_dir, normalized_traj=fake_traj)
        obs = res["scientific_observations"]
        self.assertEqual(len(obs), 2)
        # Knowledge-base family observation keeps the plain `sci:N` id...
        self.assertEqual(obs[0]["sci_id"], "sci:1")
        self.assertEqual(obs[0]["error_family"], "mlip_rollout_ood_instability")
        self.assertEqual(obs[0]["source_ref"], "trajectory:step:1")
        # ...and the divergence observation continues the ordinal sequence.
        self.assertEqual(obs[1]["sci_id"], "sci:recompute_divergence:2")
        self.assertEqual(obs[1]["error_family"], "verifier_recompute_divergence")
        # Trajectory steps exist but never report the value -> no corroboration.
        self.assertFalse(obs[1]["agent_reported_matches"])
        self.assertIsNone(obs[1]["agent_ref"])

    def test_end_to_end_real_case_fixture(self) -> None:
        res = extract_scientific_errors(trial_dir=FIXTURE_TRIAL_DIR, normalized_traj=None)
        obs = res["scientific_observations"]
        self.assertEqual(len(obs), 1)
        o = obs[0]
        self.assertEqual(o["sci_id"], "sci:recompute_divergence:1")
        self.assertEqual(o["software"], "verifier")
        self.assertEqual(o["error_family"], "verifier_recompute_divergence")
        self.assertEqual(o["aliases"], ["numerical_divergence"])
        self.assertIsNone(o["reference_anchor"])
        self.assertEqual(o["matched_text"], P1_LINE)
        self.assertEqual(o["source_ref"], "verifier:verify.log:L1")
        self.assertEqual(o["metric"], "max_force")
        self.assertEqual(o["reported_value"], 0.02863)
        self.assertEqual(o["recomputed_value"], 0.03666)
        self.assertEqual(o["tolerance"], 0.001)
        self.assertEqual(o["check_kind"], "recompute_divergence")
        self.assertEqual(o["agent_reported_value"], 0.02863)
        self.assertEqual(o["agent_ref"], "trajectory:step:12")
        self.assertTrue(o["agent_reported_matches"])
        self.assertTrue(o["discriminating"])
        # Schema-compat keys required by the attribution gates.
        self.assertIsInstance(o["candidate_causes"], list)
        self.assertIsInstance(o["required_discriminating_evidence"], list)

    def test_missing_trajectory_yields_null_corroboration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            trial_dir = Path(tmp)
            (trial_dir / "verifier").mkdir()
            (trial_dir / "verifier" / "verify.log").write_text(P1_LINE + "\n", encoding="utf-8")
            res = extract_scientific_errors(trial_dir=trial_dir, normalized_traj=None)
        obs = res["scientific_observations"]
        self.assertEqual(len(obs), 1)
        o = obs[0]
        self.assertFalse(o["agent_reported_matches"])
        self.assertIsNone(o["agent_reported_value"])
        self.assertIsNone(o["agent_ref"])
        self.assertTrue(o["discriminating"])

    def test_trial_without_verifier_log_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            res = extract_scientific_errors(trial_dir=Path(tmp), normalized_traj=None)
        self.assertEqual(res["scientific_observations"], [])

    def test_p2b_delta_mismatch_from_verifier_report(self) -> None:
        report = {
            "failed_checks": [
                {
                    "layer": "L3",
                    "name": "neutral_stationary",
                    "ok": False,
                    "detail": "R02-M01/site000003 charge differs from independent frozen EQeq replay by 1.59869e-06 e beyond its decimal serialization bound 1.00001e-10 e",
                    "source": "raw-verifier.json",
                }
            ],
            "messages": [
                "neutral_stationary: R02-M01/site000003 charge differs from independent frozen EQeq replay by 1.59869e-06 e beyond its decimal serialization bound 1.00001e-10 e"
            ],
        }
        res = extract_scientific_errors(trial_dir=None, verifier_report=report)
        obs = res["scientific_observations"]
        # Duplicate in messages should be deduplicated
        self.assertEqual(len(obs), 1)
        o = obs[0]
        self.assertEqual(o["software"], "verifier")
        self.assertEqual(o["error_family"], "verifier_recompute_divergence")
        self.assertEqual(o["metric"], "charge")
        self.assertTrue(o.get("is_delta_divergence"))
        self.assertAlmostEqual(o["reported_value"], 1.59869e-06)
        self.assertEqual(o["recomputed_value"], 0.0)
        self.assertAlmostEqual(o["tolerance"], 1.00001e-10)
        self.assertEqual(o["source_ref"], "verifier:raw-verifier.json:neutral_stationary")
        self.assertTrue(o["discriminating"])
        # No trial artifacts: a verifier delta must never be reused as the
        # agent's delivered value.
        self.assertIsNone(o["agent_reported_value"])
        self.assertIsNone(o["agent_ref"])
        self.assertIsNone(o["agent_artifact_ref"])
        self.assertFalse(o["agent_reported_matches"])

    def test_delta_divergence_binds_real_delivered_value_from_artifact(self) -> None:
        line = (
            "R02-M01/site000003 charge differs from independent frozen EQeq replay "
            "by 1.59869e-06 e beyond its decimal serialization bound 1.00001e-10 e"
        )
        with tempfile.TemporaryDirectory() as tmp:
            trial_dir = Path(tmp)
            outputs = trial_dir / "artifacts" / "app" / "outputs"
            outputs.mkdir(parents=True)
            (outputs / "charges.csv").write_text(
                "run_id,atom_id,charge_e\n"
                "R01-M01,site000003,0.893661300984714\n"
                "R02-M01,site000003,0.898982845013923\n"
                "R02-M01,site000004,0.100000000000000\n",
                encoding="utf-8",
            )
            (trial_dir / "agent").mkdir()
            (trial_dir / "agent" / "trajectory.json").write_text(
                json.dumps(
                    {
                        "steps": [
                            {
                                "step_id": 43,
                                "tool_calls": [
                                    {
                                        "function_name": "Write",
                                        "arguments": {
                                            "file_path": "/app/outputs/code/solver.py",
                                            "content": "K_COUL = 14.399645\nSCF_E_TOL = 1e-10\n",
                                        },
                                    }
                                ],
                                "observation": "File created successfully",
                            },
                            {
                                "step_id": 55,
                                "tool_calls": [
                                    {
                                        "function_name": "Bash",
                                        "arguments": {
                                            "command": (
                                                "python -I /app/outputs/code/solver.py "
                                                "--inputs /opt/qeq-public/inputs --output /app/outputs"
                                            )
                                        },
                                    }
                                ],
                                "observation": "wrote outputs to /app/outputs",
                            },
                            {
                                "step_id": 61,
                                "message": (
                                    "Done. All deliverables are generated; charge cutoff 1.6e-6."
                                ),
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            steps = _load_trajectory_steps(trial_dir)
            divs = extract_verifier_divergences(
                [(1, line)],
                trajectory_steps=steps,
                source_label="verifier:raw-verifier.json",
                trial_dir=trial_dir,
            )

        self.assertEqual(len(divs), 1)
        d = divs[0]
        self.assertTrue(d.get("is_delta_divergence"))
        self.assertEqual(d["agent_reported_value"], 0.898982845013923)
        self.assertNotAlmostEqual(d["agent_reported_value"], 1.59869e-06)
        self.assertFalse(d["agent_reported_matches"])
        self.assertIn("charges.csv:L3", d["agent_artifact_ref"])
        # Only execution steps are candidates: step 61 (final answer) is absent.
        self.assertEqual(d["agent_ref"], "trajectory:step:55")

    def test_delta_divergence_abstains_without_bound_artifact(self) -> None:
        line = (
            "R02-M01/site000003 charge differs from independent frozen EQeq replay "
            "by 1.59869e-06 e beyond its decimal serialization bound 1.00001e-10 e"
        )
        with tempfile.TemporaryDirectory() as tmp:
            trial_dir = Path(tmp)
            (trial_dir / "agent").mkdir()
            (trial_dir / "agent" / "trajectory.json").write_text(
                json.dumps(
                    {
                        "steps": [
                            {
                                "step_id": 61,
                                "message": "Done. charge = 1.6e-6, all outputs generated.",
                                "reasoning_content": "charge looks fine",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            steps = _load_trajectory_steps(trial_dir)
            divs = extract_verifier_divergences(
                [(1, line)],
                trajectory_steps=steps,
                source_label="verifier:raw-verifier.json",
                trial_dir=trial_dir,
            )
        d = divs[0]
        self.assertIsNone(d["agent_reported_value"])
        self.assertIsNone(d["agent_ref"])
        self.assertIsNone(d["agent_artifact_ref"])
        self.assertFalse(d["agent_reported_matches"])

    def test_delta_divergence_abstains_on_ambiguous_or_unbound_selector(self) -> None:
        line = (
            "R02-M01/site000003 charge differs from independent frozen EQeq replay "
            "by 1.59869e-06 e beyond its decimal serialization bound 1.00001e-10 e"
        )
        cases = (
            # `site000003` appears in two columns -> the record is ambiguous.
            "run_id,atom_id,note,charge_e\nR02-M01,site000003,site000003,0.898982845013923\n",
            # `R02-M01` never appears -> one identifier cannot be bound at all.
            "run_id,atom_id,charge_e\nR01-M01,site000003,0.893661300984714\n",
        )
        for csv_text in cases:
            with self.subTest(csv_text=csv_text.splitlines()[0]):
                with tempfile.TemporaryDirectory() as tmp:
                    trial_dir = Path(tmp)
                    outputs = trial_dir / "artifacts" / "app" / "outputs"
                    outputs.mkdir(parents=True)
                    (outputs / "charges.csv").write_text(csv_text, encoding="utf-8")
                    divs = extract_verifier_divergences(
                        [(1, line)],
                        trajectory_steps=[],
                        source_label="verifier:raw-verifier.json",
                        trial_dir=trial_dir,
                    )
                d = divs[0]
                self.assertIsNone(d["agent_reported_value"])
                self.assertIsNone(d["agent_artifact_ref"])
                self.assertFalse(d["agent_reported_matches"])


class TestMultiFormatAndCrossCheckPrecision(unittest.TestCase):
    """Covers .jsonl/messages.json corroboration, scale/sign hints, stale sync, and Gate 4/6 enrichment."""

    def test_jsonl_trajectory_corroboration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            trial_dir = Path(tmp)
            (trial_dir / "agent").mkdir()
            (trial_dir / "verifier").mkdir()
            jsonl_records = [
                {"role": "user", "content": "Compute max_force."},
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "call_1",
                            "name": "Bash",
                            "input": {"command": "python calc.py"},
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "call_1",
                            "content": "BFGS step: max_force = 0.02863 eV/A",
                        }
                    ],
                },
            ]
            (trial_dir / "agent" / "trajectory.jsonl").write_text(
                "\n".join(json.dumps(r) for r in jsonl_records) + "\n",
                encoding="utf-8",
            )
            (trial_dir / "verifier" / "verify.log").write_text(P1_LINE + "\n", encoding="utf-8")

            steps = _load_trajectory_steps(trial_dir)
            self.assertGreaterEqual(len(steps), 1)
            res = extract_scientific_errors(trial_dir=trial_dir)

        obs = res["scientific_observations"]
        self.assertEqual(len(obs), 1)
        self.assertTrue(obs[0]["agent_reported_matches"])
        self.assertEqual(obs[0]["agent_reported_value"], 0.02863)
        self.assertEqual(obs[0]["agent_ref"], "trajectory:step:2")

    def test_messages_json_corroboration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            trial_dir = Path(tmp)
            (trial_dir / "agent").mkdir()
            (trial_dir / "verifier").mkdir()
            messages_payload = {
                "messages": [
                    {"role": "user", "content": "Run calculation"},
                    {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": "tc_1",
                                "type": "function",
                                "function": {
                                    "name": "Bash",
                                    "arguments": '{"command": "python run.py"}',
                                },
                            }
                        ],
                    },
                    {
                        "role": "tool",
                        "tool_call_id": "tc_1",
                        "content": "max_force = 0.02863 eV/A",
                    },
                ]
            }
            (trial_dir / "agent" / "messages.json").write_text(
                json.dumps(messages_payload), encoding="utf-8"
            )
            (trial_dir / "verifier" / "verify.log").write_text(P1_LINE + "\n", encoding="utf-8")
            res = extract_scientific_errors(trial_dir=trial_dir)

        obs = res["scientific_observations"]
        self.assertEqual(len(obs), 1)
        self.assertTrue(obs[0]["agent_reported_matches"])
        self.assertEqual(obs[0]["agent_reported_value"], 0.02863)

    def test_detect_scale_or_sign_factor_units_decadic_and_sign(self) -> None:
        # Hartree -> eV (both directions)
        h_fwd = detect_scale_or_sign_factor(-272.11386, -10.0)
        self.assertIsNotNone(h_fwd)
        assert h_fwd is not None
        self.assertEqual(h_fwd["kind"], "unit_conversion")
        self.assertIn("Hartree/eV", h_fwd["label"])

        h_rev = detect_scale_or_sign_factor(-10.0, -272.11386)
        self.assertIsNotNone(h_rev)
        assert h_rev is not None
        self.assertEqual(h_rev["kind"], "unit_conversion")
        self.assertIn("Hartree/eV", h_rev["label"])

        # Bohr / Angstrom
        bohr = detect_scale_or_sign_factor(0.529177, 1.0)
        self.assertIsNotNone(bohr)
        assert bohr is not None
        self.assertEqual(bohr["kind"], "unit_conversion")
        self.assertIn("Bohr/Angstrom", bohr["label"])

        # Decadic scale (1000x)
        dec = detect_scale_or_sign_factor(1500.0, 1.5)
        self.assertIsNotNone(dec)
        assert dec is not None
        self.assertEqual(dec["kind"], "decadic_scale")
        self.assertEqual(dec["factor"], 1000.0)

        # Sign flip within 2%
        sign = detect_scale_or_sign_factor(-12.34, 12.40)
        self.assertIsNotNone(sign)
        assert sign is not None
        self.assertEqual(sign["kind"], "sign_flip")
        self.assertEqual(sign["factor"], -1.0)

        # Unit-token corroboration flag
        corr_yes = detect_scale_or_sign_factor(
            -10.0, -272.11386, metric="total_energy_Ha", context_text="energy in eV"
        )
        self.assertIsNotNone(corr_yes)
        assert corr_yes is not None
        self.assertTrue(corr_yes["unit_token_corroborated"])

        corr_no = detect_scale_or_sign_factor(-10.0, -272.11386, metric="value_x", context_text="")
        self.assertIsNotNone(corr_no)
        assert corr_no is not None
        self.assertFalse(corr_no["unit_token_corroborated"])

        # Non-matching, zero, None
        self.assertIsNone(detect_scale_or_sign_factor(0.0, -10.0))
        self.assertIsNone(detect_scale_or_sign_factor(None, -10.0))
        self.assertIsNone(detect_scale_or_sign_factor(2.0, 3.0))

    def test_large_deviation_named_metric_pairing_attaches_scale_hint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            trial_dir = Path(tmp)
            outputs = trial_dir / "artifacts" / "app" / "outputs"
            outputs.mkdir(parents=True)
            # Delivered in Hartree (-10.0) while verifier recomputed in eV (-272.11386): >96% diff
            (outputs / "results.json").write_text(
                json.dumps({"total_energy_Ha": -10.0, "dipole_z": -1.85}),
                encoding="utf-8",
            )
            verifier_report = {
                "failed_checks": [
                    {
                        "name": "energy_check",
                        "layer": "L3",
                        "ok": False,
                        "evidence": {
                            "metric": "total_energy_eV",
                            "recomputed": -272.11386,
                            "dipole_z": 1.85,
                        },
                    }
                ]
            }
            pair_verifier_evidence_with_artifacts(verifier_report, trial_dir)

        fc = verifier_report["failed_checks"][0]
        comps = fc.get("artifact_comparison") or []
        self.assertEqual(len(comps), 2)
        by_metric = {c["metric"]: c for c in comps}
        self.assertIn("total_energy_Ha", by_metric)
        self.assertIn("scale_or_sign_hint", by_metric["total_energy_Ha"])
        self.assertEqual(
            by_metric["total_energy_Ha"]["scale_or_sign_hint"]["kind"], "unit_conversion"
        )
        self.assertIn("Hartree/eV", by_metric["total_energy_Ha"]["scale_or_sign_hint"]["label"])
        self.assertEqual(by_metric["dipole_z"]["scale_or_sign_hint"]["kind"], "sign_flip")

    def test_stale_deliverable_sync_detected_and_guarded(self) -> None:
        line = "FAIL: reported final_energy -11.0000 != recomputed -12.3456 (tol 0.001)"
        with tempfile.TemporaryDirectory() as tmp:
            trial_dir = Path(tmp)
            (trial_dir / "artifacts").mkdir()
            (trial_dir / "artifacts" / "results.json").write_text(
                json.dumps({"final_energy": -11.0}), encoding="utf-8"
            )
            steps = _steps(
                {"step_id": 4, "observation": "old run: final_energy = -11.0000 eV"},
                {"step_id": 9, "observation": "corrected run: final_energy = -12.3456 eV"},
            )
            obs = extract_verifier_divergences(
                _one_line(line),
                trajectory_steps=steps,
                trial_dir=trial_dir,
            )
        self.assertEqual(len(obs), 1)
        o = obs[0]
        self.assertTrue(o.get("stale_deliverable_sync"))
        self.assertEqual(o.get("recomputed_match_in_trajectory_ref"), "trajectory:step:9")

        # Guard: when artifact already holds recomputed_value, stale_deliverable_sync is not set
        with tempfile.TemporaryDirectory() as tmp:
            trial_dir = Path(tmp)
            (trial_dir / "artifacts").mkdir()
            (trial_dir / "artifacts" / "results.json").write_text(
                json.dumps({"final_energy": -12.3456}), encoding="utf-8"
            )
            obs_fresh = extract_verifier_divergences(
                _one_line(line),
                trajectory_steps=steps,
                trial_dir=trial_dir,
            )
        self.assertNotIn("stale_deliverable_sync", obs_fresh[0])

        # Guard: missing delivered artifact on disk must NOT trigger stale_deliverable_sync
        with tempfile.TemporaryDirectory() as tmp:
            trial_dir = Path(tmp)
            (trial_dir / "artifacts").mkdir()
            obs_no_art = extract_verifier_divergences(
                _one_line(line),
                trajectory_steps=steps,
                trial_dir=trial_dir,
            )
        self.assertNotIn("stale_deliverable_sync", obs_no_art[0])

        # Guard: recomputed_value appearing only inside tool_calls code or errored observation must NOT trigger
        code_only_steps = _steps(
            {
                "step_id": 6,
                "tool_calls": [
                    {"name": "Write", "arguments": {"content": "final_energy = -12.3456"}}
                ],
                "observation": "File written successfully.",
            },
            {
                "step_id": 7,
                "tool_calls": [{"name": "Bash", "arguments": {"command": "python run.py"}}],
                "observation": "Exit code: 1\nERROR: final_energy = -12.3456 aborted",
            },
        )
        with tempfile.TemporaryDirectory() as tmp:
            trial_dir = Path(tmp)
            (trial_dir / "artifacts").mkdir()
            (trial_dir / "artifacts" / "results.json").write_text(
                json.dumps({"final_energy": -11.0}), encoding="utf-8"
            )
            obs_code_only = extract_verifier_divergences(
                _one_line(line),
                trajectory_steps=code_only_steps,
                trial_dir=trial_dir,
            )
        self.assertNotIn("stale_deliverable_sync", obs_code_only[0])

        # Guard: P2_LINE (-0.02125869 vs -0.02125870) must not confuse reported with recomputed
        p2_steps = _steps(
            {"step_id": 7, "observation": "final_energy = \u22120.02125869 eV after relaxation"}
        )
        obs_p2 = extract_verifier_divergences(_one_line(P2_LINE), trajectory_steps=p2_steps)
        self.assertNotIn("stale_deliverable_sync", obs_p2[0])

    def test_gate4_enriches_scale_hint_and_stale_sync(self) -> None:
        evidence = {
            "case_id": "unit-sync-case",
            "trial_name": "trial_1",
            "runtime": {"agent_started": True, "verifier_started": True},
            "artifacts": [
                {"artifact_id": "art:trial_result", "exists": True},
                {"artifact_id": "art:trajectory_json", "exists": True},
            ],
            "missing_artifacts": [],
            "error_observations": [],
            "contract_observations": [
                {
                    "contract_id": "contract:1",
                    "item": "results.json:values.final_energy",
                    "alignment": "consistent",
                }
            ],
            "verifier_observations": [
                {
                    "obs_id": "ver:fail_log",
                    "type": "verifier_fail_message",
                    "matched_text": "FAIL: reported final_energy -10.0 != recomputed -272.11386 (tol 0.01)",
                }
            ],
            "scientific_observations": [
                {
                    "sci_id": "sci:recompute_divergence:1",
                    "software": "verifier",
                    "error_family": "verifier_recompute_divergence",
                    "metric": "final_energy",
                    "reported_value": -10.0,
                    "recomputed_value": -272.11386,
                    "tolerance": 0.01,
                    "check_kind": "recompute_divergence",
                    "agent_reported_value": -10.0,
                    "agent_ref": "trajectory:step:4",
                    "agent_reported_matches": True,
                    "discriminating": True,
                    "scale_or_sign_hint": {
                        "kind": "unit_conversion",
                        "factor": 0.036749,
                        "label": "Hartree/eV (÷27.211386)",
                    },
                    "stale_deliverable_sync": True,
                    "recomputed_match_in_trajectory_ref": "trajectory:step:9",
                    "source_ref": "verifier:verify.log:L1",
                }
            ],
            "behavioral_signals": [],
            "timeline": [
                {"event_id": "trajectory:step:4", "actor": "agent", "event_type": "tool_call"}
            ],
        }
        analysis = run_attribution_engine(evidence, {"candidate_hypotheses": []})
        self.assertEqual(
            analysis["decision_trace"]["selected"]["gate_id"], "gate4_numerical_divergence"
        )
        cf_codes = [cf["code"] for cf in analysis["contributing_factors"]]
        self.assertIn("AGENT_RESULT_VALIDATION", cf_codes)
        self.assertIn("AGENT_PLANNING", cf_codes)
        actions_text = " ".join(a["action"] for a in analysis["recommended_actions"])
        self.assertIn("Hartree/eV", actions_text)
        self.assertIn("trajectory:step:9", actions_text)
        ok, errors = validate_all(evidence, analysis)
        self.assertTrue(ok, errors)

    def test_gate6_5e_enriches_scale_hint_and_trace_provenance(self) -> None:
        evidence = {
            "case_id": "gate6-5e-enrich",
            "trial_name": "trial_1",
            "runtime": {"agent_started": True, "verifier_started": True},
            "metadata": {"declared_deliverables": ["results.json"]},
            "artifacts": [
                {"artifact_id": "art:trial_result", "exists": True},
                {"artifact_id": "art:trajectory_json", "exists": True},
            ],
            "missing_artifacts": [],
            "error_observations": [],
            "contract_observations": [],
            "verifier_observations": [
                {
                    "obs_id": "ver:fail_log",
                    "type": "verifier_fail_message",
                    "matched_text": "FAIL: dipole and execution_or_analysis_trace checks failed",
                }
            ],
            "scientific_observations": [],
            "behavioral_signals": [
                {
                    "signal_id": "sig:1",
                    "signal_type": "unverified_output",
                    "event_ref": "trajectory:step:8",
                }
            ],
            "causal_anchors": {
                "deliverable_generation": {
                    "event_id": "trajectory:step:8",
                    "command": "python write_results.py --output results.json",
                }
            },
            "timeline": [
                {"event_id": "trajectory:step:8", "actor": "agent", "event_type": "tool_call"}
            ],
            "verifier_report": {
                "failed_checks": [
                    {
                        "name": "dipole_check",
                        "failure_kind": "numerical",
                        "detail": "dipole sign inverted",
                        "artifact_comparison": [
                            {
                                "metric": "dipole_z",
                                "delivered": -1.85,
                                "recomputed": 1.85,
                                "abs_diff": 3.7,
                                "artifact_ref": "art:artifacts/results.json",
                                "scale_or_sign_hint": {
                                    "kind": "sign_flip",
                                    "factor": -1.0,
                                    "label": "sign convention flip (delivered ≈ -recomputed)",
                                },
                            }
                        ],
                    },
                    {
                        "name": "execution_or_analysis_trace",
                        "failure_kind": "trace_or_provenance",
                        "detail": "missing provenance log",
                    },
                ]
            },
        }
        analysis = run_attribution_engine(evidence, {"candidate_hypotheses": []})
        self.assertEqual(analysis["decision_trace"]["selected"]["gate_id"], "gate6_agent_primary")
        self.assertEqual(analysis["decision_trace"]["selected"]["subcase_id"], "5e")
        cf_codes = [cf["code"] for cf in analysis["contributing_factors"]]
        self.assertIn("AGENT_TASK_UNDERSTANDING", cf_codes)
        self.assertIn("AGENT_PLANNING", cf_codes)
        ok, errors = validate_all(evidence, analysis)
        self.assertTrue(ok, errors)


if __name__ == "__main__":
    unittest.main()
