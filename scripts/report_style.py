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

import re
from typing import Any, Optional

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
    for en, zh in _ENGINE_TEXT_ZH:
        if text.startswith(en):
            tail = text[len(en) :]
            if tail.startswith(": "):
                tail = "：" + tail[2:]
            return (zh + tail).strip()
    return text
