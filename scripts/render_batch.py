#!/usr/bin/env python3
"""
Batch Execution Summary Markdown Renderer (`scripts/render_batch.py`)

Renders multi-trial batch dashboard markdown (`job_summary.md`):
  - `lang="zh"`: Standalone Chinese batch summary
  - `lang="en"`: Standalone English batch summary
  - `lang="bilingual"`: Bilingual batch summary with parallel labels

Structure:
  1. Title & Overview: Job ID, total trials, pass rate, failed count, errored count
  2. Core Batch Metrics Table: Total Trials, Passed, Failed, Errored, Pass Rate
  3. Four Peer Root Cause Categories Distribution: Agent, Verifier, Case, Infra, Unknown
  4. Trial Diagnostics Table: Trial Name, Verdict, Category, Code, Confidence, Report link
     (with proper handling for collect-phase trials)

Security & Portability:
  Never outputs machine-specific local absolute paths (e.g. `/Users/...` or `/tmp/...`).
  Uses relative anchors and job identifiers.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List


def escape_md_cell(val: Any) -> str:
    if val is None:
        return "-"
    return str(val).replace("\r", " ").replace("\n", " ").replace("|", "\\|").strip()


def _scoring_coverage(
    batch_stats: Dict[str, Any],
    trials: List[Dict[str, Any]],
    total_trials: int,
) -> Dict[str, Any]:
    """
    Derive the three-state scoring coverage for a batch.

    Harbor aborts some trials *before* the verifier (artifact collection crash,
    runner interruption, agent timeout). Those trials have `reward=None`: they
    are **not scored** and must not be counted as agent failures in a pass rate.
    """
    verdict_counts = batch_stats.get("verdict_counts") or {}
    has_flags = any("verifier_started" in t for t in trials)
    if has_flags:
        scored = sum(1 for t in trials if bool(t.get("verifier_started")))
    else:
        scored = int(batch_stats.get("scored_trial_count") or 0)
        if scored == 0:
            scored = int(verdict_counts.get("passed", 0)) + int(verdict_counts.get("failed", 0))
    scored = min(max(scored, 0), total_trials)
    scored_passed = int(verdict_counts.get("passed", 0))
    return {
        "scored": scored,
        "not_scored": max(0, total_trials - scored),
        "scored_passed": scored_passed,
        "scored_failed": max(0, scored - scored_passed),
        "scored_pass_rate": (scored_passed / scored * 100.0) if scored > 0 else 0.0,
        "low_diagnosability": int(batch_stats.get("low_diagnosability_trial_count", 0) or 0)
        or sum(1 for t in trials if t.get("verifier_diagnosability") == "low"),
    }


def _render_coverage_block(
    cov: Dict[str, Any],
    lang: str,
    overall_pass_rate: float,
) -> List[str]:
    """Three-state scoring coverage subsection (additive under Section 1)."""
    ns = cov["not_scored"]
    sp = cov["scored_passed"]
    sf = cov["scored_failed"]
    sr = cov["scored_pass_rate"]

    if lang == "zh":
        out = [
            "### 1.1 评分覆盖与三态分布 (Scoring Coverage)\n\n",
            "| 状态 | 数量 | 说明 |\n|---|:---:|---|\n",
            f"| 已评分·通过 (Scored Passed) | `{sp}` | Verifier 完成且通过 |\n",
            f"| 已评分·失败 (Scored Failed) | `{sf}` | Verifier 完成但未通过 |\n",
            f"| 未评分 (Not Scored) | `{ns}` | Runner 在 verifier 前中止，`reward=None` |\n",
            f"| **可评分通过率 (Scored Pass Rate)** | **{sr:.1f}%** | 通过 / (通过 + 已评分失败) |\n\n",
            f"> **口径说明**: 全量通过率 **{overall_pass_rate:.1f}%** = 通过 / 总 Trial 数；"
            f"可评分通过率 **{sr:.1f}%** = 通过 / 实际进入 verifier 的 Trial 数。"
            "未评分 Trial 不应计为 Agent 失败。\n\n",
        ]
    elif lang == "en":
        out = [
            "### 1.1 Scoring Coverage (Three-State Breakdown)\n\n",
            "| State | Count | Meaning |\n|---|:---:|---|\n",
            f"| Scored Passed | `{sp}` | Verifier completed and passed |\n",
            f"| Scored Failed | `{sf}` | Verifier completed and failed |\n",
            f"| Not Scored | `{ns}` | Runner aborted before the verifier; `reward=None` |\n",
            f"| **Scored Pass Rate** | **{sr:.1f}%** | Passed / (Passed + Scored Failed) |\n\n",
            f"> **Rate definitions**: overall pass rate **{overall_pass_rate:.1f}%** = "
            f"Passed / Total Trials; scored pass rate **{sr:.1f}%** = Passed / Trials that "
            "actually reached the verifier. Not-scored trials are not Agent failures.\n\n",
        ]
    else:
        out = [
            "### 1.1 评分覆盖与三态分布 (Scoring Coverage / Three-State Breakdown)\n\n",
            "| 状态 (State) | 数量 (Count) | 说明 (Meaning) |\n|---|:---:|---|\n",
            f"| 已评分·通过 (Scored Passed) | `{sp}` | Verifier 完成且通过 / completed and passed |\n",
            f"| 已评分·失败 (Scored Failed) | `{sf}` | Verifier 完成但未通过 / completed and failed |\n",
            f"| 未评分 (Not Scored) | `{ns}` | Runner 在 verifier 前中止，`reward=None` / aborted before verifier |\n",
            f"| **可评分通过率 (Scored Pass Rate)** | **{sr:.1f}%** | 通过 / (通过 + 已评分失败) |\n\n",
            f"> **口径说明 (Rate definitions)**: 全量通过率 / Overall **{overall_pass_rate:.1f}%** "
            f"= 通过 / 总 Trial 数; 可评分通过率 / Scored **{sr:.1f}%** = 通过 / 进入 verifier 的 Trial 数 "
            "(Passed / trials that reached the verifier). 未评分 Trial 不应计为 Agent 失败 / "
            "Not-scored trials are not Agent failures.\n\n",
        ]

    if cov["low_diagnosability"] > 0:
        n = cov["low_diagnosability"]
        if lang == "zh":
            out.append(
                f"> ⚠️ **可诊断性提示**: 本批次有 `{n}` 个 Trial 的 verifier 结论仅为泛化提示"
                "（缺少具名 check 或数值偏差），建议补充 verifier 诊断信息后再做归因。\n\n"
            )
        elif lang == "en":
            out.append(
                f"> ⚠️ **Diagnosability Notice**: `{n}` trial(s) report only a terse generic "
                "verifier message (no named check or numeric deviation); enrich the verifier "
                "output before attributing these failures.\n\n"
            )
        else:
            out.append(
                f"> ⚠️ **可诊断性提示 (Diagnosability Notice)**: 本批次有 `{n}` 个 Trial 的 "
                "verifier 结论仅为泛化提示 (terse generic message, no named check / numeric "
                "deviation)，建议补充诊断信息后再归因。\n\n"
            )
    return out


def render_job_summary_markdown(
    job_summary_dict: Dict[str, Any],
    lang: str = "bilingual",
) -> str:
    """
    Renders multi-trial batch dashboard markdown (job_summary.md).
    Never prints machine-specific absolute filesystem paths.
    """
    job_dir_raw = str(job_summary_dict.get("job_dir") or "N/A")
    job_id = job_summary_dict.get("job_id") or (
        Path(job_dir_raw).name if job_dir_raw != "N/A" else "batch_job"
    )
    batch_stats = job_summary_dict.get("batch_statistics") or {}
    trials = job_summary_dict.get("trials") or []

    total_trials = int(batch_stats.get("total_trials", len(trials)))
    verdict_counts = batch_stats.get("verdict_counts") or {}
    category_counts = batch_stats.get("category_counts") or {}
    degraded_trial_count = int(batch_stats.get("degraded_evidence_trial_count", 0) or 0)
    diagnostic_counts = batch_stats.get("diagnostic_severity_counts") or {}

    # If batch_statistics was missing or empty, compute fallback stats from trials
    if not verdict_counts and trials:
        verdict_counts = {}
        category_counts = {}
        diagnostic_counts = {"warning": 0, "error": 0}
        degraded_trial_count = 0
        for row in trials:
            v = str(row.get("verdict") or "unknown")
            verdict_counts[v] = verdict_counts.get(v, 0) + 1
            cat = row.get("category")
            if cat:
                category_counts[cat] = category_counts.get(cat, 0) + 1
            row_diag = row.get("diagnostic_counts") or {}
            row_warnings = int(row_diag.get("warning", 0) or 0)
            row_errors = int(row_diag.get("error", 0) or 0)
            diagnostic_counts["warning"] += row_warnings
            diagnostic_counts["error"] += row_errors
            if row_warnings or row_errors:
                degraded_trial_count += 1
        total_trials = len(trials)

    diag_warnings = int(diagnostic_counts.get("warning", 0) or 0)
    diag_errors = int(diagnostic_counts.get("error", 0) or 0)

    passed_cnt = int(verdict_counts.get("passed", 0))
    failed_cnt = int(verdict_counts.get("failed", 0))
    errored_cnt = int(verdict_counts.get("errored", 0))
    pass_rate = (passed_cnt / total_trials * 100.0) if total_trials > 0 else 0.0

    lines: List[str] = []

    # 1. Title and Overview
    if lang == "zh":
        lines.append(f"# 多 Trial 评测任务汇总报告: `{job_id}`\n\n")
        lines.append(
            "| 诊断维度 | 详情 |\n"
            "|---|---|\n"
            f"| **任务标识** | `{escape_md_cell(job_id)}` |\n"
            f"| **总 Trial 数** | `{total_trials}` |\n"
            f"| **评测通过率** | **{pass_rate:.1f}%** |\n"
            f"| **失败与异常** | 失败: `{failed_cnt}`, 异常: `{errored_cnt}` |\n\n"
        )
        lines.append(
            f"> 🎯 **批次总览**: 本批次共执行 `{total_trials}` 个 Trial，"
            f"测试通过 `{passed_cnt}` 个，判定失败 `{failed_cnt}` 个，执行异常 `{errored_cnt}` 个，"
            f"整体通过率为 **{pass_rate:.1f}%**。\n\n"
        )
    elif lang == "en":
        lines.append(f"# Multi-Trial Batch Execution Summary: `{job_id}`\n\n")
        lines.append(
            "| Dimension | Details |\n"
            "|---|---|\n"
            f"| **Job ID** | `{escape_md_cell(job_id)}` |\n"
            f"| **Total Trials** | `{total_trials}` |\n"
            f"| **Pass Rate** | **{pass_rate:.1f}%** |\n"
            f"| **Failed / Errored** | Failed: `{failed_cnt}`, Errored: `{errored_cnt}` |\n\n"
        )
        lines.append(
            f"> 🎯 **Batch Overview**: Executed `{total_trials}` trials in total: "
            f"`{passed_cnt}` passed, `{failed_cnt}` failed, `{errored_cnt}` errored, "
            f"overall pass rate **{pass_rate:.1f}%**.\n\n"
        )
    else:  # bilingual
        lines.append(
            f"# Multi-Trial Batch Execution Summary / 多 Trial 批处理执行汇总: `{job_id}`\n\n"
        )
        lines.append("> **Language / 语言版本**: 中英双语对照版 (Bilingual Edition)\n\n")
        lines.append(
            "| 诊断维度 (Dimension) | 详情 (Details) |\n"
            "|---|---|\n"
            f"| **任务标识 (Job ID)** | `{escape_md_cell(job_id)}` |\n"
            f"| **总 Trial 数 (Total Trials)** | `{total_trials}` |\n"
            f"| **评测通过率 (Pass Rate)** | **{pass_rate:.1f}%** |\n"
            f"| **失败与异常 (Failed / Errored)** | 失败 / Failed: `{failed_cnt}`, 异常 / Errored: `{errored_cnt}` |\n\n"
        )
        lines.append(
            f"> 🎯 **批次总览 (Batch Overview)**: 共执行 `{total_trials}` 个 Trial，"
            f"通过 `{passed_cnt}` 个，失败 `{failed_cnt}` 个，异常 `{errored_cnt}` 个，"
            f"综合通过率为 **{pass_rate:.1f}%** "
            f"(Passed: `{passed_cnt}`, Failed: `{failed_cnt}`, Errored: `{errored_cnt}`, Pass Rate: **{pass_rate:.1f}%**)。\n\n"
        )

    if degraded_trial_count > 0:
        if lang == "zh":
            lines.append(
                f"> ⚠️ **证据降级提示**: 本批次有 `{degraded_trial_count}` 个 Trial 的证据采集发生降级"
                f"（warning: `{diag_warnings}`, error: `{diag_errors}`），相关归因结论的置信度可能受影响；"
                "详见各 Trial 报告第 2 节证据降级诊断。\n\n"
            )
        elif lang == "en":
            lines.append(
                f"> ⚠️ **Evidence Degradation Notice**: `{degraded_trial_count}` trial(s) "
                f"in this batch ran on degraded evidence (warning: `{diag_warnings}`, "
                f"error: `{diag_errors}`); attribution confidence may be affected — see "
                "the degradation diagnostics in each trial report's Section 2.\n\n"
            )
        else:
            lines.append(
                f"> ⚠️ **证据降级提示 (Evidence Degradation Notice)**: 本批次有 "
                f"`{degraded_trial_count}` 个 Trial 的证据采集发生降级 (warning: "
                f"`{diag_warnings}`, error: `{diag_errors}`)，相关归因结论的置信度可能受影响；"
                "详见各 Trial 报告第 2 节 / See each trial report's Section 2.\n\n"
            )

    # 2. Core Batch Metrics Table
    if lang == "zh":
        lines.append("## 1. 核心批次指标汇总\n\n")
        lines.append(
            "| 总 Trial 数 | 测试通过 | 判定失败 | 执行异常 | 通过率 |\n"
            "|:---:|:---:|:---:|:---:|:---:|\n"
            f"| {total_trials} | {passed_cnt} | {failed_cnt} | {errored_cnt} | **{pass_rate:.1f}%** |\n\n"
        )
    elif lang == "en":
        lines.append("## 1. Batch Metrics Summary\n\n")
        lines.append(
            "| Total Trials | Passed | Failed | Errored | Pass Rate |\n"
            "|:---:|:---:|:---:|:---:|:---:|\n"
            f"| {total_trials} | {passed_cnt} | {failed_cnt} | {errored_cnt} | **{pass_rate:.1f}%** |\n\n"
        )
    else:  # bilingual
        lines.append("## 1. 核心批次指标汇总 (Batch Metrics Summary)\n\n")
        lines.append(
            "| 总 Trial 数 (Total Trials) | 测试通过 (Passed) | 判定失败 (Failed) | 执行异常 (Errored) | 通过率 (Pass Rate) |\n"
            "|:---:|:---:|:---:|:---:|:---:|\n"
            f"| {total_trials} | {passed_cnt} | {failed_cnt} | {errored_cnt} | **{pass_rate:.1f}%** |\n\n"
        )

    # 2b. Three-state scoring coverage (passed / scored-failed / not-scored).
    # Additive: keeps the legacy all-trial pass rate above intact while surfacing
    # the harbor trials that never reached the verifier.
    coverage = _scoring_coverage(batch_stats, trials, total_trials)
    lines.extend(_render_coverage_block(coverage, lang, pass_rate))

    # 3. Four Peer Root Cause Categories Distribution Table
    cat_keys = ["agent", "verifier", "case", "infra", "unknown"]
    if lang == "zh":
        lines.append("## 2. 根因分类分布统计\n\n")
        lines.append(
            "| 根因大类 (Category) | 数量 (Count) | 占比 (Percentage) |\n|---|:---:|:---:|\n"
        )
        cat_labels_zh = {
            "agent": "智能体决策与执行失误 (`agent`)",
            "verifier": "评测验证与规则判定缺陷 (`verifier`)",
            "case": "基准题目与规格设计缺陷 (`case`)",
            "infra": "运行环境与系统设施缺陷 (`infra`)",
            "unknown": "未分类或信息缺失 (`unknown`)",
        }
        for ck in cat_keys:
            cnt = int(category_counts.get(ck, 0))
            pct = (cnt / total_trials * 100.0) if total_trials > 0 else 0.0
            lines.append(f"| {cat_labels_zh[ck]} | {cnt} | {pct:.1f}% |\n")
        lines.append("\n")
    elif lang == "en":
        lines.append("## 2. Root Cause Category Distribution\n\n")
        lines.append("| Category | Count | Percentage |\n|---|:---:|:---:|\n")
        cat_labels_en = {
            "agent": "Agent Decisions & Execution (`agent`)",
            "verifier": "Evaluation & Verifier Logic (`verifier`)",
            "case": "Case Specification & Design (`case`)",
            "infra": "Environment & Infrastructure (`infra`)",
            "unknown": "Unknown / Unclassified (`unknown`)",
        }
        for ck in cat_keys:
            cnt = int(category_counts.get(ck, 0))
            pct = (cnt / total_trials * 100.0) if total_trials > 0 else 0.0
            lines.append(f"| {cat_labels_en[ck]} | {cnt} | {pct:.1f}% |\n")
        lines.append("\n")
    else:  # bilingual
        lines.append("## 2. 根因分类分布统计 (Root Cause Distribution)\n\n")
        lines.append(
            "| 根因大类 (Category) | 数量 (Count) | 占比 (Percentage) |\n|---|:---:|:---:|\n"
        )
        cat_labels_bi = {
            "agent": "智能体决策与执行失误 (Agent Decisions, `agent`)",
            "verifier": "评测验证与规则判定缺陷 (Evaluation & Verifier, `verifier`)",
            "case": "基准题目与规格设计缺陷 (Case Design, `case`)",
            "infra": "运行环境与系统设施缺陷 (Environment & Infra, `infra`)",
            "unknown": "未分类或信息缺失 (Unknown/Unclassified, `unknown`)",
        }
        for ck in cat_keys:
            cnt = int(category_counts.get(ck, 0))
            pct = (cnt / total_trials * 100.0) if total_trials > 0 else 0.0
            lines.append(f"| {cat_labels_bi[ck]} | {cnt} | {pct:.1f}% |\n")
        lines.append("\n")

    # 4. Trial Diagnostics Table
    if lang == "zh":
        lines.append("## 3. 各 Trial 详细诊断清单\n\n")
        lines.append(
            "| Trial 名称 | 最终裁定 (Verdict) | 根因分类 (Category) | 根因编码 (Code) | 置信度 (Confidence) | 证据降级 (Evidence) | 诊断报告 (Report) |\n"
            "|---|:---:|:---:|:---:|:---:|:---:|:---:|\n"
        )
    elif lang == "en":
        lines.append("## 3. Trial Diagnostics List\n\n")
        lines.append(
            "| Trial Name | Verdict | Category | Code | Confidence | Degraded Evidence | Report |\n"
            "|---|:---:|:---:|:---:|:---:|:---:|:---:|\n"
        )
    else:  # bilingual
        lines.append("## 3. 各 Trial 详细诊断清单 (Trial Diagnostics List)\n\n")
        lines.append(
            "| Trial 名称 (Trial Name) | 最终裁定 (Verdict) | 根因大类 (Category) | 根因编码 (Code) | 置信度 (Confidence) | 证据降级 (Degraded) | 诊断报告 (Report) |\n"
            "|---|:---:|:---:|:---:|:---:|:---:|:---:|\n"
        )

    if not trials:
        if lang == "zh":
            lines.append("| - | - | - | - | - | - | *暂无记录* |\n")
        elif lang == "en":
            lines.append("| - | - | - | - | - | - | *No trials recorded* |\n")
        else:
            lines.append("| - | - | - | - | - | - | *暂无记录 / No records* |\n")
    else:
        for t in trials:
            t_name = str(t.get("trial_name") or "unknown")
            is_collect = t.get("phase") == "collect"
            v_raw = t.get("verdict")
            v_str = f"`{escape_md_cell(v_raw)}`" if v_raw else "-"

            if is_collect:
                if lang == "zh":
                    cat_cell = "收集阶段"
                    rep_link = f"[evidence.json]({t_name}/evidence.json)"
                elif lang == "en":
                    cat_cell = "Collect Phase"
                    rep_link = f"[evidence.json]({t_name}/evidence.json)"
                else:
                    cat_cell = "收集阶段 (Collect)"
                    rep_link = f"[evidence.json]({t_name}/evidence.json)"
                code_cell = "-"
                conf_cell = "-"
            else:
                cat_raw = t.get("category")
                cat_cell = f"`{escape_md_cell(cat_raw)}`" if cat_raw else "-"

                code_raw = t.get("code")
                code_cell = f"`{escape_md_cell(code_raw)}`" if code_raw else "-"

                conf_raw = t.get("confidence")
                conf_cell = f"{float(conf_raw):.2f}" if conf_raw is not None else "-"

                if lang == "zh":
                    rep_link = f"[report.zh.md]({t_name}/report.zh.md)"
                elif lang == "en":
                    rep_link = f"[report.en.md]({t_name}/report.en.md)"
                else:
                    rep_link = f"[report.md]({t_name}/report.md)"

            row_diag = t.get("diagnostic_counts") or {}
            diag_total = int(row_diag.get("warning", 0) or 0) + int(row_diag.get("error", 0) or 0)
            diag_cell = f"⚠️ {diag_total}" if diag_total else "-"
            lines.append(
                f"| `{escape_md_cell(t_name)}` | {v_str} | {cat_cell} | {code_cell} | {conf_cell} | {diag_cell} | {rep_link} |\n"
            )

    return "".join(lines)
