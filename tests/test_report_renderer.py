#!/usr/bin/env python3
"""
Unit tests for the refactored report renderer (`scripts/render_report.py`).

Verifies Phase 1 requirements:
  1. No redundant Part II English copy in bilingual reports.
  2. Standalone pure Chinese (`lang="zh"`) and pure English (`lang="en"`) output.
  3. Compact Markdown property table in Section 1.
  4. Callout quotes for direct failure cause and internal root cause.
  5. Gate warning suppression for normal runs and emission for pre-startup/abort.
  6. Elimination of Python list repr syntax leaks in competing hypotheses.
  7. Consolidated single-line summary when all logs are missing.
  8. Missing artifacts tiered filtering for core vs secondary temporary files.
  9. Elimination of long absolute path clutter in evidence file pointers.
 10. Strict compliance with validate_analysis.REQUIRED_REPORT_SECTIONS.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_DIR = REPO_ROOT / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from render_report import (
    filter_core_missing_artifacts,
    format_evidence_list_text,
    render_job_summary_markdown,
    render_report_markdown,
)
from report_style import section_title
from validate_analysis import validate_report_sections


class TestReportRenderer(unittest.TestCase):
    def setUp(self) -> None:
        self.sample_evidence = {
            "case_id": "test_case",
            "trial_name": "trial_001",
            "trial_dir": "/path/to/trials/trial_001",
            "task_dir": "/path/to/tasks/task_001",
            "artifacts": [
                {
                    "artifact_id": "art:trajectory_json",
                    "rel_path": "agent/trajectory.json",
                    "scope": "trial",
                    "exists": True,
                    "abs_path": "/path/to/trials/trial_001/agent/trajectory.json",
                },
                {
                    "artifact_id": "art:verify_log",
                    "rel_path": "verifier/verify.log",
                    "scope": "trial",
                    "exists": True,
                    "abs_path": "/path/to/trials/trial_001/verifier/verify.log",
                },
                {
                    "artifact_id": "art:instruction_md",
                    "rel_path": "instruction.md",
                    "scope": "task",
                    "exists": True,
                    "abs_path": "/path/to/tasks/task_001/instruction.md",
                },
                {
                    "artifact_id": "art:results_json",
                    "rel_path": "results.json",
                    "scope": "trial",
                    "exists": True,
                    "abs_path": "/path/to/trials/trial_001/results.json",
                },
            ],
            "runtime": {
                "agent_started": True,
                "verifier_started": True,
                "execution_status": "completed",
                "verification_status": "failed",
                "reward": 0.0,
                "started_at": "2026-10-08T10:00:00Z",
                "finished_at": "2026-10-08T10:05:00Z",
            },
            "timeline": [],
            "behavioral_signals": [{"signal_id": "sig_1", "description": "Loop"}],
            "error_observations": [],
            "contract_observations": [],
            "verifier_observations": [
                {
                    "obs_id": "vobs_1",
                    "type": "verifier_fail_message",
                    "matched_text": "AssertionError: 3.14 != 2.71",
                }
            ],
            "scientific_observations": [],
            "missing_artifacts": [
                "agent/trajectory.json",
                "job:lock.json",
                "artifacts/manifest.json",
            ],
        }
        self.sample_analysis = {
            "schema_version": "failure-analysis-v1",
            "case_id": "test_case",
            "trial_name": "trial_001",
            "verdict": "failed",
            "failure_stage": "agent_execution",
            "detection_stage": "verifier_execution",
            "first_unrecovered_deviation": {
                "status": "identified",
                "event_ref": "ev_04",
                "timestamp": "2026-10-08T10:02:00Z",
                "summary": "Agent chose incorrect physical constant",
            },
            "failure_manifestation": {
                "type": "verifier_assertion_failure",
                "summary": "Verifier rejected output metric due to discrepancy",
            },
            "primary_root_cause": {
                "category": "agent",
                "subtype": "scientific_constant_error",
                "code": "AGENT_CONSTANT_ERROR",
                "confidence": 0.85,
                "confidence_kind": "heuristic_evidence_score",
                "evidence_strength": "high",
                "summary": "The model used pi instead of e in the kinetic calculation.",
            },
            "contributing_factors": [
                {
                    "category": "case",
                    "code": "CASE_PROMPT_AMBIGUITY",
                    "confidence": 0.35,
                    "summary": "Prompt did not specify symbol convention.",
                }
            ],
            "competing_hypotheses": [
                {
                    "hypothesis_id": "H1",
                    "category": "agent",
                    "subtype": "scientific_constant_error",
                    "confidence": 0.85,
                    "claim": "Agent substituted mathematical constants.",
                    "evidence_for": ["art:trajectory_json"],
                    "evidence_against": [],
                },
                {
                    "hypothesis_id": "H2",
                    "category": "verifier",
                    "subtype": "tolerance_too_strict",
                    "confidence": 0.20,
                    "claim": "Verifier tolerance was overly narrow.",
                    "evidence_for": [],
                    "evidence_against": ["art:verify_log"],
                },
            ],
            "evidence_refs": ["art:trajectory_json", "art:verify_log"],
            "excluded_hypotheses": [
                {
                    "hypothesis_id": "H_INFRA",
                    "category": "infra",
                    "reason": "Host environment and containers ran smoothly.",
                }
            ],
            "recommended_actions": [
                {"owner": "Agent Policy", "action": "Enforce unit and constant verification."}
            ],
            "skill_prescription": None,
        }

    def test_bilingual_report_structure_and_no_part_ii(self) -> None:
        """Verify report.md in bilingual mode renders 4 sections without Part II English edition."""
        report = render_report_markdown(
            self.sample_evidence, self.sample_analysis, lang="bilingual"
        )
        self.assertIn("案例失败归因报告 / Case Failure Attribution", report)
        for num in (1, 2, 3, 4):
            self.assertIn(f"## {section_title(num, 'bilingual')}", report)

        # Ensure Part II English Edition is completely removed
        self.assertNotIn("Part II: English Edition", report)
        self.assertNotIn("### E1.", report)
        self.assertNotIn("Jump to Part II", report)

        # Ensure validator passes
        errs = validate_report_sections(report)
        self.assertEqual(errs, [])

    def test_standalone_chinese_edition(self) -> None:
        """Verify report.zh.md renders pure Chinese headers and passes validation."""
        report = render_report_markdown(self.sample_evidence, self.sample_analysis, lang="zh")
        self.assertIn("# 案例失败归因报告", report)
        self.assertIn(f"## {section_title(1, 'zh')}", report)
        self.assertNotIn("Root Cause Verdict", report)
        for num in (2, 3, 4):
            self.assertIn(f"## {section_title(num, 'zh')}", report)

        errs = validate_report_sections(report)
        self.assertEqual(errs, [])

    def test_standalone_english_edition(self) -> None:
        """Verify report.en.md renders pure English headers and passes validation."""
        report = render_report_markdown(self.sample_evidence, self.sample_analysis, lang="en")
        self.assertIn("# Case Failure Attribution", report)
        for num in (1, 2, 3, 4):
            self.assertIn(f"## {section_title(num, 'en')}", report)
        self.assertNotIn("归因结论", report)

        errs = validate_report_sections(report)
        self.assertEqual(errs, [])

    def test_section1_property_table_and_callout_quotes(self) -> None:
        """Verify Section 1 contains Markdown property table and callout blockquotes."""
        report = render_report_markdown(
            self.sample_evidence, self.sample_analysis, lang="bilingual"
        )

        # Property table
        self.assertIn("| 项目 (Field) | 结论 (Finding) |", report)
        self.assertIn("| **评测结论 (Verdict)** |", report)
        self.assertIn("| **主因 (Primary cause)** |", report)
        self.assertIn("| **阶段 (Stages)** |", report)
        self.assertIn("| **置信度 (Confidence)** |", report)

        # Callout highlights (no decorative emoji)
        self.assertIn("> **失败表现 (Failure manifestation)**:", report)
        self.assertIn("> **归因判断 (Attribution)**:", report)
        self.assertNotIn("🎯", report)
        self.assertNotIn("🔍", report)

    def test_gate_warning_omitted_when_normal(self) -> None:
        """Verify gate warning is hidden under normal execution (agent_started=True, verifier_started=True)."""
        report = render_report_markdown(
            self.sample_evidence, self.sample_analysis, lang="bilingual"
        )
        self.assertNotIn("运行门控警示", report)
        self.assertNotIn("Runtime Gate Warning", report)

    def test_gate_warning_present_on_prestartup_failure(self) -> None:
        """Verify gate warning appears when agent did not start."""
        ev = dict(self.sample_evidence)
        ev["runtime"] = dict(ev["runtime"])
        ev["runtime"]["agent_started"] = False
        ev["runtime"]["verifier_started"] = False

        report = render_report_markdown(ev, self.sample_analysis, lang="bilingual")
        self.assertIn("运行门控警示 (Runtime Gate Warning)", report)
        self.assertIn("agent_started=False", report)

    def test_gate_warning_omitted_for_passed_cases(self) -> None:
        """Verify gate warning is not emitted when verdict is passed."""
        ev = dict(self.sample_evidence)
        ev["runtime"] = dict(ev["runtime"])
        ev["runtime"]["agent_started"] = False
        an = dict(self.sample_analysis)
        an["verdict"] = "passed"

        report = render_report_markdown(ev, an, lang="bilingual")
        self.assertNotIn("运行门控警示", report)

    def test_evidence_list_formatting_no_python_repr(self) -> None:
        """Verify format_evidence_list_text produces clean text without python list syntax leaks."""
        text_zh = format_evidence_list_text(["art:job_result"], [], lang="zh")
        self.assertIn("（支持证据：`art:job_result`）", text_zh)
        self.assertNotIn("['", text_zh)
        self.assertNotIn("[]", text_zh)

        text_bi = format_evidence_list_text(["art:job_result"], ["art:other"], lang="bilingual")
        self.assertIn("支持 / For: `art:job_result`", text_bi)
        self.assertIn("反驳 / Against: `art:other`", text_bi)
        self.assertNotIn("['", text_bi)

        report = render_report_markdown(
            self.sample_evidence, self.sample_analysis, lang="bilingual"
        )
        self.assertNotIn("['art:", report)
        self.assertNotIn("反驳: []", report)
        self.assertNotIn("against: []", report)

    def test_all_logs_missing_consolidated_into_single_line(self) -> None:
        """Verify when all logs are missing, Section 2 emits a single summary instead of 4 missing lines."""
        ev = dict(self.sample_evidence)
        ev["artifacts"] = []

        report = render_report_markdown(ev, self.sample_analysis, lang="bilingual")
        self.assertIn(
            "- **核心日志与产物 (Core Logs & Artifacts)**: 全部缺失 / All missing", report
        )
        # Should NOT contain 4 separate category lines claiming missing
        self.assertNotIn("交互轨迹与代码记录 (Agent Trajectory):", report)
        self.assertNotIn("验证器执行与判定日志 (Verifier Logs):", report)

    def test_missing_artifacts_filtering(self) -> None:
        """Verify missing_artifacts filters core files and converges temporary/internal ones."""
        missing = [
            "job:lock.json",
            "artifacts/manifest.json",
            "verifier/reward.txt",
            "agent/trajectory.json",
            "task:environment/Dockerfile",
        ]
        core, secondary_cnt = filter_core_missing_artifacts(missing)
        self.assertEqual(core, ["agent/trajectory.json"])
        self.assertEqual(secondary_cnt, 4)

    def test_report_renders_evidence_integrity_diagnostics(self) -> None:
        """Warning/error diagnostics must be visible in Section 1 and detailed in Section 2."""
        evidence = dict(self.sample_evidence)
        evidence["diagnostics"] = [
            {
                "diagnostic_id": "diag:verifier_check_stats:VERIFIER_CHECK_STATS_BUILD_FAILED",
                "severity": "warning",
                "stage": "verifier_check_stats",
                "code": "VERIFIER_CHECK_STATS_BUILD_FAILED",
                "message": "Verifier check statistics could not be built.",
                "impact": "Localization evidence is unavailable.",
                "source_ref": "verifier/verify.log",
            }
        ]
        for lang, warning_label, detail_title in (
            ("bilingual", "证据完整性警示", "证据降级诊断"),
            ("zh", "证据完整性警示", "证据降级诊断"),
            ("en", "Evidence Integrity Warning", "Evidence Degradation Diagnostics"),
        ):
            with self.subTest(lang=lang):
                report = render_report_markdown(evidence, self.sample_analysis, lang=lang)
                self.assertIn(warning_label, report)
                self.assertIn(detail_title, report)
                self.assertIn("VERIFIER_CHECK_STATS_BUILD_FAILED", report)
                self.assertIn("Localization evidence is unavailable.", report)
                self.assertIn("verifier/verify.log", report)

    def test_report_omits_integrity_block_without_diagnostics(self) -> None:
        report = render_report_markdown(
            self.sample_evidence, self.sample_analysis, lang="bilingual"
        )
        self.assertNotIn("证据完整性警示", report)
        self.assertNotIn("证据降级诊断", report)

    def test_no_absolute_paths_in_file_pointers(self) -> None:
        """Verify evidence pointers use relative paths anchored to case/trial context, eliminating path noise."""
        report = render_report_markdown(
            self.sample_evidence, self.sample_analysis, lang="bilingual"
        )
        self.assertIn("任务与试次 (Case & Trial)", report)
        self.assertIn("任务 `test_case`, 试次 `trial_001`, 题目 `task_001`", report)
        # Check that individual artifact lines don't leak absolute paths
        self.assertNotIn("/path/to/trials", report)
        self.assertNotIn("/path/to/tasks", report)
        self.assertNotIn("/Users/", report)
        self.assertNotIn("/tmp/", report)
        self.assertIn("[trial] `agent/trajectory.json`", report)
        self.assertIn("[trial] `verifier/verify.log`", report)


class TestJobSummaryRenderer(unittest.TestCase):
    def setUp(self) -> None:
        self.sample_job_summary = {
            "job_dir": "/path/to/jobs/quantum_espresso_batch",
            "batch_statistics": {
                "total_trials": 3,
                "agent_started_count": 3,
                "verifier_started_count": 2,
                "verdict_counts": {
                    "passed": 1,
                    "failed": 2,
                },
                "failure_stage_counts": {
                    "agent_execution": 2,
                },
                "category_counts": {
                    "agent": 2,
                    "none": 1,
                },
                "root_cause_code_counts": {
                    "AGENT_CALCULATION_ERROR": 2,
                },
            },
            "trials": [
                {
                    "trial_name": "trial_01_pass",
                    "verdict": "passed",
                    "agent_started": True,
                    "verifier_started": True,
                    "failure_stage": "none",
                    "detection_stage": "none",
                    "category": "none",
                    "code": None,
                    "confidence": None,
                },
                {
                    "trial_name": "trial_02_fail",
                    "verdict": "failed",
                    "agent_started": True,
                    "verifier_started": True,
                    "failure_stage": "agent_execution",
                    "detection_stage": "verifier_execution",
                    "category": "agent",
                    "code": "AGENT_CALCULATION_ERROR",
                    "confidence": 0.90,
                },
                {
                    "trial_name": "trial_03_collect",
                    "verdict": "failed",
                    "agent_started": True,
                    "verifier_started": False,
                    "phase": "collect",
                },
            ],
        }

    def test_job_summary_bilingual_edition(self) -> None:
        md = render_job_summary_markdown(self.sample_job_summary, lang="bilingual")
        self.assertIn("# Multi-Trial Batch Execution Summary / 多 Trial 批处理执行汇总", md)
        self.assertIn("quantum_espresso_batch", md)
        self.assertIn("Language / 语言版本", md)

        # Overview and KPI
        self.assertIn("33.3%", md)  # 1/3 = 33.3%
        self.assertIn("Passed: `1`", md)
        self.assertIn("Failed: `2`", md)

        # Section 1: Core Batch Metrics
        self.assertIn("## 1. 核心批次指标汇总 (Batch Metrics Summary)", md)
        self.assertIn(
            "| 总 Trial 数 (Total Trials) | 测试通过 (Passed) | 判定失败 (Failed) | 执行异常 (Errored) | 通过率 (Pass Rate) |",
            md,
        )
        self.assertIn("| 3 | 1 | 2 | 0 | **33.3%** |", md)

        # Section 2: Root cause category distribution
        self.assertIn("## 2. 根因分类分布统计 (Root Cause Distribution)", md)
        self.assertIn("智能体决策与执行失误 (Agent Decisions, `agent`)", md)
        self.assertIn("评测验证与规则判定缺陷 (Evaluation & Verifier, `verifier`)", md)
        self.assertIn("基准题目与规格设计缺陷 (Case Design, `case`)", md)
        self.assertIn("运行环境与系统设施缺陷 (Environment & Infra, `infra`)", md)
        self.assertIn("未分类或信息缺失 (Unknown/Unclassified, `unknown`)", md)
        self.assertIn("66.7%", md)  # 2/3 agent failures = 66.7%

        # Section 3: Trial diagnostics table
        self.assertIn("## 3. 各 Trial 详细诊断清单 (Trial Diagnostics List)", md)
        self.assertIn("`trial_01_pass`", md)
        self.assertIn("`passed`", md)
        self.assertIn("[report.md](trial_01_pass/report.md)", md)

        self.assertIn("`trial_02_fail`", md)
        self.assertIn("`failed`", md)
        self.assertIn("`agent`", md)
        self.assertIn("`AGENT_CALCULATION_ERROR`", md)
        self.assertIn("0.90", md)
        self.assertIn("[report.md](trial_02_fail/report.md)", md)

        # Collect phase
        self.assertIn("`trial_03_collect`", md)
        self.assertIn("收集阶段 (Collect)", md)
        self.assertIn("[evidence.json](trial_03_collect/evidence.json)", md)

    def test_job_summary_chinese_edition(self) -> None:
        md = render_job_summary_markdown(self.sample_job_summary, lang="zh")
        self.assertIn("# 多 Trial 评测任务汇总报告", md)
        self.assertIn("## 1. 核心批次指标汇总", md)
        self.assertIn("## 2. 根因分类分布统计", md)
        self.assertIn("## 3. 各 Trial 详细诊断清单", md)
        self.assertIn("[report.zh.md](trial_01_pass/report.zh.md)", md)
        self.assertIn("[report.zh.md](trial_02_fail/report.zh.md)", md)
        self.assertIn("[evidence.json](trial_03_collect/evidence.json)", md)
        self.assertNotIn("(Batch Metrics Summary)", md)

    def test_job_summary_english_edition(self) -> None:
        md = render_job_summary_markdown(self.sample_job_summary, lang="en")
        self.assertIn("# Multi-Trial Batch Execution Summary", md)
        self.assertIn("## 1. Batch Metrics Summary", md)
        self.assertIn("## 2. Root Cause Category Distribution", md)
        self.assertIn("## 3. Trial Diagnostics List", md)
        self.assertIn("[report.en.md](trial_01_pass/report.en.md)", md)
        self.assertIn("[report.en.md](trial_02_fail/report.en.md)", md)
        self.assertIn("[evidence.json](trial_03_collect/evidence.json)", md)
        self.assertNotIn("核心批次指标", md)

    def test_job_summary_surfaces_degraded_evidence(self) -> None:
        data = {
            "job_dir": "/path/to/jobs/degraded_batch",
            "batch_statistics": {
                "total_trials": 2,
                "verdict_counts": {"passed": 1, "failed": 1},
                "degraded_evidence_trial_count": 1,
                "diagnostic_severity_counts": {"warning": 2, "error": 0},
            },
            "trials": [
                {
                    "trial_name": "trial_clean",
                    "verdict": "passed",
                    "diagnostic_counts": {"warning": 0, "error": 0},
                },
                {
                    "trial_name": "trial_degraded",
                    "verdict": "failed",
                    "category": "unknown",
                    "code": "UNKNOWN_INSUFFICIENT_EVIDENCE",
                    "confidence": 0.0,
                    "diagnostic_counts": {"warning": 2, "error": 0},
                },
            ],
        }
        for lang, notice, degraded_cell in (
            ("bilingual", "证据降级提示", "⚠️ 2"),
            ("zh", "证据降级提示", "⚠️ 2"),
            ("en", "Evidence Degradation Notice", "⚠️ 2"),
        ):
            with self.subTest(lang=lang):
                md = render_job_summary_markdown(data, lang=lang)
                self.assertIn(notice, md)
                self.assertIn("`1`", md)  # one degraded trial
                self.assertIn(degraded_cell, md)
                self.assertIn("trial_degraded", md)

    def test_job_summary_without_degraded_evidence_has_no_notice(self) -> None:
        md = render_job_summary_markdown(self.sample_job_summary, lang="bilingual")
        self.assertNotIn("证据降级提示", md)

    def test_job_summary_empty_trials(self) -> None:
        empty_data = {
            "job_dir": "/path/to/empty_job",
            "batch_statistics": {},
            "trials": [],
        }
        md = render_job_summary_markdown(empty_data, lang="bilingual")
        self.assertIn("0.0%", md)
        self.assertIn("暂无记录 / No records", md)

    def test_job_summary_fallback_computation_without_batch_stats(self) -> None:
        data = {
            "job_dir": "/path/to/job_no_stats",
            "trials": [
                {
                    "trial_name": "t1",
                    "verdict": "passed",
                },
                {
                    "trial_name": "t2",
                    "verdict": "failed",
                    "category": "agent",
                    "code": "ERR",
                    "confidence": 0.8,
                },
            ],
        }

    def test_job_summary_no_absolute_paths(self) -> None:
        """Verifies job_summary.md does not leak absolute paths from job_dir."""
        data = {
            "job_dir": "/Users/hx/workspace/benchmarks/job_run_123",
            "batch_statistics": {"total_trials": 1, "verdict_counts": {"passed": 1}},
            "trials": [{"trial_name": "t1", "verdict": "passed"}],
        }
        md = render_job_summary_markdown(data, lang="bilingual")
        self.assertNotIn("/Users/", md)
        self.assertNotIn("/workspace/", md)
        self.assertIn("job_run_123", md)


class TestReportEdgeCasesAndContracts(unittest.TestCase):
    def test_passed_lightweight_view_structure_and_budget(self) -> None:
        """Verifies passed edition is < 30 lines, has only 2 sections, and zero failure semantics."""
        evidence = {
            "case_id": "case_passed_01",
            "trial_name": "trial_pass",
            "trial_dir": "/tmp/job/trial_pass",
            "task_dir": "/tmp/tasks/task_01",
            "artifacts": [
                {"artifact_id": "art:reward", "rel_path": "verifier/reward.txt", "exists": True},
                {"artifact_id": "art:res", "rel_path": "result.json", "exists": True},
            ],
            "runtime": {
                "agent_started": True,
                "verifier_started": True,
                "execution_status": "completed",
                "verification_status": "passed",
                "reward": 1.0,
            },
            "timeline": [],
            "behavioral_signals": [],
            "error_observations": [],
            "contract_observations": [],
            "verifier_observations": [],
            "scientific_observations": [],
            "missing_artifacts": [],
        }
        analysis = {
            "schema_version": "failure-analysis-v1",
            "case_id": "case_passed_01",
            "trial_name": "trial_pass",
            "verdict": "passed",
            "failure_stage": "none",
            "detection_stage": "none",
            "first_unrecovered_deviation": None,
            "failure_manifestation": None,
            "primary_root_cause": None,
            "contributing_factors": [],
            "competing_hypotheses": [],
            "evidence_refs": ["art:trial_result"],
            "excluded_hypotheses": [],
            "recommended_actions": [],
            "skill_prescription": None,
        }

        # Bilingual passed view
        rep_bi = render_report_markdown(evidence, analysis, lang="bilingual")
        val_errors_bi = validate_report_sections(rep_bi, verdict="passed")
        self.assertEqual(val_errors_bi, [])

        lines_bi = [line for line in rep_bi.splitlines() if line.strip()]
        self.assertLess(len(lines_bi), 30)
        self.assertIn(f"## {section_title(1, 'bilingual', passed=True)}", rep_bi)
        self.assertIn(f"## {section_title(2, 'bilingual', passed=True)}", rep_bi)
        self.assertNotIn("## 3.", rep_bi)
        self.assertNotIn("## 4.", rep_bi)
        self.assertNotIn("失败表现", rep_bi)
        self.assertNotIn("归因判断", rep_bi)
        self.assertNotIn("竞争假设", rep_bi)
        self.assertNotIn("修复行动", rep_bi)
        self.assertNotIn("/tmp/", rep_bi)

        # Standalone Chinese passed view
        rep_zh = render_report_markdown(evidence, analysis, lang="zh")
        self.assertEqual(validate_report_sections(rep_zh, verdict="passed"), [])
        self.assertIn(f"## {section_title(1, 'zh', passed=True)}", rep_zh)
        self.assertIn(f"## {section_title(2, 'zh', passed=True)}", rep_zh)
        self.assertNotIn("Verdict & Pass Summary", rep_zh)

        # Standalone English passed view
        rep_en = render_report_markdown(evidence, analysis, lang="en")
        self.assertEqual(validate_report_sections(rep_en, verdict="passed"), [])
        self.assertIn(f"## {section_title(1, 'en', passed=True)}", rep_en)
        self.assertIn(f"## {section_title(2, 'en', passed=True)}", rep_en)
        self.assertNotIn("通过结论", rep_en)

    def test_passed_report_places_degraded_warning_before_section_two(self) -> None:
        """A passed trial with degraded evidence must warn in Section 1 and revise its pass claim."""
        evidence = {
            "case_id": "case_passed_degraded",
            "trial_name": "trial_pass_degraded",
            "task_dir": "/tmp/tasks/task_02",
            "artifacts": [],
            "diagnostics": [
                {
                    "diagnostic_id": "diag:verifier_contract:VERIFIER_AST_PARSE_FAILED",
                    "severity": "warning",
                    "stage": "verifier_contract",
                    "code": "VERIFIER_AST_PARSE_FAILED",
                    "message": "Could not parse tests/verify.py.",
                    "impact": "Contract audit is incomplete.",
                    "source_ref": "verifier/verify.log",
                }
            ],
        }
        analysis = {
            "case_id": "case_passed_degraded",
            "trial_name": "trial_pass_degraded",
            "verdict": "passed",
        }
        for lang, warn_label, detail_title, section_two, stale_claims in (
            (
                "bilingual",
                "证据完整性警示",
                "证据降级诊断",
                f"## {section_title(2, 'bilingual', passed=True)}",
                ("未检测到未恢复异常", "no unrecovered runtime"),
            ),
            (
                "zh",
                "证据完整性警示",
                "证据降级诊断",
                f"## {section_title(2, 'zh', passed=True)}",
                ("未检测到未恢复异常",),
            ),
            (
                "en",
                "Evidence Integrity Warning",
                "Evidence Degradation Diagnostics",
                f"## {section_title(2, 'en', passed=True)}",
                ("no unrecovered runtime",),
            ),
        ):
            with self.subTest(lang=lang):
                report = render_report_markdown(evidence, analysis, lang=lang)
                warn_pos = report.index(f"> ⚠️ **{warn_label}")
                section_two_pos = report.index(section_two)
                detail_pos = report.index(f"### {detail_title}")
                self.assertLess(warn_pos, section_two_pos)
                self.assertLess(section_two_pos, detail_pos)
                self.assertTrue(
                    report[:warn_pos].endswith("\n\n"),
                    "warning blockquote must be separated by a blank line",
                )
                for claim in stale_claims:
                    self.assertNotIn(claim, report)
                self.assertIn("VERIFIER_AST_PARSE_FAILED", report)
                self.assertEqual(validate_report_sections(report, verdict="passed"), [])

    def test_hypothesis_pruning_when_agent_not_started(self) -> None:
        """Verifies candidate hypotheses blaming agent are pruned when agent never started."""
        evidence = {
            "case_id": "infra_fail",
            "trial_name": "t_errored",
            "trial_dir": "/tmp/t_errored",
            "artifacts": [],
            "runtime": {
                "agent_started": False,
                "verifier_started": False,
                "execution_status": "setup_failed",
            },
            "timeline": [],
            "behavioral_signals": [],
            "error_observations": [],
            "contract_observations": [],
            "verifier_observations": [],
            "scientific_observations": [],
            "missing_artifacts": [],
        }
        analysis = {
            "schema_version": "failure-analysis-v1",
            "case_id": "infra_fail",
            "verdict": "errored",
            "failure_stage": "pre_startup",
            "detection_stage": "pre_startup",
            "first_unrecovered_deviation": {
                "status": "identified",
                "event_ref": "err_01",
                "summary": "Container failed to start",
            },
            "failure_manifestation": "Docker setup exited with 127",
            "primary_root_cause": {
                "category": "infra",
                "subtype": "container_setup_failure",
                "code": "INFRA_SETUP_FAILED",
                "confidence": 0.95,
                "reasoning": "Docker exited before agent could boot",
            },
            "contributing_factors": [],
            "competing_hypotheses": [
                {
                    "hypothesis_id": "H_INFRA",
                    "category": "infra",
                    "subtype": "container_setup_failure",
                    "confidence": 0.95,
                    "claim": "Host setup script crashed",
                    "evidence_for": ["err_01"],
                },
                {
                    "hypothesis_id": "H_AGENT",
                    "category": "agent",
                    "subtype": "wrong_reasoning",
                    "confidence": 0.0,
                    "claim": "Agent chose wrong tools",
                    "evidence_for": [],
                },
            ],
            "evidence_refs": ["err_01"],
            "excluded_hypotheses": [
                {
                    "hypothesis_id": "H_EX_AGENT",
                    "category": "agent",
                    "reason": "Agent never launched",
                }
            ],
            "recommended_actions": [{"owner": "Infra", "action": "Fix Dockerfile"}],
            "skill_prescription": None,
        }

        report = render_report_markdown(evidence, analysis, lang="bilingual")
        self.assertIn("H_INFRA", report)
        # H_AGENT candidate should be pruned
        self.assertNotIn("候选假设 `H_AGENT`", report)
        self.assertNotIn("H_AGENT (`agent`", report)

    def test_filter_core_missing_artifacts_exact_basename_boundaries(self) -> None:
        """Verifies exact basename whitelist and rejection of substring traps."""
        missing = [
            "agent/trajectory.json",
            "verifier/verify.log",
            "results.json",
            "instruction.md",
            "tests/refs.json",
            # Substring traps and secondary files:
            "old_trajectory.json.bak",
            "verify.log.old",
            "results.json.tar.gz",
            "verifier/reward.txt",
            "artifacts/manifest.json",
            "job:lock.json",
            "setup.log",
            "task:environment/Dockerfile",
        ]
        core, secondary_cnt = filter_core_missing_artifacts(missing)
        expected_core = [
            "agent/trajectory.json",
            "verifier/verify.log",
            "results.json",
            "instruction.md",
            "tests/refs.json",
        ]
        self.assertEqual(core, expected_core)
        self.assertEqual(secondary_cnt, 8)

    def test_report_budget_and_sections_contract_across_three_archetypes(self) -> None:
        """
        Enforces content and length budget contracts across three key archetypes:
          1. Passed trial: 2 sections, < 30 non-empty lines, zero failure semantics.
          2. Normal failed trial: 4 sections, < 60 non-empty lines, no false gate warnings.
          3. Pre-startup errored trial: 4 sections, < 45 non-empty lines, gate warning active, agent blame pruned.
        All three MUST be 100% free of local absolute path leaks (/Users/, /home/, /tmp/).
        """
        # --- 1. Passed Trial ---
        ev_pass = {
            "case_id": "c_pass",
            "trial_name": "t_pass",
            "trial_dir": "/Users/hx/workspace/benchmarks/job/t_pass",
            "task_dir": "/Users/hx/workspace/benchmarks/tasks/task_01",
            "artifacts": [{"artifact_id": "art:res", "rel_path": "result.json", "exists": True}],
            "runtime": {
                "agent_started": True,
                "verifier_started": True,
                "execution_status": "completed",
                "verification_status": "passed",
                "reward": 1.0,
            },
            "timeline": [],
            "behavioral_signals": [],
            "error_observations": [],
            "contract_observations": [],
            "verifier_observations": [],
            "scientific_observations": [],
            "missing_artifacts": [],
        }
        an_pass = {
            "schema_version": "failure-analysis-v1",
            "case_id": "c_pass",
            "trial_name": "t_pass",
            "verdict": "passed",
            "failure_stage": "none",
            "detection_stage": "none",
            "first_unrecovered_deviation": None,
            "failure_manifestation": None,
            "primary_root_cause": None,
            "contributing_factors": [],
            "competing_hypotheses": [],
            "evidence_refs": ["art:res"],
            "excluded_hypotheses": [],
            "recommended_actions": [],
            "skill_prescription": None,
        }
        rep_pass = render_report_markdown(ev_pass, an_pass, lang="bilingual")
        lines_pass = [l for l in rep_pass.splitlines() if l.strip()]
        self.assertLess(len(lines_pass), 30, f"Passed report too long: {len(lines_pass)} lines")
        self.assertIn(f"## {section_title(1, 'bilingual', passed=True)}", rep_pass)
        self.assertIn(f"## {section_title(2, 'bilingual', passed=True)}", rep_pass)
        self.assertNotIn("## 3.", rep_pass)
        self.assertNotIn("## 4.", rep_pass)
        self.assertNotIn("/Users/", rep_pass)
        self.assertNotIn("/home/", rep_pass)
        self.assertNotIn("/tmp/", rep_pass)

        # --- 2. Normal Failed Trial ---
        ev_fail = {
            "case_id": "c_fail",
            "trial_name": "t_fail",
            "trial_dir": "/Users/hx/workspace/benchmarks/job/t_fail",
            "task_dir": "/Users/hx/workspace/benchmarks/tasks/task_01",
            "artifacts": [
                {"artifact_id": "art:traj", "rel_path": "agent/trajectory.json", "exists": True},
                {"artifact_id": "art:vlog", "rel_path": "verifier/verify.log", "exists": True},
            ],
            "runtime": {
                "agent_started": True,
                "verifier_started": True,
                "execution_status": "completed",
                "verification_status": "failed",
                "reward": 0.0,
            },
            "timeline": [],
            "behavioral_signals": [{"signal_id": "s1", "description": "loop"}],
            "error_observations": [],
            "contract_observations": [],
            "verifier_observations": [
                {"obs_id": "vo1", "type": "verifier_fail_message", "matched_text": "Failed test"}
            ],
            "scientific_observations": [],
            "missing_artifacts": ["results.json", "job:lock.json"],
        }
        an_fail = {
            "schema_version": "failure-analysis-v1",
            "case_id": "c_fail",
            "trial_name": "t_fail",
            "verdict": "failed",
            "failure_stage": "agent_execution",
            "detection_stage": "verifier_execution",
            "first_unrecovered_deviation": {
                "status": "identified",
                "event_ref": "art:traj",
                "summary": "Wrong calculation",
            },
            "failure_manifestation": "Calculation mismatch",
            "primary_root_cause": {
                "category": "agent",
                "subtype": "calculation_error",
                "code": "AGENT_CALC_ERR",
                "confidence": 0.85,
            },
            "contributing_factors": [],
            "competing_hypotheses": [
                {
                    "hypothesis_id": "H1",
                    "category": "agent",
                    "subtype": "calc_error",
                    "confidence": 0.85,
                    "claim": "Math bug",
                    "evidence_for": ["art:traj"],
                },
            ],
            "evidence_refs": ["art:traj", "art:vlog"],
            "excluded_hypotheses": [],
            "recommended_actions": [{"owner": "Agent", "action": "Fix formula"}],
            "skill_prescription": None,
        }
        rep_fail = render_report_markdown(ev_fail, an_fail, lang="bilingual")
        lines_fail = [l for l in rep_fail.splitlines() if l.strip()]
        self.assertLess(
            len(lines_fail), 60, f"Normal failure report too long: {len(lines_fail)} lines"
        )
        for num in (1, 2, 3, 4):
            self.assertIn(f"## {section_title(num, 'bilingual')}", rep_fail)
        self.assertNotIn("运行门控警示", rep_fail)  # Normal run hides gate warning
        self.assertNotIn("/Users/", rep_fail)
        self.assertNotIn("/home/", rep_fail)
        self.assertNotIn("/tmp/", rep_fail)

        # --- 3. Pre-startup Errored Trial ---
        ev_err = {
            "case_id": "c_err",
            "trial_name": "t_err",
            "trial_dir": "/Users/hx/workspace/benchmarks/job/t_err",
            "task_dir": "/Users/hx/workspace/benchmarks/tasks/task_01",
            "artifacts": [],
            "runtime": {
                "agent_started": False,
                "verifier_started": False,
                "execution_status": "container_crash",
            },
            "timeline": [],
            "behavioral_signals": [],
            "error_observations": [],
            "contract_observations": [],
            "verifier_observations": [],
            "scientific_observations": [],
            "missing_artifacts": ["results.json"],
        }
        an_err = {
            "schema_version": "failure-analysis-v1",
            "case_id": "c_err",
            "trial_name": "t_err",
            "verdict": "errored",
            "failure_stage": "pre_startup",
            "detection_stage": "runner",
            "first_unrecovered_deviation": {
                "status": "identified",
                "event_ref": "art:lock",
                "summary": "Container exited 137",
            },
            "failure_manifestation": "Container crash during boot",
            "primary_root_cause": {
                "category": "infra",
                "subtype": "container_crash",
                "code": "INFRA_CONTAINER_CRASH",
                "confidence": 0.95,
            },
            "contributing_factors": [],
            "competing_hypotheses": [
                {
                    "hypothesis_id": "H_INFRA",
                    "category": "infra",
                    "subtype": "oom",
                    "confidence": 0.95,
                    "claim": "OOM in init",
                    "evidence_for": ["art:lock"],
                },
                {
                    "hypothesis_id": "H_AGENT",
                    "category": "agent",
                    "subtype": "bad_plan",
                    "confidence": 0.0,
                    "claim": "Speculative agent blame",
                    "evidence_for": [],
                },
            ],
            "evidence_refs": ["art:lock"],
            "excluded_hypotheses": [
                {
                    "hypothesis_id": "H_EX_AGENT",
                    "category": "agent",
                    "reason": "Agent never launched",
                }
            ],
            "recommended_actions": [{"owner": "Infra", "action": "Increase memory"}],
            "skill_prescription": None,
        }
        rep_err = render_report_markdown(ev_err, an_err, lang="bilingual")
        lines_err = [l for l in rep_err.splitlines() if l.strip()]
        self.assertLess(
            len(lines_err), 45, f"Pre-startup error report too long: {len(lines_err)} lines"
        )
        self.assertIn("运行门控警示", rep_err)  # Pre-startup shows prominent gate warning
        self.assertNotIn("候选假设 `H_AGENT`", rep_err)  # Agent candidate hypothesis pruned
        self.assertNotIn("/Users/", rep_err)
        self.assertNotIn("/home/", rep_err)
        self.assertNotIn("/tmp/", rep_err)


if __name__ == "__main__":
    unittest.main()
