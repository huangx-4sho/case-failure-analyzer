"""Classify the evidenced decision within an agent failure, scoped to one trial."""

from __future__ import annotations

DIMENSIONS = {
    "observable_definition": (
        "科学问题与物理量定义",
        "Scientific question and observable definition",
    ),
    "physical_model": ("体系构建与物理建模", "System construction and physical modelling"),
    "numerical_adequacy": ("数值收敛与采样充分性", "Numerical convergence and sampling adequacy"),
    "workflow_state": ("工作流编排与状态传递", "Workflow and state handoff"),
    "execution_recovery": ("软件执行与故障恢复", "Tool execution and recovery"),
    "postprocessing_provenance": ("后处理与结果溯源", "Post-processing and result provenance"),
    "independent_validation": ("独立验证与自检", "Independent validation and self-checking"),
    "conclusion_calibration": ("科学结论与不确定性判断", "Scientific conclusions and uncertainty"),
}


def unassigned() -> dict:
    return {"status": "needs_review", "dimension": None, "evidence_refs": []}


def static_diagnosis(evidence: dict, analysis: dict, events: set) -> dict:
    """Use decision-specific observations. Generic root codes and failed checks abstain."""
    code = (analysis.get("primary_root_cause") or {}).get("code")
    cited = set(analysis.get("evidence_refs") or [])

    def candidate(dimension, event, refs, zh, en):
        return {
            "status": "candidate",
            "dimension": dimension,
            "event_ref": event,
            "evidence_refs": list(dict.fromkeys([event, *refs])),
            "rationale_zh": zh,
            "rationale_en": en,
        }

    if code == "AGENT_TASK_UNDERSTANDING":
        findings = (evidence.get("scientific_contract_review") or {}).get("findings") or []
        for finding in findings:
            event = (finding.get("observed_code") or {}).get("event_ref")
            contracts = [
                c["contract_id"]
                for c in evidence.get("contract_observations") or []
                if c.get("contract_id") in cited
                and c.get("item") == "stopping_force_definition"
                and event in (c.get("source_refs") or [])
            ]
            if (
                finding.get("code") == "AGENT_FORCE_STOP_METRIC_MISMATCH"
                and event in events
                and contracts
                and finding.get("public_requirement")
                and finding.get("saved_failure")
                and finding.get("application_binding") == "bound"
            ):
                return candidate(
                    "observable_definition",
                    event,
                    contracts,
                    "实际停止条件取最大力分量。公开协议写“最大原子力”，评分器取每原子受力向量模长的最大值。",
                    "The stopping test used the largest force component. The public protocol specifies maximum atomic force, and the scorer takes the largest per-atom force norm.",
                )

    for signal in evidence.get("behavioral_signals") or []:
        event, ref = signal.get("event_ref"), signal.get("signal_id")
        if event not in events or ref not in cited:
            continue
        if code == "AGENT_PREMATURE_TERMINATION" and signal.get(
            "confirmed_unfinished_remote_delivery"
        ):
            return candidate(
                "workflow_state",
                event,
                [ref],
                "末次调度查询仍显示作业运行，Agent 已结束本轮，声明的交付文件随后被记录为缺失。",
                "The final scheduler query showed active jobs when the agent ended the turn; the required deliverable was subsequently recorded missing.",
            )
        if (
            code == "AGENT_ERROR_DIAGNOSIS"
            and signal.get("signal_type") == "repeated_failed_action"
        ):
            earlier = {}
            retry = {}
            for action in evidence.get("timeline") or []:
                if action.get("event_id") == event:
                    retry = action
                    break
                earlier[action.get("event_id")] = action
            for obs in evidence.get("scientific_observations") or []:
                first = earlier.get(obs.get("source_ref"), {})
                if (
                    obs.get("sci_id") in cited
                    and obs.get("source_ref") in events
                    and first.get("command")
                    and first["command"] == retry.get("command")
                    and obs.get("recovered") is False
                    and obs.get("matched_text")
                    and obs["matched_text"] in (first.get("observation") or "")
                    and obs["matched_text"] in (retry.get("observation") or "")
                ):
                    return candidate(
                        "execution_recovery",
                        event,
                        [obs["source_ref"], ref, obs["sci_id"]],
                        "相同命令再次返回已记录的科学计算错误。需核对两次生效的输入和恢复方案。",
                        "The repeated command returned the same recorded scientific error. Compare effective inputs and recovery choices across the two attempts.",
                    )
    return unassigned()


def reviewed_diagnosis(annotation: dict, decision: dict, level: str, events: set) -> dict:
    """Bind one authored dimension to the decision whose sources were already checked."""
    if not isinstance(annotation, dict):
        raise ValueError("capability_dimension must be an object")
    abstain = annotation.get("status") == "insufficient_evidence"
    fields = {"dimension", "rationale_zh", "rationale_en", "evidence_refs"}
    if abstain:
        fields = {"status", "rationale_zh", "rationale_en"}
    if set(annotation) != fields:
        raise ValueError(f"capability_dimension requires {', '.join(sorted(fields))}")
    if not all(
        isinstance(annotation[k], str) and annotation[k].strip()
        for k in ("rationale_zh", "rationale_en")
    ):
        raise ValueError(
            "capability_dimension requires a written rationale in both report languages"
        )
    if abstain:
        return {**unassigned(), **annotation}
    if decision["kind"] not in ("scientific", "parameter", "execution", "delivery"):
        raise ValueError("capability_dimension requires a reviewed agent decision")
    if not isinstance(annotation["dimension"], str) or annotation["dimension"] not in DIMENSIONS:
        raise ValueError("Unknown capability dimension")
    refs = annotation["evidence_refs"]
    event = decision.get("event_ref")
    if (
        not isinstance(refs, list)
        or not all(isinstance(r, str) for r in refs)
        or event not in events
        or event not in refs
    ):
        raise ValueError("capability_dimension must cite the reviewed execution decision")
    bound = decision["execution_binding"]
    basis = set(decision["basis"].get("evidence_refs") or [])
    outcome = set(decision["outcome"].get("evidence_refs") or [])
    allowed = (
        basis
        | outcome
        | set(bound.get("evidence_refs") or [])
        | set(bound.get("effective_input_refs") or [])
        | set(bound.get("effective_output_refs") or [])
    )
    if (
        not set(refs).issubset(allowed)
        or not (basis.intersection(refs) - {event})
        or not (outcome.intersection(refs) - {event})
    ):
        raise ValueError(
            "capability_dimension requires cited basis and outcome from the same decision"
        )
    return {
        **annotation,
        "evidence_refs": list(dict.fromkeys(refs)),
        "event_ref": event,
        "status": "supported_in_trial" if level == "evidence_supported_cause" else "candidate",
    }


def render_diagnosis(analysis: dict, lang: str) -> str:
    cap = (analysis.get("causal_assessment") or {}).get("capability") or {}
    diagnosis = cap.get("diagnosis")
    if not diagnosis:
        return ""
    en = lang == "en"
    header = "**Agent capability in this trial**" if en else "**本次 Agent 能力诊断**"
    dimension = diagnosis.get("dimension")
    if dimension is None:
        if diagnosis.get("status") == "insufficient_evidence":
            return (
                header
                + (" · Insufficient evidence. " if en else " · 证据不足。")
                + diagnosis["rationale_en" if en else "rationale_zh"]
            )
        return header + (
            " · Awaiting dimension review. Inspect the applied decision, its requirement and its effect on the failed check."
            if en
            else " · 待标注。请核对实际采用的做法、适用要求及其与失败项的关系。"
        )
    supported = diagnosis["status"] == "supported_in_trial"
    status = (
        ("Supported in this trial" if supported else "Candidate")
        if en
        else ("本次审查支持" if supported else "候选")
    )
    refs = ", ".join(f"`{r}`" for r in diagnosis["evidence_refs"])
    return f"{header} · {DIMENSIONS[dimension][int(en)]} ({status}) · {diagnosis['rationale_en' if en else 'rationale_zh']} {refs}"
