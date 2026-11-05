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

from extract_scientific_errors import extract_scientific_errors, extract_verifier_divergences


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
    """Serialize raw ATIF steps the same way `_load_trajectory_steps` does."""
    return [
        (step.get("step_id"), json.dumps(step, ensure_ascii=False, default=str)) for step in steps
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
    """Agent-side cross-corroboration against serialized trajectory steps."""

    def test_corroboration_hit_same_step(self) -> None:
        steps = _steps(
            {"step_id": 11, "message": "running relaxation"},
            {"step_id": 12, "message": "BFGS step: max_force = 0.02863 eV/A"},
            {"step_id": 13, "message": "done"},
        )
        obs = extract_verifier_divergences(_one_line(P1_LINE), trajectory_steps=steps)
        self.assertEqual(len(obs), 1)
        o = obs[0]
        self.assertTrue(o["agent_reported_matches"])
        self.assertEqual(o["agent_reported_value"], 0.02863)
        self.assertEqual(o["agent_ref"], "trajectory:step:12")

    def test_corroboration_miss_when_value_absent(self) -> None:
        steps = _steps({"step_id": 12, "message": "BFGS step: max_force = 0.05 eV/A"})
        obs = extract_verifier_divergences(_one_line(P1_LINE), trajectory_steps=steps)
        self.assertEqual(len(obs), 1)
        o = obs[0]
        self.assertFalse(o["agent_reported_matches"])
        self.assertIsNone(o["agent_reported_value"])
        self.assertIsNone(o["agent_ref"])

    def test_corroboration_requires_metric_in_same_step(self) -> None:
        # Value string appears but the metric name is in a different step.
        steps = _steps(
            {"step_id": 5, "message": "printed 0.02863 somewhere"},
            {"step_id": 6, "message": "max_force convergence check"},
        )
        obs = extract_verifier_divergences(_one_line(P1_LINE), trajectory_steps=steps)
        self.assertEqual(len(obs), 1)
        self.assertFalse(obs[0]["agent_reported_matches"])
        self.assertIsNone(obs[0]["agent_ref"])

    def test_corroboration_handles_unicode_minus(self) -> None:
        steps = _steps(
            {"step_id": 7, "message": "final_energy = \u22120.02125869 eV after relaxation"}
        )
        obs = extract_verifier_divergences(_one_line(P2_LINE), trajectory_steps=steps)
        self.assertEqual(len(obs), 1)
        self.assertTrue(obs[0]["agent_reported_matches"])
        self.assertEqual(obs[0]["agent_reported_value"], -0.02125869)
        self.assertEqual(obs[0]["agent_ref"], "trajectory:step:7")

    def test_corroboration_falls_back_to_step_ordinal_without_step_id(self) -> None:
        steps = _steps({"message": "BFGS step: max_force = 0.02863 eV/A"})
        obs = extract_verifier_divergences(_one_line(P1_LINE), trajectory_steps=steps)
        self.assertEqual(len(obs), 1)
        self.assertTrue(obs[0]["agent_reported_matches"])
        self.assertEqual(obs[0]["agent_ref"], "trajectory:step:1")


class TestExtractionIntegration(unittest.TestCase):
    """Wiring through `extract_scientific_errors` incl. coexistence and fixtures."""

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
            "messages": [],
        }
        res = extract_scientific_errors(trial_dir=None, verifier_report=report)
        obs = res["scientific_observations"]
        self.assertEqual(len(obs), 1)
        o = obs[0]
        self.assertEqual(o["software"], "verifier")
        self.assertEqual(o["error_family"], "verifier_recompute_divergence")
        self.assertEqual(o["metric"], "charge")
        self.assertAlmostEqual(o["reported_value"], 1.59869e-06)
        self.assertEqual(o["recomputed_value"], 0.0)
        self.assertAlmostEqual(o["tolerance"], 1.00001e-10)
        self.assertEqual(o["source_ref"], "verifier:raw-verifier.json:neutral_stationary")
        self.assertTrue(o["discriminating"])


if __name__ == "__main__":
    unittest.main()
