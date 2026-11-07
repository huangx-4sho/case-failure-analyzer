#!/usr/bin/env python3
"""
Tests for trajectory causal anchor tracing (`scripts/trace_causal_anchors.py`).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
SCRIPT_DIR = ROOT_DIR / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from normalize_trajectory import SELF_CHECK_SCRIPT_RE
from trace_causal_anchors import (
    _clean_code_snippet,
    _clean_thought_snippet,
    render_causal_anchors_markdown,
    trace_causal_anchors,
)


class TestCausalAnchors(unittest.TestCase):
    def test_empty_timeline_returns_empty(self) -> None:
        self.assertEqual(trace_causal_anchors({}), {})
        self.assertEqual(trace_causal_anchors({"timeline": []}), {})
        self.assertEqual(render_causal_anchors_markdown({}), [])

    def test_trace_algo_and_deliverable_anchors(self) -> None:
        evidence = {
            "metadata": {"declared_deliverables": ["charges.csv"]},
            "timeline": [
                {
                    "event_id": "trajectory:step:1:thought",
                    "observation": "Let us prepare the calculation script.",
                },
                {
                    "event_id": "trajectory:step:1:tool:0",
                    "tool_name": "Write",
                    "command": "/app/outputs/code/analysis.py",
                    "code_payload": "def solve_eqeq(energy, tolerance=1e-6):\n    return charges\n",
                },
                {
                    "event_id": "trajectory:step:5:thought",
                    "observation": "Now generating final charges.csv.",
                },
                {
                    "event_id": "trajectory:step:5:tool:0",
                    "tool_name": "Bash",
                    "command": "python3 /app/outputs/code/analysis.py --output /app/outputs",
                    "observation": "Wrote charges.csv successfully.",
                },
            ],
        }
        anchors = trace_causal_anchors(evidence)
        self.assertIn("algorithm_or_param_definition", anchors)
        self.assertIn("deliverable_generation", anchors)

        algo = anchors["algorithm_or_param_definition"]
        self.assertEqual(algo["event_id"], "trajectory:step:1:tool:0")
        self.assertIn("solve_eqeq", algo["code_snippet"])
        self.assertIn("prepare the calculation script", algo["thought"])

        deliv = anchors["deliverable_generation"]
        self.assertEqual(deliv["event_id"], "trajectory:step:5:tool:0")
        self.assertIn("python3 /app/outputs/code/analysis.py", deliv["command"])
        self.assertIn("generating final charges.csv", deliv["thought"])

        md = render_causal_anchors_markdown(anchors, lang="zh")
        text = "\n".join(md)
        self.assertIn("核心因果链锚点", text)
        self.assertIn("参数与算法定义", text)
        self.assertIn("交付物生成执行", text)
        self.assertIn("solve_eqeq", text)

    def test_self_check_anchor_records_coverage_gap(self) -> None:
        evidence = {
            "verifier_check_stats": {
                "failed_check_names": ["spin_selection", "energy_parameters"],
            },
            "timeline": [
                {
                    "event_id": "trajectory:step:62:tool:0",
                    "event_type": "file_write",
                    "tool_name": "Write",
                    "command": "/app/outputs/native/verify_outputs.py",
                    "observation": (
                        'File created. content: check(S["schema"], "spin_selection parity")\n'
                        "check(len(rows) > 0, 'rows exist')\n"
                    ),
                },
                {
                    "event_id": "trajectory:step:70:tool:0",
                    "event_type": "tool_call",
                    "tool_name": "Bash",
                    "command": "python3 build_outputs.py && python3 verify_outputs.py && echo VERIFY OK",
                    "observation": "VERIFY OK\nALL CHECKS PASSED",
                },
            ],
        }
        anchors = trace_causal_anchors(evidence)
        sc = anchors["self_check"]
        self.assertEqual(sc["event_id"], "trajectory:step:70:tool:0")
        self.assertEqual(sc["script_event_ref"], "trajectory:step:62:tool:0")
        self.assertTrue(sc["self_check_passed"])
        self.assertEqual(sc["checked_failed_checks"], ["spin_selection"])
        self.assertEqual(sc["uncovered_failed_checks"], ["energy_parameters"])
        self.assertIn("spin_selection parity", sc["self_check_criteria"])

        text = "\n".join(render_causal_anchors_markdown(anchors, lang="zh"))
        self.assertIn("自检覆盖", text)
        self.assertIn("energy_parameters", text)
        self.assertIn("脚本实际检查项", text)

    def test_snippets_skip_imports_and_dangling_thought_fragment(self) -> None:
        code = (
            "import numpy as np\n"
            "import csv\n"
            "K_COUL = 14.399645\n"
            "SCF_E_TOL = 1e-10\n"
            "def solve():\n"
            "    return K_COUL\n"
        )
        snippet = _clean_code_snippet(code, max_lines=4, max_chars=200)
        self.assertNotIn("import numpy", snippet)
        self.assertIn("K_COUL", snippet)

        thought = (
            "=0.02 (η=50), Rr=14 gives meanCu=0.898982842, matching α=0.05,0.1 exactly. "
            "And Rr=12 gives 0.898982758.\n\n"
            "So converged charges are confirmed: meanCu=0.898982842."
        )
        cleaned = _clean_thought_snippet(thought, max_lines=4, max_chars=250)
        self.assertFalse(cleaned.startswith("0.02"))
        self.assertIn("So converged charges are confirmed", cleaned)

    def test_canonical_self_check_pattern_excludes_benchmark_verify_py(self) -> None:
        self.assertIsNotNone(SELF_CHECK_SCRIPT_RE.search("python3 verify_outputs.py"))
        self.assertIsNotNone(SELF_CHECK_SCRIPT_RE.search("./self_check.sh"))
        self.assertIsNone(SELF_CHECK_SCRIPT_RE.search("python3 tests/verify.py"))
        self.assertIsNone(SELF_CHECK_SCRIPT_RE.search("python3 check.py"))

    def test_self_check_anchor_ignores_read_and_thought_mentions(self) -> None:
        evidence = {
            "verifier_check_stats": {"failed_check_names": ["spin_selection"]},
            "timeline": [
                {
                    "event_id": "trajectory:step:1:thought",
                    "event_type": "agent_thought",
                    "command": "python3 verify_outputs.py",
                    "observation": "will verify later",
                },
                {
                    "event_id": "trajectory:step:2:tool:0",
                    "event_type": "file_read",
                    "tool_name": "Read",
                    "command": "cat /app/outputs/native/verify_outputs.py",
                    "observation": "script contents",
                },
            ],
        }
        anchors = trace_causal_anchors(evidence)
        self.assertNotIn("self_check", anchors)

    def test_self_check_anchor_abstains_without_script_source(self) -> None:
        evidence = {
            "verifier_check_stats": {"failed_check_names": ["spin_selection"]},
            "timeline": [
                {
                    "event_id": "trajectory:step:70:tool:0",
                    "event_type": "tool_call",
                    "tool_name": "Bash",
                    "command": "python3 verify_outputs.py && echo VERIFY OK",
                    "observation": "VERIFY OK",
                }
            ],
        }
        anchors = trace_causal_anchors(evidence)
        self.assertNotIn("self_check", anchors)


if __name__ == "__main__":
    unittest.main()
