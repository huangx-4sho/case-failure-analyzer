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
from render_verdict import build_verdict_card, render_verdict_card_markdown
from report_style import (
    format_confidence_breakdown,
    localize_engine_text,
    render_error_matrix_table,
    resolve_evidence_item,
    resolve_key_evidence_summary,
    section_title,
)
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


class TestRedesignedReportingAndLocalization(unittest.TestCase):
    """
    Validates Subagent W3 tasks:
      1. Zero English leaks in Chinese mode (Gate 4, Gate 6, Gate 1c sentences).
      2. Confidence receipt bar & breakdown row rendering.
      3. Structured Error Matrix table with recomputed numbers and margin ratio.
      4. Human-readable evidence resolution and key evidence summary.
      5. Harbor verifier_report suppresses false missing verify.log warnings.
      6. Passed report vendor wheel filtering and line budget (< 30 lines).
    """

    def test_chinese_localization_zero_english_leaks(self) -> None:
        """Verifies clean localization of dynamic engine sentences from Gate 4, Gate 6, and Gate 1c."""
        cases = [
            (
                "Agent delivered 'max_force' = 0.02863 (read from `artifacts/app/forces.csv`), which deviates from the verifier's independent reference/replay by 0.015 beyond its tolerance bound 0.005; the delivered result itself is inaccurate.",
                "Agent 交付的 'max_force' = 0.02863（读取自 `artifacts/app/forces.csv`）与验证器独立复算/重放结果相差 0.015，超出容差上限 0.005，交付数值本身不准确。",
            ),
            (
                "Agent delivered 'energy' = -12.34, which deviates from the verifier's independent reference/replay by 0.5 beyond its tolerance bound 0.1; the delivered result itself is inaccurate.",
                "Agent 交付的 'energy' = -12.34与验证器独立复算/重放结果相差 0.5，超出容差上限 0.1，交付数值本身不准确。",
            ),
            (
                "Agent delivered 'energy' = -12.34 read from `results.json`; the verifier's independent replay diverged by 0.5 beyond its tolerance bound 0.1.",
                "Agent 交付的 'energy' = -12.34（读取自 `results.json`），验证器独立重放检出偏差 0.5（超出容差 0.1）。",
            ),
            (
                "Agent delivered a 'max_force' result value that fails the verifier's independent recomputation (reported 0.0286 != recomputed 0.010, tol 0.005); the agent's own execution output records the same value (0.0286), so the delivered result itself is wrong.",
                "Agent 交付的 'max_force' 结果值未能通过验证器独立复算（交付值 0.0286 != 复算值 0.010，容差 0.005）；Agent 自身执行输出亦记录了相同数值 (0.0286)，交付结果本身有误。",
            ),
            (
                "The failing check names the record and metric with a declared numeric bound, and the agent's delivered value is independently located at `artifacts/forces.csv`. This is positive evidence of a delivered-value error, not of a verifier recomputation defect.",
                "失败检查明确指出了记录标识、指标名称与数值容差，且在产物 `artifacts/forces.csv` 中独立定位到了 Agent 的交付值；这属于交付数值错误的直接证据，而非验证器复算缺陷。",
            ),
            (
                "Re-derive 'energy' independently and reconcile the delivered value -12.34 with the verifier's replay before writing results (read from `results.json`).",
                "交付前请独立重新推导 'energy'，并将产物中的交付值 -12.34（位于 `results.json`）与验证器复算口径对齐核验。",
            ),
            (
                "Agent ran self-verification at `step_12` and reported success, but its script `tests/check.py` omits verifier-required checks: final_energy, band_gap (covered: forces); verifier rejected submitted outputs: Energy was not within bound",
                "Agent 在 `step_12` 执行了自检并报告通过，但其脚本 `tests/check.py` 漏检了验证器要求的检查项：final_energy, band_gap（已覆盖：forces）；验证器最终拒绝了提交产物：Energy was not within bound",
            ),
            (
                "Agent delivered /app/matrix.json with a structural, representation, or reference-ID mismatch against the verifier contract: sparse format expected",
                "Agent 提交的 /app/matrix.json 在数据结构、稀疏表示或引用 ID 上与验证器契约不符：sparse format expected",
            ),
            (
                "Software emitted `scf_nonconvergence` (Quantum ESPRESSO), but agent failed to diagnose the physical/input cause and repeatedly re-executed the failing setup (`sig_repeated_cmd`).",
                "仿真软件抛出 `scf_nonconvergence` (Quantum ESPRESSO)，但 Agent 未能诊断出物理/输入根因，反复重试同一失败配置 (`sig_repeated_cmd`)。",
            ),
            (
                "Harbor killed the agent at its configured execution timeout before the declared deliverables were complete (`missing final.json`); the verifier never started, so this trial was not scored.",
                "Harbor 在达到配置的执行超时上限时强行终止了 Agent，此时声明的交付物尚未完成（`missing final.json`）；验证器未启动，因此本试次未评分。",
            ),
            (
                "Runner aborted while collecting the agent's declared deliverables (`RUNNER_ERROR`, stage=runner, post-agent); the verifier still ran and scored the incomplete outputs.",
                "Runner 在采集 Agent 声明的交付物阶段中止（`RUNNER_ERROR`，阶段 runner，执行后）；验证器仍然运行并对不完整产物进行了评分。",
            ),
        ]
        for en_text, expected_zh in cases:
            actual_zh = localize_engine_text(en_text, lang="zh")
            self.assertEqual(actual_zh, expected_zh, f"Failed on: {en_text}")
            # Ensure English pass-through unchanged
            actual_en = localize_engine_text(en_text, lang="en")
            self.assertEqual(actual_en, en_text)

    def test_confidence_breakdown_rendering(self) -> None:
        """Verifies visual badges and Confidence breakdown row in report and verdict card."""
        evidence = {
            "case_id": "test_conf",
            "trial_name": "t1",
            "runtime": {"agent_started": True, "verifier_started": True, "reward": 0.0},
            "artifacts": [],
        }
        analysis = {
            "case_id": "test_conf",
            "trial_name": "t1",
            "verdict": "failed",
            "primary_root_cause": {
                "category": "agent",
                "subtype": "result_validation",
                "code": "AGENT_RESULT_VALIDATION",
                "confidence": 0.70,
                "confidence_breakdown": {
                    "points": 7,
                    "denominator": 10,
                    "factors": [
                        {
                            "key": "direct",
                            "delta": 2,
                            "label_zh": "直接因果实证",
                            "label_en": "Direct causal evidence",
                        },
                        {
                            "key": "cross",
                            "delta": 1,
                            "label_zh": "跨源证据互证",
                            "label_en": "Cross-source corroboration",
                        },
                        {
                            "key": "counterfactual",
                            "delta": 2,
                            "label_zh": "反事实闭环",
                            "label_en": "Counterfactual loop",
                        },
                    ],
                    "missing_to_raise": ["跨源互证", "独立复算"],
                },
                "summary": "Agent delivered invalid values.",
            },
            "failure_manifestation": "Calculation mismatch",
            "competing_hypotheses": [],
            "excluded_hypotheses": [],
            "recommended_actions": [],
        }

        # 1. Report Chinese rendering
        rep_zh = render_report_markdown(evidence, analysis, lang="zh")
        self.assertIn("🟢 **0.70**", rep_zh)
        self.assertIn("| **可信度拆解** |", rep_zh)
        self.assertIn("`7/10 分`", rep_zh)
        self.assertIn("✓ 直接因果实证(+2)", rep_zh)
        self.assertIn("提分缺口：跨源互证、独立复算", rep_zh)

        # 2. Report English rendering
        rep_en = render_report_markdown(evidence, analysis, lang="en")
        self.assertIn("🟢 **0.70**", rep_en)
        self.assertIn("| **Confidence breakdown** |", rep_en)
        self.assertIn("`7/10 pts`", rep_en)
        self.assertIn("✓ Direct causal evidence(+2)", rep_en)
        self.assertIn("Gap to raise: 跨源互证, 独立复算", rep_en)

        # 3. Verdict card rendering
        card = build_verdict_card(evidence, analysis)
        v_zh = render_verdict_card_markdown(card, lang="zh")
        self.assertIn("**可信度拆解**:", v_zh)
        self.assertIn("`7/10 分`", v_zh)
        self.assertIn("✓ 直接因果实证(+2)", v_zh)

    def test_error_matrix_rendering(self) -> None:
        """Verifies Error Matrix table rendering with recomputed numbers, tolerances and ratio."""
        evidence = {
            "case_id": "test_err_matrix",
            "trial_name": "t1",
            "runtime": {"agent_started": True, "verifier_started": True},
            "verifier_report": {
                "schema_family": "envelope+layered",
                "verdict": "fail",
                "primary_report": "core/verify.json",
                "failed_checks": [
                    {
                        "layer": "L3",
                        "name": "distances",
                        "detail": "worst deviation 3.326 A",
                        "core": True,
                        "source": "core/verify.json",
                        "failure_kind": "numerical",
                        "artifact_comparison": {"recomputed_eV": [7.4823, 20.3922]},
                    }
                ],
            },
            "scientific_observations": [
                {
                    "error_family": "verifier_recompute_divergence",
                    "metric": "distances",
                    "reported_value": 3.326,
                    "tolerance": 0.5,
                    "agent_reported_value": 4.126,
                    "agent_artifact_ref": "artifacts/app/out.csv:L10",
                    "is_delta_divergence": True,
                }
            ],
        }
        analysis = {
            "case_id": "test_err_matrix",
            "trial_name": "t1",
            "verdict": "failed",
            "primary_root_cause": {
                "category": "agent",
                "subtype": "result_validation",
                "code": "AGENT_RESULT_VALIDATION",
                "confidence": 0.85,
            },
            "failure_manifestation": "Verifier rejected distance metric",
        }

        # 1. Error Matrix table in report
        rep = render_report_markdown(evidence, analysis, lang="zh")
        self.assertIn(
            "| 检查项 | 错误性质 | 交付值 vs 复算/期望值 | 偏差 / 容差 (超标倍数) | 说明与来源 |",
            rep,
        )
        self.assertIn("`[L3] distances` [core]", rep)
        self.assertIn("数值偏差", rep)
        self.assertIn("4.126", rep)
        self.assertIn("recomputed_eV=[7.482, 20.39]", rep)
        self.assertIn("6.7× 超标", rep)
        self.assertIn("core/verify.json", rep)

        # 2. Verdict card
        card = build_verdict_card(evidence, analysis)
        v_card = render_verdict_card_markdown(card, lang="zh")
        self.assertIn("具名失败检查", v_card)
        self.assertIn("`[L3] distances` [core]", v_card)
        self.assertIn("6.7× 超标", v_card)

    def test_human_readable_evidence_resolution(self) -> None:
        """Verifies evidence resolver translates opaque IDs and collapses multiple self-checks."""
        evidence = {
            "case_id": "test_ev_res",
            "trial_name": "t1",
            "runtime": {"agent_started": True, "verifier_started": True},
            "verifier_report": {
                "primary_report": "verifier/verify.json",
                "failed_checks": [{"name": "neutral_stationary", "detail": "dev 1.6e-06"}],
            },
            "scientific_observations": [
                {
                    "sci_id": "sci:recompute_divergence:1",
                    "error_family": "verifier_recompute_divergence",
                    "metric": "energy",
                    "agent_reported_value": -12.34,
                    "recomputed_value": -10.0,
                    "tolerance": 0.05,
                }
            ],
            "behavioral_signals": [
                {
                    "signal_id": "sig:self_check:1:0",
                    "signal_type": "self_verification_executed",
                    "description": "self-check run 1",
                },
                {
                    "signal_id": "sig:self_check:2:0",
                    "signal_type": "self_verification_executed",
                    "description": "self-check run 2",
                },
            ],
        }
        analysis = {
            "case_id": "test_ev_res",
            "trial_name": "t1",
            "verdict": "failed",
            "primary_root_cause": {
                "category": "agent",
                "subtype": "result_validation",
                "code": "AGENT_RESULT_VALIDATION",
                "confidence": 0.8,
            },
            "evidence_refs": [
                "art:artifacts/app/outputs/charges.csv:L628",
                "sci:recompute_divergence:1",
                "ver:fail_log",
                "sig:self_check:1:0",
                "sig:self_check:2:0",
            ],
        }

        rep = render_report_markdown(evidence, analysis, lang="zh")
        # Human readable summary
        self.assertIn("### 核心实证清单", rep)
        self.assertIn(
            "📄 **交付产物** (`artifacts/app/outputs/charges.csv:L628`): 定位到交付记录实测值", rep
        )
        self.assertIn(
            "⚖️ **数值/科学观测** (`sci:recompute_divergence:1`): energy 交付值 -12.34 与复算值 -10.0 不符（容差 0.05）",
            rep,
        )
        self.assertIn(
            "🛡️ **验证器判定** (`verifier/verify.json`): 未通过 1 项具名检查：`neutral_stationary`",
            rep,
        )
        self.assertIn(
            "🚩 **行为信号** (`self_verification_executed`): 记录了 2 次自检执行事件（已折叠）", rep
        )

        # Raw IDs preserved intact
        self.assertIn(
            "- **证据引用**: `art:artifacts/app/outputs/charges.csv:L628`, `sci:recompute_divergence:1`, `ver:fail_log`, `sig:self_check:1:0`, `sig:self_check:2:0`",
            rep,
        )

    def test_harbor_verifier_report_suppresses_missing_log(self) -> None:
        """Verifies structured verifier_report suppresses false missing verify.log warnings."""
        evidence = {
            "case_id": "harbor_trial",
            "trial_name": "t1",
            "runtime": {"agent_started": True, "verifier_started": True, "reward": 0.0},
            "artifacts": [
                {"artifact_id": "art:traj", "rel_path": "agent/trajectory.json", "exists": True},
            ],
            "verifier_report": {
                "schema_family": "envelope+layered",
                "primary_report": "verifier/verify.json",
                "source_files": ["verifier/verify.json", "verifier/raw.json"],
                "failed_checks": [{"name": "test_pass", "detail": "failed"}],
            },
            "missing_artifacts": [
                "verifier/verify.log",
                "results.json",
                "job:lock.json",
            ],
        }
        analysis = {
            "case_id": "harbor_trial",
            "trial_name": "t1",
            "verdict": "failed",
            "primary_root_cause": {
                "category": "agent",
                "subtype": "result_validation",
                "code": "AGENT_RESULT_VALIDATION",
                "confidence": 0.8,
            },
            "evidence_refs": ["art:traj"],
        }

        rep = render_report_markdown(evidence, analysis, lang="zh")
        # Must surface structured verifier report
        self.assertIn("[trial] `verifier/verify.json` (结构化验证报告)", rep)
        # Must NOT emit missing verify.log
        self.assertNotIn("[trial] `verifier/verify.log` 【缺失】", rep)
        # Section 4 must NOT complain missing verifier/verify.log
        self.assertNotIn("`verifier/verify.log`", rep)
        self.assertIn("`results.json`", rep)

    def test_passed_report_filters_vendor_wheels_and_budget(self) -> None:
        """Verifies passed reports strip vendor .whl noise, summarize coverage, and stay < 30 lines."""
        wheel_artifacts = [
            {
                "artifact_id": f"art:whl_{i}",
                "rel_path": f"environment/assets/wheels/pkg_{i}-1.0-py3-none-any.whl",
                "exists": True,
            }
            for i in range(15)
        ]
        evidence = {
            "case_id": "c_pass_wheels",
            "trial_name": "t_pass",
            "task_dir": "/tmp/task_pass",
            "runtime": {
                "agent_started": True,
                "verifier_started": True,
                "reward": 1.0,
                "verification_status": "passed",
            },
            "verifier_report": {
                "passed_check_count": 8,
                "verification_coverage": 1.0,
            },
            "scientific_observations": [{"error_family": "scf_nonconvergence", "recovered": True}],
            "artifacts": [
                {"artifact_id": "art:res", "rel_path": "results.json", "exists": True},
                {"artifact_id": "art:traj", "rel_path": "agent/trajectory.json", "exists": True},
                {"artifact_id": "art:vlog", "rel_path": "verifier/verify.log", "exists": True},
                *wheel_artifacts,
            ],
        }
        analysis = {
            "case_id": "c_pass_wheels",
            "trial_name": "t_pass",
            "verdict": "passed",
        }

        # 1. Chinese edition
        rep_zh = render_report_markdown(evidence, analysis, lang="zh")
        non_empty_zh = [l for l in rep_zh.splitlines() if l.strip()]
        self.assertLess(
            len(non_empty_zh), 30, f"Passed report exceeded line budget: {len(non_empty_zh)}"
        )
        self.assertLess(
            len(non_empty_zh), 28, f"Passed report should stay < 28 lines: {len(non_empty_zh)}"
        )
        self.assertNotIn(".whl", rep_zh)
        self.assertIn("等共 18 项产物（已折叠环境依赖包）", rep_zh)
        self.assertIn("- **检查覆盖**: 通过检查项 `8` · 覆盖率 `100.0%`", rep_zh)
        self.assertIn("- **自愈警告**: 检出并自愈 1 项瞬态运行警告", rep_zh)

        # 2. English edition
        rep_en = render_report_markdown(evidence, analysis, lang="en")
        non_empty_en = [l for l in rep_en.splitlines() if l.strip()]
        self.assertLess(len(non_empty_en), 30)
        self.assertNotIn(".whl", rep_en)
        self.assertIn("vendor wheels omitted", rep_en)
        self.assertIn("- **Check coverage**: passed checks `8`, coverage `100.0%`", rep_en)
        self.assertIn(
            "- **Recovered warnings**: 1 transient diagnostic warning(s) recovered cleanly", rep_en
        )


if __name__ == "__main__":
    unittest.main()
