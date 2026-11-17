#!/usr/bin/env python3
"""
Attribution characterization net (`tests/test_attribution_characterization.py`)

Snapshots the full `analysis` dict produced by `analyze_single_trial` for each
archetype in `tests/archetype_builders.py` and compares it against committed
baselines in `tests/fixtures/attribution_baseline/`.

This is the equality proof for the attribution refactor: the 927-line
`_build_raw_causal_attribution` was split into per-gate modules, and the ONLY
acceptable outcome is that every archetype still produces byte-identical output.
The comparison is over the serialized JSON text (not parsed dicts) so that a
change in key ORDER is caught too — `dict == dict` ignores key order, and key
order is part of the 15-key output contract.

The 10 archetypes collectively exercise all 8 attribution return paths:

    gate0  passed .................... golden9
    gate1a infra pre-startup ......... golden1
    gate1b pre-startup, no evidence .. golden7, golden8
    gate2  case defect ............... golden2
    gate3  verifier defect ........... golden5
    gate4  numerical divergence ....... golden6
    gate5  no positive agent evidence  golden10
    gate6  agent primary ............. golden3 (subcase 5b), golden4 (subcase 5a)

Regenerate baselines after an INTENTIONAL output change:

    UPDATE_BASELINE=1 python3 -m unittest tests.test_attribution_characterization

Review the resulting diff before committing — a baseline update is a claim that
the output change was deliberate.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_DIR = REPO_ROOT / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from analyze_case import analyze_single_trial
from discover_artifacts import discover_all
from tests.archetype_builders import ARCHETYPES

BASELINE_DIR = Path(__file__).resolve().parent / "fixtures" / "attribution_baseline"
UPDATE_BASELINE = os.environ.get("UPDATE_BASELINE") == "1"

# Keys excluded from the snapshot. `decision_trace` is added to `analysis.json`
# as an additive 16th key in a later step of the refactor; it is a *description*
# of the decision, not a change to it, so it is stripped here. That keeps this
# net able to detect a real regression anywhere in the other 15 keys instead of
# failing wholesale the moment the trace appears.
# The causal-assessment extension is covered by test_causal_review; this
# net still pins the original attribution fields, including prescription suppression.
STRIPPED_KEYS: Tuple[str, ...] = ("decision_trace", "causal_assessment")


def render_analysis(analysis: Dict[str, Any], tmp_root: Path) -> str:
    """Serialize `analysis` to the exact text stored in the baseline file."""
    payload = {k: v for k, v in analysis.items() if k not in STRIPPED_KEYS}
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    return text.replace(str(tmp_root), "<TMP>")


class TestAttributionCharacterization(unittest.TestCase):
    """Every archetype must reproduce its committed `analysis.json` byte-for-byte."""

    def _analyze(self, name: str, tmp_root: Path) -> Dict[str, Any]:
        job_dir, task_dir = ARCHETYPES[name](tmp_root)
        disc = discover_all(job_path=job_dir, task_path=task_dir)
        _, analysis, _, _ = analyze_single_trial(disc["trials"][0], job_dir=job_dir)
        return analysis

    def test_archetype_analysis_matches_baseline(self) -> None:
        BASELINE_DIR.mkdir(parents=True, exist_ok=True)
        for name in sorted(ARCHETYPES):
            with self.subTest(archetype=name):
                baseline_path = BASELINE_DIR / f"{name}.json"
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_root = Path(tmp)
                    actual = render_analysis(self._analyze(name, tmp_root), tmp_root)

                if UPDATE_BASELINE:
                    baseline_path.write_text(actual + "\n", encoding="utf-8")
                    continue

                self.assertTrue(
                    baseline_path.exists(),
                    f"Missing baseline {baseline_path.name}. "
                    "Run with UPDATE_BASELINE=1 to generate it.",
                )
                expected = baseline_path.read_text(encoding="utf-8").rstrip("\n")
                self.assertEqual(
                    expected,
                    actual,
                    f"Attribution output for archetype '{name}' changed. "
                    f"If this was intentional, regenerate with UPDATE_BASELINE=1 "
                    f"and review the diff.",
                )

    def test_baselines_cover_every_baseline_file(self) -> None:
        """A baseline without a builder is stale — it would never be exercised."""
        if not BASELINE_DIR.exists():
            self.skipTest("no baselines generated yet")
        orphans = sorted(p.stem for p in BASELINE_DIR.glob("*.json") if p.stem not in ARCHETYPES)
        self.assertEqual(orphans, [], f"Stale baseline files with no builder: {orphans}")


if __name__ == "__main__":
    unittest.main()
