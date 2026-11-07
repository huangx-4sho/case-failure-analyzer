#!/usr/bin/env python3
"""
Trajectory Causal Anchor Tracing (`scripts/trace_causal_anchors.py`)

Extracts high-signal trajectory anchors connecting agent decisions to failure outcomes:
1. `algorithm_or_param_definition`: Where the agent formulated or edited the core computational
   method, solver parameters, or simulation setup.
2. `deliverable_generation`: Where the agent executed the command or wrote the final artifacts
   required by the benchmark contract.
3. `self_check`: Where the agent ran its own verification script, plus which named verifier
   checks that script actually covered. This makes the self-check coverage gap explicit
   instead of inferring it from a passing `VERIFY OK` line.

Pairs each anchor with the agent's verbatim thought / reasoning at that step and code payloads.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple

from normalize_trajectory import SELF_CHECK_RUN_RE, SELF_CHECK_SCRIPT_RE

_SELF_CHECK_PASS_RE = re.compile(
    r"(?:VERIFY\s*OK|ALL\s+CHECKS\s+PASSED|SELF[-_ ]?CHECK\s+(?:OK|PASSED)|"
    r"\b0\s+CHECK\(S\)\s+FAILED\b)",
    re.IGNORECASE,
)
_IMPORT_LINE_RE = re.compile(
    r"^\s*(?:import\s|from\s+\S+\s+import\b|#!|#\s*-\*-\s*coding)",
    re.IGNORECASE,
)
_CODE_DEF_RE = re.compile(r"^\s*(?:def|class)\s")
_CODE_NUMERIC_ASSIGN_RE = re.compile(r"^\s*[A-Za-z_][A-Za-z0-9_]*\s*=\s*[-+]?\d")
_CODE_CONST_RE = re.compile(r"^\s*[A-Z][A-Z0-9_]*\s*=")
_CODE_SIGNAL_RE = re.compile(
    r"\b(?:tol|tolerance|cutoff|eta|alpha|spin|charge|energy|converge|scf|k_coul)\b",
    re.IGNORECASE,
)
_CHECK_MESSAGE_RE = re.compile(r"f?[\"']([^\"']{4,120})[\"']")
_PRINT_SECTION_RE = re.compile(r"print\s*\(\s*\\?[\"']([A-Za-z0-9_.-]{3,60})\\?[\"']\s*\)")
_METADATA_KEYS = frozenset(
    {"type", "create", "filePath", "content", "schema", "name", "source", "role", "status"}
)

_GENERIC_STOPWORDS = frozenset(
    {
        "uses",
        "unknown",
        "differs",
        "from",
        "independent",
        "native",
        "calculation",
        "check",
        "failed",
        "the",
        "a",
        "an",
        "at",
        "by",
        "is",
        "are",
        "of",
        "in",
        "to",
        "for",
        "and",
        "or",
        "with",
        "on",
        "not",
    }
)

_TRADEOFF_PARAM_RE = re.compile(
    r"\b(?:eta|cutoff|alpha|tol|tolerance|ewald|convergence|grid|kpoints|k[-_]points|basis)\b|[ηα]",
    re.IGNORECASE,
)
_TRADEOFF_CHOICE_RE = re.compile(
    r"\b(?:I'll use|i will use|choose|chosen|choosing|cheap and converged|should I use|tradeoff|trade[- ]off|balance|compromise|settle on|select|decide on|trying different|tested|comparing)\b",
    re.IGNORECASE,
)


def _extract_failed_check_targets(evidence: Dict[str, Any]) -> Tuple[List[str], Set[str]]:
    """Extract failed check names and key identifier tokens from evidence."""
    failed_names = _failed_check_names(evidence)
    identifier_tokens: Set[str] = set()

    report = evidence.get("verifier_report")
    if isinstance(report, dict):
        for check in report.get("failed_checks") or []:
            if not isinstance(check, dict):
                continue
            name = check.get("name")
            if name and str(name) not in failed_names:
                failed_names.append(str(name))
            detail = str(check.get("detail") or "")
            if detail:
                tokens = re.findall(r"[A-Za-z0-9_.-]+", detail)
                for tok in tokens:
                    low = tok.lower()
                    if low in _GENERIC_STOPWORDS or len(tok) < 3:
                        continue
                    # Key identifier tokens: compound names with _ or . or - or digits
                    if "_" in tok or "-" in tok or "." in tok or any(c.isdigit() for c in tok):
                        identifier_tokens.add(tok)

    cleaned_failed_names = [n for n in failed_names if n.lower() not in _GENERIC_STOPWORDS]
    return cleaned_failed_names, identifier_tokens


def _score_relevance(
    text: str,
    failed_names: List[str],
    identifier_tokens: Set[str],
) -> Tuple[int, List[str]]:
    """Score text against failed checks and return (relevance_bonus, matched_failed_checks).

    Awards +12 for exact failed-check name or identifier token match,
    and +5 for component token match (>3 chars).
    """
    relevance_bonus = 0
    matched_names: List[str] = []
    text_lower = text.lower()

    for fname in failed_names:
        if fname.lower() in text_lower:
            relevance_bonus += 12
            matched_names.append(fname)

    for tok in identifier_tokens:
        if tok.lower() in text_lower:
            relevance_bonus += 12
            if tok in failed_names and tok not in matched_names:
                matched_names.append(tok)

    matched_set = {m.lower() for m in matched_names}
    all_targets = set(failed_names) | identifier_tokens
    for target in all_targets:
        components = re.split(r"[_.-]+", target)
        for comp in components:
            if len(comp) > 3 and comp.lower() not in _GENERIC_STOPWORDS:
                if comp.lower() in text_lower and comp.lower() not in matched_set:
                    relevance_bonus += 5
                    matched_set.add(comp.lower())

    return relevance_bonus, list(dict.fromkeys(matched_names))


def _detect_parameter_tradeoff(thought: str, code: str) -> bool:
    """Detect explicit parameter selection / tradeoff in thought and code."""
    combined = f"{thought}\n{code}"
    return bool(_TRADEOFF_PARAM_RE.search(combined) and _TRADEOFF_CHOICE_RE.search(combined))


def _step_id_from_event(event_id: str) -> str:
    """Extract step prefix like 'trajectory:step:43' from 'trajectory:step:43:tool:0'."""
    parts = event_id.split(":")
    if len(parts) >= 3 and parts[0] == "trajectory" and parts[1] == "step":
        return f"trajectory:step:{parts[2]}"
    return event_id


def _find_thought_for_step(timeline: List[Dict[str, Any]], step_prefix: str) -> Optional[str]:
    """Find agent thought in the same step."""
    for e in timeline:
        eid = e.get("event_id", "")
        if eid.startswith(step_prefix) and "thought" in eid:
            obs = (e.get("observation") or "").strip()
            if obs:
                return obs
    return None


def _clean_thought_snippet(thought: str, max_lines: int = 4, max_chars: int = 250) -> str:
    """Remove a dangling leading clause, then truncate thought text.

    ATIF thought payloads are sometimes cut mid-sentence (e.g. `=0.02 (η=50)...`).
    A paragraph that starts with a number/operator is a fragment of a previous
    cut and is dropped; the remaining paragraphs start with complete sentences.
    """
    if not thought:
        return ""
    text = thought.strip()
    text = re.sub(r"^[\s=,;:.\-+*~#`]+", "", text)
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    if paragraphs and re.match(r"^(?:\d|[\-+*~#`=\(\)\[\]])", paragraphs[0]):
        paragraphs = paragraphs[1:]
    text = "\n\n".join(paragraphs)
    if not text:
        return ""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    truncated = "\n".join(lines[:max_lines])
    if len(lines) > max_lines:
        truncated += "\n..."
    if len(truncated) > max_chars:
        truncated = truncated[: max_chars - 3] + "..."
    return truncated


def _code_line_salience(line: str) -> int:
    """Score a code line for how much causal information it carries."""
    score = 0
    if _CODE_DEF_RE.match(line):
        score += 3
    if _CODE_NUMERIC_ASSIGN_RE.match(line):
        score += 4
    if _CODE_CONST_RE.match(line):
        score += 2
    if _CODE_SIGNAL_RE.search(line):
        score += 2
    if _IMPORT_LINE_RE.match(line):
        score -= 5
    return score


def _clean_code_snippet(code: str, max_lines: int = 8, max_chars: int = 400) -> str:
    """Select the most informative contiguous lines, skipping imports/docstrings."""
    if not code:
        return ""
    cleaned = code
    m_doc = re.match(r"^\s*(?:#.*?\n)*\s*(?:\"\"\"[\s\S]*?\"\"\"|'''[\s\S]*?''')\s*\n?", cleaned)
    if m_doc and len(cleaned) > m_doc.end():
        cleaned = cleaned[m_doc.end() :]
    all_lines = [ln.rstrip() for ln in cleaned.strip().splitlines() if ln.strip()]
    lines = [ln for ln in all_lines if not _IMPORT_LINE_RE.match(ln)] or all_lines
    if not lines:
        return ""

    # Sliding window: prefer the block carrying the constants / definitions that
    # actually caused the deviation, not the module's import preamble.
    best_start = 0
    best_score: Optional[int] = None
    for start in range(len(lines)):
        score = sum(_code_line_salience(ln) for ln in lines[start : start + max_lines])
        if best_score is None or score > best_score:
            best_score = score
            best_start = start
    window = lines[best_start : best_start + max_lines]
    truncated = "\n".join(window)
    if best_start + len(window) < len(lines):
        truncated += "\n..."
    if len(truncated) > max_chars:
        truncated = truncated[: max_chars - 3] + "..."
    return truncated


def _failed_check_names(evidence: Dict[str, Any]) -> List[str]:
    """Named failing checks that the verifier actually rejected."""
    names: List[str] = []
    stats = evidence.get("verifier_check_stats")
    if isinstance(stats, dict):
        names.extend(str(name) for name in stats.get("failed_check_names") or [] if name)
    report = evidence.get("verifier_report")
    if isinstance(report, dict):
        for check in report.get("failed_checks") or []:
            if isinstance(check, dict) and check.get("name"):
                names.append(str(check["name"]))
    return list(dict.fromkeys(names))


def _self_check_criteria(script_text: str, observation_text: str = "") -> List[str]:
    """Human-readable `check(...)` messages and section labels from the script."""
    criteria: List[str] = []
    lines = script_text.splitlines()
    for index, line in enumerate(lines):
        if "check(" not in line and not re.search(r"print\s*\(\s*[\"'][A-Za-z]", line):
            continue
        for candidate in lines[index : index + 2]:
            for match in _CHECK_MESSAGE_RE.finditer(candidate):
                text = match.group(1).strip()
                if (
                    len(text) < 4
                    or text in criteria
                    or text in _METADATA_KEYS
                    or "\\" in text
                    or "{msg}" in text
                    or text.startswith(("  ok", "  FAIL", "def ", "class ", "#", "/"))
                    or text.endswith(".py")
                    or not re.search(r"[A-Za-z]", text)
                    or not re.search(r"[\s._\d{]", text)
                ):
                    continue
                criteria.append(text)
    for match in _PRINT_SECTION_RE.finditer(observation_text or ""):
        label = match.group(1).strip()
        if label and label not in criteria and label not in _METADATA_KEYS:
            criteria.append(label)
    return criteria[:8]


def _trace_self_check_anchor(
    timeline: List[Dict[str, Any]], evidence: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """Bind an agent self-check execution to the verifier's named requirements.

    The self-check script's own source (from its write/edit event) is searched for
    the literal failed-check names. Checks that never appear in that source are
    the coverage gap; no semantic guessing about "related" words is performed.
    """
    check_event: Optional[Dict[str, Any]] = None
    script_name = ""
    for event in reversed(timeline):
        # Only real execution events qualify: a `Read`/`View` of the script, a
        # `cat`/`grep` of it, or a thought mentioning it must not create an anchor.
        if event.get("event_type") not in ("tool_call", "shell_error", "repeated_command"):
            continue
        cmd = (event.get("command") or "").strip()
        if not cmd or not SELF_CHECK_RUN_RE.search(cmd):
            continue
        match = SELF_CHECK_SCRIPT_RE.search(cmd)
        if match:
            check_event = event
            script_name = match.group(0).split("/")[-1]
            break
    if check_event is None or not script_name:
        return None

    script_event: Optional[Dict[str, Any]] = None
    best_size = -1
    for event in timeline:
        if event.get("tool_name") not in ("Write", "Edit"):
            continue
        event_text = f"{event.get('command') or ''}\n{event.get('observation') or ''}"
        if script_name not in event_text:
            continue
        size = len(str(event.get("observation") or "")) + len(str(event.get("code_payload") or ""))
        if size > best_size:
            best_size = size
            script_event = event
    # Without the script's own source we cannot claim which checks it covered or
    # omitted; abstain instead of reporting a fabricated coverage gap.
    if script_event is None:
        return None

    script_text = ""
    coverage_text = ""
    script_source_observation = ""
    script_path = script_name
    script_event_ref: Optional[str] = None
    payload_code = str(script_event.get("code_payload") or "")
    script_source_observation = str(script_event.get("observation") or "")
    # Criteria come from the summarized code first: the Write observation is
    # a `[metadata]` JSON envelope whose keys are not check messages.
    script_text = payload_code if "check(" in payload_code else script_source_observation
    coverage_text = script_source_observation + "\n" + payload_code
    raw_path = (script_event.get("command") or "").strip().splitlines()
    if raw_path and raw_path[0].strip().endswith(".py"):
        script_path = raw_path[0].strip()
    script_event_ref = script_event.get("event_id")

    failed_names = _failed_check_names(evidence)
    lowered = coverage_text.lower()
    covered = [name for name in failed_names if name.lower() in lowered]
    uncovered = [name for name in failed_names if name not in covered]

    event_id = str(check_event.get("event_id") or "")
    observation = str(check_event.get("observation") or "").strip()
    return {
        "event_id": event_id,
        "step_id": _step_id_from_event(event_id),
        "tool_name": check_event.get("tool_name") or "Command",
        "command": _clean_code_snippet(
            str(check_event.get("command") or ""), max_lines=3, max_chars=200
        ),
        "thought": _clean_thought_snippet(
            _find_thought_for_step(timeline, _step_id_from_event(event_id)) or "",
            max_lines=3,
            max_chars=200,
        ),
        "observation_snippet": _clean_code_snippet(observation, max_lines=3, max_chars=200),
        "script_path": script_path,
        "script_event_ref": script_event_ref,
        "self_check_passed": bool(_SELF_CHECK_PASS_RE.search(observation)),
        "self_check_criteria": _self_check_criteria(script_text, script_source_observation),
        "checked_failed_checks": covered,
        "uncovered_failed_checks": uncovered,
    }


def trace_causal_anchors(evidence: Dict[str, Any]) -> Dict[str, Any]:
    """Extract algorithm, deliverable-generation and self-check anchors."""
    timeline = evidence.get("timeline") or []
    if not timeline:
        return {}

    declared = (evidence.get("metadata") or {}).get("declared_deliverables") or []
    failed_names, id_tokens = _extract_failed_check_targets(evidence)

    # 1. Trace algorithm_or_param_definition
    algo_anchor: Optional[Dict[str, Any]] = None
    candidates: List[Tuple[int, Dict[str, Any], List[str]]] = []
    for e in timeline:
        tool = e.get("tool_name") or ""
        cmd = e.get("command") or ""
        obs = e.get("observation") or ""
        code = e.get("code_payload") or ""
        step_prefix = _step_id_from_event(e.get("event_id", ""))
        thought = _find_thought_for_step(timeline, step_prefix) or ""
        text = f"{cmd}\n{obs}\n{code}\n{thought}"

        if tool in ("Write", "Edit") or "cat <<" in cmd or "cat >" in cmd:
            score = 0
            if re.search(
                r"\b(analysis|run_state|solve|calc|simulator|model|eqeq|dft|scf)\.py\b", text, re.I
            ):
                score += 6
            if re.search(
                r"\b(energy|charge|force|hamiltonian|tolerance|iteration|converge|cutoff|spin)\b",
                text,
                re.I,
            ):
                score += 3
            if len(code) > 80:
                score += 2

            rel_bonus, matched_names = _score_relevance(text, failed_names, id_tokens)
            score += rel_bonus

            if score > 0:
                candidates.append((score, e, matched_names))

    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        best_algo_ev = candidates[0][1]
        best_matched = candidates[0][2]
        step_prefix = _step_id_from_event(best_algo_ev.get("event_id", ""))
        thought = _find_thought_for_step(timeline, step_prefix)
        code_snip = best_algo_ev.get("code_payload") or best_algo_ev.get("command") or ""
        tradeoff_detected = _detect_parameter_tradeoff(thought or "", code_snip)
        algo_anchor = {
            "event_id": best_algo_ev.get("event_id"),
            "step_id": step_prefix,
            "tool_name": best_algo_ev.get("tool_name", "Tool"),
            "target_path": best_algo_ev.get("command", "").split()[0]
            if best_algo_ev.get("command")
            else "",
            "thought": _clean_thought_snippet(thought or "", max_lines=4, max_chars=250),
            "code_snippet": _clean_code_snippet(code_snip, max_lines=6, max_chars=350),
            "matched_failed_checks": best_matched,
            "parameter_tradeoff_detected": tradeoff_detected,
        }

    # 2. Trace deliverable_generation (working backward from end of trajectory, weighted by relevance)
    deliv_anchor: Optional[Dict[str, Any]] = None
    deliv_candidates: List[Tuple[float, Dict[str, Any], List[str]]] = []
    total_events = max(1, len(timeline))

    for idx, e in enumerate(timeline):
        cmd = (e.get("command") or "").strip()
        tool = e.get("tool_name") or ""
        obs = e.get("observation") or ""
        code = e.get("code_payload") or ""
        step_prefix = _step_id_from_event(e.get("event_id", ""))
        thought = _find_thought_for_step(timeline, step_prefix) or ""
        text = f"{cmd}\n{obs}\n{code}\n{thought}"

        if not cmd:
            continue
        if cmd.startswith(("rm ", "rm -", "rmdir ")):
            continue

        is_exec = False
        base_score = 10
        if re.search(r"\b(?:build_outputs|analysis|solve|calc)\.py\b", cmd) and (
            "python" in cmd or "./" in cmd
        ):
            is_exec = True
            base_score = 15
        elif re.search(r"(?:python\S*|bash|sh)\s+.*?(--output\b|-o\b)", cmd):
            is_exec = True
            base_score = 12
        elif tool == "Write" and not cmd.endswith((".md", ".txt")):
            if any(d in cmd for d in declared):
                is_exec = True
                base_score = 14
        elif any(
            f"> {d}" in cmd or f">{d}" in cmd for d in declared if not d.endswith((".md", ".txt"))
        ):
            is_exec = True
            base_score = 13
        elif "results.json" in cmd and (">" in cmd or tool == "Write"):
            is_exec = True
            base_score = 11

        if is_exec:
            rel_bonus, matched_names = _score_relevance(text, failed_names, id_tokens)
            recency_bonus = (idx / total_events) * 2.0
            total_score = base_score + rel_bonus + recency_bonus
            deliv_candidates.append((total_score, e, matched_names))

    if deliv_candidates:
        deliv_candidates.sort(key=lambda x: x[0], reverse=True)
        best_deliv_ev = deliv_candidates[0][1]
        best_deliv_matched = deliv_candidates[0][2]
        step_prefix = _step_id_from_event(best_deliv_ev.get("event_id", ""))
        thought = _find_thought_for_step(timeline, step_prefix)
        deliv_anchor = {
            "event_id": best_deliv_ev.get("event_id"),
            "step_id": step_prefix,
            "tool_name": best_deliv_ev.get("tool_name") or "Command",
            "command": _clean_code_snippet(
                str(best_deliv_ev.get("command") or ""), max_lines=3, max_chars=200
            ),
            "thought": _clean_thought_snippet(thought or "", max_lines=4, max_chars=250),
            "observation_snippet": _clean_code_snippet(
                str(best_deliv_ev.get("observation") or "").strip(), max_lines=4, max_chars=250
            ),
            "matched_failed_checks": best_deliv_matched,
        }

    result: Dict[str, Any] = {}
    if algo_anchor:
        result["algorithm_or_param_definition"] = algo_anchor
    if deliv_anchor:
        result["deliverable_generation"] = deliv_anchor
    self_check_anchor = _trace_self_check_anchor(timeline, evidence)
    if self_check_anchor:
        result["self_check"] = self_check_anchor
    return result


def render_causal_anchors_markdown(anchors: Dict[str, Any], lang: str = "zh") -> List[str]:
    """Render markdown card for causal anchors."""
    if not anchors:
        return []

    lines: List[str] = []
    is_zh = lang != "en"

    title = (
        "### 核心因果链锚点 (Key Trajectory Causal Anchors)"
        if lang == "bilingual"
        else ("### 核心因果链锚点" if is_zh else "### Key Trajectory Causal Anchors")
    )
    lines.append(f"{title}\n")

    algo = anchors.get("algorithm_or_param_definition")
    if algo:
        label = (
            "参数与算法定义 (Algorithm / Param Definition)"
            if lang == "bilingual"
            else ("参数与算法定义" if is_zh else "Algorithm / Param Definition")
        )
        tool_desc = f"`{algo['event_id']}` ({algo.get('tool_name', 'Tool')}: `{algo.get('target_path', '')}`)"
        lines.append(f"- **{label}**: {tool_desc}")
        if algo.get("thought"):
            thought_lbl = (
                "智能体思考 / Agent Thought"
                if lang == "bilingual"
                else ("智能体思考" if is_zh else "Agent Thought")
            )
            lines.append(f'  > **{thought_lbl}**: "{algo["thought"]}"')
        if algo.get("code_snippet"):
            lines.append("  ```python")
            for cl in algo["code_snippet"].splitlines():
                lines.append(f"  {cl}")
            lines.append("  ```")
        if algo.get("matched_failed_checks"):
            m_lbl = (
                "关联失败项 / Matched failed checks"
                if lang == "bilingual"
                else ("关联失败项" if is_zh else "Matched failed checks")
            )
            lines.append(f"  - **{m_lbl}**: {', '.join(algo['matched_failed_checks'])}")
        if algo.get("parameter_tradeoff_detected"):
            t_lbl = (
                "参数权衡决策 / Parameter tradeoff"
                if lang == "bilingual"
                else ("参数权衡决策" if is_zh else "Parameter tradeoff")
            )
            t_val = (
                "检测到显式权衡 / detected"
                if lang == "bilingual"
                else ("检测到显式权衡" if is_zh else "detected")
            )
            lines.append(f"  - **{t_lbl}**: {t_val}")

    deliv = anchors.get("deliverable_generation")
    if deliv:
        label = (
            "交付物生成执行 (Deliverable Generation)"
            if lang == "bilingual"
            else ("交付物生成执行" if is_zh else "Deliverable Generation")
        )
        tool_desc = f"`{deliv['event_id']}` ({deliv.get('tool_name', 'Command')})"
        lines.append(f"- **{label}**: {tool_desc}")
        if deliv.get("thought"):
            thought_lbl = (
                "智能体思考 / Agent Thought"
                if lang == "bilingual"
                else ("智能体思考" if is_zh else "Agent Thought")
            )
            lines.append(f'  > **{thought_lbl}**: "{deliv["thought"]}"')
        if deliv.get("command"):
            lines.append("  ```bash")
            for cl in deliv["command"].splitlines():
                lines.append(f"  {cl}")
            lines.append("  ```")
        if deliv.get("matched_failed_checks"):
            m_lbl = (
                "关联失败项 / Matched failed checks"
                if lang == "bilingual"
                else ("关联失败项" if is_zh else "Matched failed checks")
            )
            lines.append(f"  - **{m_lbl}**: {', '.join(deliv['matched_failed_checks'])}")

    sc = anchors.get("self_check")
    if sc:
        label = (
            "自检覆盖 (Self-check Coverage)"
            if lang == "bilingual"
            else ("自检覆盖" if is_zh else "Self-check Coverage")
        )
        passed_lbl = (
            "自检结果 / Self-check"
            if lang == "bilingual"
            else ("自检结果" if is_zh else "Self-check")
        )
        covered_lbl = (
            "已覆盖 / Covered" if lang == "bilingual" else ("已覆盖" if is_zh else "Covered")
        )
        uncovered_lbl = (
            "未覆盖 / Uncovered" if lang == "bilingual" else ("未覆盖" if is_zh else "Uncovered")
        )
        status = (
            ("通过 `VERIFY OK`" if is_zh else "passed (`VERIFY OK`)")
            if sc.get("self_check_passed")
            else ("未见通过标记" if is_zh else "no pass marker observed")
        )
        tool_desc = (
            f"`{sc['event_id']}` ({sc.get('tool_name', 'Command')}: `{sc.get('script_path', '')}`)"
        )
        if sc.get("script_event_ref"):
            tool_desc += f" / `{sc['script_event_ref']}`"
        lines.append(f"- **{label}**: {tool_desc}")
        lines.append(f"  - **{passed_lbl}**: {status}")
        lines.append(
            f"  - **{covered_lbl}**: {', '.join(sc.get('checked_failed_checks') or []) or '—'}"
        )
        lines.append(
            f"  - **{uncovered_lbl}**: {', '.join(sc.get('uncovered_failed_checks') or []) or '—'}"
        )
        criteria = sc.get("self_check_criteria") or []
        if criteria:
            criteria_lbl = (
                "脚本实际检查项 / Script criteria"
                if lang == "bilingual"
                else ("脚本实际检查项" if is_zh else "Script criteria")
            )
            lines.append(f"  - **{criteria_lbl}**:")
            for item in criteria[:6]:
                lines.append(f"    - `{item}`")
        if sc.get("command"):
            lines.append("  ```bash")
            for cl in sc["command"].splitlines():
                lines.append(f"  {cl}")
            lines.append("  ```")

    lines.append("")
    return lines
