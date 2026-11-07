#!/usr/bin/env python3
"""
Report style, terminology and titles (`scripts/report_style.py`)

Single source of truth for user-facing wording. Renderers
(`render_report.py`, `render_verdict.py`) and the validator
(`validate_analysis.py`) all import from here so headings cannot drift apart.

Three rules the wording follows:

- **Accurate**: no overstated certainty (`attribution`, not "internal root
  cause"), no internal enum values leaking into prose, no field names used as
  headings, consistent Case/Trial terminology.
- **Readable**: verifier JSON envelopes are stripped from prose and shown once
  in a bounded code block instead of being repeated in five places.
- **Parallel**: zh / en / bilingual editions use the same heading structure,
  with the English gloss only in the bilingual edition.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

# ── Prose cleaning ──────────────────────────────────────────────────────────

#: A verifier envelope embedded in an otherwise human-readable sentence.
_EMBEDDED_JSON_RE = re.compile(r"[:：]\s*\{", re.MULTILINE)
_JSON_MESSAGE_RE = re.compile(
    r'"(?:message|summary|detail|reason)"\s*:\s*"((?:[^"\\]|\\.)*)"', re.IGNORECASE
)


def extract_verifier_message(value: Any, limit: int = 300) -> str:
    """
    Pull the human-readable `message` out of a raw verifier JSON payload.

    Harbor verifiers often dump their whole envelope into `verify.log`. Showing
    that blob is unreadable; the `message` field is the actual failure text.
    Returns "" when the input is not a JSON envelope.
    """
    text = str(value or "")
    if "{" not in text:
        return ""
    m = _JSON_MESSAGE_RE.search(text)
    if not m:
        return ""
    message = m.group(1).replace('\\"', '"').replace("\\n", " ").replace("\\t", " ")
    return " ".join(message.split())[:limit]


def clean_prose(value: Any, limit: int = 260) -> str:
    """
    Make an analysis sentence fit for a report.

    - Drops an embedded JSON envelope but keeps the readable prefix (and, when
      the prefix is only a label, uses the envelope's `message` instead).
    - Collapses whitespace, escapes Markdown table pipes, and bounds length at
      a clause boundary so tables and list items stay narrow.
    """
    text = " ".join(str(value or "").split())
    if not text:
        return "—"

    brace = _EMBEDDED_JSON_RE.search(text)
    if brace is None and text.lstrip().startswith("{"):
        extracted = extract_verifier_message(text, limit)
        text = extracted or "(raw verifier payload omitted)"
    elif brace is not None:
        prefix = text[: brace.start()].rstrip(" :;,")
        extracted = extract_verifier_message(text[brace.start() :], limit)
        if prefix and len(prefix) >= 12:
            text = prefix
        elif extracted:
            text = extracted
        else:
            text = prefix or "(raw verifier payload omitted)"

    text = text.replace("|", "\\|")
    if len(text) > limit:
        cut = text[: limit - 1]
        # Prefer a clause boundary, then a word boundary.
        boundary = max(cut.rfind("; "), cut.rfind(". "), cut.rfind(", "))
        if boundary > limit // 2:
            cut = cut[: boundary + 1]
        else:
            cut = cut.rsplit(" ", 1)[0]
        text = cut.rstrip(" ,;:.") + "…"
    return text or "—"


def clean_code_line(value: Any, limit: int = 200) -> str:
    """
    Single line for a fenced code block; never dumps a whole JSON envelope.

    Returns "" when the input is a JSON envelope with no human-readable
    `message`, so callers can skip the block instead of printing a placeholder.
    """
    raw = " ".join(str(value or "").split())
    if not raw:
        return ""
    extracted = extract_verifier_message(raw, limit)
    if extracted:
        return extracted[:limit]
    if raw.lstrip().startswith("{") or _EMBEDDED_JSON_RE.search(raw):
        return ""
    return raw[:limit]


# ── Canonical section titles ───────────────────────────────────────────────

#: (number, Chinese title, English title). Failure/errored edition.
FAILURE_SECTIONS = (
    (1, "归因结论", "Root Cause Verdict"),
    (2, "失败证据链", "Failure Evidence Chain"),
    (3, "假设检验与排除", "Hypothesis Audit"),
    (4, "改进建议", "Recommended Actions"),
)

#: (number, Chinese title, English title). Passed edition (lightweight).
PASSED_SECTIONS = (
    (1, "通过结论", "Verdict & Pass Summary"),
    (2, "验证证据", "Verification Evidence"),
)

#: Older headings kept as aliases so previously generated reports still validate.
LEGACY_SECTIONS = (
    (1, "诊断结论与运行态概览", "Verdict & Root Cause Summary"),
    (2, "故障现场与因果证据链", "Failure Manifestation & Evidence Chain"),
    (3, "竞争假设裁决与伴随信号", "Hypothesis Audit & Signals"),
    (4, "修复行动与处方建议", "Recommended Actions & Notes"),
)
LEGACY_PASSED_SECTIONS = (
    (1, "评测裁定与通过概览", "Verdict & Pass Summary"),
    (2, "验证证据与产物索引", "Verification Evidence & Artifacts"),
)


def section_title(num: int, lang: str, passed: bool = False) -> str:
    """
    Heading line (without the leading ``## ``) for one section.

    ``lang='zh'`` -> Chinese only, ``lang='en'`` -> English only, otherwise
    ``中文 (English)``. All editions keep the same numbering.
    """
    table = PASSED_SECTIONS if passed else FAILURE_SECTIONS
    zh, en = next((z, e) for n, z, e in table if n == num)
    if lang == "en":
        return f"{num}. {en}"
    if lang == "zh":
        return f"{num}. {zh}"
    return f"{num}. {zh} ({en})"


def accepted_titles(num: int, passed: bool = False) -> tuple[str, ...]:
    """All Chinese/English titles the validator accepts for a section number."""
    current = PASSED_SECTIONS if passed else FAILURE_SECTIONS
    legacy = LEGACY_PASSED_SECTIONS if passed else LEGACY_SECTIONS
    out: list[str] = []
    for table in (current, legacy):
        for n, zh, en in table:
            if n == num:
                out.extend((zh, en, f"{zh} ({en})"))
    return tuple(dict.fromkeys(out))


# ── Terminology ────────────────────────────────────────────────────────────

CATEGORY_LABELS_ZH = {
    "agent": "智能体决策与执行",
    "verifier": "验证器规则与判定",
    "case": "基准题目与规格设计",
    "infra": "运行环境与基础设施",
    "unknown": "信息缺失，无法归因",
    "none": "无失败",
}
CATEGORY_LABELS_EN = {
    "agent": "Agent decisions & execution",
    "verifier": "Verifier rules & scoring",
    "case": "Case & task design",
    "infra": "Runtime & infrastructure",
    "unknown": "Insufficient evidence",
    "none": "No failure",
}

STAGE_LABELS_ZH = {
    "environment_build": "环境构建",
    "environment_runtime": "运行环境",
    "agent_setup": "Agent 准备",
    "agent_execution": "Agent 执行",
    "runner": "Runner 产物采集",
    "verifier_execution": "验证器执行",
    "unknown": "未定位",
    "none": "无",
}
STAGE_LABELS_EN = {
    "environment_build": "Environment build",
    "environment_runtime": "Environment runtime",
    "agent_setup": "Agent setup",
    "agent_execution": "Agent execution",
    "runner": "Runner collection",
    "verifier_execution": "Verifier execution",
    "unknown": "Unknown",
    "none": "None",
}

#: Short human label for each subtype; the phrase is a full Chinese sentence
#: fragment used when the report needs prose instead of an enum value.
SUBTYPE_LABELS = {
    "result_validation": ("结果未复核", "Result not validated"),
    "self_check_gap": ("自检覆盖不足", "Self-verification coverage gap"),
    "task_understanding": ("任务理解偏差", "Task misunderstood"),
    "hidden_contract": ("隐含契约未满足", "Hidden contract unmet"),
    "schema_mismatch": ("产物结构不符", "Output schema mismatch"),
    "scientific_parameter_selection": ("科学参数选择错误", "Wrong parameter choice"),
    "scientific_method_selection": ("科学方法选择错误", "Wrong method choice"),
    "scientific_constant_error": ("物理常数误用", "Wrong physical constant"),
    "scientific_notation_missing_d_exponent": ("科学计数法缺 D 指数", "Missing D exponent"),
    "calculation_error": ("计算实现错误", "Calculation error"),
    "calc_error": ("计算实现错误", "Calculation error"),
    "recompute_defect": ("复算实现缺陷", "Recompute defect"),
    "wrong_reasoning": ("推理错误", "Wrong reasoning"),
    "planning": ("规划不当", "Poor planning"),
    "bad_plan": ("规划不当", "Poor planning"),
    "tool_use": ("工具使用错误", "Tool misuse"),
    "error_diagnosis": ("错误诊断不当", "Poor error diagnosis"),
    "path_or_dependency_discovery": ("依赖/路径探索不足", "Dependency discovery"),
    "premature_termination": ("过早终止", "Premature termination"),
    "agent_timeout": ("Agent 超时", "Agent timeout"),
    "tolerance_too_strict": ("容差过严", "Tolerance too strict"),
    "regex_or_parser_defect": ("解析/正则缺陷", "Parser or regex defect"),
    "namelist_slash_truncation": ("输入文件截断", "Input file truncation"),
    "implicit_thermo_column_index": ("热力学列序假设错误", "Wrong thermo column index"),
    "instantaneous_trajectory_rmsd": ("瞬时构型 RMSD 误用", "Instantaneous RMSD misuse"),
    "missing_asset": ("任务资产缺失", "Missing task asset"),
    "ambiguous_contract": ("规格歧义", "Ambiguous specification"),
    "container_build": ("容器构建失败", "Container build failure"),
    "container_setup_failure": ("容器初始化失败", "Container setup failure"),
    "container_runtime": ("容器运行时异常", "Container runtime failure"),
    "container_crash": ("容器崩溃", "Container crash"),
    "oom": ("内存耗尽", "Out of memory"),
    "external_network": ("外部网络故障", "External network failure"),
    "artifact_collection_crash": ("产物采集崩溃", "Artifact collection crashed"),
    "runner_interrupted": ("Runner 静默中断", "Runner interrupted"),
    "deliverable_not_written": ("交付物未生成", "Deliverable not written"),
    "insufficient_evidence": ("证据不足", "Insufficient evidence"),
    "none": ("无", "None"),
}

#: One-line Chinese/English explanation of what the subtype means in practice.
SUBTYPE_PHRASES = {
    ("agent", "result_validation"): (
        "结果产物未经数值自检即写入，验证器独立复算后检出偏差",
        "results were written without a numerical self-check, and independent recomputation detected the deviation",
    ),
    ("agent", "self_check_gap"): (
        "智能体执行了自检但覆盖口径存在盲区，未覆盖验证器全部判定准则",
        "the agent ran self-verification but its criteria had coverage gaps and missed verifier rules",
    ),
    ("agent", "task_understanding"): (
        "对题目要求或科学约定的理解与规格不一致",
        "the agent's reading of the task or scientific convention diverged from the specification",
    ),
    ("agent", "hidden_contract"): (
        "数据隐含契约（列序、单位、符号约定）未被满足",
        "an implicit data contract (column order, units, sign convention) was not satisfied",
    ),
    ("agent", "schema_mismatch"): (
        "输出文件的结构或字段与验证器契约不符",
        "the output structure or fields did not match the verifier contract",
    ),
    ("agent", "scientific_parameter_selection"): (
        "选用了错误的科学参数（温度、截断、泛函或系综等）",
        "a wrong scientific parameter was chosen (temperature, cutoff, functional, ensemble, …)",
    ),
    ("agent", "scientific_method_selection"): (
        "选取了不适用于该体系的科学方法",
        "a method unsuitable for the system was selected",
    ),
    ("agent", "scientific_constant_error"): (
        "物理常数取值错误",
        "a physical constant was used with the wrong value",
    ),
    ("agent", "path_or_dependency_discovery"): (
        "依赖或数据路径未充分探索即下结论",
        "dependencies or data paths were not explored before concluding",
    ),
    ("agent", "premature_termination"): (
        "在产物完备前终止执行",
        "execution stopped before the deliverables were complete",
    ),
    ("infra", "artifact_collection_crash"): (
        "Runner 在采集产物阶段中止，试次未进入验证",
        "the runner aborted while collecting artifacts, so the trial never reached verification",
    ),
    ("infra", "premature_termination"): (
        "Runner 异常中断，试次未进入验证",
        "the runner was interrupted, so the trial never reached verification",
    ),
    ("infra", "agent_timeout"): (
        "Agent 在时限内未完成，试次未进入验证",
        "the agent did not finish within the time limit, so the trial never reached verification",
    ),
    ("infra", "container_runtime"): (
        "容器运行时异常中断",
        "the container runtime failed mid-run",
    ),
    ("verifier", "tolerance_too_strict"): (
        "容差设置严于题目规格或参考实现",
        "the tolerance is stricter than the task specification or the reference implementation",
    ),
    ("verifier", "regex_or_parser_defect"): (
        "验证器的解析或正则匹配存在缺陷",
        "the verifier's parsing or regex matching is defective",
    ),
    ("case", "missing_asset"): (
        "题目资产缺失，正常执行也会失败",
        "a task asset is missing, so a correct run would also fail",
    ),
    ("case", "ambiguous_contract"): (
        "题目规格存在歧义，无法唯一判定通过",
        "the specification is ambiguous and cannot be judged uniquely",
    ),
    ("unknown", "insufficient_evidence"): (
        "现有日志与产物不足以唯一定位根因",
        "the available logs and artifacts cannot isolate a single root cause",
    ),
}

EVIDENCE_STRENGTH_LABELS = {
    "high": ("高", "High"),
    "medium": ("中", "Medium"),
    "low": ("低", "Low"),
}

CONFIDENCE_KIND_LABELS = {
    "heuristic_evidence_score": ("启发式证据评分", "Heuristic evidence score"),
    "calibrated_evidence_score": ("校准证据评分", "Calibrated evidence score"),
}

VERDICT_LABELS = {
    "passed": ("通过", "Passed"),
    "failed": ("未通过", "Failed"),
    "errored": ("执行异常", "Errored"),
    "unknown": ("未评分", "Unknown"),
}

SEVERITY_LABELS = {
    "warning": ("警告", "warning"),
    "error": ("错误", "error"),
    "info": ("提示", "info"),
}


def stage_label(stage: Any, lang: str = "zh", with_code: bool = True) -> str:
    """`agent_execution` -> `Agent 执行 (`agent_execution`)`."""
    key = str(stage or "unknown")
    table = STAGE_LABELS_EN if lang == "en" else STAGE_LABELS_ZH
    label = table.get(key, key)
    return f"{label} (`{key}`)" if with_code else label


def evidence_strength_label(value: Any, lang: str = "zh") -> str:
    pair = EVIDENCE_STRENGTH_LABELS.get(str(value or "").lower())
    if pair:
        return pair[1 if lang == "en" else 0]
    return str(value or "—")


def confidence_kind_label(value: Any, lang: str = "zh") -> str:
    pair = CONFIDENCE_KIND_LABELS.get(str(value or ""))
    if pair:
        return pair[1 if lang == "en" else 0]
    return str(value or "—")


def category_label(category: Any, lang: str = "zh") -> str:
    table = CATEGORY_LABELS_EN if lang == "en" else CATEGORY_LABELS_ZH
    key = str(category or "unknown")
    return table.get(key, key)


def verdict_label(verdict: Any, lang: str = "zh") -> Optional[str]:
    pair = VERDICT_LABELS.get(str(verdict or ""))
    return pair[1 if lang == "en" else 0] if pair else None


def severity_label(value: Any, lang: str = "zh") -> str:
    pair = SEVERITY_LABELS.get(str(value or "").lower())
    return pair[1 if lang == "en" else 0] if pair else str(value or "—")


def subtype_label(subtype: Any, lang: str = "zh") -> str:
    pair = SUBTYPE_LABELS.get(str(subtype or ""))
    if pair:
        return pair[1 if lang == "en" else 0]
    return str(subtype or "—")


def cause_narrative(category: Any, subtype: Any, lang: str = "zh") -> Optional[str]:
    """One-sentence explanation of a (category, subtype) pair, or None."""
    key = (str(category or ""), str(subtype or ""))
    pair = SUBTYPE_PHRASES.get(key)
    if pair is None:
        pair = SUBTYPE_PHRASES.get(("agent", key[1])) or SUBTYPE_PHRASES.get(("infra", key[1]))
    if pair is None:
        return None
    return pair[1 if lang == "en" else 0]


#: Concrete next action per (category, subtype). Used when an analysis action
#: merely restates the cause instead of prescribing a fix.
SUBTYPE_REMEDIATIONS = {
    ("agent", "result_validation"): (
        "交付前对关键数值做独立复算，并核对量纲、符号与守恒关系是否和验证口径一致",
        "Independently recompute the key values and check dimensions, signs, and conservation "
        "against the verifier's definition before delivering final deliverables",
    ),
    ("agent", "self_check_gap"): (
        "在交付前对齐验证标准，将独立复算与全套判定规则纳入自检脚本",
        "Align validation criteria with task requirements and include independent recomputation in self-checks",
    ),
    ("agent", "schema_mismatch"): (
        "核对输出产物的字段结构、数组维度、稀疏表示及参考位点/记录 ID 映射，确保与题目契约一致",
        "Verify output fields, array shapes, sparse/dense conventions, and reference IDs against the task contract before submission",
    ),
    ("agent", "task_understanding"): (
        "重新对齐题目要求与交付物契约，逐条确认显式要求都已落到产物中",
        "Re-read the instruction and deliverable contract, and map every explicit requirement "
        "to a produced artifact",
    ),
    ("agent", "premature_termination"): (
        "结束前逐一确认声明的交付物存在且通过基本自检",
        "Verify that every declared deliverable exists and passes a basic sanity check before finishing",
    ),
    ("agent", "error_diagnosis"): (
        "从日志的首个错误向前定位责任输入或命令，修复根因而不是重复同一操作",
        "Trace the first error in the log back to the responsible input or command and fix that "
        "cause instead of retrying the same action",
    ),
    ("agent", "path_or_dependency_discovery"): (
        "执行前先探测所需可执行文件与数据路径，并使用经过验证的绝对路径",
        "Probe the environment for the required executables/modules and use verified absolute paths",
    ),
    ("agent", "scientific_parameter_selection"): (
        "依据题目规格确定参数，并在正式计算前做收敛性或截断敏感性检查",
        "Derive parameters from the task specification and check convergence or cutoff sensitivity "
        "before production runs",
    ),
    ("agent", "scientific_method_selection"): (
        "对照体系与目标量论证方法选择，并用参考值或第二种方法交叉验证",
        "Justify the method against the system and target observable, and cross-check with a "
        "reference value or a second method",
    ),
    ("agent", "scientific_constant_error"): (
        "统一使用题目给定或权威来源的常数取值，并在代码中集中常量化",
        "Use the constants given by the task or an authoritative source, defined once in code",
    ),
    ("agent", "planning"): (
        "把任务拆成带检查点的显式步骤，避免边做边改的临时流程",
        "Decompose the task into explicit steps with checkpoints instead of improvising the workflow",
    ),
    ("agent", "tool_use"): (
        "校验工具调用语法与返回码，显式处理失败分支",
        "Validate tool invocation syntax and return codes, and handle failure branches explicitly",
    ),
}


def remediation(category: Any, subtype: Any, lang: str = "zh") -> str:
    """Concrete next action for a (category, subtype) pair, or ``""``."""
    pair = SUBTYPE_REMEDIATIONS.get((str(category or ""), str(subtype or "")))
    if pair is None:
        return ""
    return pair[1 if lang == "en" else 0]


# ── Localizing analysis prose ──────────────────────────────────────────────

#: The attribution engine writes its reasoning in English. Reports are often
#: Chinese, so the renderer translates the known sentence templates and keeps
#: any trailing evidence excerpt (after ": ") verbatim. Unknown sentences are
#: returned unchanged instead of being reworded or dropped.
_ENGINE_TEXT_ZH = (
    (
        "No failure occurred; trial completed and passed verification (reward >= 1.0).",
        "未发生失败；试次完成并通过验证（reward ≥ 1.0）。",
    ),
    (
        "Trial completed and passed all verifier checks (reward=1.0).",
        "试次完成并通过全部验证器检查（reward=1.0）。",
    ),
    (
        "Agent did not start and no exception or container build logs are available to pinpoint the failure.",
        "Agent 未启动，且没有异常记录或容器构建日志可用于定位失败点。",
    ),
    (
        "Agent did not start and no exception or build logs are available.",
        "Agent 未启动，且没有异常记录或构建日志。",
    ),
    (
        "Insufficient log artifacts to determine why execution terminated prior to agent startup.",
        "日志产物不足，无法确定执行为何在 Agent 启动前终止。",
    ),
    (
        "No unrecovered agent decision or deterministic infrastructure/verifier defect could be isolated from available artifacts.",
        "现有产物无法定位出未恢复的 Agent 决策错误，或确定性的基础设施/验证器缺陷。",
    ),
    (
        "Available logs and artifacts cannot deterministically isolate a single root cause.",
        "现有日志与产物不足以唯一定位根因。",
    ),
    (
        "Agent decision, input parameter, or missing output violated explicit task requirements.",
        "Agent 的决策、输入参数或产物缺失违反了任务显式要求。",
    ),
    (
        "Infrastructure prevented simulation execution.",
        "基础设施故障阻止了模拟执行。",
    ),
    (
        "Benchmark case did not fix RNG seeds or specify ensemble statistical verification bounds.",
        "基准题目未固定随机种子，也未给出系综统计的验证边界。",
    ),
    (
        "Lyapunov-sensitive MD trajectory divergence while ensemble statistics remained consistent.",
        "MD 轨迹因 Lyapunov 敏感性发散，但系综统计量仍保持一致。",
    ),
    (
        "Verifier compared instantaneous late-step coordinates/energies rather than ensemble averages despite physical/numerical chaos.",
        "在物理/数值混沌的前提下，验证器比较的是末步瞬时构型/能量而非系综平均。",
    ),
    (
        "Prompt did not explicitly constrain input/log syntax formatting assumed by `verify.py`.",
        "题目未显式约束 `verify.py` 所假设的输入/日志语法格式。",
    ),
    (
        "Agent wrote results.json without sanity validation, producing inaccurate physical/numerical values",
        "Agent 将未经合理性校验的结果写入 `results.json`，数值与独立复算不符",
    ),
    (
        "Agent misunderstood task specification or physical conventions (e.g. data fitting window)",
        "Agent 误解了题目规格或物理约定（如数据拟合窗口）",
    ),
    (
        "Agent did not leave the declared deliverable(s) in the container",
        "Agent 未将声明的交付物留在容器中",
    ),
    (
        "Downstream runner artifact collection also failed",
        "下游 Runner 产物采集同样失败",
    ),
    (
        "Co-occurring verifier defect observed during verification",
        "验证阶段同时检出了验证器缺陷",
    ),
    (
        "Accompanying infrastructure error",
        "伴随的基础设施错误",
    ),
    (
        "Underlying method selection discrepancy",
        "底层方法选择偏差",
    ),
    (
        "Underlying simulation parameter discrepancy",
        "底层模拟参数偏差",
    ),
    (
        "Underlying deliverable schema/reference mismatch",
        "底层产物结构或引用 ID 不匹配",
    ),
    (
        "Causal anchor thought records an explicit cutoff/convergence parameter tradeoff prior to generating the diverging metric.",
        "因果锚点思考记录显示 Agent 在生成偏差指标前曾对截断半径/收敛参数做过显式权衡取舍。",
    ),
    (
        "Agent self-check omitted benchmark-mandated validation criteria or independent recomputation.",
        "Agent 自检脚本遗漏了基准要求的验证准则或独立复算校验。",
    ),
    (
        "Environment build/setup failed",
        "环境构建/初始化失败",
    ),
    (
        "Clarify specification in instruction.md",
        "在 `instruction.md` 中澄清规格",
    ),
    (
        "Container built cleanly, agent executed commands (`runtime.agent_started == true`), and verifier ran to completion.",
        "容器构建正常、Agent 已执行命令，且验证器已完成判定。",
    ),
    (
        "Container built cleanly and agent executed commands (`runtime.agent_started == true`).",
        "容器构建正常且 Agent 已执行命令。",
    ),
    (
        "verifier reported a non-pass outcome without naming a failed check",
        "验证器给出了未通过结论，但未指明具体失败的检查项",
    ),
    (
        "failure reason is a terse generic message with no named check or value",
        "失败原因只有一句笼统描述，既无具名检查也无具体数值",
    ),
    (
        "failed checks carry no detail text",
        "失败检查没有给出详细说明",
    ),
    (
        "multiple failed checks share one terse message",
        "多项失败检查共用同一条简短消息",
    ),
    (
        "failed check details contain no numerical deviation",
        "失败检查的说明中不含数值偏差量",
    ),
)


_AGENT_WROTE_RE = re.compile(
    r"^Agent wrote (?P<target>`?[^`\s]+`?) without sanity validation, producing inaccurate physical/numerical values"
)

_RE_DEV_1 = re.compile(
    r"^Agent delivered '(?P<metric>[^']+)' = (?P<val>[^\s,(]+)"
    r"(?:\s*\(?read from `(?P<ref>[^`]+)`\)?)?"
    r", which deviates from the verifier's independent reference/replay by (?P<delta>[^\s;]+)"
    r" beyond its tolerance bound (?P<tol>[^;]+);"
    r" the delivered result itself is inaccurate\.",
    re.IGNORECASE,
)

_RE_DEV_1B = re.compile(
    r"^Agent delivered '(?P<metric>[^']+)' = (?P<val>[^\s,(]+)"
    r"(?:\s*\(?read from `(?P<ref>[^`]+)`\)?)?"
    r"; the verifier's independent reference/replay deviates by (?P<delta>[^\s;]+)"
    r" beyond its tolerance bound (?P<tol>[^\s;,]+)"
    r", so the agent's parameter/scientific-method selection is at fault\.",
    re.IGNORECASE,
)

_RE_DEV_1C = re.compile(
    r"^Agent delivered '(?P<metric>[^']+)' = (?P<val>[^\s,(]+)"
    r"(?:\s*\(?read from `(?P<ref>[^`]+)`\)?)?"
    r"; the verifier's independent replay diverged by (?P<delta>[^\s;]+)"
    r" beyond its tolerance bound (?P<tol>[^\s;,]+?)\.?$",
    re.IGNORECASE,
)

_RE_DEV_1D = re.compile(
    r"^Agent's delivered '(?P<metric>[^']+)' value"
    r"(?:\s*\(?read from `(?P<ref>[^`]+)`\)?)?"
    r", which deviates from the verifier's independent reference/replay by (?P<delta>[^\s;]+)"
    r" beyond its tolerance bound (?P<tol>[^;]+);"
    r" the delivered result itself is inaccurate\.",
    re.IGNORECASE,
)

_RE_RECOMP_1 = re.compile(
    r"^Agent delivered a '(?P<metric>[^']+)' result value that fails the verifier's independent recomputation"
    r" \(reported (?P<rep>[^\s!=]+) != recomputed (?P<rec>[^\s,]+), tol (?P<tol>[^\s)]+)\);"
    r" (?P<corrob>[^,;]+), so the delivered result itself is wrong\.",
    re.IGNORECASE,
)

_RE_RECOMP_1B = re.compile(
    r"^Agent's selected '(?P<metric>[^']+)' fails the verifier's independent recomputation/reference comparison"
    r" \(reported (?P<rep>[^\s!=]+) != recomputed (?P<rec>[^\s,]+), tol (?P<tol>[^\s)]+)\);"
    r" (?P<corrob>[^,;]+), so the agent's parameter/scientific-method selection is at fault\.",
    re.IGNORECASE,
)

_RE_FAILING_CHECK_NAMES = re.compile(
    r"^The failing check names the record and metric with a declared numeric bound"
    r"(?:, and the agent's delivered value is independently located at `(?P<ref>[^`]+)`)?"
    r"\. This is positive evidence of a delivered-value error, not of a verifier recomputation defect\.",
    re.IGNORECASE,
)

_RE_REDERIVE_1 = re.compile(
    r"^Re-derive '(?P<metric>[^']+)' independently and reconcile the delivered value (?P<val>[^\s(]+)"
    r" with the verifier's replay before writing results"
    r"(?:\s*\(read from `(?P<ref>[^`]+)`\))?\.",
    re.IGNORECASE,
)

_RE_REDERIVE_1B = re.compile(
    r"^Re-derive and sanity-check the reported '(?P<metric>[^']+)' against an independent reference calculation before writing deliverables\.",
    re.IGNORECASE,
)

_RE_SELF_CHECK = re.compile(
    r"^Agent ran self-verification"
    r"(?: at `(?P<ev>[^`]+)`)?"
    r"(?P<status> and reported success| without a pass marker)?"
    r", but (?P<gap>[^;]+); verifier rejected submitted outputs:(?P<tail>.*)$",
    re.IGNORECASE,
)

_RE_SELF_CHECK_GUIDANCE_1 = re.compile(
    r"^Include all task specification criteria and independent ground-truth checks in pre-submission validation\.",
    re.IGNORECASE,
)
_RE_SELF_CHECK_GUIDANCE_2 = re.compile(
    r"^Explicitly reproduce the verifier's named checks before submission:\s*(?P<checks>.*)\.",
    re.IGNORECASE,
)

_RE_SCHEMA_MISMATCH = re.compile(
    r"^Agent delivered (?P<target>[^\s]+) with a structural, representation, or reference-ID mismatch against the verifier contract(?::\s*(?P<tail>.*)|\.)?$",
    re.IGNORECASE,
)

_RE_SOFTWARE_EMITTED = re.compile(
    r"^Software emitted `(?P<fam>[^`]+)` \((?P<sw>[^)]+)\), but agent failed to diagnose the physical/input cause and repeatedly re-executed the failing setup \(`(?P<sig>[^`]+)`\)\.",
    re.IGNORECASE,
)

_RE_SIM_FAILED = re.compile(
    r"^Simulation failed with `(?P<fam>[^`]+)` \((?P<sw>[^)]+)\) because agent did not search container directories \(`(?P<dirs>[^`]+)`\) or resolve model/potential/force-field paths\.",
    re.IGNORECASE,
)

_RE_SIM_CONTINUATION = re.compile(
    r"^Simulation continuation failed `(?P<fam>[^`]+)` \((?P<sw>[^)]+)\) due to altered state/parameter continuation settings \((?P<causes>[^)]+)\)\.",
    re.IGNORECASE,
)

_RE_SCHEMA_REQ_FAIL = re.compile(
    r"^Agent failed to satisfy explicit output file or JSON schema requirements \(`(?P<detail>[^`]+)`\)\.",
    re.IGNORECASE,
)

_RE_HARBOR_KILLED = re.compile(
    r"^Harbor killed the agent at its configured execution timeout before the declared deliverables were complete \(`(?P<txt>[^`]+)`\); (?P<note>.*)$",
    re.IGNORECASE,
)

_RE_RUNNER_ABORTED = re.compile(
    r"^Runner aborted while collecting the agent's declared deliverables \(`(?P<code>[^`]+)`, stage=runner, post-agent\); (?P<note>.*)$",
    re.IGNORECASE,
)


def localize_engine_text(value: Any, lang: str = "zh") -> str:
    """
    Translate a known analysis sentence into Chinese, keeping any ``: evidence``
    tail untouched. ``lang='en'`` and unknown sentences pass through unchanged.
    """
    text = " ".join(str(value or "").split())
    if not text or lang == "en":
        return text

    m_wrote = _AGENT_WROTE_RE.match(text)
    if m_wrote:
        target = m_wrote.group("target")
        if not target.startswith("`"):
            target = f"`{target}`"
        tail = text[m_wrote.end() :]
        if tail.startswith(": "):
            tail = "：" + tail[2:]
        return f"Agent 将未经合理性校验的结果写入 {target}，数值与独立复算不符{tail}".strip()

    m = _RE_DEV_1.match(text)
    if m:
        ref_clause = f"（读取自 `{m.group('ref')}`）" if m.group("ref") else ""
        return f"Agent 交付的 '{m.group('metric')}' = {m.group('val')}{ref_clause}与验证器独立复算/重放结果相差 {m.group('delta')}，超出容差上限 {m.group('tol')}，交付数值本身不准确。"

    m = _RE_DEV_1B.match(text)
    if m:
        ref_clause = f"（读取自 `{m.group('ref')}`）" if m.group("ref") else ""
        return f"Agent 交付的 '{m.group('metric')}' = {m.group('val')}{ref_clause}，验证器独立参考/重放检出偏差 {m.group('delta')}（超出容差上限 {m.group('tol')}），责任在 Agent 参数/科学方法选择。"

    m = _RE_DEV_1C.match(text)
    if m:
        ref_clause = f"（读取自 `{m.group('ref')}`）" if m.group("ref") else ""
        return f"Agent 交付的 '{m.group('metric')}' = {m.group('val')}{ref_clause}，验证器独立重放检出偏差 {m.group('delta')}（超出容差 {m.group('tol')}）。"

    m = _RE_DEV_1D.match(text)
    if m:
        return f"Agent 交付的 '{m.group('metric')}' 数值与验证器独立复算/重放结果相差 {m.group('delta')}，超出容差上限 {m.group('tol')}，交付数值本身不准确。"

    m = _RE_RECOMP_1.match(text)
    if m:
        corrob = m.group("corrob")
        if "records the same value" in corrob:
            m_sub = re.search(r"records the same value \(([^)]+)\)", corrob)
            val = m_sub.group(1) if m_sub else ""
            corrob_zh = f"Agent 自身执行输出亦记录了相同数值 ({val})"
        elif "independently recomputed" in corrob:
            corrob_zh = "验证器独立复算了该指标"
        else:
            corrob_zh = corrob
        return f"Agent 交付的 '{m.group('metric')}' 结果值未能通过验证器独立复算（交付值 {m.group('rep')} != 复算值 {m.group('rec')}，容差 {m.group('tol')}）；{corrob_zh}，交付结果本身有误。"

    m = _RE_RECOMP_1B.match(text)
    if m:
        corrob = m.group("corrob")
        if "records the same value" in corrob:
            m_sub = re.search(r"records the same value \(([^)]+)\)", corrob)
            val = m_sub.group(1) if m_sub else ""
            corrob_zh = f"Agent 自身执行输出亦记录了相同数值 ({val})"
        elif "independently recomputed" in corrob:
            corrob_zh = "验证器独立复算了该指标"
        else:
            corrob_zh = corrob
        return f"Agent 选择的 '{m.group('metric')}' 未能通过验证器独立复算/参考对比（交付值 {m.group('rep')} != 复算值 {m.group('rec')}，容差 {m.group('tol')}）；{corrob_zh}，责任在 Agent 参数/科学方法选择。"

    m = _RE_FAILING_CHECK_NAMES.match(text)
    if m:
        ref = m.group("ref")
        if ref:
            return f"失败检查明确指出了记录标识、指标名称与数值容差，且在产物 `{ref}` 中独立定位到了 Agent 的交付值；这属于交付数值错误的直接证据，而非验证器复算缺陷。"
        return "失败检查明确指出了记录标识、指标名称与数值容差；这属于交付数值错误的直接证据，而非验证器复算缺陷。"

    m = _RE_REDERIVE_1.match(text)
    if m:
        ref = m.group("ref")
        if ref:
            return f"交付前请独立重新推导 '{m.group('metric')}'，并将产物中的交付值 {m.group('val')}（位于 `{ref}`）与验证器复算口径对齐核验。"
        return f"交付前请独立重新推导 '{m.group('metric')}'，并将产物中的交付值 {m.group('val')} 与验证器复算口径对齐核验。"

    m = _RE_REDERIVE_1B.match(text)
    if m:
        return f"交付产物前请独立重新推导并合理性核验汇报的 '{m.group('metric')}'。"

    m = _RE_SELF_CHECK.match(text)
    if m:
        anchor_ev = m.group("ev")
        status = m.group("status") or ""
        if "and reported success" in status:
            status_zh = "并报告通过"
        elif "without a pass marker" in status:
            status_zh = "但未带有通过标识"
        else:
            status_zh = ""
        prefix_zh = (
            f"Agent 在 `{anchor_ev}` 执行了自检{status_zh}" if anchor_ev else "Agent 执行了自检"
        )

        gap = m.group("gap").strip()
        m_gap_omits = re.match(
            r"^its script `(?P<scr>[^`]+)` omits verifier-required checks:\s*(?P<checks>[^(]+?)(?:\s*\(covered:\s*(?P<cov>[^)]+)\))?$",
            gap,
        )
        if m_gap_omits:
            scr = m_gap_omits.group("scr")
            checks = m_gap_omits.group("checks").strip()
            cov = m_gap_omits.group("cov")
            cov_zh = f"（已覆盖：{cov.strip()}）" if cov else ""
            gap_zh = f"其脚本 `{scr}` 漏检了验证器要求的检查项：{checks}{cov_zh}"
        elif "did not reproduce the verifier's independent recomputation criteria" in gap:
            m_scr = re.search(r"its script `([^`]+)`", gap)
            scr = m_scr.group(1) if m_scr else "自检脚本"
            gap_zh = f"其脚本 `{scr}` 未复现验证器的独立复算准则"
        elif "no self-check script source was captured" in gap:
            gap_zh = "未捕获到自检脚本源码，无法与验证器具名失败检查对齐准则"
        else:
            gap_zh = gap

        tail = m.group("tail").strip()
        return f"{prefix_zh}，但{gap_zh}；验证器最终拒绝了提交产物：{tail}"

    m = _RE_SELF_CHECK_GUIDANCE_1.match(text)
    if m:
        return "交付前请在自检脚本中纳入全部题目规格准则与独立真值比对。"

    m = _RE_SELF_CHECK_GUIDANCE_2.match(text)
    if m:
        return f"提交前请在自检中显式复现验证器的具名检查项：{m.group('checks')}。"

    m = _RE_SCHEMA_MISMATCH.match(text)
    if m:
        target = m.group("target")
        tail = m.group("tail")
        tail_zh = f"：{tail.strip()}" if tail else "。"
        return f"Agent 提交的 {target} 在数据结构、稀疏表示或引用 ID 上与验证器契约不符{tail_zh}"

    m = _RE_SOFTWARE_EMITTED.match(text)
    if m:
        return f"仿真软件抛出 `{m.group('fam')}` ({m.group('sw')})，但 Agent 未能诊断出物理/输入根因，反复重试同一失败配置 (`{m.group('sig')}`)。"

    m = _RE_SIM_FAILED.match(text)
    if m:
        return f"模拟因 `{m.group('fam')}` ({m.group('sw')}) 失败，原因是 Agent 未检索容器目录（`{m.group('dirs')}`），也未解析模型/势函数/力场路径。"

    m = _RE_SIM_CONTINUATION.match(text)
    if m:
        return f"模拟续跑因 `{m.group('fam')}` ({m.group('sw')}) 失败，原因是状态/参数续跑设置发生改变 ({m.group('causes')})。"

    m = _RE_SCHEMA_REQ_FAIL.match(text)
    if m:
        return f"Agent 未能满足显式输出文件或 JSON Schema 契约要求（`{m.group('detail')}`）。"

    m = _RE_HARBOR_KILLED.match(text)
    if m:
        note = m.group("note").strip().rstrip(".")
        if "verifier never started" in note:
            note_zh = "验证器未启动，因此本试次未评分"
        elif "verifier still ran" in note:
            note_zh = "验证器仍然运行并对不完整产物进行了评分"
        else:
            note_zh = note
        return f"Harbor 在达到配置的执行超时上限时强行终止了 Agent，此时声明的交付物尚未完成（`{m.group('txt')}`）；{note_zh}。"

    m = _RE_RUNNER_ABORTED.match(text)
    if m:
        note = m.group("note").strip().rstrip(".")
        if "verifier never started" in note:
            note_zh = "验证器未启动，因此本试次未评分"
        elif "verifier still ran" in note:
            note_zh = "验证器仍然运行并对不完整产物进行了评分"
        else:
            note_zh = note
        return f"Runner 在采集 Agent 声明的交付物阶段中止（`{m.group('code')}`，阶段 runner，执行后）；{note_zh}。"

    for en, zh in _ENGINE_TEXT_ZH:
        if text.startswith(en):
            tail = text[len(en) :]
            if tail.startswith(": "):
                tail = "：" + tail[2:]
            return (zh + tail).strip()
    return text


# ── Confidence & Error Matrix & Evidence helpers ──────────────────────────


def format_confidence_breakdown(
    breakdown: Optional[Dict[str, Any]],
    conf_val: float,
    lang: str = "zh",
) -> Optional[str]:
    """
    Renders a structured Confidence Receipt Bar row for the Section 1 table.
    """
    if not isinstance(breakdown, dict):
        return None
    factors = breakdown.get("factors") or []
    if not factors:
        return None
    points = breakdown.get("points")
    denom = breakdown.get("denominator", 10)
    score_label = f"`{points}/{denom} 分`" if lang != "en" else f"`{points}/{denom} pts`"
    factor_parts = []
    for f in factors:
        delta = f.get("delta", 0)
        lbl = f.get("label_en") if lang == "en" else (f.get("label_zh") or f.get("key"))
        sign = f"+{delta}" if delta > 0 else str(delta)
        icon = "✓" if delta > 0 else ("⚠️" if delta < 0 else "—")
        factor_parts.append(f"{icon} {lbl}({sign})")

    parts_str = " · ".join(factor_parts)
    full_str = f"{score_label} · {parts_str}" if factor_parts else score_label

    missing = breakdown.get("missing_to_raise") or []
    if missing and conf_val < 0.75:
        gap_label = "提分缺口：" if lang != "en" else "Gap to raise: "
        gap_items = "、".join(missing) if lang == "zh" else ", ".join(missing)
        gap_str = f"（{gap_label}{gap_items}）" if lang != "en" else f" ({gap_label}{gap_items})"
        full_str += gap_str

    if lang == "zh":
        return f"| **可信度拆解** | {full_str} |\n"
    elif lang == "en":
        return f"| **Confidence breakdown** | {full_str} |\n"
    else:
        return f"| **可信度拆解 (Confidence breakdown)** | {full_str} |\n"


def format_artifact_comparison(comp: Any, lang: str = "zh") -> str:
    """Concise representation of artifact_comparison / evidence dict or list for error matrix."""
    is_en = lang == "en"
    if isinstance(comp, list):
        dict_items = [x for x in comp if isinstance(x, dict) and "delivered" in x]
        if dict_items:
            first = dict_items[0]
            metric = first.get("metric") or "val"
            dlv = first.get("delivered")
            rec = first.get("recomputed")
            ref = first.get("artifact_ref") or ""
            dlv_s = f"{dlv:.11g}" if isinstance(dlv, float) else str(dlv)
            rec_s = f"{rec:.11g}" if isinstance(rec, float) else str(rec)
            extra = len(dict_items) - 1
            more = (
                (f" (+{extra} more)" if is_en else f" (等共 {len(dict_items)} 项)")
                if extra > 0
                else ""
            )
            ref_s = f", {ref}" if ref else ""
            return f"{metric}: {dlv_s} vs {rec_s}{more}{ref_s}"
        return clean_prose(", ".join(str(x) for x in comp[:3]), 120)
    if not isinstance(comp, dict):
        return clean_prose(comp, 120)
    parts = []
    for k, v in comp.items():
        if isinstance(v, list):
            if len(v) > 3:
                vals = ", ".join(
                    f"{x:.4g}" if isinstance(x, (int, float)) else str(x) for x in v[:3]
                )
                v_str = f"[{vals}, ...]"
            else:
                vals = ", ".join(f"{x:.4g}" if isinstance(x, (int, float)) else str(x) for x in v)
                v_str = f"[{vals}]"
        elif isinstance(v, dict):
            dump = json.dumps(v, ensure_ascii=False)
            v_str = dump[:30] + "…}" if len(dump) > 32 else dump
        elif isinstance(v, float):
            v_str = f"{v:.4g}"
        else:
            v_str = str(v)
        parts.append(f"{k}={v_str}")
    return "; ".join(parts)


def render_error_matrix_table(evidence: Dict[str, Any], lang: str = "zh") -> List[str]:
    """
    Renders a crisp Markdown Error Matrix table for named failed checks and
    verifier recompute divergences.
    """
    is_en = lang == "en"
    vrep = evidence.get("verifier_report") or {}
    failed_checks = vrep.get("failed_checks") or [] if isinstance(vrep, dict) else []
    sci_obs = evidence.get("scientific_observations") or []
    divergences = [s for s in sci_obs if s.get("error_family") == "verifier_recompute_divergence"]

    if not failed_checks and not divergences:
        return []

    if lang == "zh":
        headers = [
            "| 检查项 | 错误性质 | 交付值 vs 复算/期望值 | 偏差 / 容差 (超标倍数) | 说明与来源 |",
            "|---|---|---|---|---|",
        ]
    elif lang == "en":
        headers = [
            "| Check | Kind | Delivered vs Expected | Delta / Tol (Ratio) | Detail & Source |",
            "|---|---|---|---|---|",
        ]
    else:  # bilingual
        headers = [
            "| 检查项 (Check) | 错误性质 (Kind) | 交付值 vs 复算/期望值 (Delivered vs Expected) | 偏差 / 容差 (Delta / Tol) | 说明与来源 (Detail & Source) |",
            "|---|---|---|---|---|",
        ]

    rows: List[str] = []

    def get_kind_badge(kind_str: Optional[str], has_num: bool) -> str:
        k = str(kind_str or "").lower()
        if not k:
            k = "numerical" if has_num else "general"
        kind_map_zh = {
            "numerical": "数值偏差",
            "schema_or_id": "契约/引用ID",
            "trace_or_provenance": "过程追踪",
            "env_or_runtime": "环境/验证异常",
            "general": "规则检查",
        }
        kind_map_en = {
            "numerical": "numerical",
            "schema_or_id": "schema/ID",
            "trace_or_provenance": "trace",
            "env_or_runtime": "runtime",
            "general": "rule",
        }
        return kind_map_en.get(k, k) if is_en else kind_map_zh.get(k, k)

    if failed_checks:
        for idx, c in enumerate(failed_checks):
            name = str(c.get("name") or f"check_{idx + 1}")
            layer = str(c.get("layer") or "L?")
            core = " [core]" if c.get("core") else ""
            check_col = f"`[{layer}] {name}`{core}"

            # Look for matching divergence
            div = next(
                (
                    d
                    for d in divergences
                    if str(d.get("metric", "")).lower() in name.lower()
                    or name.lower() in str(d.get("metric", "")).lower()
                    or name.lower() in str(d.get("matched_text", "")).lower()
                ),
                None,
            )
            if not div and len(divergences) == 1 and len(failed_checks) == 1:
                div = divergences[0]

            has_num = bool(div or "deviation" in str(c.get("detail", "")).lower())
            kind_col = get_kind_badge(c.get("failure_kind"), has_num)

            # Values col
            comp = c.get("artifact_comparison") or c.get("evidence")
            comp_str = format_artifact_comparison(comp, lang=lang) if comp else ""
            if div:
                rep = (
                    div.get("agent_reported_value")
                    if div.get("agent_reported_value") is not None
                    else div.get("reported_value")
                )
                rec = div.get("recomputed_value")
                ref = div.get("agent_artifact_ref")
                rep_s = str(rep) if rep is not None else "—"
                if ref:
                    rep_s += f" (`{ref}`)"
                rec_s = (
                    ("独立复算重放" if not is_en else "Replay")
                    if div.get("is_delta_divergence")
                    else (str(rec) if rec is not None else "—")
                )
                if comp_str:
                    values_col = f"`{rep_s}` vs `{rec_s}` (`{comp_str}`)"
                else:
                    values_col = f"`{rep_s}` vs `{rec_s}`"
            elif comp_str:
                values_col = f"`{comp_str}`"
            else:
                values_col = "—"

            # Delta / Tol
            if div:
                delta = div.get("reported_value") if div.get("is_delta_divergence") else None
                if (
                    delta is None
                    and div.get("agent_reported_value") is not None
                    and div.get("recomputed_value") is not None
                ):
                    try:
                        delta = abs(
                            float(div["agent_reported_value"]) - float(div["recomputed_value"])
                        )
                        delta = f"{delta:.4g}"
                    except Exception:
                        pass
                if delta is None:
                    delta = div.get("reported_value")
                tol = div.get("tolerance")
                ratio_str = ""
                if tol and delta is not None:
                    try:
                        f_delta = float(delta)
                        f_tol = float(tol)
                        if f_tol > 0:
                            r = f_delta / f_tol
                            ratio_str = f" ({r:.1f}× 超标)" if not is_en else f" ({r:.1f}× bound)"
                    except Exception:
                        pass
                d_txt = f"Δ = {delta}" if delta is not None else ""
                t_txt = f"tol {tol}" if tol is not None else ""
                delta_col = f"{d_txt} / {t_txt}{ratio_str}".strip(" /") or "—"
            elif isinstance(comp, list) and any(
                isinstance(x, dict) and x.get("abs_diff") is not None for x in comp
            ):
                max_diff = max(
                    float(x["abs_diff"])
                    for x in comp
                    if isinstance(x, dict) and x.get("abs_diff") is not None
                )
                delta_col = f"max |Δ| = {max_diff:.3g}"
            else:
                m_dev = re.search(
                    r"deviation\s+([0-9.eE+-]+(?:\s*[A-Za-z/]+)?)",
                    str(c.get("detail", "")),
                    re.IGNORECASE,
                )
                delta_col = (
                    (f"偏差 {m_dev.group(1)}" if not is_en else f"dev {m_dev.group(1)}")
                    if m_dev
                    else "—"
                )

            # Detail and source
            detail = clean_prose(c.get("detail"), 160)
            source = c.get("source")
            desc_col = (
                f"{detail} ({'来源' if not is_en else 'source'}: `{source}`)" if source else detail
            )

            # Escape pipes (Python 3.9 compatible)
            esc_pipe = "\\|"
            row = (
                f"| {check_col.replace('|', esc_pipe)} "
                f"| {kind_col.replace('|', esc_pipe)} "
                f"| {values_col.replace('|', esc_pipe)} "
                f"| {delta_col.replace('|', esc_pipe)} "
                f"| {desc_col.replace('|', esc_pipe)} |"
            )
            rows.append(row)
    else:
        # Divergences without failed_checks
        for d in divergences:
            metric = d.get("metric") or "divergence"
            check_col = f"`[verifier] {metric}`"
            kind_col = "数值偏差" if not is_en else "numerical"
            rep = (
                d.get("agent_reported_value")
                if d.get("agent_reported_value") is not None
                else d.get("reported_value")
            )
            rec = d.get("recomputed_value")
            ref = d.get("agent_artifact_ref")
            rep_s = str(rep) if rep is not None else "—"
            if ref:
                rep_s += f" (`{ref}`)"
            rec_s = (
                ("独立复算重放" if not is_en else "Replay")
                if d.get("is_delta_divergence")
                else (str(rec) if rec is not None else "—")
            )
            values_col = f"`{rep_s}` vs `{rec_s}`"

            delta = d.get("reported_value")
            tol = d.get("tolerance")
            ratio_str = ""
            if tol and delta is not None:
                try:
                    r = float(delta) / float(tol)
                    ratio_str = f" ({r:.1f}× 超标)" if not is_en else f" ({r:.1f}× bound)"
                except Exception:
                    pass
            d_txt = f"Δ = {delta}" if delta is not None else ""
            t_txt = f"tol {tol}" if tol is not None else ""
            delta_col = f"{d_txt} / {t_txt}{ratio_str}".strip(" /") or "—"
            desc_col = clean_prose(d.get("matched_text") or "数值独立复算不符", 160)

            esc_pipe = "\\|"
            row = (
                f"| {check_col.replace('|', esc_pipe)} "
                f"| {kind_col.replace('|', esc_pipe)} "
                f"| {values_col.replace('|', esc_pipe)} "
                f"| {delta_col.replace('|', esc_pipe)} "
                f"| {desc_col.replace('|', esc_pipe)} |"
            )
            rows.append(row)

    return headers + rows


def resolve_evidence_item(ref: str, evidence: Dict[str, Any], lang: str = "zh") -> str:
    """
    Translates an opaque evidence reference ID (art:..., sci:..., ver:..., sig:..., etc.)
    into a human-grounded description with an appropriate visual badge.
    """
    is_en = lang == "en"
    ref_s = str(ref or "").strip()

    # 1. Delivered artifact
    if ref_s.startswith("art:"):
        path = ref_s[4:]
        title = "Delivered Artifact" if is_en else "交付产物"
        if ":L" in path or "/outputs/" in path or "/app/" in path:
            desc = (
                "Located delivered measurement in output artifact"
                if is_en
                else "定位到交付记录实测值"
            )
        elif "results" in path or "result" in path:
            desc = "Simulation results submitted by agent" if is_en else "Agent 提交的计算结果文件"
        elif "trajectory" in path:
            desc = (
                "Execution turns & tool calls recorded" if is_en else "Agent 交互轨迹与工具调用记录"
            )
        elif "verify" in path:
            desc = "Verifier log artifact" if is_en else "验证器评测日志产物"
        else:
            desc = "Referenced trial artifact" if is_en else "引用的相关产物"
        return f"📄 **{title}** (`{path}`): {desc}"

    # 2. Scientific / numerical observation
    if ref_s.startswith("sci:"):
        title = "Scientific Observation" if is_en else "数值/科学观测"
        sci_list = evidence.get("scientific_observations") or []
        obs = next(
            (s for s in sci_list if s.get("sci_id") == ref_s or s.get("error_id") == ref_s),
            None,
        )
        if obs:
            metric = obs.get("metric") or obs.get("error_family") or "metric"
            rep = (
                obs.get("agent_reported_value")
                if obs.get("agent_reported_value") is not None
                else obs.get("reported_value")
            )
            rec = obs.get("recomputed_value")
            tol = obs.get("tolerance")
            if obs.get("is_delta_divergence"):
                delta_val = obs.get("reported_value")
                dlv_val = obs.get("agent_reported_value")
                if dlv_val is not None and delta_val is not None:
                    desc = (
                        f"{metric} delivered {dlv_val}, diverged by Δ = {delta_val} beyond bound {tol}"
                        if is_en
                        else f"{metric} 交付值 {dlv_val}，独立重放偏差 Δ = {delta_val}（超出容差 {tol}）"
                    )
                else:
                    shown_delta = delta_val if delta_val is not None else rep
                    desc = (
                        f"{metric} diverged by {shown_delta} beyond bound {tol}"
                        if is_en
                        else f"{metric} 检出偏差 {shown_delta}（超出容差 {tol}）"
                    )
            elif rep is not None and rec is not None:
                desc = (
                    f"{metric} reported {rep} != recomputed {rec} (tol {tol})"
                    if is_en
                    else f"{metric} 交付值 {rep} 与复算值 {rec} 不符（容差 {tol}）"
                )
            else:
                desc = clean_prose(
                    obs.get("matched_text") or obs.get("summary") or "numerical divergence",
                    120,
                )
        else:
            desc = "Numerical divergence observed" if is_en else "检出数值计算偏离"
        return f"⚖️ **{title}** (`{ref_s}`): {desc}"

    # 3. Verifier observation / fail log
    if ref_s.startswith("ver:") or ref_s in ("verifier:fail_log", "verify.log"):
        title = "Verifier Judgment" if is_en else "验证器判定"
        vrep = evidence.get("verifier_report")
        if isinstance(vrep, dict):
            primary = vrep.get("primary_report") or "verify.json"
            failed = vrep.get("failed_checks") or []
            if failed:
                names = ", ".join(f"`{c.get('name')}`" for c in failed[:3])
                desc = (
                    f"Rejected {len(failed)} named check(s): {names}"
                    if is_en
                    else f"未通过 {len(failed)} 项具名检查：{names}"
                )
            else:
                desc = clean_prose(vrep.get("summary") or "verification rejected", 120)
            return f"🛡️ **{title}** (`{primary}`): {desc}"
        vobs_list = evidence.get("verifier_observations") or []
        vobs = next(
            (v for v in vobs_list if v.get("obs_id") == ref_s or ref_s in str(v.get("type", ""))),
            None,
        )
        if vobs:
            desc = clean_code_line(
                vobs.get("matched_text") or vobs.get("summary") or "assertion failed", 120
            )
        else:
            desc = "Verifier recorded failure" if is_en else "验证器记录失败结论"
        return f"🛡️ **{title}** (`verifier/verify.log`): {desc}"

    # 4. Trajectory step
    if ref_s.startswith("trajectory:step:") or ref_s.startswith("ev_") or "step" in ref_s:
        title = "Agent Trajectory" if is_en else "轨迹动作"
        timeline = evidence.get("timeline") or []
        ev = next(
            (e for e in timeline if e.get("event_id") == ref_s or str(e.get("step_id")) == ref_s),
            None,
        )
        if ev:
            tool = ev.get("tool_name") or ev.get("event_type") or "action"
            cmd = ev.get("command") or ev.get("target_path") or ""
            if cmd:
                cmd_clean = clean_code_line(cmd, 65).replace("`", "'")
                desc = f"{tool} `{cmd_clean}`"
            else:
                fallback_action = "Executed key action" if is_en else "执行了关键动作"
                desc = clean_prose(
                    ev.get("summary") or ev.get("matched_text") or fallback_action, 80
                )
            return f"🧠 **{title}** (`{ref_s}`): {desc}"
        return (
            f"🧠 **{title}** (`{ref_s}`): "
            f"{'Executed key command/action' if is_en else '执行了关键命令/动作'}"
        )

    # 5. Behavioral signals
    if ref_s.startswith("sig:") or ref_s.startswith("sig_"):
        title = "Behavioral Signal" if is_en else "行为信号"
        sigs = evidence.get("behavioral_signals") or []
        sig = next((s for s in sigs if s.get("signal_id") == ref_s), None)
        if sig:
            stype = sig.get("signal_type", "behavioral_signal")
            desc = clean_prose(
                sig.get("description") or sig.get("matched_text") or "signal detected", 120
            )
            return f"🚩 **{title}** (`{stype}`): {desc}"
        return f"🚩 **{title}** (`{ref_s}`): {'Behavioral anomaly signal' if is_en else '行为异常信号'}"

    # 6. Contract observation
    if ref_s.startswith("contract:") or ref_s.startswith("cont:"):
        title = "Contract Audit" if is_en else "契约比对"
        contracts = evidence.get("contract_observations") or []
        co = next((c for c in contracts if c.get("contract_id") == ref_s), None)
        if co:
            desc = clean_prose(
                co.get("details") or co.get("alignment") or "contract audit mismatch", 120
            )
            return f"📋 **{title}** (`{ref_s}`): {desc}"
        return (
            f"📋 **{title}** (`{ref_s}`): "
            f"{'Contract requirement audit' if is_en else '契约要求审计项'}"
        )

    return f"- `{ref_s}`"


def resolve_key_evidence_summary(
    refs: List[str],
    evidence: Dict[str, Any],
    lang: str = "zh",
    limit: int = 5,
) -> List[str]:
    """
    Renders up to `limit` human-grounded evidence summary bullets from a list of
    evidence ref strings, collapsing multiple self-check signals into a single line.
    """
    is_en = lang == "en"
    clean_refs = [r for r in dict.fromkeys(refs) if r]
    self_check_sigs = [
        r for r in clean_refs if r.startswith("sig:self_check:") or "self_check" in r
    ]
    other_refs = [r for r in clean_refs if r not in self_check_sigs]

    items: List[str] = []
    if len(self_check_sigs) > 1:
        title = "Behavioral Signal" if is_en else "行为信号"
        desc = (
            f"Captured {len(self_check_sigs)} self-verification execution attempts"
            if is_en
            else f"记录了 {len(self_check_sigs)} 次自检执行事件（已折叠）"
        )
        items.append(f"🚩 **{title}** (`self_verification_executed`): {desc}")
    elif len(self_check_sigs) == 1:
        other_refs.insert(0, self_check_sigs[0])

    for r in other_refs:
        if len(items) >= limit:
            break
        resolved = resolve_evidence_item(r, evidence, lang=lang)
        if resolved and resolved not in items:
            items.append(resolved)

    return items[:limit]
