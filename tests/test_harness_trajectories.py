"""Recorded harness formats; fixtures intentionally contain no executable validation."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from normalize_trajectory import normalize_trajectory
from runtime_state import resolve_trajectory_path


class HarnessTrajectories(unittest.TestCase):
    def normalize(self, records, suffix=""):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trajectory.jsonl"
            path.write_text("\n".join(json.dumps(r) for r in records) + suffix)
            return normalize_trajectory(path)

    def tools(self, result):
        return [e for e in result["events"] if e.get("tool_name")]

    def test_codex_rollout(self):
        result = self.normalize(
            [
                {"type": "session_meta", "payload": {"id": "session"}},
                {
                    "type": "response_item",
                    "payload": {
                        "type": "function_call",
                        "call_id": "a",
                        "name": "exec_command",
                        "arguments": '{"cmd":"python run.py"}',
                    },
                },
                {"type": "event_msg", "payload": {"type": "agent_message", "message": "Done"}},
                {
                    "type": "response_item",
                    "payload": {
                        "type": "function_call_output",
                        "call_id": "a",
                        "output": "Process exited with code 2\nERROR: failed",
                    },
                },
            ]
        )
        event = self.tools(result)[0]
        self.assertEqual(event["command"], "python run.py")
        self.assertEqual(event["event_type"], "shell_error")
        self.assertEqual(result["normalization"]["coverage"], "complete")

    def test_chat_multiple_ids_and_orphan(self):
        result = self.normalize(
            [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": id_,
                            "function": {"name": "Bash", "arguments": json.dumps({"command": id_})},
                        }
                        for id_ in ("a", "b")
                    ],
                },
                {"role": "assistant", "content": "I executed a command and wrote results.json"},
                {"role": "tool", "tool_call_id": "b", "content": "b output", "exit_code": 7},
                {"role": "tool", "tool_call_id": "a", "content": "a output"},
                {"role": "tool", "tool_call_id": "orphan", "content": "ignored"},
            ]
        )
        events = self.tools(result)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["observation"], "a output")
        self.assertEqual(events[1]["exit_code"], 7)
        self.assertEqual(result["written_files"], [])
        self.assertIn("Unmatched tool result", " ".join(result["warnings"]))

    def test_claude_code(self):
        result = self.normalize(
            [
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "w",
                                "name": "Write",
                                "input": {"file_path": "/app/data.json", "content": "{}"},
                            }
                        ],
                    },
                },
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "w",
                                "content": [{"type": "text", "text": "Permission denied"}],
                                "is_error": True,
                            }
                        ],
                    },
                },
            ]
        )
        self.assertIn("ERROR", self.tools(result)[0]["observation"])
        self.assertEqual(result["written_files"], [])
        self.assertFalse(self.tools(result)[0]["tool_succeeded"])
        self.assertEqual(result["normalization"]["format"], "claude_code")

    def test_responses_and_patch(self):
        result = self.normalize(
            [
                {
                    "type": "response.output_item.done",
                    "item": {
                        "type": "custom_tool_call",
                        "call_id": "p",
                        "name": "apply_patch",
                        "input": "*** Begin Patch\n*** Add File: /app/a.py\n+x=1\n*** Update File: /app/b.py\n@@\n-x=0\n+x=1\n*** End Patch",
                    },
                },
                {"type": "custom_tool_call_output", "call_id": "p", "output": "ok"},
            ]
        )
        self.assertEqual(result["written_files"], ["/app/a.py", "/app/b.py"])
        unsupported = self.normalize(
            [
                {
                    "type": "custom_tool_call",
                    "call_id": "p",
                    "name": "apply_patch",
                    "input": {"unsupported": "/fabricated"},
                }
            ]
        )
        self.assertEqual(unsupported["written_files"], [])
        self.assertEqual(unsupported["normalization"]["coverage"], "partial")

    def test_truncated_and_unknown(self):
        result = self.normalize(
            [{"role": "assistant", "content": "Done"}, {"type": "unknown", "metadata": "anything"}],
            '\n{"type":',
        )
        self.assertEqual(self.tools(result), [])
        self.assertEqual(result["normalization"]["coverage"], "partial")
        self.assertIn("Malformed JSONL", " ".join(result["warnings"]))

    def test_failed_patch_and_unobserved_write_are_attempts(self):
        failed = self.normalize(
            [
                {
                    "type": "custom_tool_call",
                    "call_id": "p",
                    "name": "apply_patch",
                    "input": "*** Begin Patch\n*** Add File: /app/results.json\n+{}\n*** End Patch",
                },
                {"type": "custom_tool_call_output", "call_id": "p", "output": "Permission denied"},
            ]
        )
        self.assertEqual(failed["written_files"], [])
        self.assertNotIn("script_targets", failed)
        self.assertEqual(self.tools(failed)[0]["event_type"], "file_write")
        self.assertFalse(self.tools(failed)[0]["tool_succeeded"])
        self.assertNotIn(
            "unverified_output", [s["signal_type"] for s in failed["behavioral_signals"]]
        )
        unobserved = self.normalize(
            [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "w",
                            "function": {
                                "name": "Write",
                                "arguments": json.dumps(
                                    {
                                        "file_path": "/app/run.py",
                                        "content": "open('/app/results.json', 'w').write('{}')",
                                    }
                                ),
                            },
                        }
                    ],
                }
            ]
        )
        self.assertEqual(unobserved["written_files"], [])
        self.assertNotIn("script_targets", unobserved)
        self.assertFalse(self.tools(unobserved)[0]["result_recorded"])
        self.assertEqual(unobserved["normalization"]["coverage"], "partial")

    def test_responses_snapshots_and_codex_duplicates(self):
        call = {
            "type": "function_call",
            "id": "fc_item",
            "call_id": "a",
            "name": "exec_command",
            "arguments": '{"cmd":"python run.py"}',
        }
        output = {"type": "function_call_output", "call_id": "a", "output": "ok"}
        result = self.normalize(
            [
                {"type": "response.output_item.done", "item": call},
                {"type": "response.completed", "response": {"output": [call]}},
                {"type": "response_item", "payload": call},
                {"type": "response_item", "payload": output},
                {"type": "response_item", "payload": output},
            ]
        )
        self.assertEqual(len(self.tools(result)), 1)
        self.assertEqual(self.tools(result)[0]["tool_call_id"], "a")
        self.assertEqual(result["normalization"]["coverage"], "complete")
        repeated = self.normalize(
            [call, output, dict(call, call_id="b"), dict(output, call_id="b")]
        )
        self.assertEqual(len(self.tools(repeated)), 2)
        self.assertEqual(self.tools(repeated)[1]["event_type"], "repeated_command")

    def test_conflicting_snapshots_remain_partial(self):
        call = {
            "type": "function_call",
            "call_id": "a",
            "name": "Write",
            "arguments": '{"file_path":"/app/results.json","content":"{}"}',
        }
        result = self.normalize(
            [
                call,
                dict(call, arguments='{"file_path":"/app/other.json","content":"{}"}'),
                {"type": "function_call_output", "call_id": "a", "output": "ok"},
                {"type": "function_call_output", "call_id": "a", "output": "Permission denied"},
            ]
        )
        self.assertEqual(len(self.tools(result)), 1)
        self.assertEqual(result["normalization"]["coverage"], "partial")
        self.assertIn("Conflicting tool call", " ".join(result["warnings"]))
        self.assertIn("Conflicting tool results", " ".join(result["warnings"]))
        self.assertEqual(result["written_files"], [])

    def test_atif_cross_step_result(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trajectory.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": "ATIF-v1.7",
                        "steps": [
                            {
                                "step_id": 1,
                                "source": "agent",
                                "tool_calls": [
                                    {
                                        "tool_call_id": "x",
                                        "function_name": "Bash",
                                        "arguments": {"command": "run"},
                                    }
                                ],
                            },
                            {
                                "step_id": 2,
                                "source": "environment",
                                "observation": {
                                    "results": [
                                        {"source_call_id": "x", "content": "failed", "exit_code": 3}
                                    ]
                                },
                            },
                        ],
                    }
                )
            )
            result = normalize_trajectory(path)
        self.assertEqual(self.tools(result)[0]["exit_code"], 3)
        self.assertEqual(result["normalization"]["pointer_space"], "source")

    def test_discovery_order(self):
        with tempfile.TemporaryDirectory() as directory:
            trial = Path(directory)
            for name in ("rollout-z.jsonl", "rollout-a.jsonl"):
                (trial / name).write_text("{}")
            self.assertEqual(resolve_trajectory_path(trial).name, "rollout-a.jsonl")
            (trial / "messages.json").write_text("[]")
            self.assertEqual(resolve_trajectory_path(trial).name, "messages.json")
