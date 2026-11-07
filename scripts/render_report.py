#!/usr/bin/env python3
"""
Report, Job Summary & Skill Prescription Renderer (`scripts/render_report.py`)

Renders:
  - `report.md` (`lang="bilingual"`): Compact Chinese-English bilingual report (4 core sections)
  - `report.zh.md` (`lang="zh"`): Standalone pure Chinese report (4 core sections)
  - `report.en.md` (`lang="en"`): Standalone pure English report (4 core sections)
  - `job_summary.md`, `job_summary.zh.md`, `job_summary.en.md` (multi-trial batch dashboard)
  - `skill-prescription.md`, `skill-prescription.zh.md`, `skill-prescription.en.md` (when eligible)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from render_batch import render_job_summary_markdown
from report_style import (
    category_label,
    cause_narrative,
    clean_code_line,
    clean_prose,
    confidence_kind_label,
    evidence_strength_label,
    localize_engine_text,
    remediation,
    section_title,
    severity_label,
    stage_label,
    subtype_label,
)

__all__ = [
    "CORE_ARTIFACT_BASENAMES",
    "SECONDARY_ARTIFACT_BASENAMES",
    "filter_core_missing_artifacts",
    "format_evidence_list_text",
    "render_job_summary_markdown",
    "render_report_markdown",
    "render_skill_prescription_markdown",
]


def escape_md_cell(val: Any) -> str:
    if val is None:
        return "-"
    return str(val).replace("\r", " ").replace("\n", " ").replace("|", "\\|").strip()


def select_timeline_events(
    timeline: List[Dict[str, Any]],
    fud_ref: Optional[str],
    limit: int = 25,
) -> List[Dict[str, Any]]:
    """
    Selects up to `limit` timeline events while preserving chronological order:
      - Head events (initial instruction & setup)
      - Context window around `first_unrecovered_deviation` (`fud_ref`) and `shell_error` events
      - Tail events (final steps & verification outcome)
    """
    n = len(timeline)
    if n <= limit:
        return timeline

    keep_indices: Set[int] = set()
    head_count = min(6, max(2, limit // 4))
    tail_count = min(8, max(3, limit // 3))

    for i in range(head_count):
        keep_indices.add(i)
    for i in range(max(0, n - tail_count), n):
        keep_indices.add(i)

    if fud_ref:
        for idx, ev in enumerate(timeline):
            if ev.get("event_id") == fud_ref:
                for w in range(max(0, idx - 2), min(n, idx + 3)):
                    keep_indices.add(w)
                break

    for idx, ev in enumerate(timeline):
        if len(keep_indices) >= limit:
            break
        if ev.get("event_type") == "shell_error" or (ev.get("exit_code") not in (None, 0)):
            keep_indices.add(idx)

    idx_finger = head_count
    while len(keep_indices) < limit and idx_finger < n:
        keep_indices.add(idx_finger)
        idx_finger += 1

    sorted_indices = sorted(keep_indices)[:limit]
    if fud_ref:
        fud_idx = next((i for i, ev in enumerate(timeline) if ev.get("event_id") == fud_ref), None)
        if fud_idx is not None and fud_idx not in sorted_indices:
            sorted_indices[-1] = fud_idx
            sorted_indices.sort()
    return [timeline[i] for i in sorted_indices]


CORE_ARTIFACT_BASENAMES = {
    "trajectory.json",
    "claude-code.txt",
    "verify.log",
    "test-stdout.txt",
    "results.json",
    "result.json",
    "instruction.md",
    "solve.sh",
    "verify.py",
    "refs.json",
}

SECONDARY_ARTIFACT_BASENAMES = {
    "config.json",
    "lock.json",
    "trial.log",
    "job.log",
    "manifest.json",
    ".cfa_manifest.json",
    "exception.txt",
    "runner.log",
    "setup.log",
    "docker.log",
}


def filter_core_missing_artifacts(missing: List[str]) -> Tuple[List[str], int]:
    """
    Filters missing_artifacts to core inputs and calculation outputs only,
    converging internal runtime and temporary files into a summary count.
    Matches against explicit filename basenames, preventing broad substring mismatch.
    """
    core: List[str] = []
    secondary_count = 0
    for item in missing:
        raw = item.split(":", 1)[1] if ":" in item else item
        bname = Path(raw.strip()).name.lower()
        if bname in CORE_ARTIFACT_BASENAMES:
            core.append(item)
        else:
            secondary_count += 1
    return core, secondary_count


def _prune_hypotheses_for_report(
    comp_hyps: List[Dict[str, Any]],
    ex_hyps: List[Dict[str, Any]],
    runtime: Dict[str, Any],
    primary_category: str,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Prunes low-value or contradictory hypotheses for report display:
      1. If agent never started (runtime.agent_started is False), prunes candidate
         hypotheses claiming 'agent'.
      2. If candidates with positive confidence or non-empty evidence exist,
         filters out 0.0-confidence candidates that have zero evidence.
      3. If all candidates have 0.0 confidence and empty evidence, keeps only the
         top candidate aligned with primary_category (or top 1), pruning irrelevant ones.
    """
    agent_started = bool(runtime.get("agent_started", False))
    pruned_comp: List[Dict[str, Any]] = []

    for ch in comp_hyps:
        cat = ch.get("category", "")
        # Rule 1: Agent never started -> cannot blame agent as candidate hypothesis
        if not agent_started and cat == "agent":
            continue
        pruned_comp.append(ch)

    # Rule 2: If meaningful candidates exist, prune zero-evidence zero-confidence speculative ones
    meaningful = [
        ch
        for ch in pruned_comp
        if float(ch.get("confidence", 0.0)) > 0.0 or bool(ch.get("evidence_for"))
    ]
    if meaningful:
        pruned_comp = meaningful
    elif len(pruned_comp) > 1:
        # All have 0 score and empty evidence: keep only the top matching primary category
        matching = [ch for ch in pruned_comp if ch.get("category") == primary_category]
        if matching:
            pruned_comp = matching[:1]
        else:
            pruned_comp = pruned_comp[:1]

    # Rule 3: A resolved attribution does not need a residual "insufficient evidence"
    # placeholder, and candidates far below the selected cause only add noise.
    if primary_category not in ("", "unknown", "none"):
        pruned_comp = [
            ch
            for ch in pruned_comp
            if not (
                ch.get("category") == "unknown"
                and ch.get("subtype") == "insufficient_evidence"
                and not ch.get("evidence_for")
            )
        ]
        pruned_comp = [
            ch
            for ch in pruned_comp
            if ch.get("category") == primary_category or float(ch.get("confidence", 0.0)) >= 0.30
        ]
    pruned_comp = sorted(
        pruned_comp, key=lambda ch: float(ch.get("confidence", 0.0)), reverse=True
    )[:3]

    # Excluded hypotheses
    pruned_ex = list(ex_hyps)

    return pruned_comp, pruned_ex


def format_evidence_list_text(
    ev_for: Optional[List[Any]],
    ev_against: Optional[List[Any]],
    lang: str = "bilingual",
) -> str:
    """
    Formats hypothesis evidence_for and evidence_against into natural human-readable text,
    eliminating Python list repr syntax leaks (e.g. ['art:job_result']).
    """
    ev_for = ev_for or []
    ev_against = ev_against or []
    parts: List[str] = []

    def _refs(items: List[Any], limit: int = 3) -> str:
        shown = ", ".join(f"`{x}`" for x in items[:limit])
        if len(items) > limit:
            shown += f" 等 {len(items)} 项" if lang != "en" else f" (+{len(items) - limit})"
        return shown

    if lang == "zh":
        if ev_for:
            parts.append("支持证据：" + _refs(ev_for))
        else:
            parts.append("支持证据：无")
        if ev_against:
            parts.append("反驳证据：" + _refs(ev_against))
        return f"（{'；'.join(parts)}）" if parts else ""
    elif lang == "en":
        if ev_for:
            parts.append("for: " + _refs(ev_for))
        else:
            parts.append("for: none")
        if ev_against:
            parts.append("against: " + _refs(ev_against))
        return f" [{', '.join(parts)}]" if parts else ""
    else:  # bilingual
        if ev_for:
            parts.append("支持 / For: " + _refs(ev_for))
        else:
            parts.append("支持 / For: 无")
        if ev_against:
            parts.append("反驳 / Against: " + _refs(ev_against))
        return f" ({', '.join(parts)})" if parts else ""


def _display_actions(analysis: Dict[str, Any], lang: str) -> List[tuple[str, str]]:
    """(owner, action) pairs; a restated cause is replaced by a concrete fix."""
    prc = analysis.get("primary_root_cause") or {}
    summary = " ".join(str(prc.get("summary") or "").split())
    out: List[tuple[str, str]] = []
    for r in analysis.get("recommended_actions") or []:
        raw = " ".join(str(r.get("action") or "").split())
        restates = bool(summary) and bool(raw) and raw[:80] == summary[:80]
        rem = remediation(prc.get("category"), prc.get("subtype"), lang) if restates else ""
        text = rem or (raw if lang == "en" else localize_engine_text(raw, lang))
        out.append((str(r.get("owner") or "General"), text))
    return out


def _manifestation_summary(evidence: Dict[str, Any], direct_cause: Any, lang: str = "zh") -> str:
    """
    Human-facing "what failed" line.

    A verifier report with named checks is far more useful than the raw
    concatenation of terse check messages, so prefer the check list when
    available and fall back to the cleaned analysis summary otherwise.
    """
    report = evidence.get("verifier_report")
    if isinstance(report, dict):
        failed = [c for c in (report.get("failed_checks") or []) if c.get("name")]
        names = [str(c["name"]) for c in failed]
        if names:
            shown = "、".join(names[:4]) if lang != "en" else ", ".join(names[:4])
            more = len(names) - 4
            if lang == "en":
                tail = f" (+{more} more)" if more > 0 else ""
                return f"The verifier failed {len(names)} named check(s): {shown}{tail}"
            tail = f" 等 {len(names)} 项" if more > 0 else f" {len(names)} 项"
            return f"验证器具名检查未通过{tail}：{shown}"
    return clean_prose(direct_cause, 300)


def _attribution_sentence(prc: Dict[str, Any], fallback: Any, lang: str = "zh") -> str:
    """Localized one-line cause statement built from the structured finding."""
    cat = prc.get("category", "unknown")
    subtype = prc.get("subtype", "unknown")
    narrative = cause_narrative(cat, subtype, lang)
    if narrative:
        if lang == "en":
            return f"{category_label(cat, 'en')} — {subtype_label(subtype, 'en')}: {narrative}."
        return f"{category_label(cat, 'zh')} · {subtype_label(subtype, 'zh')}：{narrative}。"
    return clean_prose(localize_engine_text(fallback, lang), 300)


def _excluded_reason(eh: Dict[str, Any], runtime: Dict[str, Any], lang: str = "zh") -> str:
    """Localized reason for an excluded hypothesis, grounded in runtime facts."""
    cat = str(eh.get("category") or "")
    agent_started = bool(runtime.get("agent_started", False))
    verifier_started = bool(runtime.get("verifier_started", False))
    if cat == "infra" and agent_started:
        if lang == "en":
            return (
                "the container built and the agent ran"
                + (", and the verifier ran to completion" if verifier_started else "")
                + " (`runtime.agent_started == true`)"
            )
        return (
            "容器构建与 Agent 执行均正常"
            + ("，验证器也已完成判定" if verifier_started else "")
            + "（`runtime.agent_started == true`）"
        )
    return clean_prose(localize_engine_text(eh.get("reason"), lang), 220)


def _render_gate_warning(
    runtime: Dict[str, Any],
    verdict: str = "failed",
    lang: str = "bilingual",
) -> Optional[str]:
    """
    Hides normal gate status (agent_started=True, verifier_started=True),
    only emitting a prominent blockquote Warning when pre-startup failed or container aborted.
    """
    if verdict == "passed":
        return None

    agent_started = bool(runtime.get("agent_started", False))
    verifier_started = bool(runtime.get("verifier_started", False))
    exec_status = str(runtime.get("execution_status", runtime.get("exit_status", ""))).lower()

    is_abnormal_container = exec_status in ("container_oom", "container_crash", "setup_failed")
    if agent_started and verifier_started and not is_abnormal_container:
        return None

    if not agent_started:
        if lang == "zh":
            return (
                f"> ⚠️ **运行门控警示**: 智能体未启动 (`agent_started=False`, `verifier_started={verifier_started}`)"
                f"，触发启动前环境或系统设施异常拦截。\n"
            )
        elif lang == "en":
            return (
                f"> ⚠️ **Runtime Gate Warning**: Agent did not start (`agent_started=False`, `verifier_started={verifier_started}`)"
                f"; pre-startup infrastructure or setup exception triggered.\n"
            )
        else:
            return (
                f"> ⚠️ **运行门控警示 (Runtime Gate Warning)**: 智能体未启动 (`agent_started=False`, `verifier_started={verifier_started}`)"
                f"，触发启动前环境/基础设施异常拦截 (Pre-startup interception).\n"
            )
    elif not verifier_started:
        if lang == "zh":
            return (
                "> ⚠️ **运行门控警示**: 评测验证器未启动 (`agent_started=True`, `verifier_started=False`)"
                "，执行在进入评测验证前中断。\n"
            )
        elif lang == "en":
            return (
                "> ⚠️ **Runtime Gate Warning**: Verifier did not start (`agent_started=True`, `verifier_started=False`)"
                "; execution aborted prior to evaluation phase.\n"
            )
        else:
            return (
                "> ⚠️ **运行门控警示 (Runtime Gate Warning)**: 评测验证器未启动 (`agent_started=True`, `verifier_started=False`)"
                "，执行在进入评测验证前中断 (Aborted before verifier execution).\n"
            )
    elif is_abnormal_container:
        if lang == "zh":
            return f"> ⚠️ **运行门控警示**: 容器运行时发生异常中断 (`execution_status={exec_status}`)。\n"
        elif lang == "en":
            return f"> ⚠️ **Runtime Gate Warning**: Abnormal container runtime termination (`execution_status={exec_status}`).\n"
        else:
            return f"> ⚠️ **运行门控警示 (Runtime Gate Warning)**: 容器运行时异常中断 (`execution_status={exec_status}`).\n"
    return None


_DEGRADED_SEVERITIES = ("warning", "error")


def _degraded_diagnostics(evidence: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Warning/error diagnostics — recoveries that weakened the collected evidence."""
    return [
        record
        for record in evidence.get("diagnostics") or []
        if isinstance(record, dict) and record.get("severity") in _DEGRADED_SEVERITIES
    ]


def _render_evidence_integrity_warning(
    evidence: Dict[str, Any], lang: str = "bilingual"
) -> Optional[str]:
    """Prominent callout when attribution ran on degraded evidence."""
    records = _degraded_diagnostics(evidence)
    if not records:
        return None
    warnings = sum(1 for r in records if r.get("severity") == "warning")
    errors = sum(1 for r in records if r.get("severity") == "error")
    if lang == "zh":
        return (
            f"> ⚠️ **证据完整性警示**: 本 Trial 有 {len(records)} 项证据采集降级"
            f"（warning: `{warnings}`, error: `{errors}`），结论可能建立在弱化证据之上；"
            "详见第 2 节证据降级诊断。\n"
        )
    if lang == "en":
        return (
            f"> ⚠️ **Evidence Integrity Warning**: {len(records)} evidence-collection "
            f"degradation(s) on this trial (warning: `{warnings}`, error: `{errors}`); "
            "conclusions may rest on weakened evidence — see the degradation "
            "diagnostics in Section 2.\n"
        )
    return (
        f"> ⚠️ **证据完整性警示 (Evidence Integrity Warning)**: 本 Trial 有 {len(records)} 项"
        f"证据采集降级 (warning: `{warnings}`, error: `{errors}`)，结论可能建立在弱化证据之上；"
        "详见第 2 节 / See the degradation diagnostics in Section 2.\n"
    )


def _render_verifier_report_block(evidence: Dict[str, Any], lang: str = "bilingual") -> List[str]:
    """Named failed checks + deviation values from the normalized verifier report.

    Harbor-style verifiers ship layered JSON reports instead of a `verify.log`.
    Rendering their check names here is what makes a failure actionable: without
    it the report can only say "agent produced inaccurate values".
    """
    report = evidence.get("verifier_report")
    if not isinstance(report, dict):
        return []
    failed = report.get("failed_checks") or []
    low_diag = report.get("diagnosability") == "low"
    if not failed and not low_diag:
        return []

    labels = {
        "zh": (
            "### 验证器报告",
            "失败检查",
            "来源",
            "可诊断性偏低",
            "验证器结论",
            "失败来源",
            "核心失败检查",
        ),
        "en": (
            "### Verifier Report",
            "Failed checks",
            "Source",
            "Low diagnosability",
            "Verifier verdict",
            "Failure origin",
            "Core failures",
        ),
    }.get(
        lang,
        (
            "### 验证器报告 (Verifier Report)",
            "失败检查 (Failed checks)",
            "来源 (Source)",
            "可诊断性偏低 (Low diagnosability)",
            "验证器结论 (Verifier verdict)",
            "失败来源 (Failure origin)",
            "核心失败检查 (Core failures)",
        ),
    )
    title, failed_label, source_label, diag_label, verdict_label = labels[:5]
    origin_label, core_label = labels[5], labels[6]

    meta = (
        f"报告族 `{report.get('schema_family') or 'unknown'}` · "
        f"{verdict_label} `{report.get('verdict') or 'unknown'}` · "
        f"reward `{report.get('reward')}` · "
        f"主报告 `{report.get('primary_report') or '—'}`"
    )
    lines = [title + "\n", f"- {meta}\n"]
    origin = report.get("failure_origin")
    if isinstance(origin, dict) and origin:
        reason = origin.get("reason_code") or origin.get("role") or ""
        if reason:
            lines.append(f"- **{origin_label}**: `{reason}`\n")
    if report.get("core_failures"):
        lines.append(
            f"- **{core_label}**: "
            + ", ".join(f"`{c}`" for c in report["core_failures"][:4])
            + "\n"
        )
    if failed:
        lines.append(f"- **{failed_label}** ({len(failed)}):\n")
        for check in failed[:5]:
            name = str(check.get("name") or "unnamed_check")
            layer = str(check.get("layer") or "unspecified")
            core = " [core]" if check.get("core") else ""
            detail = clean_prose(check.get("detail"), 260)
            source = check.get("source")
            line = f"  - `[{layer}] {name}`{core}"
            if detail:
                line += f" — {detail}"
            if source:
                line += f" ({source_label}: `{source}`)"
            lines.append(line + "\n")
        if len(failed) > 5:
            lines.append(f"  - … +{len(failed) - 5}\n")
    if low_diag:
        sep = "; " if lang == "en" else "；"
        reasons = sep.join(
            localize_engine_text(r, lang) for r in (report.get("diagnosability_reasons") or [])
        )
        lines.append(f"- ⚠️ **{diag_label}**: {reasons}\n")
    # `render_report_markdown` joins with "\n"; keep the section header's own
    # blank-line padding but avoid doubling every body line.
    return [line if line.startswith("\n") else line.rstrip("\n") for line in lines]


def _render_diagnostics_block(evidence: Dict[str, Any], lang: str = "bilingual") -> List[str]:
    """Detailed warning/error diagnostics for the evidence-chain section."""
    records = _degraded_diagnostics(evidence)
    if not records:
        return []
    if lang == "zh":
        lines = ["\n### 证据降级诊断\n"]
    elif lang == "en":
        lines = ["\n### Evidence Degradation Diagnostics\n"]
    else:
        lines = ["\n### 证据降级诊断 (Evidence Degradation Diagnostics)\n"]
    for record in records:
        code = record.get("code") or "UNKNOWN_DIAGNOSTIC"
        stage = record.get("stage") or "unknown"
        severity = record.get("severity")
        message = clean_prose(record.get("message"), 300)
        impact = clean_prose(record.get("impact"), 200)
        source_ref = record.get("source_ref")
        if lang == "zh":
            line = (
                f"- **`{code}`** · {stage_label(stage, 'zh')} · "
                f"严重度 {severity_label(severity, 'zh')}：{message}"
            )
            if impact:
                line += f"（影响：{impact}）"
            if source_ref:
                line += f" · 来源 `{source_ref}`"
        elif lang == "en":
            line = (
                f"- **`{code}`** · {stage_label(stage, 'en')} · "
                f"severity {severity_label(severity, 'en')}: {message}"
            )
            if impact:
                line += f" (impact: {impact})"
            if source_ref:
                line += f" · source `{source_ref}`"
        else:
            line = (
                f"- **`{code}`** · {stage_label(stage, 'zh')} · "
                f"严重度 {severity_label(severity, 'zh')}: {message}"
            )
            if impact:
                line += f" — 影响 / Impact: {impact}"
            if source_ref:
                line += f" · 来源 / Source: `{source_ref}`"
        lines.append(line)
    return lines


def _render_evidence_file_pointers(
    evidence: Dict[str, Any],
    lang: str = "bilingual",
    output_format: str = "both",
) -> List[str]:
    artifacts = evidence.get("artifacts") or []
    art_map = {a.get("rel_path"): a for a in artifacts if a.get("rel_path")}

    trial_name = evidence.get("trial_name") or (
        Path(evidence.get("trial_dir")).name if evidence.get("trial_dir") else "trial"
    )
    case_id = evidence.get("case_id") or "case"
    task_dir = evidence.get("task_dir")
    task_name = Path(task_dir).name if task_dir and task_dir != "N/A" else "N/A"

    lines: List[str] = []
    if lang == "zh":
        lines.append("\n### 证据文件索引")
        lines.append(f"- **任务与试次**: 任务 `{case_id}`, 试次 `{trial_name}`, 题目 `{task_name}`")
    elif lang == "en":
        lines.append("\n### Evidence Files")
        lines.append(
            f"- **Case & Trial**: case `{case_id}`, trial `{trial_name}`, task `{task_name}`"
        )
    else:  # bilingual
        lines.append("\n### 证据文件索引 (Evidence Files)")
        lines.append(
            f"- **任务与试次 (Case & Trial)**: 任务 `{case_id}`, 试次 `{trial_name}`, 题目 `{task_name}`"
        )

    # 1. Trajectory
    traj = art_map.get("agent/trajectory.json")
    cc = art_map.get("agent/claude-code.txt")
    # 2. Verifier
    vlog = art_map.get("verifier/verify.log")
    vout = art_map.get("verifier/test-stdout.txt")
    # 3. Task
    inst = art_map.get("instruction.md")
    refs = art_map.get("tests/refs.json") or art_map.get("refs.json")
    # 4. Results
    res_json = art_map.get("results.json")

    has_traj = bool((traj and traj.get("exists")) or (cc and cc.get("exists")))
    has_vlog = bool((vlog and vlog.get("exists")) or (vout and vout.get("exists")))
    has_task = bool((inst and inst.get("exists")) or (refs and refs.get("exists")))
    has_results = bool(res_json and res_json.get("exists"))

    # When all key logs/artifacts are missing, summarize in a single line
    if not has_traj and not has_vlog and not has_task and not has_results:
        if lang == "zh":
            lines.append(
                "- **核心日志与产物**: 全部缺失（未检测到交互轨迹、评测日志、任务说明或计算产物）。"
            )
        elif lang == "en":
            lines.append(
                "- **Core Logs & Artifacts**: All missing (no trajectory, verifier log, task specification, or simulation results found)."
            )
        else:
            lines.append(
                "- **核心日志与产物 (Core Logs & Artifacts)**: 全部缺失 / All missing (未检测到交互轨迹、评测日志、任务说明或计算产物)."
            )
    else:
        # Trajectory
        traj_items = []
        if traj and traj.get("exists"):
            desc = (
                "交互轨迹及工具调用"
                if lang == "zh"
                else (
                    "turns & tool calls" if lang == "en" else "交互轨迹及工具调用 / Turns & Tools"
                )
            )
            traj_items.append(f"[trial] `agent/trajectory.json` ({desc})")
        elif traj:
            m_tag = (
                "【缺失】"
                if lang == "zh"
                else ("[Missing]" if lang == "en" else "【缺失 / Missing】")
            )
            traj_items.append(f"[trial] `agent/trajectory.json` {m_tag}")

        if cc and cc.get("exists"):
            desc = (
                "执行日志"
                if lang == "zh"
                else ("execution log" if lang == "en" else "执行日志 / Execution Log")
            )
            traj_items.append(f"[trial] `agent/claude-code.txt` ({desc})")
        elif cc and not traj:
            m_tag = (
                "【缺失】"
                if lang == "zh"
                else ("[Missing]" if lang == "en" else "【缺失 / Missing】")
            )
            traj_items.append(f"[trial] `agent/claude-code.txt` {m_tag}")

        label = (
            "交互轨迹与代码记录"
            if lang == "zh"
            else (
                "Agent Trajectory & Commands"
                if lang == "en"
                else "交互轨迹与代码记录 (Agent Trajectory)"
            )
        )
        if traj_items:
            lines.append(f"- **{label}**: " + ", ".join(traj_items))
        else:
            m_tag = (
                "【缺失】"
                if lang == "zh"
                else ("[Missing]" if lang == "en" else "【缺失 / Missing】")
            )
            lines.append(f"- **{label}**: [trial] `agent/trajectory.json` {m_tag}")

        # Verifier
        v_items = []
        if vlog and vlog.get("exists"):
            desc = (
                "评测判定日志"
                if lang == "zh"
                else ("judgment log" if lang == "en" else "评测判定日志 / Judgment Log")
            )
            v_items.append(f"[trial] `verifier/verify.log` ({desc})")
        elif vlog:
            m_tag = (
                "【缺失】"
                if lang == "zh"
                else ("[Missing]" if lang == "en" else "【缺失 / Missing】")
            )
            v_items.append(f"[trial] `verifier/verify.log` {m_tag}")

        if vout and vout.get("exists"):
            desc = (
                "测试输出"
                if lang == "zh"
                else ("test stdout" if lang == "en" else "测试输出 / Test Stdout")
            )
            v_items.append(f"[trial] `verifier/test-stdout.txt` ({desc})")
        elif vout and not vlog:
            m_tag = (
                "【缺失】"
                if lang == "zh"
                else ("[Missing]" if lang == "en" else "【缺失 / Missing】")
            )
            v_items.append(f"[trial] `verifier/test-stdout.txt` {m_tag}")

        label = (
            "验证器执行与判定日志"
            if lang == "zh"
            else (
                "Verifier Execution & Judgment"
                if lang == "en"
                else "验证器执行与判定日志 (Verifier Logs)"
            )
        )
        if v_items:
            lines.append(f"- **{label}**: " + ", ".join(v_items))
        else:
            m_tag = (
                "【缺失】"
                if lang == "zh"
                else ("[Missing]" if lang == "en" else "【缺失 / Missing】")
            )
            lines.append(f"- **{label}**: [trial] `verifier/verify.log` {m_tag}")

        # Task
        t_items = []
        if inst and inst.get("exists"):
            desc = (
                "题目要求"
                if lang == "zh"
                else ("task prompt" if lang == "en" else "题目要求 / Task Specification")
            )
            t_items.append(f"[task] `instruction.md` ({desc})")
        elif inst:
            m_tag = (
                "【缺失】"
                if lang == "zh"
                else ("[Missing]" if lang == "en" else "【缺失 / Missing】")
            )
            t_items.append(f"[task] `instruction.md` {m_tag}")

        if refs and refs.get("exists"):
            desc = (
                "参考真值"
                if lang == "zh"
                else ("ground truth" if lang == "en" else "参考真值 / Ground Truth")
            )
            t_items.append(f"[task] `tests/refs.json` ({desc})")
        elif refs:
            m_tag = (
                "【缺失】"
                if lang == "zh"
                else ("[Missing]" if lang == "en" else "【缺失 / Missing】")
            )
            t_items.append(f"[task] `tests/refs.json` {m_tag}")

        label = (
            "任务定义与参考真值"
            if lang == "zh"
            else (
                "Task Specification & Reference Values"
                if lang == "en"
                else "任务定义与参考真值 (Task Specification)"
            )
        )
        if t_items:
            lines.append(f"- **{label}**: " + ", ".join(t_items))
        else:
            m_tag = (
                "【缺失】"
                if lang == "zh"
                else ("[Missing]" if lang == "en" else "【缺失 / Missing】")
            )
            lines.append(f"- **{label}**: [task] `instruction.md` {m_tag}")

        # Results
        if res_json:
            label = (
                "计算产物提交"
                if lang == "zh"
                else (
                    "Simulation Output Submission"
                    if lang == "en"
                    else "计算产物提交 (Result Artifacts)"
                )
            )
            if res_json.get("exists"):
                desc = (
                    "Agent 最终生成数据"
                    if lang == "zh"
                    else (
                        "simulation output" if lang == "en" else "Agent 最终生成数据 / Output Data"
                    )
                )
                lines.append(f"- **{label}**: [trial] `results.json` ({desc})")
            else:
                m_tag = (
                    "【缺失】(未生成有效产物)"
                    if lang == "zh"
                    else (
                        "[Missing] (no output generated)"
                        if lang == "en"
                        else "【缺失 / Missing】(未生成有效产物)"
                    )
                )
                lines.append(f"- **{label}**: [trial] `results.json` {m_tag}")

    # Output format
    if output_format in ("json", "both"):
        if lang == "zh":
            lines.append(
                "- **完整证据与归因数据**: `data/evidence.json`（时间线、契约观测）与 `data/analysis.json`（归因推导）\n"
            )
        elif lang == "en":
            lines.append(
                "- **Full evidence & analysis**: `data/evidence.json` (timeline, contract observations) and `data/analysis.json` (attribution)\n"
            )
        else:
            lines.append(
                "- **完整证据与归因数据 (Full evidence & analysis)**: `data/evidence.json` 与 `data/analysis.json` (Timeline & contracts / Attribution)\n"
            )
    else:
        if lang == "zh":
            lines.append(
                "- **结构化归因产物**: 本次按 `--format markdown` 渲染，未生成独立的 `evidence.json` / `analysis.json`。\n"
            )
        elif lang == "en":
            lines.append(
                "- **Structured Analysis Artifacts**: Rendered with `--format markdown`; `evidence.json` / `analysis.json` were not written.\n"
            )
        else:
            lines.append(
                "- **结构化归因产物 (Structured Artifacts)**: 本次按 `--format markdown` 渲染，未生成独立的 `evidence.json` / `analysis.json`。\n"
            )

    return lines


def _render_chinese_bilingual_core(
    evidence: Dict[str, Any],
    analysis: Dict[str, Any],
    include_en_labels: bool = True,
    output_format: str = "both",
) -> str:
    case_id = analysis.get("case_id", evidence.get("case_id", "unknown"))
    trial_name = analysis.get("trial_name", evidence.get("trial_name", case_id))
    verdict = analysis.get("verdict", "failed")
    f_stage = analysis.get("failure_stage", "unknown")
    d_stage = analysis.get("detection_stage", "unknown")
    prc = analysis.get("primary_root_cause") or {}
    manifestation = analysis.get("failure_manifestation") or {}
    fud = analysis.get("first_unrecovered_deviation") or {}
    runtime = evidence.get("runtime") or {}
    conf_val = float(prc.get("confidence", 0.0))
    conf_kind = prc.get("confidence_kind", "heuristic_evidence_score")
    ev_strength = prc.get("evidence_strength", "medium")
    exec_status = runtime.get("execution_status", runtime.get("exit_status"))

    cat = prc.get("category", "unknown")
    subtype = prc.get("subtype", "unknown")
    code = prc.get("code", "UNKNOWN")
    cat_zh = category_label(cat, "zh")
    subtype_zh = subtype_label(subtype, "zh")

    lines: List[str] = []
    if include_en_labels:
        lines.append(
            f"# 案例失败归因报告 / Case Failure Attribution: `{case_id}` (`{trial_name}`)\n"
        )
        lines.append("> **语言 / Language**: 中英双语 (Bilingual)\n")
    else:
        lines.append(f"# 案例失败归因报告: `{case_id}` (`{trial_name}`)\n")

    lang = "bilingual" if include_en_labels else "zh"

    # 1. 归因结论
    lines.append(f"## {section_title(1, lang)}\n")

    if isinstance(manifestation, dict):
        direct_cause = manifestation.get("summary") or "N/A"
    elif isinstance(manifestation, str):
        direct_cause = manifestation
    else:
        direct_cause = "N/A"

    prc_summary = prc.get("summary") or prc.get("reasoning") if isinstance(prc, dict) else None
    internal_cause = prc_summary or direct_cause or "N/A"

    exec_status = runtime.get("execution_status", runtime.get("exit_status"))
    verification = runtime.get("verification_status", "unknown")
    reward = runtime.get("reward")
    conf_kind_label = confidence_kind_label(conf_kind, "en" if include_en_labels else "zh")
    strength_label = evidence_strength_label(ev_strength, "en" if include_en_labels else "zh")
    if include_en_labels:
        lines.append(
            "| 项目 (Field) | 结论 (Finding) |\n"
            "|---|---|\n"
            f"| **评测结论 (Verdict)** | `{verdict}` · 执行 `{exec_status}` · 验证 `{verification}` · reward `{reward}` |\n"
            f"| **主因 (Primary cause)** | `{cat}` {cat_zh} → {subtype_zh} `{subtype}` · `{code}` |\n"
            f"| **阶段 (Stages)** | 发生 {stage_label(f_stage, 'zh')} → 检出 {stage_label(d_stage, 'zh')} |\n"
            f"| **置信度 (Confidence)** | **{conf_val:.2f}** · {conf_kind_label} · 证据强度 {strength_label} |\n"
        )
    else:
        lines.append(
            "| 项目 | 结论 |\n"
            "|---|---|\n"
            f"| **评测结论** | `{verdict}` · 执行 `{exec_status}` · 验证 `{verification}` · reward `{reward}` |\n"
            f"| **主因** | `{cat}` {cat_zh} → {subtype_zh} `{subtype}` · `{code}` |\n"
            f"| **阶段** | 发生 {stage_label(f_stage, 'zh')} → 检出 {stage_label(d_stage, 'zh')} |\n"
            f"| **置信度** | **{conf_val:.2f}** · {conf_kind_label} · 证据强度 {strength_label} |\n"
        )

    gate_warn = _render_gate_warning(
        runtime, verdict=verdict, lang="bilingual" if include_en_labels else "zh"
    )
    if gate_warn:
        lines.append(gate_warn)
    integrity_warn = _render_evidence_integrity_warning(
        evidence, lang="bilingual" if include_en_labels else "zh"
    )
    if integrity_warn:
        lines.append(integrity_warn)

    manifestation = _manifestation_summary(evidence, direct_cause, "zh")
    attribution = _attribution_sentence(prc, internal_cause, "zh")
    if include_en_labels:
        lines.append(
            f"> **失败表现 (Failure manifestation)**: {clean_prose(manifestation, 300)}  \n"
            f"> **归因判断 (Attribution)**: {clean_prose(attribution, 300)}\n"
        )
    else:
        lines.append(
            f"> **失败表现**: {clean_prose(manifestation, 300)}  \n"
            f"> **归因判断**: {clean_prose(attribution, 300)}\n"
        )

    # 2. 失败证据链
    lines.append(f"## {section_title(2, lang)}\n")
    lines.extend(_render_verifier_report_block(evidence, lang=lang))

    from trace_causal_anchors import trace_causal_anchors, render_causal_anchors_markdown

    stored_anchors = evidence.get("causal_anchors")
    anchors = (
        stored_anchors
        if isinstance(stored_anchors, dict) and stored_anchors
        else trace_causal_anchors(evidence)
    )
    if anchors:
        lines.extend(render_causal_anchors_markdown(anchors, lang=lang))

    fail_snippets = []
    for vobs in evidence.get("verifier_observations") or []:
        if vobs.get("type") == "verifier_fail_message" and (vobs.get("matched_text") or "").strip():
            snippet = clean_code_line(vobs.get("matched_text", ""))
            if snippet:
                fail_snippets.append(snippet)
    if fail_snippets:
        lines.append(
            "- **验证器原始结论 (Verifier message)**:"
            if include_en_labels
            else "- **验证器原始结论**:"
        )
        lines.append("```text")
        for snip in dict.fromkeys(fail_snippets[:2]):
            lines.append(snip)
        lines.append("```")

    fud_ref: Optional[str] = None
    if fud:
        fud_status = fud.get("status", "identified" if fud.get("event_ref") else "not_identified")
        fud_ref = fud.get("event_ref")
        status_txt = "已定位" if fud_status == "identified" else "未定位"
        fud_summary = localize_engine_text(fud.get("summary"), "zh")
        if not fud_summary:
            fud_summary = cause_narrative(prc.get("category"), prc.get("subtype"), "zh") or ""
        lines.append(
            "- **首个未恢复偏差 (First unrecovered deviation)**: "
            f"{status_txt} · 事件 `{fud_ref or '—'}` · {clean_prose(fud_summary, 200)}"
            if include_en_labels
            else "- **首个未恢复偏差**: "
            f"{status_txt} · 事件 `{fud_ref or '—'}` · {clean_prose(fud_summary, 200)}"
        )

    ev_refs = analysis.get("evidence_refs") or ["art:trial_result"]
    lines.append(
        "- **证据引用 (Evidence references)**: " + ", ".join(f"`{r}`" for r in ev_refs)
        if include_en_labels
        else "- **证据引用**: " + ", ".join(f"`{r}`" for r in ev_refs)
    )

    lines.extend(
        _render_diagnostics_block(evidence, lang="bilingual" if include_en_labels else "zh")
    )

    lines.extend(
        _render_evidence_file_pointers(
            evidence,
            lang="bilingual" if include_en_labels else "zh",
            output_format=output_format,
        )
    )

    # 3. 假设检验与排除
    lines.append(f"## {section_title(3, lang)}\n")
    comp_hyps_raw = analysis.get("competing_hypotheses") or []
    ex_hyps_raw = analysis.get("excluded_hypotheses") or []
    runtime = evidence.get("runtime") or {}
    prc = analysis.get("primary_root_cause") or {}
    prc_cat = prc.get("category", "")
    prc_subtype = prc.get("subtype", "")
    comp_hyps, ex_hyps = _prune_hypotheses_for_report(comp_hyps_raw, ex_hyps_raw, runtime, prc_cat)
    for ch in comp_hyps:
        hid = ch.get("hypothesis_id", "H")
        cat_h = ch.get("category", "")
        subtype_h = ch.get("subtype", "")
        score = float(ch.get("confidence", 0.0))
        claim = ch.get("claim", "")
        ev_text = format_evidence_list_text(
            ch.get("evidence_for"),
            ch.get("evidence_against"),
            lang="bilingual" if include_en_labels else "zh",
        )
        role = "主因假设" if (cat_h, subtype_h) == (prc_cat, prc_subtype) else "竞争假设"
        lines.append(
            f"- **{role} `{hid}` (`{cat_h}` · {subtype_label(subtype_h, 'zh')} / `{subtype_h}`，"
            f"置信度 `{score:.2f}`)**: {clean_prose(localize_engine_text(claim, 'zh'), 220)}{ev_text}"
        )

    for eh in ex_hyps:
        reason = _excluded_reason(eh, runtime, "zh")
        if include_en_labels:
            lines.append(
                f"- **已排除 `{eh.get('hypothesis_id', 'H')}` (`{eh.get('category')}`)**: {reason}"
            )
        else:
            lines.append(
                f"- **已排除 `{eh.get('hypothesis_id', 'H')}` (`{eh.get('category')}`)**: {reason}"
            )

    if not comp_hyps and not ex_hyps:
        lines.append(
            "- 该试次已通过验证，无竞争性失败假设 / Case passed; no competing hypotheses."
            if include_en_labels
            else "- 该试次已通过验证，无竞争性失败假设。"
        )

    cfs = analysis.get("contributing_factors") or []
    for cf in cfs:
        cf_cat = cf.get("category")
        cf_code = cf.get("code")
        cf_score = float(cf.get("confidence", 0.0))
        cf_summary = clean_prose(localize_engine_text(cf.get("summary"), "zh"), 220)
        if include_en_labels:
            lines.append(
                f"- **伴随因素 (`{cf_cat}` / `{cf_code}`，置信度 `{cf_score:.2f}`)**: {cf_summary}"
            )
        else:
            lines.append(
                f"- **伴随因素 (`{cf_cat}` / `{cf_code}`, 置信度 `{cf_score:.2f}`)**: {cf_summary}"
            )

    sigs = evidence.get("behavioral_signals") or []
    if sigs:
        if include_en_labels:
            lines.append(
                f"- **行为信号 (Behavioral signals)**: {len(sigs)} 项，详见 `data/evidence.json` / {len(sigs)} recorded"
            )
        else:
            lines.append(
                f"- **行为信号**: {len(sigs)} 项，明细见 `data/evidence.json` 的 `behavioral_signals`"
            )
    lines.append("")

    # 4. 改进建议
    lines.append(f"## {section_title(4, lang)}\n")
    recs = analysis.get("recommended_actions") or []
    if recs:
        for owner, text in _display_actions(analysis, "zh"):
            lines.append(f"- **[{owner}]**: {clean_prose(text, 240)}")
    else:
        lines.append(
            "- 无需改进（该试次已通过验证） / No action required (case passed)."
            if include_en_labels
            else "- 无需改进（该试次已通过验证）。"
        )

    sp = analysis.get("skill_prescription")
    if sp and isinstance(sp, dict):
        rec_skill = sp.get("recommended_skill") or sp
        name = rec_skill.get("name")
        gap_str = ", ".join(rec_skill.get("capability_gap") or [])
        if include_en_labels:
            lines.append(
                f"- **技能处方 (Skill prescription)**: 建议新增/更新 `{name}` (Capability gap: {gap_str})"
            )
        else:
            lines.append(f"- **技能处方**: 建议新增/更新 `{name}`（能力缺口: {gap_str}）")

    missing = evidence.get("missing_artifacts") or []
    if missing:
        core_missing, secondary_cnt = filter_core_missing_artifacts(missing)
        if core_missing:
            core_str = ", ".join(f"`{m}`" for m in core_missing)
            omitted = f"（另有 {secondary_cnt} 项次要临时文件未列出）" if secondary_cnt > 0 else ""
            lines.append(
                f"- **缺失产物 (Missing artifacts)**: {core_str} "
                f"({secondary_cnt} secondary files omitted)"
                if include_en_labels
                else f"- **缺失产物**: {core_str}{omitted}"
            )
        elif secondary_cnt > 0:
            lines.append(
                f"- **产物完整性 (Artifact integrity)**: 核心输入与计算产物完整 "
                f"({secondary_cnt} 项次要临时文件未列出 / {secondary_cnt} secondary files omitted)."
                if include_en_labels
                else f"- **产物完整性**: 核心输入与计算产物完整（另有 {secondary_cnt} 项次要临时文件未列出）。"
            )
    lines.append("")

    return "\n".join(lines)


def _render_english_edition(
    evidence: Dict[str, Any],
    analysis: Dict[str, Any],
    output_format: str = "both",
) -> str:
    case_id = analysis.get("case_id", evidence.get("case_id", "unknown"))
    trial_name = analysis.get("trial_name", evidence.get("trial_name", case_id))
    verdict = analysis.get("verdict", "failed")
    f_stage = analysis.get("failure_stage", "unknown")
    d_stage = analysis.get("detection_stage", "unknown")
    prc = analysis.get("primary_root_cause") or {}
    manifestation = analysis.get("failure_manifestation") or {}
    fud = analysis.get("first_unrecovered_deviation") or {}
    runtime = evidence.get("runtime") or {}
    conf_val = float(prc.get("confidence", 0.0))
    conf_kind = prc.get("confidence_kind", "heuristic_evidence_score")
    ev_strength = prc.get("evidence_strength", "medium")
    exec_status = runtime.get("execution_status", runtime.get("exit_status"))

    lines: List[str] = []
    lines.append(f"# Case Failure Attribution: `{case_id}` (`{trial_name}`)\n")

    cat = prc.get("category", "unknown")
    subtype = prc.get("subtype", "unknown")
    code = prc.get("code", "UNKNOWN")
    cat_en = category_label(cat, "en")

    # 1. Root Cause Verdict
    lines.append(f"## {section_title(1, 'en')}\n")
    if isinstance(manifestation, dict):
        direct_cause = manifestation.get("summary") or "N/A"
    elif isinstance(manifestation, str):
        direct_cause = manifestation
    else:
        direct_cause = "N/A"

    prc_summary = prc.get("summary") or prc.get("reasoning") if isinstance(prc, dict) else None
    internal_cause = prc_summary or direct_cause or "N/A"
    manifestation_line = _manifestation_summary(evidence, direct_cause, "en")
    attribution_line = _attribution_sentence(prc, internal_cause, "en")
    lines.append(
        "| Field | Finding |\n"
        "|---|---|\n"
        f"| **Verdict** | `{verdict}` · execution `{exec_status}` · verification `{runtime.get('verification_status', 'unknown')}` · reward `{runtime.get('reward')}` |\n"
        f"| **Primary cause** | `{cat}` {cat_en} → {subtype_label(subtype, 'en')} `{subtype}` · `{code}` |\n"
        f"| **Stages** | occurred at {stage_label(f_stage, 'en')} → detected at {stage_label(d_stage, 'en')} |\n"
        f"| **Confidence** | **{conf_val:.2f}** · {confidence_kind_label(conf_kind, 'en')} · evidence strength {evidence_strength_label(ev_strength, 'en')} |\n"
    )

    gate_warn = _render_gate_warning(runtime, verdict=verdict, lang="en")
    if gate_warn:
        lines.append(gate_warn)
    integrity_warn = _render_evidence_integrity_warning(evidence, lang="en")
    if integrity_warn:
        lines.append(integrity_warn)

    lines.append(
        f"> **Failure manifestation**: {clean_prose(manifestation_line, 300)}  \n"
        f"> **Attribution**: {clean_prose(attribution_line, 300)}\n"
    )

    # 2. Failure Evidence Chain
    lines.append(f"## {section_title(2, 'en')}\n")
    lines.extend(_render_verifier_report_block(evidence, lang="en"))

    from trace_causal_anchors import trace_causal_anchors, render_causal_anchors_markdown

    stored_anchors = evidence.get("causal_anchors")
    anchors = (
        stored_anchors
        if isinstance(stored_anchors, dict) and stored_anchors
        else trace_causal_anchors(evidence)
    )
    if anchors:
        lines.extend(render_causal_anchors_markdown(anchors, lang="en"))

    fail_snippets = []
    for vobs in evidence.get("verifier_observations") or []:
        if vobs.get("type") == "verifier_fail_message" and (vobs.get("matched_text") or "").strip():
            snippet = clean_code_line(vobs.get("matched_text", ""))
            if snippet:
                fail_snippets.append(snippet)
    if fail_snippets:
        lines.append("- **Verifier message**:")
        lines.append("```text")
        for snip in dict.fromkeys(fail_snippets[:2]):
            lines.append(snip)
        lines.append("```")

    fud_ref: Optional[str] = None
    if fud:
        fud_status = fud.get("status", "identified" if fud.get("event_ref") else "not_identified")
        fud_ref = fud.get("event_ref")
        status_txt = "identified" if fud_status == "identified" else "not identified"
        lines.append(
            "- **First unrecovered deviation**: "
            f"{status_txt} · event `{fud_ref or '—'}` · {clean_prose(fud.get('summary'), 200)}"
        )

    ev_refs = analysis.get("evidence_refs") or ["art:trial_result"]
    lines.append("- **Evidence references**: " + ", ".join(f"`{r}`" for r in ev_refs))

    lines.extend(_render_diagnostics_block(evidence, lang="en"))

    lines.extend(_render_evidence_file_pointers(evidence, lang="en", output_format=output_format))

    # 3. Hypothesis Audit
    lines.append(f"## {section_title(3, 'en')}\n")
    comp_hyps_raw = analysis.get("competing_hypotheses") or []
    ex_hyps_raw = analysis.get("excluded_hypotheses") or []
    runtime = evidence.get("runtime") or {}
    prc = analysis.get("primary_root_cause") or {}
    prc_cat = prc.get("category", "")
    prc_subtype = prc.get("subtype", "")
    comp_hyps, ex_hyps = _prune_hypotheses_for_report(comp_hyps_raw, ex_hyps_raw, runtime, prc_cat)
    for ch in comp_hyps:
        hid = ch.get("hypothesis_id", "H")
        cat_h = ch.get("category", "")
        subtype_h = ch.get("subtype", "")
        score = float(ch.get("confidence", 0.0))
        claim = ch.get("claim", "")
        ev_text = format_evidence_list_text(
            ch.get("evidence_for"), ch.get("evidence_against"), lang="en"
        )
        role = (
            "Primary hypothesis"
            if (cat_h, subtype_h) == (prc_cat, prc_subtype)
            else "Competing hypothesis"
        )
        lines.append(
            f"- **{role} `{hid}` (`{cat_h}` / `{subtype_h}`, confidence `{score:.2f}`)**: "
            f"{clean_prose(claim, 220)}{ev_text}"
        )

    for eh in ex_hyps:
        lines.append(
            f"- **Excluded `{eh.get('hypothesis_id')}` (`{eh.get('category')}`)**: "
            f"{_excluded_reason(eh, runtime, 'en')}"
        )
    if not comp_hyps and not ex_hyps:
        lines.append("- Case passed; no competing failure hypotheses.")

    for cf in analysis.get("contributing_factors") or []:
        cf_cat = cf.get("category")
        cf_code = cf.get("code")
        cf_score = float(cf.get("confidence", 0.0))
        cf_summary = clean_prose(localize_engine_text(cf.get("summary"), "zh"), 220)
        lines.append(
            f"- **Contributing `{cf_cat}` / `{cf_code}` (confidence `{cf_score:.2f}`)**: {cf_summary}"
        )

    sigs = evidence.get("behavioral_signals") or []
    if sigs:
        lines.append(
            f"- **Behavioral signals**: {len(sigs)} recorded (details in `data/evidence.json` → `behavioral_signals`)"
        )
    lines.append("")

    # 4. Recommended Actions
    lines.append(f"## {section_title(4, 'en')}\n")
    recs = analysis.get("recommended_actions") or []
    if recs:
        for owner, text in _display_actions(analysis, "en"):
            lines.append(f"- **[{owner}]**: {clean_prose(text, 240)}")
    else:
        lines.append("- No action required (case passed).")

    sp = analysis.get("skill_prescription")
    if sp and isinstance(sp, dict):
        rec_skill = sp.get("recommended_skill") or sp
        name = rec_skill.get("name")
        gap_str = ", ".join(rec_skill.get("capability_gap") or [])
        lines.append(
            f"- **Skill prescription**: add or update `{name}` (capability gap: {gap_str})"
        )

    missing = evidence.get("missing_artifacts") or []
    if missing:
        core_missing, secondary_cnt = filter_core_missing_artifacts(missing)
        if core_missing:
            core_str = ", ".join(f"`{m}`" for m in core_missing)
            if secondary_cnt > 0:
                lines.append(
                    f"- **Missing artifacts**: {core_str} ({secondary_cnt} secondary files omitted)"
                )
            else:
                lines.append(f"- **Missing artifacts**: {core_str}")
        elif secondary_cnt > 0:
            lines.append(
                f"- **Artifact integrity**: core inputs and outputs intact "
                f"({secondary_cnt} secondary files omitted)."
            )
    lines.append("")
    return "\n".join(lines)


def _render_passed_edition(
    evidence: Dict[str, Any],
    analysis: Dict[str, Any],
    lang: str = "bilingual",
    output_format: str = "both",
) -> str:
    """
    Lightweight passed-trial view (< 30 lines):
      - Verdict & Pass Summary (verdict, reward, pass basis)
      - Verification Evidence (case/trial context, verified artifacts, matrix index)
    No failure semantics: no root cause, no competing hypotheses, no missing-artifact noise.
    """
    case_id = analysis.get("case_id", evidence.get("case_id", "unknown"))
    trial_name = analysis.get("trial_name", evidence.get("trial_name", case_id))
    task_dir = evidence.get("task_dir")
    task_name = Path(task_dir).name if task_dir and task_dir != "N/A" else "N/A"
    runtime = evidence.get("runtime") or {}
    reward = runtime.get("reward")

    lines: List[str] = []
    integrity_warn = _render_evidence_integrity_warning(evidence, lang=lang)

    titles = {
        "zh": f"# 案例验证通过报告: `{case_id}` (`{trial_name}`)\n",
        "en": f"# Case Verification Pass: `{case_id}` (`{trial_name}`)\n",
        "bilingual": f"# 案例验证通过报告 / Case Verification Pass: `{case_id}` (`{trial_name}`)\n",
    }
    lines.append(titles.get(lang, titles["bilingual"]))

    if lang == "en":
        lines.append("> **Language**: English edition\n")
    elif lang == "zh":
        lines.append("> **语言**: 简体中文\n")
    else:
        lines.append("> **语言 / Language**: 中英双语 (Bilingual)\n")

    # 1. Verdict & Pass Summary
    lines.append(f"## {section_title(1, lang, passed=True)}\n")
    if lang == "en":
        lines.append(
            "| Field | Finding |\n"
            "|---|---|\n"
            f"| **Verdict** | `passed` · reward `{reward}` |\n"
            "| **Stages** | execution and verification both passed |\n"
            "| **Confidence** | **1.00** · rule-based decision |\n"
        )
    elif lang == "zh":
        lines.append(
            "| 项目 | 结论 |\n"
            "|---|---|\n"
            f"| **评测结论** | `passed` · reward `{reward}` |\n"
            "| **阶段** | 执行与验证均通过 |\n"
            "| **置信度** | **1.00** · 规则判定 |\n"
        )
    else:
        lines.append(
            "| 项目 (Field) | 结论 (Finding) |\n"
            "|---|---|\n"
            f"| **评测结论 (Verdict)** | `passed` · reward `{reward}` |\n"
            "| **阶段 (Stages)** | 执行与验证均通过 (execution and verification both passed) |\n"
            "| **置信度 (Confidence)** | **1.00** · 规则判定 (rule-based) |\n"
        )

    if integrity_warn:
        if lang == "en":
            lines.append(
                "> **Pass criteria**: evaluation rules reported pass, but evidence collection "
                "was degraded, so the finding rests on weakened evidence (see the warning below).\n"
            )
        elif lang == "zh":
            lines.append(
                "> **通过依据**: 评测规则判定通过；但证据采集存在降级，通过结论建立在弱化证据之上（见下方警示）。\n"
            )
        else:
            lines.append(
                "> **通过依据 (Pass criteria)**: 评测规则判定通过，但证据采集存在降级，通过结论建立在弱化证据之上 "
                "(rules reported pass, but evidence collection was degraded).\n"
            )
        lines.append("\n" + integrity_warn + "\n")
    else:
        if lang == "en":
            lines.append(
                "> **Pass criteria**: evaluation rules fully satisfied; no unrecovered deviation or specification violation detected.\n"
            )
        elif lang == "zh":
            lines.append("> **通过依据**: 评测规则全部通过，未检出未恢复偏差或规格违规。\n")
        else:
            lines.append(
                "> **通过依据 (Pass criteria)**: 评测规则全部通过，未检出未恢复偏差或规格违规 "
                "(rules fully satisfied; no unrecovered deviation or specification violation).\n"
            )

    # 2. Verification Evidence
    lines.append(f"## {section_title(2, lang, passed=True)}\n")
    if lang == "en":
        lines.append(
            f"- **Case & Trial**: case `{case_id}`, trial `{trial_name}`, task `{task_name}`\n"
        )
    elif lang == "zh":
        lines.append(
            f"- **任务与试次**: 任务 `{task_name}`, 试次 `{trial_name}`, 案例 `{case_id}`\n"
        )
    else:
        lines.append(
            f"- **任务与试次 (Case & Trial)**: 任务 `{task_name}`, 试次 `{trial_name}`, 案例 `{case_id}`\n"
        )

    diagnostics_block = _render_diagnostics_block(evidence, lang=lang)
    if diagnostics_block:
        lines.append("\n".join(diagnostics_block) + "\n")

    artifacts = evidence.get("artifacts") or []
    existing = [a.get("rel_path") for a in artifacts if a.get("exists") and a.get("rel_path")]
    if existing:
        art_items = ", ".join(f"`{p}`" for p in existing)
        if lang == "en":
            lines.append(f"- **Verified artifacts**: {art_items}\n")
        elif lang == "zh":
            lines.append(f"- **已验证产物**: {art_items}\n")
        else:
            lines.append(f"- **已验证产物 (Verified artifacts)**: {art_items}\n")
    else:
        if lang == "en":
            lines.append("- **Verified artifacts**: passed with no additional artifact records.\n")
        elif lang == "zh":
            lines.append("- **已验证产物**: 评测规则判定通过（无额外产物记录）。\n")
        else:
            lines.append(
                "- **已验证产物 (Verified artifacts)**: 评测规则判定通过，无额外产物记录 "
                "(passed with no additional artifacts).\n"
            )

    if output_format in ("json", "both"):
        if lang == "en":
            lines.append(
                "- **Full evidence & analysis**: see `data/evidence.json` and `data/analysis.json`.\n"
            )
        elif lang == "zh":
            lines.append(
                "- **完整证据与归因数据**: 参见 `data/evidence.json` 与 `data/analysis.json`。\n"
            )
        else:
            lines.append(
                "- **完整证据与归因数据 (Full evidence & analysis)**: 参见 `data/evidence.json` 与 `data/analysis.json`。\n"
            )
    else:
        if lang == "en":
            lines.append(
                "- **Structured analysis artifacts**: rendered with `--format markdown`; "
                "`evidence.json` / `analysis.json` were not written.\n"
            )
        elif lang == "zh":
            lines.append(
                "- **结构化归因产物**: 本次按 `--format markdown` 渲染，未生成 `evidence.json` / `analysis.json`。\n"
            )
        else:
            lines.append(
                "- **结构化归因产物 (Structured artifacts)**: 本次按 `--format markdown` 渲染，"
                "未生成 `evidence.json` / `analysis.json`。\n"
            )

    return "".join(lines)


def render_report_markdown(
    evidence: Dict[str, Any],
    analysis: Dict[str, Any],
    lang: str = "bilingual",
    output_format: str = "both",
) -> str:
    """
    Renders report markdown:
      - For passed trials: lightweight 2-section view (< 30 lines) without failure semantics
      - `lang="zh"`: Standalone pure Chinese report
      - `lang="en"`: Standalone pure English report
      - `lang="bilingual"`: Compact bilingual edition with parallel bilingual labels (no duplicated Part II)
    """
    verdict = analysis.get("verdict", "failed")
    if verdict == "passed":
        return _render_passed_edition(evidence, analysis, lang=lang, output_format=output_format)

    if lang == "en":
        return _render_english_edition(evidence, analysis, output_format=output_format)
    if lang == "zh":
        return _render_chinese_bilingual_core(
            evidence, analysis, include_en_labels=False, output_format=output_format
        )

    # Default `bilingual`: Compact Chinese-English bilingual labeled edition (4 core sections).
    return _render_chinese_bilingual_core(
        evidence, analysis, include_en_labels=True, output_format=output_format
    )


def render_skill_prescription_markdown(
    skill_prescription: Dict[str, Any],
    lang: str = "bilingual",
) -> str:
    rec = skill_prescription.get("recommended_skill") or skill_prescription
    name = rec.get("name", "domain-skill")

    yaml_lines = [
        "```yaml",
        "recommended_skill:",
        f"  name: {name}",
        "  trigger:",
    ]
    for t in rec.get("trigger") or []:
        yaml_lines.append(f'    - "{t}"')
    yaml_lines.append("  capability_gap:")
    for g in rec.get("capability_gap") or []:
        yaml_lines.append(f'    - "{g}"')
    yaml_lines.append("  required_guidance:")
    for rg in rec.get("required_guidance") or []:
        yaml_lines.append(f'    - "{rg}"')
    yaml_lines.append("  anti_patterns:")
    for ap in rec.get("anti_patterns") or []:
        yaml_lines.append(f'    - "{ap}"')
    yaml_lines.append("  evidence_cases:")
    for ec in rec.get("evidence_cases") or []:
        yaml_lines.append(f'    - "{ec}"')
    yaml_lines.append("```\n")
    yaml_block = "\n".join(yaml_lines)

    zh_section = "\n".join(
        [
            f"- **建议技能名称**: `{name}`",
            "- **触发条件 (`trigger`)**: " + "；".join(rec.get("trigger") or []),
            "- **能力缺口 (`capability_gap`)**: " + "；".join(rec.get("capability_gap") or []),
            "- **核心执行指引 (`required_guidance`)**: "
            + "；".join(rec.get("required_guidance") or []),
            "- **禁止的反模式 (`anti_patterns`)**: " + "；".join(rec.get("anti_patterns") or []),
            "",
        ]
    )

    en_section = "\n".join(
        [
            f"- **Recommended Skill Name**: `{name}`",
            "- **Trigger Conditions (`trigger`)**: " + "; ".join(rec.get("trigger") or []),
            "- **Capability Gap (`capability_gap`)**: "
            + "; ".join(rec.get("capability_gap") or []),
            "- **Required Guidance (`required_guidance`)**: "
            + "; ".join(rec.get("required_guidance") or []),
            "- **Anti-Patterns to Avoid (`anti_patterns`)**: "
            + "; ".join(rec.get("anti_patterns") or []),
            "",
        ]
    )

    if lang == "zh":
        return f"# 领域技能处方 (`Skill Prescription`): `{name}`\n\n## 1. 结构化定义 (YAML)\n\n{yaml_block}\n## 2. 中文执行指引\n\n{zh_section}"
    if lang == "en":
        return f"# Domain Skill Prescription: `{name}`\n\n## 1. Structured Specification (YAML)\n\n{yaml_block}\n## 2. Execution Guidance\n\n{en_section}"

    return (
        f"# Skill Prescription / 领域技能处方 (中英双版): `{name}`\n\n"
        f"## 1. Structured Specification (YAML)\n\n{yaml_block}\n"
        f"## 2. 中文说明 (Chinese Guidance)\n\n{zh_section}\n"
        f"## 3. English Guidance\n\n{en_section}"
    )


def main() -> None:
    import sys
    from validate_analysis import validate_all

    parser = argparse.ArgumentParser(
        description="Render bilingual report.md and optional skill-prescription.md."
    )
    parser.add_argument("--evidence", required=True, type=Path, help="Path to evidence.json")
    parser.add_argument("--analysis", required=True, type=Path, help="Path to analysis.json")
    parser.add_argument("--output-report", required=True, type=Path, help="Path to write report.md")
    parser.add_argument(
        "--output-prescription",
        required=False,
        type=Path,
        default=None,
        help="Optional path to write skill-prescription.md",
    )
    parser.add_argument(
        "--lang",
        choices=["bilingual", "zh", "en"],
        default="bilingual",
        help="Report language edition (default: bilingual)",
    )
    args = parser.parse_args()

    if not args.evidence.is_file():
        print(f"ERROR: --evidence file does not exist: {args.evidence}", file=sys.stderr)
        sys.exit(2)
    if not args.analysis.is_file():
        print(f"ERROR: --analysis file does not exist: {args.analysis}", file=sys.stderr)
        sys.exit(2)

    evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
    analysis = json.loads(args.analysis.read_text(encoding="utf-8"))

    report_md = render_report_markdown(evidence, analysis, lang=args.lang)
    ok, val_errors = validate_all(evidence, analysis, report_md)
    if not ok:
        for err in val_errors:
            print(f"VALIDATION ERROR: {err}", file=sys.stderr)
        sys.exit(1)

    args.output_report.parent.mkdir(parents=True, exist_ok=True)
    args.output_report.write_text(report_md, encoding="utf-8")

    # Also emit companion report.zh.md and report.en.md when rendering standard report.md
    if args.output_report.name == "report.md":
        (args.output_report.parent / "report.zh.md").write_text(
            render_report_markdown(evidence, analysis, lang="zh"), encoding="utf-8"
        )
        (args.output_report.parent / "report.en.md").write_text(
            render_report_markdown(evidence, analysis, lang="en"), encoding="utf-8"
        )

    sp = analysis.get("skill_prescription")
    if sp and args.output_prescription:
        args.output_prescription.write_text(
            render_skill_prescription_markdown(sp, lang="bilingual"), encoding="utf-8"
        )
        if args.output_prescription.name == "skill-prescription.md":
            (args.output_prescription.parent / "skill-prescription.zh.md").write_text(
                render_skill_prescription_markdown(sp, lang="zh"), encoding="utf-8"
            )
            (args.output_prescription.parent / "skill-prescription.en.md").write_text(
                render_skill_prescription_markdown(sp, lang="en"), encoding="utf-8"
            )


if __name__ == "__main__":
    main()
