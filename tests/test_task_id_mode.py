#!/usr/bin/env python3
"""
Task-ID quick-answer mode tests (`tests/test_task_id_mode.py`)

Covers the `/case failure analyzer task208` contract:

- task reference normalization and resolution (numeric exactness, explicit
  trial, newest trial wins, report landing);
- the verdict card's required answer order and evidence backing;
- the sanctioned `failure-report` isolation exception and its boundaries;
- `task208.*` artifact naming plus the stdout direct answer.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
SCRIPT_DIR = ROOT_DIR / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from analyze_case import _artifact_name
from render_verdict import build_verdict_card, render_verdict_card_markdown
from staging import manifest_filename, publish_staging_to_output, validate_input_output_isolation
from task_ref import REPORT_DIR_NAME, TaskRefError, normalize_task_ref, resolve_task_ref


def _make_harbor_tree(root: Path, number: str = "208", hash_suffix: str = "jaekojA") -> Path:
    """Build a minimal harbor checkout with one task definition and one trial."""
    case = root / "32-qeq-electrostatics"
    task = case / f"task {number}"
    trial = case / "jobs" / "job1" / f"task {number}__{hash_suffix}"
    task.mkdir(parents=True, exist_ok=True)
    (trial / "agent").mkdir(parents=True)
    (trial / "verifier").mkdir(parents=True)
    (case / "jobs" / "job1").mkdir(parents=True, exist_ok=True)
    (task / "task.toml").write_text(
        'schema_version = "1.4"\nartifacts = ["/app/outputs"]\n\n[task]\nname = "demo"\n',
        encoding="utf-8",
    )
    (task / "instruction.md").write_text("Produce /app/outputs/results.json\n", encoding="utf-8")
    (trial / "agent" / "trajectory.json").write_text(
        json.dumps(
            {
                "schema_version": "ATIF-v1.7",
                "steps": [
                    {
                        "step_id": 1,
                        "source": "agent",
                        "message": "",
                        "tool_calls": [
                            {
                                "tool_call_id": "c0",
                                "function_name": "Bash",
                                "arguments": {"command": "echo ok > /app/outputs/results.json"},
                            }
                        ],
                        "observation": {"results": [{"source_call_id": "c0", "content": ""}]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (trial / "result.json").write_text(
        json.dumps(
            {
                "task_name": "demo",
                "trial_name": f"task {number}__{hash_suffix}",
                "task_id": {"path": f"task {number}"},
                "verifier_result": {"rewards": {"reward": 0.0}},
                "agent_result": {"n_input_tokens": 1},
            }
        ),
        encoding="utf-8",
    )
    (trial / "verifier" / "verify.log").write_text(
        "FAIL: reported max_force 0.02863 != recomputed 0.03666 (tol 0.001)\n",
        encoding="utf-8",
    )
    (case / "jobs" / "job1" / "job.log").write_text("job started\n", encoding="utf-8")
    (trial / "trial.log").write_text("trial started\n", encoding="utf-8")
    return trial


class TaskRefNormalizationTests(unittest.TestCase):
    def test_canonical_forms(self) -> None:
        for raw in ("task208", "208", "task 208", "Task-208", "task0208", "task 208__jaekojA"):
            self.assertEqual(normalize_task_ref(raw), "task208", raw)

    def test_exact_numeric_match_no_prefix_leak(self) -> None:
        self.assertEqual(normalize_task_ref("task2080"), "task2080")
        self.assertNotEqual(normalize_task_ref("task2080"), "task208")

    def test_invalid_reference_raises(self) -> None:
        for raw in ("", "   ", "case42", "abc"):
            with self.assertRaises(TaskRefError):
                normalize_task_ref(raw)


class TaskRefResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_resolves_trial_task_def_and_report_dir(self) -> None:
        trial = _make_harbor_tree(self.root)
        # A domain folder grouping several task definitions is not the bench root:
        # reports land next to the domain folders, not inside one of them.
        sibling = self.root / "32-qeq-electrostatics" / "task 207"
        sibling.mkdir(parents=True, exist_ok=True)
        (sibling / "task.toml").write_text('schema_version = "1.4"\n', encoding="utf-8")
        ref = resolve_task_ref("task208", self.root)
        self.assertEqual(ref.task_id, "task208")
        self.assertEqual(ref.report_basename, "task208")
        self.assertEqual(ref.trial_dir, trial.resolve())
        self.assertEqual(ref.job_dir, trial.parent.resolve())
        self.assertEqual(ref.task_def_dir.name, "task 208")
        self.assertEqual(ref.bench_root, self.root.resolve())
        self.assertEqual(ref.report_root, self.root.resolve() / REPORT_DIR_NAME)
        self.assertEqual(ref.report_dir, ref.report_root / "task208")
        self.assertEqual(ref.report_dir.parent, ref.report_root)

    def test_flat_layout_lands_report_at_bench_root(self) -> None:
        task = self.root / "task 208"
        trial = self.root / "jobs" / "job1" / "task 208__flat"
        task.mkdir(parents=True)
        (task / "task.toml").write_text('schema_version = "1.4"\n', encoding="utf-8")
        trial.mkdir(parents=True)
        (self.root / "jobs" / "job1" / "job.log").write_text("ok\n", encoding="utf-8")
        (trial / "result.json").write_text("{}", encoding="utf-8")
        ref = resolve_task_ref("task208", self.root)
        self.assertEqual(ref.bench_root, self.root.resolve())
        self.assertEqual(ref.report_dir, self.root.resolve() / REPORT_DIR_NAME / "task208")

    def test_jobs_root_pointing_at_jobs_tree(self) -> None:
        _make_harbor_tree(self.root)
        case = self.root / "32-qeq-electrostatics"
        ref = resolve_task_ref("task208", case / "jobs")
        self.assertEqual(ref.bench_root, case.resolve())
        self.assertEqual(ref.report_dir, case.resolve() / REPORT_DIR_NAME / "task208")

    def test_no_partial_numeric_match(self) -> None:
        _make_harbor_tree(self.root, number="2080")
        with self.assertRaises(TaskRefError):
            resolve_task_ref("task208", self.root)

    def test_explicit_trial_name_selects_that_trial(self) -> None:
        _make_harbor_tree(self.root, hash_suffix="aaa")
        _make_harbor_tree(self.root, hash_suffix="bbb")
        ref = resolve_task_ref("task 208__aaa", self.root)
        self.assertEqual(ref.trial_dir.name, "task 208__aaa")
        self.assertEqual(len(ref.candidates), 1)

    def test_report_dir_override(self) -> None:
        _make_harbor_tree(self.root)
        override = self.root / "custom-reports"
        ref = resolve_task_ref("task208", self.root, report_dir_override=override)
        self.assertEqual(ref.report_dir, override.resolve())

    def test_missing_jobs_root_raises(self) -> None:
        with self.assertRaises(TaskRefError):
            resolve_task_ref("task208", self.root / "nope")


class ArtifactNamingTests(unittest.TestCase):
    def test_legacy_names_unchanged(self) -> None:
        self.assertEqual(_artifact_name("report.md", None), "report.md")
        self.assertEqual(_artifact_name("analysis.json", None), "analysis.json")

    def test_task_id_names(self) -> None:
        self.assertEqual(_artifact_name("report.md", "task208"), "task208.md")
        self.assertEqual(_artifact_name("report.zh.md", "task208"), "task208.zh.md")
        self.assertEqual(_artifact_name("report.en.md", "task208"), "task208.en.md")
        self.assertEqual(_artifact_name("analysis.json", "task208"), "task208.analysis.json")
        self.assertEqual(_artifact_name("summary.md", "task208"), "task208.summary.md")


class VerdictCardTests(unittest.TestCase):
    def _analysis(self) -> dict:
        return {
            "trial_name": "task 208__jaekojA",
            "verdict": "failed",
            "primary_root_cause": {
                "category": "agent",
                "subtype": "result_validation",
                "code": "AGENT_RESULT_VALIDATION",
                "confidence": 0.71,
                "summary": "Agent wrote results.json without sanity validation.",
            },
            "failure_manifestation": {"summary": "[L3] charge deviates by 1.6e-06 e"},
            "contributing_factors": [],
            "competing_hypotheses": [
                {"hypothesis_id": "H1", "category": "agent", "claim": "primary"},
            ],
            "excluded_hypotheses": [
                {"hypothesis_id": "H2", "category": "infra", "reason": "container healthy"},
            ],
            "evidence_refs": ["ver:fail_log"],
            "recommended_actions": [{"owner": "Agent Policy", "action": "Validate outputs"}],
            "decision_trace": {"selected": {"gate_id": "gate6_agent_primary"}},
        }

    def test_four_categories_then_subcategory(self) -> None:
        evidence = {"runtime": {"agent_started": True, "verifier_started": True}}
        card = build_verdict_card(
            evidence, self._analysis(), task_id="task208", report_basename="task208"
        )
        self.assertEqual(
            [c["category"] for c in card["four_categories"]], ["agent", "verifier", "case", "infra"]
        )
        self.assertEqual(card["four_categories"][0]["status"], "primary")
        self.assertEqual(card["four_categories"][3]["status"], "excluded")
        self.assertEqual(card["primary_root_cause"]["code"], "AGENT_RESULT_VALIDATION")
        self.assertEqual(card["subcategories"][0]["subtype"], "result_validation")
        md = render_verdict_card_markdown(card, lang="zh")
        self.assertLess(md.index("四大类裁决"), md.index("小分类"))
        self.assertIn("AGENT_RESULT_VALIDATION", md)
        self.assertIn("失败原因", md)

    def test_english_card_has_no_chinese_labels(self) -> None:
        analysis = self._analysis()
        analysis["contributing_factors"] = [
            {
                "category": "case",
                "subtype": "missing_asset",
                "code": "CASE_MISSING_ASSET",
                "summary": "asset absent",
            }
        ]
        evidence = {"runtime": {"agent_started": True, "verifier_started": True}}
        card = build_verdict_card(evidence, analysis, task_id="task208", report_basename="task208")
        md = render_verdict_card_markdown(card, lang="en")
        self.assertIn("# task208", md)
        self.assertIn("Four peer categories", md)
        self.assertIn("PRIMARY", md)
        self.assertIn("Contributing: asset absent", md)
        self.assertNotIn("四大类裁决", md)
        self.assertNotIn("主因", md)
        self.assertNotIn("伴随因素", md)

    def test_english_fallback_reasons(self) -> None:
        evidence = {"runtime": {"agent_started": True, "verifier_started": True}}
        card = build_verdict_card(evidence, self._analysis())
        md = render_verdict_card_markdown(card, lang="en")
        self.assertIn("Contract audit found no missing task asset or ambiguous specification", md)
        self.assertIn("No verifier crash, parser defect", md)

    def test_contributing_and_case_defect_reasons_are_evidence_backed(self) -> None:
        analysis = self._analysis()
        analysis["contributing_factors"] = [
            {
                "category": "agent",
                "subtype": "deliverable_not_written",
                "code": "AGENT_MISSING_DECLARED_DELIVERABLE",
                "summary": "missing /app/final",
            }
        ]
        evidence = {
            "runtime": {"agent_started": True, "verifier_started": True},
            "verifier_report": {
                "failed_checks": [{"name": "neutral_stationary", "detail": "dev 1.6e-06"}]
            },
            "contract_observations": [{"alignment": "case_defect", "details": "asset missing"}],
        }
        card = build_verdict_card(evidence, analysis)
        by_cat = {c["category"]: c for c in card["four_categories"]}
        self.assertIn("具名失败检查", by_cat["verifier"]["reason"])
        self.assertIn("题目/规格缺陷观测", by_cat["case"]["reason"])
        self.assertEqual(len(card["subcategories"]), 2)
        self.assertEqual(card["subcategories"][1]["role"], "contributing")

    def test_grouped_checks_avoid_repeating_identical_detail(self) -> None:
        evidence = {
            "runtime": {"agent_started": True, "verifier_started": True},
            "verifier_report": {
                "failed_checks": [
                    {"layer": "L3", "name": "spin_selection", "detail": "same deviation"},
                    {"layer": "L3", "name": "energy_parameters", "detail": "same deviation"},
                    {"layer": "L2", "name": "trace", "detail": "same deviation"},
                ]
            },
        }
        card = build_verdict_card(evidence, self._analysis())
        reason = card["failure_reason"]
        self.assertEqual(reason.count("same deviation"), 1)
        self.assertIn("spin_selection", reason)
        self.assertIn("energy_parameters", reason)

    def test_action_restating_cause_becomes_concrete_fix(self) -> None:
        analysis = self._analysis()
        analysis["recommended_actions"] = [
            {"owner": "Agent Policy", "action": analysis["primary_root_cause"]["summary"]}
        ]
        card = build_verdict_card({"runtime": {}}, analysis)
        action = card["recommended_actions"][0]
        self.assertNotEqual(action["action"], analysis["primary_root_cause"]["summary"])
        self.assertIn("recompute", action["action"])
        self.assertIn("独立复算", action["action_zh"])

    def test_concrete_action_is_preserved(self) -> None:
        analysis = self._analysis()
        analysis["recommended_actions"] = [{"owner": "Infra", "action": "Preserve trial logs"}]
        card = build_verdict_card({"runtime": {}}, analysis)
        self.assertEqual(card["recommended_actions"][0]["action"], "Preserve trial logs")

    def test_infra_exclusion_reason_has_no_duplicated_prefix(self) -> None:
        evidence = {"runtime": {"agent_started": True, "verifier_started": True}}
        card = build_verdict_card(evidence, self._analysis())
        infra_reason = card["four_categories"][3]["reason"]
        self.assertNotIn("已排除", infra_reason)
        self.assertIn("容器构建", infra_reason)

    def test_embedded_json_blob_is_stripped(self) -> None:
        analysis = self._analysis()
        analysis["primary_root_cause"]["summary"] = (
            'Agent wrote results without validation: {"schema_version": "v1", "score": 0.0}'
        )
        card = build_verdict_card({"runtime": {}}, analysis)
        self.assertNotIn("schema_version", card["primary_root_cause"]["summary"])


class IsolationExceptionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_failure_report_subfolder_is_allowed(self) -> None:
        task = self.root / "task 208"
        task.mkdir()
        report = task / REPORT_DIR_NAME
        # Should not raise / exit.
        validate_input_output_isolation(
            out_dir=report,
            input_roots=[(task, "--task")],
            planned_trial_names=[],
            allowed_report_dir=report,
        )

    def test_other_subfolder_inside_input_is_rejected(self) -> None:
        task = self.root / "task 208"
        task.mkdir()
        with self.assertRaises(SystemExit) as ctx:
            validate_input_output_isolation(
                out_dir=task / "notes",
                input_roots=[(task, "--task")],
                planned_trial_names=[],
                allowed_report_dir=None,
            )
        self.assertEqual(ctx.exception.code, 2)

    def test_output_equal_to_input_still_rejected(self) -> None:
        task = self.root / "task 208"
        task.mkdir()
        with self.assertRaises(SystemExit) as ctx:
            validate_input_output_isolation(
                out_dir=task,
                input_roots=[(task, "--task")],
                planned_trial_names=[],
                allowed_report_dir=None,
            )
        self.assertEqual(ctx.exception.code, 2)


class TaskIdCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_end_to_end_direct_answer_and_task_named_reports(self) -> None:
        _make_harbor_tree(self.root)
        cli = SCRIPT_DIR / "analyze_case.py"
        proc = subprocess.run(
            [
                sys.executable,
                str(cli),
                "--task-id",
                "task208",
                "--jobs-root",
                str(self.root),
                "--format",
                "both",
                "--lang",
                "zh",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # stdout IS the direct answer, in the required order.
        self.assertIn("# task208", proc.stdout)
        self.assertLess(proc.stdout.index("四大类裁决"), proc.stdout.index("小分类"))
        self.assertIn("REPORT_DIR:", proc.stdout)
        report_dir = proc.stdout.strip().split("REPORT_DIR: ")[-1].strip()
        out = Path(report_dir)
        self.assertEqual(out.name, "task208")
        self.assertEqual(out.parent.name, REPORT_DIR_NAME)
        expected_top = {
            "task208.md",
            "summary.md",
            "data",
            ".cfa_manifest.json",
        }
        self.assertTrue(expected_top.issubset({p.name for p in out.iterdir()}))
        self.assertFalse((out / "task208.zh.md").exists())
        data_files = {p.name for p in (out / "data").iterdir()}
        self.assertTrue(
            {"evidence.json", "analysis.json", "summary.json", "candidate-hypotheses.json"}.issubset(
                data_files
            )
        )
        card = json.loads((out / "data" / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(len(card["four_categories"]), 4)
        self.assertEqual(card["task_id"], "task208")
        self.assertTrue((out.parent / "INDEX.md").is_file())

    def test_report_dir_override_outside_inputs_needs_no_exception(self) -> None:
        _make_harbor_tree(self.root)
        out = self.root / "custom" / REPORT_DIR_NAME
        proc = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_DIR / "analyze_case.py"),
                "--task-id",
                "task208",
                "--jobs-root",
                str(self.root),
                "--report-dir",
                str(out),
                "--no-verdict-stdout",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue((out / "task208.md").is_file())
        self.assertEqual(proc.stdout.strip(), "")

    def test_nested_non_report_dir_is_rejected(self) -> None:
        trial = _make_harbor_tree(self.root)
        bad = trial.parent.parent.parent / "task 208" / "notes"
        bad.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_DIR / "analyze_case.py"),
                "--task-id",
                "task208",
                "--jobs-root",
                str(self.root),
                "--report-dir",
                str(bad),
                "--no-verdict-stdout",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("cannot be identical to, inside, or a parent", proc.stderr)

    def test_legacy_path_mode_still_silent(self) -> None:
        trial = _make_harbor_tree(self.root)
        out = self.root / "out"
        proc = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_DIR / "analyze_case.py"),
                "--job",
                str(trial),
                "--output",
                str(out),
                "--format",
                "both",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("REPORT_DIR:", proc.stdout)
        self.assertTrue((out / "report.md").is_file())
        self.assertTrue((out / "analysis.json").is_file())


class SharedReportDirManifestTests(unittest.TestCase):
    """A shared bench-level failure-report/ must never delete another task's files."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.out = self.root / "failure-report"
        self.out.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _publish(self, key: str, files: dict) -> None:
        staging = Path(tempfile.mkdtemp(dir=self.root))
        for rel, text in files.items():
            dest = staging / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(text, encoding="utf-8")
        publish_staging_to_output(staging, self.out, is_multi_trial=False, manifest_key=key)

    def test_manifest_filename_is_task_scoped(self) -> None:
        self.assertEqual(manifest_filename(), ".cfa_manifest.json")
        self.assertEqual(manifest_filename("task208"), ".cfa_manifest.task208.json")
        self.assertEqual(manifest_filename("task 20/8"), ".cfa_manifest.task_20_8.json")

    def test_tasks_do_not_delete_each_other(self) -> None:
        self._publish("task207", {"task207.md": "a", "task207.json": "b"})
        self._publish("task208", {"task208.md": "c", "task208.json": "d"})
        for name in ("task207.md", "task207.json", "task208.md", "task208.json"):
            self.assertTrue((self.out / name).is_file(), name)
        self.assertTrue((self.out / ".cfa_manifest.task207.json").is_file())
        self.assertTrue((self.out / ".cfa_manifest.task208.json").is_file())

        # Re-analysing task207 with markdown only must drop its own stale JSON
        # while leaving task208 completely untouched.
        self._publish("task207", {"task207.md": "a2"})
        self.assertFalse((self.out / "task207.json").exists())
        self.assertTrue((self.out / "task208.json").is_file())
        self.assertTrue((self.out / "task208.md").is_file())

    def test_unmanaged_user_files_survive(self) -> None:
        notes = self.out / "notes.md"
        notes.write_text("user notes", encoding="utf-8")
        self._publish("task207", {"task207.md": "a"})
        self._publish("task207", {"task207.md": "b"})
        self.assertTrue(notes.is_file())


if __name__ == "__main__":
    unittest.main()
