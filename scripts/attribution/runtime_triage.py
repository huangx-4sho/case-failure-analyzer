"""Compare candidate upstream explanations for a recorded timeout or SDK stop."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from pathlib import PurePosixPath


TRIAGE_CODES = {
    "INFRA_AGENT_TIMEOUT",
    "INFRA_AGENT_OUTPUT_TOKEN_LIMIT",
    "INFRA_AGENT_AUTOCOMPACT_THRASHING",
}


def build_runtime_triage(
    primary: dict, timeline: list, signals: list, runtime: dict | None = None
) -> dict | None:
    """Extract bounded, ordered observations; terminal limits do not assign upstream fault."""
    code = primary["code"]
    if code not in TRIAGE_CODES:
        return None
    tools = [e for e in timeline if e.get("event_id") and e.get("tool_name")]
    last = tools[-5:]
    trace_refs = {e["event_id"]: e for e in tools}
    triage = {
        "status": "review_required",
        "terminal_ref": primary["error_id"],
        "tool_events": len(tools),
        "failed_tool_events": sum(_failed(e) for e in tools),
        "last_tool_events": [
            {
                "event_ref": e["event_id"],
                "tool": e["tool_name"],
                "result_recorded": e.get("result_recorded"),
                "exit_code": e.get("exit_code"),
            }
            for e in last
        ],
        "leads": [],
    }
    leads = triage["leads"]
    agent_stage = ((runtime or {}).get("stages") or {}).get("agent_execution") or {}
    if last:
        last_timestamp = last[-1].get("timestamp")
        finish = agent_stage.get("finished_at")
        if isinstance(last_timestamp, str) and isinstance(finish, str):
            try:
                elapsed = (
                    datetime.fromisoformat(finish.replace("Z", "+00:00"))
                    - datetime.fromisoformat(last_timestamp.replace("Z", "+00:00"))
                ).total_seconds()
            except (TypeError, ValueError):
                pass
            else:
                if elapsed >= 0:
                    triage["last_tool_to_agent_finish_seconds"] = round(elapsed, 1)
    if code == "INFRA_AGENT_TIMEOUT":
        cap = primary.get("timeout_seconds")
        if cap is not None:
            triage["timeout_seconds"] = cap
            leads.append(_lead("case", "configured_budget_vs_workload", [primary["error_id"]]))
        retries = [
            trace_refs[s["event_ref"]]
            for s in signals
            if s.get("signal_type") in {"iterative_debugging_failure", "repeated_failed_action"}
            and s.get("event_ref") in trace_refs
            and _failed(trace_refs[s["event_ref"]])
        ]
        if retries:
            leads.append(_lead("agent", "unproductive_retry_or_timeout_burn", _refs(retries[-3:])))
        reads = [
            e
            for e in last
            if e["tool_name"].casefold() == "read"
            and e.get("command")
            and "the contents are empty" in str(e.get("observation") or "")
        ]
        repeated = Counter(e["command"] for e in reads)
        if repeated and max(repeated.values()) >= 2:
            target = repeated.most_common(1)[0][0]
            refs = _refs(e for e in reads if e["command"] == target)
            leads.append(_lead("agent", "repeated_empty_reads_before_stop", refs))
        if last and last[-1].get("result_recorded") is False:
            leads.append(_lead("agent", "unfinished_terminal_tool_call", _refs(last[-1:])))
    elif code == "INFRA_AGENT_OUTPUT_TOKEN_LIMIT":
        if "output_token_ceiling" in primary:
            triage["output_token_ceiling"] = primary["output_token_ceiling"]
        if primary.get("terminal_thinking_estimate"):
            triage["terminal_thinking_estimate"] = primary["terminal_thinking_estimate"]
            leads.append(_lead("agent", "reasoning_token_runaway", [primary["error_id"]]))
    else:
        image_reads = [
            e
            for e in last
            if e["tool_name"].casefold() == "read"
            and PurePosixPath(e.get("command") or "").suffix.casefold()
            in {".png", ".jpg", ".jpeg", ".webp", ".gif"}
            and '"type": "image"' in str(e.get("observation") or "")
        ]
        if len(image_reads) >= 2:
            leads.append(_lead("agent", "context_overflow_by_large_read", _refs(image_reads)))
    return triage


def _failed(event: dict) -> bool:
    return event.get("exit_code") not in (None, 0) or event.get("event_type") == "shell_error"


def _refs(events) -> list:
    return [e["event_id"] for e in events]


def _lead(category: str, mechanism: str, refs: list) -> dict:
    return {"category": category, "mechanism": mechanism, "evidence_refs": refs}


def render_runtime_triage(analysis: dict, lang: str) -> str:
    manifestation = analysis.get("failure_manifestation")
    data = manifestation.get("runtime_triage") if isinstance(manifestation, dict) else None
    if not data:
        return ""
    en = lang == "en"
    actions = data["tool_events"]
    failures = data["failed_tool_events"]
    lines = [
        f"> Recorded tool calls · {actions}, with {failures} recorded failures."
        if en
        else f"> 已记录工具调用 {actions} 次，其中 {failures} 次记录为失败。"
    ]
    if (budget := data.get("timeout_seconds")) is not None:
        lines.append(
            f"> Runner timeout · {budget:g} s." if en else f"> 执行器记录的时限为 {budget:g} 秒。"
        )
    if (gap := data.get("last_tool_to_agent_finish_seconds")) is not None:
        lines.append(
            f"> {gap:g} s elapsed from the last recorded tool call to the recorded end of agent execution."
            if en
            else f"> 最后一次有记录的工具调用距执行阶段结束 {gap:g} 秒。"
        )
    if estimate := data.get("terminal_thinking_estimate"):
        value = estimate["estimated_tokens"]
        cap = data.get("output_token_ceiling")
        ceiling = f" Recorded output limit: {cap:,} tokens." if cap else ""
        zh_ceiling = f"，输出上限为 {cap:,} token" if cap else ""
        lines.append(
            f"> SDK thinking estimate near the stop · {value:,} tokens.{ceiling}"
            if en
            else f"> 中断前 SDK 估算思考用量为 {value:,} token{zh_ceiling}。"
        )
    labels = {
        "configured_budget_vs_workload": (
            "配置时限与实际工作量",
            "configured budget versus workload",
        ),
        "unproductive_retry_or_timeout_burn": ("仍失败的重试", "retries with recorded failure"),
        "repeated_empty_reads_before_stop": (
            "中断前重复读取无内容的文件",
            "repeated empty reads before the stop",
        ),
        "unfinished_terminal_tool_call": (
            "末次调用缺少结果记录",
            "final tool call with no recorded result",
        ),
        "reasoning_token_runaway": (
            "输出上限附近的思考用量",
            "thinking usage near the output limit",
        ),
        "context_overflow_by_large_read": (
            "上下文压缩中断前的连续图像读取",
            "repeated image reads before the compaction stop",
        ),
    }
    for lead in data["leads"]:
        refs = ", ".join(f"`{ref}`" for ref in lead["evidence_refs"])
        label = labels[lead["mechanism"]][int(en)]
        prefix = "Review lead" if en else "待复核线索"
        lines.append(f"> {prefix} · {label} ({refs}).")
    return "\n".join(lines) + "\n"
