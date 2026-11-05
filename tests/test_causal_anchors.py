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

from trace_causal_anchors import render_causal_anchors_markdown, trace_causal_anchors


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


if __name__ == "__main__":
    unittest.main()
