#!/usr/bin/env python3
"""
Trajectory Causal Anchor Tracing (`scripts/trace_causal_anchors.py`)

Extracts high-signal trajectory anchors connecting agent decisions to failure outcomes:
1. `algorithm_or_param_definition`: Where the agent formulated or edited the core computational
   method, solver parameters, or simulation setup.
2. `deliverable_generation`: Where the agent executed the command or wrote the final artifacts
   required by the benchmark contract.

Pairs each anchor with the agent's verbatim thought / reasoning at that step and code payloads.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple


READONLY_TOKENS = frozenset(
    {
        "cd", "cat", "head", "tail", "ls", "sed", "grep", "sleep",
        "ps", "kill", "find", "rm", "rmdir", "echo", "pwd", "true",
    }
)


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


def _clean_code_snippet(code: str, max_lines: int = 8, max_chars: int = 400) -> str:
    """Clean and truncate a code snippet to keep report concise."""
    if not code:
        return ""
    lines = [ln for ln in code.strip().splitlines() if ln.strip()]
    truncated = "\n".join(lines[:max_lines])
    if len(lines) > max_lines:
        truncated += "\n..."
    if len(truncated) > max_chars:
        truncated = truncated[:max_chars - 3] + "..."
    return truncated


def trace_causal_anchors(evidence: Dict[str, Any]) -> Dict[str, Any]:
    """Extract algorithm_or_param_definition and deliverable_generation anchors."""
    timeline = evidence.get("timeline") or []
    if not timeline:
        return {}

    declared = (evidence.get("metadata") or {}).get("declared_deliverables") or []

    # 1. Trace algorithm_or_param_definition
    algo_anchor: Optional[Dict[str, Any]] = None
    candidates: List[Tuple[int, Dict[str, Any]]] = []
    for e in timeline:
        tool = e.get("tool_name") or ""
        cmd = e.get("command") or ""
        obs = e.get("observation") or ""
        code = e.get("code_payload") or ""
        text = f"{cmd}\n{obs}\n{code}"

        if tool in ("Write", "Edit") or "cat <<" in cmd or "cat >" in cmd:
            score = 0
            if re.search(r"\b(analysis|run_state|solve|calc|simulator|model|eqeq|dft|scf)\.py\b", text, re.I):
                score += 6
            if re.search(r"\b(energy|charge|force|hamiltonian|tolerance|iteration|converge|cutoff|spin)\b", text, re.I):
                score += 3
            if len(code) > 80:
                score += 2
            if score > 0:
                candidates.append((score, e))

    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        best_algo_ev = candidates[0][1]
        step_prefix = _step_id_from_event(best_algo_ev.get("event_id", ""))
        thought = _find_thought_for_step(timeline, step_prefix)
        code_snip = best_algo_ev.get("code_payload") or best_algo_ev.get("command") or ""
        algo_anchor = {
            "event_id": best_algo_ev.get("event_id"),
            "step_id": step_prefix,
            "tool_name": best_algo_ev.get("tool_name", "Tool"),
            "target_path": best_algo_ev.get("command", "").split()[0] if best_algo_ev.get("command") else "",
            "thought": _clean_code_snippet(thought or "", max_lines=4, max_chars=250),
            "code_snippet": _clean_code_snippet(code_snip, max_lines=6, max_chars=350),
        }

    # 2. Trace deliverable_generation (working backward from end of trajectory)
    deliv_anchor: Optional[Dict[str, Any]] = None
    for e in reversed(timeline):
        cmd = (e.get("command") or "").strip()
        tool = e.get("tool_name") or ""
        if not cmd:
            continue
        if cmd.startswith(("rm ", "rm -", "rmdir ")):
            continue
        # Check if command is a script execution producing outputs
        is_exec = False
        if re.search(r"\b(?:build_outputs|analysis|solve|calc)\.py\b", cmd) and ("python" in cmd or "./" in cmd):
            is_exec = True
        elif re.search(r"(?:python\S*|bash|sh)\s+.*?(--output\b|-o\b)", cmd):
            is_exec = True
        elif tool == "Write" and not cmd.endswith((".md", ".txt")):
            if any(d in cmd for d in declared):
                is_exec = True
        elif any(f"> {d}" in cmd or f">{d}" in cmd for d in declared if not d.endswith((".md", ".txt"))):
            is_exec = True
        elif "results.json" in cmd and (">" in cmd or tool == "Write"):
            is_exec = True

        if is_exec:
            step_prefix = _step_id_from_event(e.get("event_id", ""))
            thought = _find_thought_for_step(timeline, step_prefix)
            obs = (e.get("observation") or "").strip()
            deliv_anchor = {
                "event_id": e.get("event_id"),
                "step_id": step_prefix,
                "tool_name": tool or "Command",
                "command": _clean_code_snippet(cmd, max_lines=3, max_chars=200),
                "thought": _clean_code_snippet(thought or "", max_lines=4, max_chars=250),
                "observation_snippet": _clean_code_snippet(obs, max_lines=4, max_chars=250),
            }
            break

    result: Dict[str, Any] = {}
    if algo_anchor:
        result["algorithm_or_param_definition"] = algo_anchor
    if deliv_anchor:
        result["deliverable_generation"] = deliv_anchor
    return result


def render_causal_anchors_markdown(anchors: Dict[str, Any], lang: str = "zh") -> List[str]:
    """Render markdown card for causal anchors."""
    if not anchors:
        return []

    lines: List[str] = []
    is_zh = lang != "en"

    title = "### 核心因果链锚点 (Key Trajectory Causal Anchors)" if lang == "bilingual" else (
        "### 核心因果链锚点" if is_zh else "### Key Trajectory Causal Anchors"
    )
    lines.append(f"{title}\n")

    algo = anchors.get("algorithm_or_param_definition")
    if algo:
        label = "参数与算法定义 (Algorithm / Param Definition)" if lang == "bilingual" else (
            "参数与算法定义" if is_zh else "Algorithm / Param Definition"
        )
        tool_desc = f"`{algo['event_id']}` ({algo.get('tool_name', 'Tool')}: `{algo.get('target_path', '')}`)"
        lines.append(f"- **{label}**: {tool_desc}")
        if algo.get("thought"):
            thought_lbl = "智能体思考 / Agent Thought" if lang == "bilingual" else ("智能体思考" if is_zh else "Agent Thought")
            lines.append(f"  > 💭 **{thought_lbl}**: \"{algo['thought']}\"")
        if algo.get("code_snippet"):
            lines.append("  ```python")
            for cl in algo["code_snippet"].splitlines():
                lines.append(f"  {cl}")
            lines.append("  ```")

    deliv = anchors.get("deliverable_generation")
    if deliv:
        label = "交付物生成执行 (Deliverable Generation)" if lang == "bilingual" else (
            "交付物生成执行" if is_zh else "Deliverable Generation"
        )
        tool_desc = f"`{deliv['event_id']}` ({deliv.get('tool_name', 'Command')})"
        lines.append(f"- **{label}**: {tool_desc}")
        if deliv.get("thought"):
            thought_lbl = "智能体思考 / Agent Thought" if lang == "bilingual" else ("智能体思考" if is_zh else "Agent Thought")
            lines.append(f"  > 💭 **{thought_lbl}**: \"{deliv['thought']}\"")
        if deliv.get("command"):
            lines.append("  ```bash")
            for cl in deliv["command"].splitlines():
                lines.append(f"  {cl}")
            lines.append("  ```")

    lines.append("")
    return lines
