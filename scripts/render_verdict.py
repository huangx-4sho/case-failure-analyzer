#!/usr/bin/env python3
"""
Verdict Card Renderer (`scripts/render_verdict.py`)

Builds the *direct answer* for a task-ID invocation (`/case failure analyzer
task208`): the primary root cause with its failure reason, the four peer-level
categories with an explicit status, and only then the subcategory (subtype /
code) breakdown under the winning category.

The card is deliberately evidence-backed. Every line comes from `analysis.json`
/ `evidence.json`; nothing is inferred from the task number. It is rendered both
to stdout (so the skill answers immediately) and to `summary.md`, while
the machine-readable form goes to `data/summary.json`.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from report_style import (
    localize_engine_text,
    remediation,
    render_error_matrix_table,
    subtype_label,
)

#: Fixed peer-level order. `unknown` is an auxiliary bucket, not a peer cause.
PEER_CATEGORIES = ("agent", "verifier", "case", "infra")

CATEGORY_LABELS = {
    "agent": ("智能体决策与执行失误", "Agent decisions & execution"),
    "verifier": ("评测验证与规则判定缺陷", "Verifier rules & scoring"),
    "case": ("基准题目与规格设计缺陷", "Case / task design"),
    "infra": ("运行环境与系统设施缺陷", "Runtime & infrastructure"),
    "unknown": ("未分类或信息缺失", "Unknown / insufficient evidence"),
    "none": ("无失败", "No failure"),
}

STATUS_LABELS = {
    "primary": ("主因", "PRIMARY"),
    "contributing": ("伴随因素", "CONTRIBUTING"),
    "excluded": ("已排除", "EXCLUDED"),
    "not_selected": ("未选为主因", "not selected"),
    "candidate": ("候选假设", "candidate"),
}


def _text(value: Any, limit: int = 260) -> str:
    """Single-line, pipe-safe, bounded text for markdown table cells.

    Harbor verifier envelopes sometimes embed an entire raw JSON document in
    the failure text; that noise is dropped so the direct answer stays readable.
    """
    s = " ".join(str(value or "").split())
    # Strip an embedded JSON object (keep any human-readable prefix before it).
    brace = s.find("{")
    if brace > 0 and s.count("{") >= 1 and '"' in s[brace:]:
        s = s[:brace].rstrip(" :;,：")
    if not s:
        s = "(no detail text)"
    s = s.replace("|", "\\|")
    if len(s) > limit:
        s = s[: limit - 1] + "…"
    return s


def _category_label(cat: str) -> str:
    return CATEGORY_LABELS.get(cat, (cat, cat))[0]


def _subtype_label(subtype: str) -> str:
    if not subtype:
        return "—"
    return f"{subtype_label(subtype, 'zh')} (`{subtype}`)"


def _subtype_label_en(subtype: str) -> str:
    if not subtype:
        return "—"
    return f"{subtype_label(subtype, 'en')} (`{subtype}`)"


def _failure_reason(evidence: Dict[str, Any], analysis: Dict[str, Any]) -> str:
    """Best available one-line statement of *why the trial failed*.

    Named checks are preferred over the verifier's flat summary: when several
    checks repeat the same explanation, the check names are grouped so the
    answer shows *which* checks failed without printing the same detail thrice.
    """
    checks = _named_checks(evidence)
    if checks:
        detail = _text(checks[0].get("detail"), 300)
        labels = [f"[{c.get('layer') or 'L?'}] {c.get('name') or 'check'}" for c in checks[:3]]
        same_detail = len({_text(c.get("detail"), 300) for c in checks}) == 1
        if detail == "(no detail text)":
            return _text(", ".join(labels))
        if len(checks) > 1 and same_detail:
            extra = f" (+{len(checks) - len(labels)} more)" if len(checks) > len(labels) else ""
            return _text(f"{', '.join(labels)}: {detail}{extra}")
        head = labels[0]
        extra = f" (+{len(checks) - 1} more)" if len(checks) > 1 else ""
        return _text(f"{head}: {detail}{extra}")
    report = evidence.get("verifier_report") or {}
    if isinstance(report, dict) and report.get("summary"):
        return _text(report["summary"])
    fm = analysis.get("failure_manifestation") or {}
    if isinstance(fm, dict) and fm.get("summary"):
        return _text(fm["summary"])
    if isinstance(fm, str) and fm.strip():
        return _text(fm)
    return _text((analysis.get("primary_root_cause") or {}).get("summary"))


def _named_checks(evidence: Dict[str, Any]) -> List[Dict[str, Any]]:
    report = evidence.get("verifier_report") or {}
    if not isinstance(report, dict):
        return []
    failed = report.get("failed_checks") or []
    return [c for c in failed if isinstance(c, dict)]


def _status_for(
    category: str,
    primary_cat: str,
    contributing: List[Dict[str, Any]],
    excluded: List[Dict[str, Any]],
    hypotheses: List[Dict[str, Any]],
) -> str:
    if category == primary_cat:
        return "primary"
    if any(c.get("category") == category for c in contributing):
        return "contributing"
    if any(e.get("category") == category for e in excluded):
        return "excluded"
    if any(h.get("category") == category for h in hypotheses):
        return "candidate"
    return "not_selected"


def _reason_for(
    category: str,
    status: str,
    analysis: Dict[str, Any],
    evidence: Dict[str, Any],
    lang: str = "zh",
) -> str:
    en = lang == "en"
    prc = analysis.get("primary_root_cause") or {}
    contributing = analysis.get("contributing_factors") or []
    excluded = analysis.get("excluded_hypotheses") or []

    if status == "primary":
        return _text(localize_engine_text(prc.get("summary"), lang))
    if status == "contributing":
        item = next(c for c in contributing if c.get("category") == category)
        prefix = "Contributing: " if en else "伴随因素："
        detail = localize_engine_text(item.get("summary") or item.get("code"), lang)
        return _text(f"{prefix}{detail}")
    if status == "excluded":
        item = next(e for e in excluded if e.get("category") == category)
        runtime = evidence.get("runtime") or {}
        if category == "infra" and runtime.get("agent_started"):
            detail = (
                "Container built cleanly and the agent ran"
                + (
                    ", and the verifier ran to completion"
                    if runtime.get("verifier_started")
                    else ""
                )
                + " (`runtime.agent_started == true`)"
                if en
                else "容器构建与 Agent 执行均正常"
                + ("，验证器也已完成判定" if runtime.get("verifier_started") else "")
                + "（`runtime.agent_started == true`）"
            )
        else:
            detail = localize_engine_text(item.get("reason"), lang)
        return _text(detail)

    # Evidence-grounded fallbacks: never claim a cause without an observation.
    if category == "verifier":
        checks = _named_checks(evidence)
        crashes = [
            o
            for o in (evidence.get("verifier_observations") or [])
            if o.get("type") == "verifier_internal_crash"
        ]
        if crashes:
            return _text(
                "Verifier internal crash observed but not selected as primary"
                if en
                else "检出验证器内部崩溃观测，但归因链未将其选为主因"
            )
        if checks:
            names = ", ".join(str(c.get("name")) for c in checks[:3])
            return _text(
                f"Verifier ran and reported {len(checks)} named failing check(s) ({names}); "
                "no verifier-side defect detected"
                if en
                else f"验证器执行并给出 {len(checks)} 项具名失败检查（{names}），未检出验证器自身缺陷"
            )
        return _text(
            "No verifier crash, parser defect, or hidden contract evidence"
            if en
            else "未检出验证器崩溃、解析缺陷或隐藏契约证据"
        )
    if category == "case":
        defects = [
            c
            for c in (evidence.get("contract_observations") or [])
            if c.get("alignment") == "case_defect"
        ]
        if defects:
            return _text(
                f"{len(defects)} case/spec defect observation(s), not selected as primary"
                if en
                else f"检出 {len(defects)} 项题目/规格缺陷观测，但未选为主因"
            )
        return _text(
            "Contract audit found no missing task asset or ambiguous specification"
            if en
            else "契约审计未发现题目资产缺失或规格歧义"
        )
    if category == "infra":
        runtime = evidence.get("runtime") or {}
        causal = [
            e for e in (evidence.get("error_observations") or []) if e.get("causal_candidate")
        ]
        if causal:
            codes = ", ".join(str(e.get("code")) for e in causal[:3])
            return _text(
                f"{len(causal)} causal infra observation(s) ({codes}), not selected as primary"
                if en
                else f"检出 {len(causal)} 条因果性 infra 观测（{codes}），但未选为主因"
            )
        return _text(
            f"No causal infra observation; agent_started={bool(runtime.get('agent_started'))}, "
            f"verifier_started={bool(runtime.get('verifier_started'))}"
            if en
            else f"未检出因果性 infra 观测；agent_started={bool(runtime.get('agent_started'))}, "
            f"verifier_started={bool(runtime.get('verifier_started'))}"
        )
    # agent
    signals = evidence.get("behavioral_signals") or []
    sci = [
        s for s in (evidence.get("scientific_observations") or []) if not s.get("recovered", False)
    ]
    return _text(
        f"No positive agent deviation evidence ({len(signals)} behavioral signals, "
        f"{len(sci)} unrecovered scientific errors)"
        if en
        else f"未检出可归因的正向 Agent 偏离证据（行为信号 {len(signals)} 项、"
        f"未恢复科学误差 {len(sci)} 项，均不足以定为主因）"
    )


def _recommended_actions(analysis: Dict[str, Any], prc: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Actions for the card, replacing a restated cause with a concrete fix."""
    summary = " ".join(str(prc.get("summary") or "").split())
    cat, subtype = prc.get("category"), prc.get("subtype")
    actions: List[Dict[str, Any]] = []
    for a in analysis.get("recommended_actions") or []:
        raw = " ".join(str(a.get("action") or "").split())
        restates_cause = bool(summary) and bool(raw) and raw[:80] == summary[:80]
        rem_zh = remediation(cat, subtype, "zh") if restates_cause else ""
        rem_en = remediation(cat, subtype, "en") if restates_cause else ""
        actions.append(
            {
                "owner": a.get("owner"),
                "action": _text(rem_en or raw, 260),
                "action_zh": _text(rem_zh or localize_engine_text(raw, "zh"), 260),
            }
        )
    return actions


def build_verdict_card(
    evidence: Dict[str, Any],
    analysis: Dict[str, Any],
    task_id: Optional[str] = None,
    report_basename: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the structured verdict card consumed by both stdout and disk."""
    prc = analysis.get("primary_root_cause") or {}
    primary_cat = str(prc.get("category") or "unknown")
    contributing = [c for c in (analysis.get("contributing_factors") or []) if isinstance(c, dict)]
    excluded = [e for e in (analysis.get("excluded_hypotheses") or []) if isinstance(e, dict)]
    hypotheses = [h for h in (analysis.get("competing_hypotheses") or []) if isinstance(h, dict)]

    categories = []
    for cat in PEER_CATEGORIES:
        status = _status_for(cat, primary_cat, contributing, excluded, hypotheses)
        categories.append(
            {
                "category": cat,
                "label": _category_label(cat),
                "label_en": CATEGORY_LABELS.get(cat, (cat, cat))[1],
                "status": status,
                "status_label": STATUS_LABELS[status][0],
                "status_label_en": STATUS_LABELS[status][1],
                "reason": _reason_for(cat, status, analysis, evidence, lang="zh"),
                "reason_en": _reason_for(cat, status, analysis, evidence, lang="en"),
            }
        )

    subcategories: List[Dict[str, Any]] = [
        {
            "category": primary_cat,
            "subtype": prc.get("subtype"),
            "subtype_label": _subtype_label(str(prc.get("subtype") or "")),
            "subtype_label_en": _subtype_label_en(str(prc.get("subtype") or "")),
            "code": prc.get("code"),
            "confidence": prc.get("confidence"),
            "summary": _text(prc.get("summary"), 400),
            "summary_zh": _text(localize_engine_text(prc.get("summary"), "zh"), 400),
            "role": "primary",
        }
    ]
    for c in contributing:
        subcategories.append(
            {
                "category": c.get("category"),
                "subtype": c.get("subtype"),
                "subtype_label": _subtype_label(str(c.get("subtype") or "")),
                "subtype_label_en": _subtype_label_en(str(c.get("subtype") or "")),
                "code": c.get("code"),
                "confidence": c.get("confidence"),
                "summary": _text(c.get("summary"), 400),
                "summary_zh": _text(localize_engine_text(c.get("summary"), "zh"), 400),
                "role": "contributing",
            }
        )
    for h in hypotheses:
        if h.get("category") == primary_cat and h.get("hypothesis_id") != "H1":
            subcategories.append(
                {
                    "category": h.get("category"),
                    "subtype": h.get("subtype"),
                    "subtype_label": _subtype_label(str(h.get("subtype") or "")),
                    "subtype_label_en": _subtype_label_en(str(h.get("subtype") or "")),
                    "code": None,
                    "confidence": h.get("confidence"),
                    "summary": _text(h.get("claim"), 400),
                    "summary_zh": _text(localize_engine_text(h.get("claim"), "zh"), 400),
                    "role": "candidate",
                }
            )

    named = _named_checks(evidence)
    trace = analysis.get("decision_trace") or {}
    selected = trace.get("selected") or {}

    from trace_causal_anchors import trace_causal_anchors

    stored_anchors = evidence.get("causal_anchors")
    anchors = (
        stored_anchors
        if isinstance(stored_anchors, dict) and stored_anchors
        else trace_causal_anchors(evidence)
    )

    return {
        "schema_version": "cfa-verdict-card-v1",
        "task_id": task_id or evidence.get("case_id"),
        "trial_name": analysis.get("trial_name") or evidence.get("trial_name"),
        "verdict": analysis.get("verdict"),
        "scored": bool((evidence.get("runtime") or {}).get("verifier_started")),
        "primary_root_cause": {
            "category": primary_cat,
            "category_label": _category_label(primary_cat),
            "subtype": prc.get("subtype"),
            "code": prc.get("code"),
            "confidence": prc.get("confidence"),
            "summary": _text(prc.get("summary"), 400),
            "summary_zh": _text(localize_engine_text(prc.get("summary"), "zh"), 400),
        },
        "failure_reason": _failure_reason(evidence, analysis),
        "four_categories": categories,
        "subcategories": subcategories,
        "causal_anchors": anchors,
        "confidence_breakdown": prc.get("confidence_breakdown"),
        "scientific_observations": list(evidence.get("scientific_observations") or []),
        "named_failed_checks": [
            {
                "layer": c.get("layer"),
                "name": c.get("name"),
                "detail": _text(c.get("detail"), 300),
                "core": bool(c.get("core")),
                "source": c.get("source"),
                "failure_kind": c.get("failure_kind"),
                "artifact_comparison": c.get("artifact_comparison") or c.get("evidence"),
            }
            for c in named[:8]
        ],
        "evidence_refs": list(analysis.get("evidence_refs") or [])[:8],
        "recommended_actions": _recommended_actions(analysis, prc),
        "selected_gate": selected.get("gate_id"),
        "report_basename": report_basename,
    }


def render_verdict_card_markdown(card: Dict[str, Any], lang: str = "zh") -> str:
    """Render the card as a compact bilingual-ish Markdown answer."""
    prc = card["primary_root_cause"]
    conf = prc.get("confidence")
    conf_txt = f"{float(conf):.2f}" if isinstance(conf, (int, float)) else "—"
    basename = card.get("report_basename") or card.get("task_id") or "task"

    is_en = lang == "en"
    if is_en:
        title = f"# {card['task_id']} — Root Cause Verdict"
        lead = (
            f"**Verdict**: `{card['verdict']}` · **Primary**: `{prc['category']}` "
            f"/ `{prc['subtype']}` / `{prc['code']}` (confidence **{conf_txt}**)"
        )
        reason_hdr = "**Why it failed**"
        cat_hdr = "## 1. Four peer categories"
        cat_cols = ("Category", "Status", "Reason")
        sub_hdr = f"## 2. Subcategory under `{prc['category']}`"
        sub_cols = ("Role", "Subtype", "Code", "Confidence", "Basis")
        ev_hdr = "**Evidence refs**"
        act_hdr = "**Recommended actions**"
        checks_hdr = "**Named failed checks**"
        gate_hdr = "Selected gate"
        report_line = f"Full report: `{basename}.md` (same folder)"
        role_labels = {
            "primary": "primary",
            "contributing": "contributing",
            "candidate": "candidate",
        }
    else:
        title = f"# {card['task_id']} — 根因速览 (Root Cause Verdict)"
        lead = (
            f"**裁定**: `{card['verdict']}` · **主根因**: "
            f"{prc['category_label']} (`{prc['category']}`) / `{prc['subtype']}` / "
            f"`{prc['code']}`（置信度 **{conf_txt}**）"
        )
        reason_hdr = "**失败原因 (Why it failed)**"
        cat_hdr = "## 1. 四大类裁决 (Four Peer Categories)"
        cat_cols = ("大类", "裁决", "依据")
        sub_hdr = f"## 2. 主因大类下的小分类 (Subcategory under `{prc['category']}`)"
        sub_cols = ("角色", "小分类 (subtype)", "编码 (code)", "置信度", "依据")
        ev_hdr = "**证据引用**"
        act_hdr = "**修复建议**"
        checks_hdr = "**具名失败检查 / Named failed checks**"
        gate_hdr = "判定门控 / Gate"
        report_line = f"完整报告：`{basename}.md`（同目录）"
        role_labels = {
            "primary": "主因",
            "contributing": "伴随因素",
            "candidate": "候选假设",
        }

    lines: List[str] = [title, "", lead]
    cb = card.get("confidence_breakdown")
    if cb and isinstance(cb, dict) and cb.get("factors"):
        badge = "🟢" if float(conf or 0) >= 0.65 else ("🟡" if float(conf or 0) >= 0.40 else "🔴")
        points = cb.get("points")
        denom = cb.get("denominator", 10)
        score_label = f"`{points}/{denom} 分`" if not is_en else f"`{points}/{denom} pts`"
        factor_parts = []
        for f in cb.get("factors") or []:
            delta = f.get("delta", 0)
            lbl = f.get("label_en") if is_en else (f.get("label_zh") or f.get("key"))
            sign = f"+{delta}" if delta > 0 else str(delta)
            icon = "✓" if delta > 0 else ("⚠️" if delta < 0 else "—")
            factor_parts.append(f"{icon} {lbl}({sign})")
        parts_str = " · ".join(factor_parts)
        cb_line = (
            f"{badge} {score_label} · {parts_str}" if factor_parts else f"{badge} {score_label}"
        )
        missing = cb.get("missing_to_raise") or []
        if missing and float(conf or 0) < 0.75:
            gap_label = "提分缺口：" if not is_en else "Gap to raise: "
            gap_items = "、".join(missing) if not is_en else ", ".join(missing)
            cb_line += f"（{gap_label}{gap_items}）" if not is_en else f" ({gap_label}{gap_items})"
        cb_hdr = "**Confidence breakdown**" if is_en else "**可信度拆解**"
        lines.append(f"{cb_hdr}: {cb_line}")

    lines += ["", f"{reason_hdr}: {card['failure_reason']}", ""]
    if card.get("selected_gate"):
        lines += [f"> {gate_hdr}: `{card['selected_gate']}` · {report_line}", ""]

    lines += [
        cat_hdr,
        "",
        f"| {cat_cols[0]} | {cat_cols[1]} | {cat_cols[2]} |",
        "|---|---|---|",
    ]
    for c in card["four_categories"]:
        label = c.get("label_en") if is_en else c["label"]
        status = c.get("status_label_en") if is_en else c["status_label"]
        reason = c.get("reason_en") if is_en else c["reason"]
        lines.append(f"| `{c['category']}` {label} | **{status}** | {reason} |")
    lines.append("")

    lines += [
        sub_hdr,
        "",
        f"| {sub_cols[0]} | {sub_cols[1]} | {sub_cols[2]} | {sub_cols[3]} | {sub_cols[4]} |",
        "|---|---|---|---|---|",
    ]
    for s in card["subcategories"]:
        conf_s = (
            f"{float(s['confidence']):.2f}"
            if isinstance(s.get("confidence"), (int, float))
            else "—"
        )
        lines.append(
            f"| {role_labels.get(s['role'], s['role'])} | "
            f"{s.get('subtype_label_en') if is_en else s['subtype_label']} | "
            f"`{s.get('code') or '—'}` | {conf_s} | "
            f"{s.get('summary') if is_en else s.get('summary_zh') or s.get('summary')} |"
        )
    lines.append("")

    if card.get("named_failed_checks") or card.get("scientific_observations"):
        ev_tmp = {
            "verifier_report": {"failed_checks": card.get("named_failed_checks") or []},
            "scientific_observations": card.get("scientific_observations") or [],
        }
        matrix = render_error_matrix_table(ev_tmp, lang=lang)
        if matrix:
            lines += [checks_hdr + ":"]
            lines.extend(matrix)
            lines.append("")
        elif card.get("named_failed_checks"):
            lines += [checks_hdr + ":"]
            for c in card["named_failed_checks"]:
                lines.append(f"- `[{c.get('layer') or 'L?'}] {c.get('name')}` — {c.get('detail')}")
            lines.append("")

    if card.get("causal_anchors"):
        from trace_causal_anchors import render_causal_anchors_markdown

        lines.extend(render_causal_anchors_markdown(card["causal_anchors"], lang=lang))

    if card.get("evidence_refs"):
        refs = ", ".join(f"`{r}`" for r in card["evidence_refs"])
        lines += [f"{ev_hdr}: {refs}", ""]
    if card.get("recommended_actions"):
        lines += [act_hdr + ":"]
        for a in card["recommended_actions"]:
            text = a.get("action") if is_en else a.get("action_zh") or a.get("action")
            lines.append(f"- **[{a.get('owner')}]** {text}")
        lines.append("")
    return "\n".join(lines)
