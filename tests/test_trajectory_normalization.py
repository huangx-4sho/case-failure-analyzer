#!/usr/bin/env python3
"""Unit tests for Phase 2 (`scripts/normalize_trajectory.py`)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from normalize_trajectory import (
    _convert_messages_to_steps,
    _extract_script_write_targets,
    normalize_trajectory,
    sha256_text,
    truncate_with_head_tail,
)


class TestTrajectoryNormalization(unittest.TestCase):
    def test_atif_v1_7_normalization_and_behavioral_signals(self) -> None:
        sample_traj = {
            "schema_version": "ATIF-v1.7",
            "session_id": "sess-1",
            "steps": [
                {
                    "step_id": 1,
                    "timestamp": "2026-09-19T05:00:00Z",
                    "source": "user",
                    "message": "Run CP2K and write results.json",
                },
                {
                    "step_id": 2,
                    "timestamp": "2026-09-19T05:01:00Z",
                    "source": "agent",
                    "message": "Running CP2K",
                    "tool_calls": [
                        {
                            "tool_call_id": "c1",
                            "function_name": "Bash",
                            "arguments": {"command": "cp2k.psmp -i h2o.inp -o h2o.out"},
                        }
                    ],
                    "observation": {
                        "results": [
                            {
                                "source_call_id": "c1",
                                "content": "ERROR: SCF run NOT converged\nExit code: 1",
                            }
                        ]
                    },
                },
                {
                    "step_id": 3,
                    "timestamp": "2026-09-19T05:02:00Z",
                    "source": "agent",
                    "message": "Retrying exact same command",
                    "tool_calls": [
                        {
                            "tool_call_id": "c2",
                            "function_name": "Bash",
                            "arguments": {"command": "cp2k.psmp -i h2o.inp -o h2o.out"},
                        }
                    ],
                    "observation": {
                        "results": [
                            {
                                "source_call_id": "c2",
                                "content": "ERROR: SCF run NOT converged\nExit code: 1",
                            }
                        ]
                    },
                },
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "trajectory.json"
            p.write_text(json.dumps(sample_traj), encoding="utf-8")
            norm = normalize_trajectory(p, max_obs_bytes=500)
            self.assertTrue(norm["exists"])
            self.assertEqual(norm["schema_version"], "ATIF-v1.7")
            sig_types = {s["signal_type"] for s in norm["behavioral_signals"]}
            self.assertIn("repeated_failed_action", sig_types)
            self.assertIn("premature_completion", sig_types)

    def test_unknown_schema_and_truncation(self) -> None:
        sample_traj = {
            "schema_version": "CUSTOM-v9.9",
            "steps": [
                {
                    "step_id": 1,
                    "source": "user",
                    "message": "A" * 5000,
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "trajectory.json"
            p.write_text(json.dumps(sample_traj), encoding="utf-8")
            norm = normalize_trajectory(p, max_obs_bytes=400)
            self.assertTrue(any("Unknown trajectory schema_version" in w for w in norm["warnings"]))
            ev = norm["events"][0]
            self.assertIn("TRUNCATED", ev["observation"])
            self.assertTrue(ev["observation_sha256"].startswith("sha256:"))

    def test_jsonl_openai_tool_call_id_folding_and_orphan_safety(self) -> None:
        records = [
            {"role": "system", "content": "You are a computational chemistry agent."},
            {"role": "user", "content": "Compute energy and write /app/results.json"},
            {"role": "tool", "tool_call_id": "orphan_call", "content": "orphan output"},
            {
                "role": "assistant",
                "content": "Running two checks",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "Bash",
                            "arguments": json.dumps({"command": "pw.x -in scf.in"}),
                        },
                    },
                    {
                        "id": "call_2",
                        "type": "function",
                        "function": {
                            "name": "Read",
                            "arguments": json.dumps({"file_path": "/app/scf.in"}),
                        },
                    },
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "content": "ERROR: SCF NOT CONVERGED\nExit code: 1",
            },
            {"role": "function", "tool_call_id": "call_2", "content": "ecutwfc = 40.0"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "trajectory.jsonl"
            p.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
            norm = normalize_trajectory(p)
            self.assertTrue(norm["exists"])
            user_events = [e for e in norm["events"] if e["event_type"] == "user_instruction"]
            self.assertEqual(len(user_events), 2)
            self.assertEqual(
                user_events[0]["observation"], "You are a computational chemistry agent."
            )
            self.assertEqual(
                user_events[1]["observation"], "Compute energy and write /app/results.json"
            )

            shell_errs = [e for e in norm["events"] if e["event_type"] == "shell_error"]
            self.assertEqual(len(shell_errs), 1)
            self.assertEqual(shell_errs[0]["exit_code"], 1)
            self.assertIn("SCF NOT CONVERGED", shell_errs[0]["observation"])

            reads = [e for e in norm["events"] if e["event_type"] == "file_read"]
            self.assertEqual(len(reads), 1)
            self.assertEqual(reads[0]["command"], "/app/scf.in")
            self.assertIn("ecutwfc = 40.0", reads[0]["observation"])

    def test_anthropic_tool_use_and_tool_result_blocks(self) -> None:
        payload = {
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "Run relaxation"}]},
                {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "Using tight tolerance."},
                        {"type": "text", "text": "Writing solver script."},
                        {
                            "type": "tool_use",
                            "id": "toolu_01",
                            "name": "Write",
                            "input": {
                                "file_path": "/app/run.py",
                                "content": "print('ok')\n",
                            },
                        },
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_01",
                            "content": [{"type": "text", "text": "File written"}],
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "missing_id", "content": "ignored"},
                        {"type": "text", "text": "Please verify now."},
                    ],
                },
            ]
        }
        steps = _convert_messages_to_steps(payload["messages"])
        # No orphan user step emitted for pure tool_result wrapper
        self.assertEqual(len(steps), 3)
        self.assertEqual(steps[0]["source"], "user")
        self.assertEqual(steps[1]["source"], "agent")
        self.assertEqual(steps[1]["thinking"], "Using tight tolerance.")
        self.assertEqual(steps[1]["tool_calls"][0]["tool_call_id"], "toolu_01")
        self.assertEqual(steps[1]["tool_calls"][0]["function_name"], "Write")
        self.assertEqual(
            steps[1]["observation"]["results"],
            [{"source_call_id": "toolu_01", "content": "File written"}],
        )
        self.assertEqual(steps[2]["source"], "user")
        self.assertEqual(steps[2]["message"], "Please verify now.")

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "messages.json"
            p.write_text(json.dumps(payload), encoding="utf-8")
            norm = normalize_trajectory(p)
            self.assertIn("/app/run.py", norm["written_files"])
            thoughts = [e for e in norm["events"] if e["event_type"] == "agent_thought"]
            self.assertEqual(len(thoughts), 1)
            self.assertIn("tight tolerance", thoughts[0]["observation"])

    def test_salience_preserving_truncation_retains_middle_diagnostics(self) -> None:
        head_lines = [f"init log line {i}" for i in range(40)]
        mid_lines = (
            [f"debug info line {i}" for i in range(30)]
            + [
                "WARNING: charge density oscillation detected",
                "energy = -76.4321",
                "rms_force: 0.0042",
                "Traceback (most recent call last):",
                "FAIL: convergence check failed",
                "ERROR: SCF run NOT converged",
                "extra_metric = 12.345",
            ]
            + [f"more filler line {i}" for i in range(30)]
        )
        tail_lines = [f"tail log line {i}" for i in range(40)]
        full_text = "\n".join(head_lines + mid_lines + tail_lines)

        truncated, digest, is_trunc = truncate_with_head_tail(full_text, max_bytes=800)
        self.assertTrue(is_trunc)
        self.assertEqual(digest, sha256_text(full_text))
        assert truncated is not None
        self.assertIn("TRUNCATED", truncated)
        self.assertIn(digest, truncated)
        self.assertIn("ERROR: SCF run NOT converged", truncated)
        self.assertIn("FAIL: convergence check failed", truncated)
        self.assertIn("WARNING: charge density oscillation detected", truncated)
        self.assertIn("Traceback (most recent call last):", truncated)
        self.assertIn("energy = -76.4321", truncated)
        self.assertIn("rms_force: 0.0042", truncated)
        # Capped at 6 salient lines, so the 7th lower-priority metric line is omitted
        self.assertNotIn("extra_metric = 12.345", truncated)

    def test_extract_script_write_targets_and_two_phase_resolution(self) -> None:
        py_code = """
import json
import numpy as np
import pandas as pd
from pathlib import Path
from ase.io import write

OUT_JSON = "/app/final/results.json"
with open(OUT_JSON, "w") as f:
    json.dump({"ok": True}, f)
with open("/app/final/append.log", "a") as f:
    f.write("done")
Path("/app/final/summary.txt").write_text("ok")
df.to_csv("/app/final/charges.csv")
df.to_json("/app/final/table.json")
np.save("/app/final/forces.npy", arr)
ase.io.write("/app/final/final.xyz", atoms)
write("/app/final/alt.xyz", atoms)
if x > self.threshold:
    pass
"""
        extracted = _extract_script_write_targets(py_code)
        for expected in (
            "/app/final/results.json",
            "/app/final/append.log",
            "/app/final/summary.txt",
            "/app/final/charges.csv",
            "/app/final/table.json",
            "/app/final/forces.npy",
            "/app/final/final.xyz",
            "/app/final/alt.xyz",
        ):
            self.assertIn(expected, extracted)
        self.assertNotIn("self.threshold", extracted)

        # Two-phase trajectory: Write script -> Edit script -> failing Bash -> succeeding Bash
        traj = {
            "schema_version": "ATIF-v1.7",
            "steps": [
                {
                    "step_id": 1,
                    "source": "agent",
                    "tool_calls": [
                        {
                            "tool_call_id": "w1",
                            "function_name": "Write",
                            "arguments": {
                                "file_path": "/app/code/pipeline.py",
                                "content": "df.to_csv('/app/final/charges.csv')\n",
                            },
                        }
                    ],
                    "observation": {"results": [{"source_call_id": "w1", "content": "Written"}]},
                },
                {
                    "step_id": 2,
                    "source": "agent",
                    "tool_calls": [
                        {
                            "tool_call_id": "e1",
                            "function_name": "Edit",
                            "arguments": {
                                "file_path": "/app/code/pipeline.py",
                                "old_string": "pass",
                                "new_string": "Path('/app/final/meta.json').write_text('{}')\n",
                            },
                        }
                    ],
                    "observation": {"results": [{"source_call_id": "e1", "content": "Edited"}]},
                },
                {
                    "step_id": 3,
                    "source": "agent",
                    "tool_calls": [
                        {
                            "tool_call_id": "b_read",
                            "function_name": "Bash",
                            "arguments": {"command": "cat /app/code/pipeline.py"},
                        },
                        {
                            "tool_call_id": "b_fail",
                            "function_name": "Bash",
                            "arguments": {"command": "python3 /app/code/pipeline.py"},
                        },
                    ],
                    "observation": {
                        "results": [
                            {"source_call_id": "b_read", "content": "Exit code: 0"},
                            {"source_call_id": "b_fail", "content": "Traceback\nExit code: 1"},
                        ]
                    },
                },
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "traj_fail.json"
            p.write_text(json.dumps(traj), encoding="utf-8")
            norm_fail = normalize_trajectory(
                p, expected_deliverables=["/app/final/charges.csv", "/app/final/meta.json"]
            )
            # Neither cat nor failed execution marks deliverables as written
            self.assertEqual(
                norm_fail["missing_deliverables"],
                ["/app/final/charges.csv", "/app/final/meta.json"],
            )

            # Now add a successful execution step + heredoc script execution
            traj["steps"].append(
                {
                    "step_id": 4,
                    "source": "agent",
                    "tool_calls": [
                        {
                            "tool_call_id": "b_ok",
                            "function_name": "Bash",
                            "arguments": {"command": "python3 /app/code/pipeline.py"},
                        },
                        {
                            "tool_call_id": "b_hd",
                            "function_name": "Bash",
                            "arguments": {
                                "command": (
                                    "cat << 'EOF' > /app/code/export.sh\n"
                                    "echo ok > /app/final/report.txt\n"
                                    "EOF\n"
                                    "bash /app/code/export.sh"
                                )
                            },
                        },
                    ],
                    "observation": {
                        "results": [
                            {"source_call_id": "b_ok", "content": "Exit code: 0"},
                            {"source_call_id": "b_hd", "content": "Exit code: 0"},
                        ]
                    },
                }
            )
            p2 = Path(tmp) / "traj_ok.json"
            p2.write_text(json.dumps(traj), encoding="utf-8")
            norm_ok = normalize_trajectory(
                p2,
                expected_deliverables=[
                    "/app/final/charges.csv",
                    "/app/final/meta.json",
                    "/app/final/report.txt",
                ],
            )
            self.assertEqual(norm_ok["missing_deliverables"], [])
            for target in (
                "/app/final/charges.csv",
                "/app/final/meta.json",
                "/app/final/report.txt",
            ):
                self.assertIn(target, norm_ok["written_files"])
            sig_types = {s["signal_type"] for s in norm_ok["behavioral_signals"]}
            self.assertNotIn("premature_completion", sig_types)


if __name__ == "__main__":
    unittest.main()
