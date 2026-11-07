#!/usr/bin/env python3
"""
Confidence scoring tests (`tests/test_confidence.py`)

Covers the evidence-strength heuristic in `scripts/confidence.py` (dynamic
denominator with case-specific components) and the verifier check-line
statistics in `scripts/verify_check_stats.py` (`build_check_stats`), using
log snippets shaped after the real `FAIL:` / `PASS:` verifier output.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
SCRIPT_DIR = ROOT_DIR / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from attribution.context import AttributionContext, _verifier_check_localization, build_context
from attribution.trace import DecisionTrace
from confidence import (
    MAX_EVIDENCE_POINTS,
    attach_confidence_metadata,
    build_confidence_breakdown,
    compute_evidence_confidence,
)
from verify_check_stats import build_check_stats


FIXTURE_VERIFY_LOG = (
    ROOT_DIR
    / "tests"
    / "fixtures"
    / "verifier_divergence"
    / "ase-custom-calculator"
    / "verifier"
    / "verify.log"
)

# Real verifier line from the committed fixture (verifier/verify.log of the
# ase-custom-calculator failure case).
REAL_FAIL_LINE = "FAIL: reported max_force 0.02863 != recomputed 0.03666 (tol 0.001)"
REAL_FAIL_SUMMARY = "reported max_force 0.02863 != recomputed 0.03666 (tol 0.001)"


class TestComputeEvidenceConfidenceBackwardCompatibility(unittest.TestCase):
    """(a) Without the new kwargs the score and kind are bit-identical to the
    legacy 6-point formula, and strength is preserved wherever the new
    score-bound thresholds agree with the old point-bound ones."""

    def test_no_new_kwargs_three_legacy_combos(self) -> None:
        # Legacy formula: min(3, direct) + (cross >= 1) + 2*cf - keyword
        # - min(2, missing) - 2*contradict, normalized by 6.
        combos = [
            # (kwargs, legacy points, legacy strength by clamped thresholds)
            ({"direct_causal_evidence": 3, "cross_source_corroboration": 1}, 4, "high"),
            ({"direct_causal_evidence": 3, "cross_source_corroboration": 0}, 3, "medium"),
            (
                {
                    "direct_causal_evidence": 0,
                    "missing_discriminating_evidence": 2,
                    "contradicting_evidence": 1,
                },
                -4,
                "low",
            ),
        ]
        for kwargs, legacy_points, legacy_strength in combos:
            with self.subTest(**kwargs):
                score, kind, strength = compute_evidence_confidence(**kwargs)
                legacy_score = round(
                    max(0, min(MAX_EVIDENCE_POINTS, legacy_points)) / MAX_EVIDENCE_POINTS, 2
                )
                self.assertEqual(score, legacy_score)
                self.assertEqual(kind, "heuristic_evidence_score")
                # New score-bound thresholds preserve the legacy strength for
                # every legacy bucket (4/6=0.67 high, 3/6=0.5 medium, <2 low).
                self.assertEqual(strength, legacy_strength)

    def test_backward_compat_contract_tuple(self) -> None:
        """Acceptance contract: the canonical legacy call is unchanged."""
        self.assertEqual(
            compute_evidence_confidence(direct_causal_evidence=3, cross_source_corroboration=1),
            (0.67, "heuristic_evidence_score", "high"),
        )

    def test_max_evidence_points_constant_unchanged(self) -> None:
        """The base scale stays 6 (consumed by existing ratio assertions)."""
        self.assertEqual(MAX_EVIDENCE_POINTS, 6)

    def test_strength_threshold_rebinding_two_of_six_now_low(self) -> None:
        """2/6 = 0.33 falls below the new 0.4 medium cutoff by design: the
        score itself is unchanged, only the strength label rebinds."""
        score, _kind, strength = compute_evidence_confidence(direct_causal_evidence=2)
        self.assertEqual(score, 0.33)
        self.assertEqual(strength, "low")


class TestMarginComponent(unittest.TestCase):
    """(b) margin_ratio: >=4 adds +2, 2..<4 adds +1, <2 adds +0; unusable
    values count as not supplied (no points, no denominator expansion)."""

    def test_margin_ratio_at_or_above_four_adds_two_points(self) -> None:
        # Base direct=1, cross=1 -> 2 points. With margin: (2+2)/8 = 0.5.
        score, _kind, strength = compute_evidence_confidence(
            direct_causal_evidence=1, cross_source_corroboration=1, margin_ratio=8.0
        )
        self.assertEqual((score, strength), (0.5, "medium"))
        # Boundary: exactly 4.0 still counts as strong (3+2)/8 = 0.62.
        score, _kind, strength = compute_evidence_confidence(
            direct_causal_evidence=3, margin_ratio=4.0
        )
        self.assertEqual((score, strength), (0.62, "medium"))

    def test_margin_ratio_between_two_and_four_adds_one_point(self) -> None:
        # Base 2 points; +1 point and +2 denominator -> 3/8 = 0.38. This
        # differs from both "no point added" (2/8 = 0.25) and "no component
        # supplied" (2/6 = 0.33), pinning the +1 point exactly.
        score, _kind, strength = compute_evidence_confidence(
            direct_causal_evidence=1, cross_source_corroboration=1, margin_ratio=3.0
        )
        self.assertEqual((score, strength), (0.38, "low"))

    def test_margin_ratio_below_two_adds_zero_but_expands_denominator(self) -> None:
        # 2 points with a supplied-but-weak margin: 2/8 = 0.25 (denominator
        # expanded from 6 to 8 even though the component adds no points).
        score, _kind, strength = compute_evidence_confidence(
            direct_causal_evidence=1, cross_source_corroboration=1, margin_ratio=1.2
        )
        self.assertEqual((score, strength), (0.25, "low"))

    def test_margin_ratio_none_zero_nan_negative_treated_as_not_supplied(self) -> None:
        baseline = compute_evidence_confidence(
            direct_causal_evidence=1, cross_source_corroboration=1
        )
        self.assertEqual(baseline, (0.33, "heuristic_evidence_score", "low"))
        for unusable in (None, 0.0, -3.0, float("nan")):
            with self.subTest(margin_ratio=unusable):
                result = compute_evidence_confidence(
                    direct_causal_evidence=1,
                    cross_source_corroboration=1,
                    margin_ratio=unusable,
                )
                # Identical to the baseline: no points AND denominator 6.
                self.assertEqual(result, baseline)


class TestLocalizationComponent(unittest.TestCase):
    """(c) check_localization: "single" +1, "widespread" -1, anything else
    counts as not supplied."""

    def test_single_failure_adds_one_point(self) -> None:
        # (2+1)/7 = 0.43.
        score, _kind, strength = compute_evidence_confidence(
            direct_causal_evidence=1, cross_source_corroboration=1, check_localization="single"
        )
        self.assertEqual((score, strength), (0.43, "medium"))

    def test_widespread_failure_subtracts_one_point(self) -> None:
        # (2-1)/7 = 0.14.
        score, _kind, strength = compute_evidence_confidence(
            direct_causal_evidence=1,
            cross_source_corroboration=1,
            check_localization="widespread",
        )
        self.assertEqual((score, strength), (0.14, "low"))

    def test_unknown_or_missing_label_treated_as_not_supplied(self) -> None:
        baseline = compute_evidence_confidence(
            direct_causal_evidence=1, cross_source_corroboration=1
        )
        self.assertEqual(baseline, (0.33, "heuristic_evidence_score", "low"))
        for unusable in (None, "", "cluster", "Single", "WIDESPREAD"):
            with self.subTest(check_localization=unusable):
                result = compute_evidence_confidence(
                    direct_causal_evidence=1,
                    cross_source_corroboration=1,
                    check_localization=unusable,
                )
                # Identical to the baseline: unrecognized labels must not
                # expand the denominator either.
                self.assertEqual(result, baseline)


class TestHypothesisSeparationComponent(unittest.TestCase):
    """(d) hypothesis_separation: >= 0.6 adds +1, < 0.6 adds +0."""

    def test_separation_at_threshold_adds_one_point(self) -> None:
        # Exactly 0.6 counts as separated: (2+1)/7 = 0.43.
        score, _kind, strength = compute_evidence_confidence(
            direct_causal_evidence=1, cross_source_corroboration=1, hypothesis_separation=0.6
        )
        self.assertEqual((score, strength), (0.43, "medium"))

    def test_separation_below_threshold_adds_zero_but_expands_denominator(self) -> None:
        # 2/7 = 0.29: denominator expanded (from 0.33) with no point added.
        score, _kind, strength = compute_evidence_confidence(
            direct_causal_evidence=1, cross_source_corroboration=1, hypothesis_separation=0.59
        )
        self.assertEqual((score, strength), (0.29, "low"))

    def test_separation_none_or_non_finite_treated_as_not_supplied(self) -> None:
        baseline = compute_evidence_confidence(
            direct_causal_evidence=1, cross_source_corroboration=1
        )
        for unusable in (None, float("nan")):
            with self.subTest(hypothesis_separation=unusable):
                result = compute_evidence_confidence(
                    direct_causal_evidence=1,
                    cross_source_corroboration=1,
                    hypothesis_separation=unusable,
                )
                self.assertEqual(result, baseline)


class TestCounterfactualAndDynamicDenominator(unittest.TestCase):
    """(e)/(f) counterfactual_supported keeps its legacy +2 and combines with
    the margin component; the denominator grows with every supplied
    component up to 10, so full scores are reachable."""

    def test_counterfactual_with_margin_lifts_above_0_8(self) -> None:
        # Base 2+1+2 = 5, margin +2 -> 7/8 = 0.88 ("high").
        score, kind, strength = compute_evidence_confidence(
            direct_causal_evidence=2,
            cross_source_corroboration=1,
            counterfactual_supported=True,
            margin_ratio=8.0,
        )
        self.assertEqual((score, kind, strength), (0.88, "heuristic_evidence_score", "high"))
        self.assertGreater(score, 0.8)

    def test_margin_only_full_score_reaches_1_0(self) -> None:
        # Base 6 (legacy max) + margin +2 on an 8-point denominator -> 1.0.
        score, _kind, strength = compute_evidence_confidence(
            direct_causal_evidence=3,
            cross_source_corroboration=1,
            counterfactual_supported=True,
            margin_ratio=8.0,
        )
        self.assertEqual((score, strength), (1.0, "high"))

    def test_full_stack_saturates_10_point_denominator(self) -> None:
        # All three components supplied: 6 + 2 + 1 + 1 = 10/10.
        score, _kind, strength = compute_evidence_confidence(
            direct_causal_evidence=3,
            cross_source_corroboration=1,
            counterfactual_supported=True,
            margin_ratio=8.0,
            check_localization="single",
            hypothesis_separation=0.9,
        )
        self.assertEqual((score, strength), (1.0, "high"))

    def test_denominator_grows_only_with_supplied_components(self) -> None:
        # loc + sep (no margin): (2+1+1)/8 = 0.5, not 4/6 = 0.67.
        score, _kind, _strength = compute_evidence_confidence(
            direct_causal_evidence=1,
            cross_source_corroboration=1,
            check_localization="single",
            hypothesis_separation=0.9,
        )
        self.assertEqual(score, 0.5)
        # margin + sep (no loc): (2+2+1)/9 = 0.56, pinning the 9 denominator.
        score, _kind, _strength = compute_evidence_confidence(
            direct_causal_evidence=1,
            cross_source_corroboration=1,
            margin_ratio=8.0,
            hypothesis_separation=0.9,
        )
        self.assertEqual(score, 0.56)
        # All three with negative contributions: (2+0-1+0)/10 = 0.1.
        score, _kind, strength = compute_evidence_confidence(
            direct_causal_evidence=1,
            cross_source_corroboration=1,
            margin_ratio=1.2,
            check_localization="widespread",
            hypothesis_separation=0.5,
        )
        self.assertEqual((score, strength), (0.1, "low"))

    def test_points_clamped_to_dynamic_denominator(self) -> None:
        # Negative components can drive raw points below zero; the clamp
        # holds at 0 on any denominator.
        score, _kind, strength = compute_evidence_confidence(
            contradicting_evidence=3, margin_ratio=8.0
        )
        self.assertEqual((score, strength), (0.0, "low"))


class TestShortCircuitsUnaffected(unittest.TestCase):
    """(g) ruled_out / verdict_passed short-circuits ignore the new
    components entirely."""

    def test_ruled_out_ignores_new_components(self) -> None:
        result = compute_evidence_confidence(
            ruled_out=True,
            margin_ratio=8.0,
            check_localization="single",
            hypothesis_separation=0.9,
        )
        self.assertEqual(result, (0.0, "heuristic_evidence_score", "low"))

    def test_verdict_passed_ignores_new_components(self) -> None:
        result = compute_evidence_confidence(
            verdict_passed=True,
            margin_ratio=8.0,
            check_localization="single",
            hypothesis_separation=0.9,
        )
        self.assertEqual(result, (1.0, "heuristic_evidence_score", "high"))


class TestAttachConfidenceMetadataPassthrough(unittest.TestCase):
    """attach_confidence_metadata forwards the three new kwargs and keeps its
    legacy defaults and return contract."""

    def test_new_kwargs_pass_through(self) -> None:
        # Base 3+1=4, all three components supplied: (4+2+1+1)/10 = 0.8.
        target = attach_confidence_metadata(
            {},
            direct_causal_evidence=3,
            cross_source_corroboration=1,
            margin_ratio=8.0,
            check_localization="single",
            hypothesis_separation=0.9,
        )
        self.assertEqual(
            (target["confidence"], target["confidence_kind"], target["evidence_strength"]),
            (0.8, "heuristic_evidence_score", "high"),
        )
        self.assertEqual(
            target,
            attach_confidence_metadata(
                {},
                direct_causal_evidence=3,
                cross_source_corroboration=1,
                margin_ratio=8.0,
                check_localization="single",
                hypothesis_separation=0.9,
            ),
        )

    def test_legacy_call_unchanged(self) -> None:
        res1 = attach_confidence_metadata(
            {}, direct_causal_evidence=3, cross_source_corroboration=1
        )
        self.assertEqual(res1["confidence"], 0.67)
        self.assertEqual(res1["confidence_kind"], "heuristic_evidence_score")
        self.assertEqual(res1["evidence_strength"], "high")
        self.assertIn("confidence_breakdown", res1)
        self.assertEqual(res1["confidence_breakdown"]["points"], 4)
        self.assertEqual(res1["confidence_breakdown"]["raw_points"], 4)
        self.assertEqual(res1["confidence_breakdown"]["denominator"], 6)

        # Legacy default (direct_causal_evidence=1) is untouched: 1/6 = 0.17.
        res2 = attach_confidence_metadata({})
        self.assertEqual(res2["confidence"], 0.17)
        self.assertEqual(res2["confidence_kind"], "heuristic_evidence_score")
        self.assertEqual(res2["evidence_strength"], "low")
        self.assertIn("confidence_breakdown", res2)
        self.assertEqual(res2["confidence_breakdown"]["points"], 1)
        self.assertEqual(res2["confidence_breakdown"]["raw_points"], 1)
        self.assertEqual(res2["confidence_breakdown"]["denominator"], 6)


class TestBuildConfidenceBreakdown(unittest.TestCase):
    """Structured confidence_breakdown receipt testing."""

    def test_verdict_passed_breakdown(self) -> None:
        bd = build_confidence_breakdown(verdict_passed=True)
        self.assertEqual(bd["points"], 6)
        self.assertEqual(bd["raw_points"], 6)
        self.assertEqual(bd["denominator"], 6)
        self.assertEqual(
            bd["factors"],
            [
                {
                    "key": "verdict_passed",
                    "delta": 6,
                    "label_zh": "验证规则全量通过",
                    "label_en": "All verification rules passed",
                }
            ],
        )
        self.assertEqual(bd["missing_to_raise"], [])

    def test_ruled_out_breakdown(self) -> None:
        bd = build_confidence_breakdown(ruled_out=True)
        self.assertEqual(bd["points"], 0)
        self.assertEqual(bd["raw_points"], 0)
        self.assertEqual(bd["denominator"], 6)
        self.assertEqual(
            bd["factors"],
            [
                {
                    "key": "ruled_out",
                    "delta": 0,
                    "label_zh": "反驳证据已排除",
                    "label_en": "Ruled out by contradicting evidence",
                }
            ],
        )
        self.assertEqual(bd["missing_to_raise"], [])

    def test_active_factors_and_deltas(self) -> None:
        bd = build_confidence_breakdown(
            direct_causal_evidence=2,
            cross_source_corroboration=1,
            counterfactual_supported=True,
            margin_ratio=5.0,
            check_localization="single",
            hypothesis_separation=0.8,
            single_keyword_only=True,
            missing_discriminating_evidence=1,
            contradicting_evidence=1,
        )
        # raw points: +2 +1 +2 +2 +1 +1 -1 -1 -2 = 5
        # denominator: 6 + 2 + 1 + 1 = 10
        self.assertEqual(bd["denominator"], 10)
        self.assertEqual(bd["raw_points"], 5)
        self.assertEqual(bd["points"], 5)
        keys = [f["key"] for f in bd["factors"]]
        self.assertEqual(
            keys,
            [
                "direct_causal_evidence",
                "cross_source_corroboration",
                "counterfactual_supported",
                "margin_ratio",
                "check_localization",
                "hypothesis_separation",
                "single_keyword_only",
                "missing_discriminating_evidence",
                "contradicting_evidence",
            ],
        )
        # Verify delta values
        deltas = {f["key"]: f["delta"] for f in bd["factors"]}
        self.assertEqual(deltas["direct_causal_evidence"], 2)
        self.assertEqual(deltas["cross_source_corroboration"], 1)
        self.assertEqual(deltas["counterfactual_supported"], 2)
        self.assertEqual(deltas["margin_ratio"], 2)
        self.assertEqual(deltas["check_localization"], 1)
        self.assertEqual(deltas["hypothesis_separation"], 1)
        self.assertEqual(deltas["single_keyword_only"], -1)
        self.assertEqual(deltas["missing_discriminating_evidence"], -1)
        self.assertEqual(deltas["contradicting_evidence"], -2)

    def test_moderate_margin_and_widespread_localization(self) -> None:
        bd = build_confidence_breakdown(
            direct_causal_evidence=3,
            margin_ratio=2.5,
            check_localization="widespread",
        )
        deltas = {f["key"]: f["delta"] for f in bd["factors"]}
        self.assertEqual(deltas["margin_ratio"], 1)
        self.assertEqual(deltas["check_localization"], -1)
        self.assertIn(
            "2.5", [f["label_zh"] for f in bd["factors"] if f["key"] == "margin_ratio"][0]
        )

    def test_clamped_points_when_raw_points_negative(self) -> None:
        bd = build_confidence_breakdown(
            direct_causal_evidence=0,
            contradicting_evidence=3,
        )
        self.assertEqual(bd["raw_points"], -6)
        self.assertEqual(bd["points"], 0)
        self.assertEqual(bd["denominator"], 6)

    def test_missing_to_raise_auto_population(self) -> None:
        # Confidence < 0.75 without counterfactual and without margin ratio
        bd = build_confidence_breakdown(
            direct_causal_evidence=3,
            cross_source_corroboration=1,
        )
        self.assertEqual(bd["points"], 4)
        self.assertEqual(bd["denominator"], 6)
        # 4/6 = 0.67 < 0.75
        self.assertEqual(len(bd["missing_to_raise"]), 2)
        self.assertIn("需独立复算值或唯一失败检查以构成反事实闭环", bd["missing_to_raise"])
        self.assertIn("验证器未提供定量数值偏差与容差比值", bd["missing_to_raise"])

    def test_missing_to_raise_not_populated_when_confidence_ge_75(self) -> None:
        bd = build_confidence_breakdown(
            direct_causal_evidence=3,
            cross_source_corroboration=1,
            counterfactual_supported=True,
            margin_ratio=8.0,
        )
        # (3 + 1 + 2 + 2) / 8 = 8/8 = 1.0 >= 0.75
        self.assertEqual(bd["missing_to_raise"], [])

    def test_missing_to_raise_custom_override(self) -> None:
        custom = ["custom check required"]
        bd = build_confidence_breakdown(
            direct_causal_evidence=1,
            missing_to_raise=custom,
        )
        self.assertEqual(bd["missing_to_raise"], ["custom check required"])


class TestBuildCheckStats(unittest.TestCase):
    """(h) build_check_stats on real-format log snippets: single failure,
    widespread failure, and no check lines."""

    def test_real_fixture_log_fail_fast_single_fail_line(self) -> None:
        """The committed verify.log: fail-fast verifiers emit exactly one
        FAIL line and no per-check PASS lines, so single_failure cannot be
        confirmed from the log alone (total < 2) but the case is a
        counterfactual candidate."""
        log_text = FIXTURE_VERIFY_LOG.read_text(encoding="utf-8")
        stats = build_check_stats(log_text)
        self.assertEqual(stats["total_checks"], 1)
        self.assertEqual(stats["passed_checks"], 0)
        self.assertEqual(stats["failed_checks"], 1)
        self.assertEqual(stats["failed_check_summaries"], [REAL_FAIL_SUMMARY])
        self.assertFalse(stats["single_failure"])
        self.assertFalse(stats["widespread_failure"])
        self.assertTrue(stats["counterfactual_candidate"])

    def test_single_failure_with_per_check_pass_lines(self) -> None:
        log_text = "\n".join(
            [
                "PASS: results.json follows the {'values': ..., 'units': ...} schema",
                "  PASS: relaxed.xyz is a force minimum of the pinned potential",
                REAL_FAIL_LINE,
            ]
        )
        stats = build_check_stats(log_text)
        self.assertEqual(stats["total_checks"], 3)
        self.assertEqual(stats["passed_checks"], 2)
        self.assertEqual(stats["failed_checks"], 1)
        self.assertEqual(stats["failed_check_summaries"], [REAL_FAIL_SUMMARY])
        self.assertTrue(stats["single_failure"])
        self.assertFalse(stats["widespread_failure"])
        self.assertTrue(stats["counterfactual_candidate"])

    def test_widespread_failure_multiple_fails(self) -> None:
        log_text = "\n".join(
            [
                "FAIL: hidden config 1: agent energy -0.410 != analytic Morse -0.500 eV (tol 1e-06)",
                "FAIL: hidden config 2: agent energy -0.401 != analytic Morse -0.500 eV (tol 1e-06)",
                (
                    "FAIL: hidden config 3: agent forces deviate from the finite-difference "
                    "gradient by 1.20e-01 eV/A (tol 1e-06)"
                ),
                "PASS: ase-custom-calculator",
            ]
        )
        stats = build_check_stats(log_text)
        self.assertEqual(stats["total_checks"], 4)
        self.assertEqual(stats["passed_checks"], 1)
        self.assertEqual(stats["failed_checks"], 3)
        self.assertEqual(len(stats["failed_check_summaries"]), 3)
        self.assertFalse(stats["single_failure"])
        self.assertTrue(stats["widespread_failure"])
        self.assertFalse(stats["counterfactual_candidate"])

    def test_no_check_lines_yields_zeroes_and_false(self) -> None:
        for empty in ("", "\n", "   \n  \n"):
            with self.subTest(text=repr(empty)):
                stats = build_check_stats(empty)
                self.assertEqual(
                    stats,
                    {
                        "total_checks": 0,
                        "passed_checks": 0,
                        "failed_checks": 0,
                        "failed_check_summaries": [],
                        "single_failure": False,
                        "widespread_failure": False,
                        "counterfactual_candidate": False,
                    },
                )
        # Runtime noise without check markers must not be miscounted.
        stats = build_check_stats(
            "Traceback (most recent call last):\nKeyError: 'values'\nreward=0\n"
        )
        self.assertEqual(stats["total_checks"], 0)
        self.assertFalse(stats["single_failure"])
        self.assertFalse(stats["widespread_failure"])
        self.assertFalse(stats["counterfactual_candidate"])

    def test_bracket_style_per_check_lines(self) -> None:
        stats = build_check_stats(
            "[PASS] files present\n[FAIL] results.json schema mismatch\n[PASS] units present\n"
        )
        self.assertEqual(stats["total_checks"], 3)
        self.assertEqual(stats["passed_checks"], 2)
        self.assertEqual(stats["failed_checks"], 1)
        self.assertEqual(stats["failed_check_summaries"], ["results.json schema mismatch"])
        self.assertTrue(stats["single_failure"])
        self.assertTrue(stats["counterfactual_candidate"])
        self.assertFalse(stats["widespread_failure"])

    def test_tap_style_per_check_lines(self) -> None:
        stats = build_check_stats(
            "ok 1 - file existence\nok 2 - schema\nnot ok 3 - energy mismatch\n"
        )
        self.assertEqual(stats["total_checks"], 3)
        self.assertEqual(stats["passed_checks"], 2)
        self.assertEqual(stats["failed_checks"], 1)
        self.assertEqual(stats["failed_check_summaries"], ["energy mismatch"])
        self.assertTrue(stats["single_failure"])
        self.assertTrue(stats["counterfactual_candidate"])

    def test_failed_ge_passed_required_for_widespread(self) -> None:
        # Three failures against five passes: failed >= 3 but not >= passed.
        log_text = "\n".join(
            ["FAIL: check %d mismatch" % i for i in range(1, 4)]
            + ["PASS: ok check %d" % i for i in range(1, 6)]
        )
        stats = build_check_stats(log_text)
        self.assertEqual((stats["failed_checks"], stats["passed_checks"]), (3, 5))
        self.assertFalse(stats["widespread_failure"])
        self.assertFalse(stats["single_failure"])
        self.assertFalse(stats["counterfactual_candidate"])


class TestCheckLocalizationSemantics(unittest.TestCase):
    """`single` is only real localization — a lone fail-fast `FAIL:` is a
    `counterfactual_candidate`, not a proven single-check failure."""

    @staticmethod
    def _context(stats) -> AttributionContext:
        return build_context({"verifier_check_stats": stats}, {}, DecisionTrace(gate_order=()))

    def test_lone_fail_fast_is_not_localization(self) -> None:
        stats = build_check_stats("FAIL: results.json missing key 'n_rows'\n")
        self.assertTrue(stats["counterfactual_candidate"])
        self.assertFalse(stats["single_failure"])
        self.assertIsNone(_verifier_check_localization(stats))
        ctx = self._context(stats)
        self.assertIsNone(ctx.check_localization)
        self.assertFalse(ctx.sole_blocker_candidate)

    def test_single_failure_is_localization_and_sole_blocker_candidate(self) -> None:
        stats = build_check_stats(
            "PASS: results.json exists\nFAIL: results.json missing key 'n_rows'\n"
        )
        self.assertTrue(stats["single_failure"])
        self.assertEqual(_verifier_check_localization(stats), "single")
        ctx = self._context(stats)
        self.assertEqual(ctx.check_localization, "single")
        self.assertTrue(ctx.sole_blocker_candidate)

    def test_widespread_failure_is_not_a_single_localization(self) -> None:
        stats = build_check_stats("FAIL: check 1\nFAIL: check 2\nFAIL: check 3\nPASS: summary\n")
        self.assertEqual(_verifier_check_localization(stats), "widespread")
        ctx = self._context(stats)
        self.assertEqual(ctx.check_localization, "widespread")
        self.assertFalse(ctx.sole_blocker_candidate)

    def test_missing_and_malformed_stats_yield_no_localization(self) -> None:
        for unusable in (None, {}, "FAIL: text", {"single_failure": False}):
            with self.subTest(stats=unusable):
                self.assertIsNone(_verifier_check_localization(unusable))
                ctx = self._context(unusable)
                self.assertIsNone(ctx.check_localization)
                self.assertFalse(ctx.sole_blocker_candidate)


if __name__ == "__main__":
    unittest.main()
