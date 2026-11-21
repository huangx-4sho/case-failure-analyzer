"""Dossier binds source slices to saved evidence and leaves private source opt-in."""

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from dossier import _MAX_PACK_CHARS, _fit_pack, build_dossier
from normalize_trajectory import normalize_trajectory
from review_cause import plan
from trajectory_source import trace_sources


class DossierTests(unittest.TestCase):
    def test_literal_edits_are_bound_to_saved_operations(self):
        def event(n, tool, args, success=True):
            return dict(
                event_id=f"trajectory:step:{n}:tool:0",
                tool_name=tool,
                arguments=args,
                command=args.get("command"),
                result_recorded=success,
                tool_succeeded=success,
            )

        filename = "/app/solver.py"
        timeline = [
            event(2, "Bash", {"command": "cat > /app/solver.py <<'PY'\nx = 1\nPY"}),
            event(3, "Edit", {"file_path": filename, "old_string": "1", "new_string": "2"}),
            event(4, "Bash", {"command": "python /app/solver.py"}),
        ]
        records, executions = trace_sources(timeline)
        self.assertEqual(records[filename]["text"], "x = 2\n")
        self.assertEqual(len(records[filename]["write_event_refs"]), 2)
        self.assertEqual(executions[filename][0]["event_ref"], "trajectory:step:4:tool:0")
        timeline.append(
            event(5, "Edit", {"file_path": filename, "old_string": "2", "new_string": "3"}, False)
        )
        self.assertIsNone(trace_sources(timeline)[0][filename]["text"])

    def test_compound_literal_heredoc_tracks_cwd_and_following_invocation(self):
        def bash(ref, command, **saved):
            return {
                "event_id": ref,
                "tool_name": "Bash",
                "command": command,
                "arguments": {},
                "result_recorded": True,
                "tool_succeeded": True,
                **saved,
            }

        timeline = [
            bash(
                "step:4",
                "mkdir -p /app/work && cd /app/work && cat > solve.py <<'PY'\n"
                "label = '$VALUE'\nPY\npython3 solve.py",
            ),
            bash("step:5", "cat <<-'SH' > check.sh\n\techo ok\n\tSH\nbash check.sh"),
        ]
        records, executions = trace_sources(timeline)
        self.assertEqual(records["/app/work/solve.py"]["text"], "label = '$VALUE'\n")
        self.assertEqual(records["/app/work/check.sh"]["text"], "echo ok\n")
        self.assertEqual(executions["/app/work/solve.py"][0]["event_ref"], "step:4")
        self.assertEqual(executions["/app/work/check.sh"][0]["event_ref"], "step:5")
        timeline.extend(
            [
                bash("step:6", "cat > solve.py <<PY\nlabel = '$VALUE'\nPY"),
                bash("step:7", "true || cat > never.py <<'PY'\nvalue = 1\nPY"),
                bash("step:8", "python - <<'PY'\nopen('check.sh', 'w').write('echo changed')\nPY"),
            ]
        )
        records, _ = trace_sources(timeline)
        self.assertIsNone(records["/app/work/solve.py"]["text"])
        self.assertIsNone(records["/app/work/check.sh"]["text"])
        self.assertNotIn("/app/work/never.py", records)

    def test_structured_slices_include_scorer_names_and_small_inventory_without_claiming_binding(
        self,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tests").mkdir()
            (root / "artifacts/app").mkdir(parents=True)
            (root / "tests/verify.py").write_text(
                "def check(submission):\n    data = read(submission / 'audit.json')\n"
                "    assert isinstance(data, dict)\n",
                encoding="utf-8",
            )
            names = ["audit.json", "manifest.json", *(f"trace-{i}.json" for i in range(5))]
            (root / "artifacts/app/audit.json").write_text(
                json.dumps({"large": "x" * 3_000_000, "result": 7}), encoding="utf-8"
            )
            for name in names[1:]:
                (root / "artifacts/app" / name).write_text("{}", encoding="utf-8")
            evidence = {
                "trial_dir": str(root),
                "task_dir": str(root),
                "artifacts": [
                    {"scope": "trial", "rel_path": f"artifacts/app/{name}"} for name in names
                ],
                "contract_review": {
                    "failure_locations": [
                        {
                            "detail": "top-level contract rejected",
                            "matching_rule": {"source": "tests/verify.py", "line": 3},
                        },
                    ],
                },
            }
            pack = build_dossier(evidence, {})
            (record,) = pack["submission_slices"]
            self.assertEqual(record["selection"], "mentioned_in_scorer")
            self.assertEqual(record["slices"][0]["keys"], ["large", "result"])
            evidence["artifacts"] = evidence["artifacts"][1:4]
            record, other = build_dossier(evidence, {})["submission_slices"]
            self.assertTrue(
                all(item["selection"] == "small_inventory_preview" for item in (record, other))
            )
            self.assertTrue(all(item["slices"][0]["sample_only"] for item in (record, other)))

    def test_budget_shortens_text_before_discarding_decision_references(self):
        sections = (
            "public_requirements",
            "agent_sources",
            "verifier_functions",
            "submission_slices",
            "contract_review_items",
            "failure_locations",
        )
        pack = {section: [] for section in sections}
        pack.update(
            limits=[],
            agent_sources=[{"text": "source" * 2000} for _ in range(4)],
            decision_context=[
                {"ref": f"step:{i}", "recorded_reasoning": {"text": "reason" * 1000}}
                for i in range(10)
            ],
        )
        _fit_pack(pack)
        self.assertLessEqual(len(json.dumps(pack, ensure_ascii=False)), _MAX_PACK_CHARS)
        self.assertEqual(
            [event["ref"] for event in pack["decision_context"]], [f"step:{i}" for i in range(10)]
        )
        self.assertTrue(pack["decision_context"][-1]["recorded_reasoning"]["excerpt_shortened"])

    def test_dossier_preserves_nested_output_and_real_reasoning_pointer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trial, task = root / "trial", root / "task"
            (trial / "artifacts/app").mkdir(parents=True)
            (task / "solution").mkdir(parents=True)
            (task / "tests").mkdir()
            (task / "solution/solve.sh").write_text("# private comparison")
            (task / "tests/verify.py").write_text("def check(value):\n    assert value > 0\n")
            (trial / "artifacts/app/audit.json").write_text('{"metrics": {"error": 3}}')
            (trial / "artifacts/app/solver.py").write_text("x = 2\n")
            trajectory = trial / "trajectory.json"
            trajectory.write_text(
                json.dumps(
                    {
                        "schema_version": "ATIF-v1.7",
                        "steps": [
                            {
                                "step_id": 2,
                                "reasoning_content": "Saved decision",
                                "tool_calls": [
                                    {
                                        "tool_call_id": "c1",
                                        "function_name": "Write",
                                        "arguments": {
                                            "file_path": "/app/solver.py",
                                            "content": "x = 2\n",
                                        },
                                    }
                                ],
                                "observation": {
                                    "results": [
                                        {
                                            "source_call_id": "c1",
                                            "content": (
                                                "File created successfully at: /app/solver.py\n"
                                                '[metadata] {"content":"ValueError: part of submitted code"}'
                                            ),
                                        }
                                    ]
                                },
                            },
                        ],
                    }
                )
            )
            evidence = {
                "trial_dir": str(trial),
                "task_dir": str(task),
                "artifacts": [
                    {
                        "scope": "trial",
                        "rel_path": f"artifacts/app/{name}",
                        "artifact_id": f"art:{name}",
                    }
                    for name in ("solver.py", "audit.json")
                ],
                "timeline": normalize_trajectory(trajectory)["events"],
                "contract_review": {
                    "failure_locations": [
                        {
                            "ref": "verifier_report.failed_checks[0]",
                            "detail": "audit.json $.metrics",
                            "matching_rule": {"source": "tests/verify.py", "line": 2},
                        },
                    ]
                },
                "causal_anchors": {
                    "algorithm_or_param_definition": {
                        "event_id": "trajectory:step:2:tool:0",
                        "target_path": "/app/solver.py",
                    }
                },
            }
            analysis = {"trial_name": "fixture", "verdict": "failed"}
            pack = build_dossier(evidence, analysis)
            self.assertEqual(pack["verifier_functions"][0]["function"], "check")
            self.assertIn('"error": 3', pack["submission_slices"][0]["slices"][0]["text"])
            self.assertEqual(pack["agent_sources"][0]["source_status"], "captured_submission")
            self.assertEqual(
                pack["agent_sources"][0]["write_event_refs"], ["trajectory:step:2:tool:0"]
            )
            thought = pack["decision_context"][0]["recorded_reasoning"]
            self.assertEqual(thought["source_pointer"], "/steps/0/reasoning_content")
            self.assertFalse(pack["oracle_assisted"])
            self.assertTrue(build_dossier(evidence, analysis, with_oracle=True)["oracle_assisted"])

    def test_review_plan_streams_large_collected_trajectory(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trajectory.json"
            data = b"z" * (1024 * 1024 + 1)
            path.write_bytes(data)
            entry = dict(
                artifact_id="art:trajectory_json",
                exists=True,
                abs_path=str(path),
                size_bytes=len(data),
                sha256="sha256:" + hashlib.sha256(data).hexdigest(),
            )
            binding = plan({"artifacts": [entry]}, {"verdict": "failed"})["binding"]
            self.assertEqual(binding["hash_sources"], ["art:trajectory_json"])
            self.assertEqual(binding["source_fingerprints"][0]["status"], "checked")
