"""Bounded static review of scientific definitions in saved execution evidence.

This module reads artifacts only. It never evaluates submitted code or recomputes
forces. Findings are candidates for causal review, not capability judgments.
"""

import csv
import io
import re

from runtime_state import resolve_trajectory_path
from normalize_trajectory import (
    _parse_trajectory_payload,
    heredoc_written_path,
    _extract_heredoc_body,
)
from pathlib import Path

MAX_BYTES = 8 * 1024 * 1024


def _read(path, uncertainty):
    if not path.is_file():
        return ""
    if path.stat().st_size > MAX_BYTES:
        uncertainty.append(f"Read limit exceeded: {path}")
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def audit_scientific_contracts(evidence):
    result = {"mode": "read_only_static_review", "findings": [], "uncertainty": []}
    uncertainty = result["uncertainty"]
    if not evidence.get("trial_dir") or not evidence.get("task_dir"):
        uncertainty.append("Trial/task locations unavailable; static definition review incomplete.")
        return result
    trial, task = Path(evidence["trial_dir"]), Path(evidence["task_dir"])
    trajectory_path = resolve_trajectory_path(trial)
    raw = _read(trajectory_path, uncertainty) if trajectory_path else ""
    trajectory = _parse_trajectory_payload(raw)
    normalization = trajectory.get("normalization") or {}
    uncertainty.extend(normalization.get("diagnostics") or [])
    if not raw:
        uncertainty.append("Saved trajectory unavailable.")
        return result
    failure_refs = []
    for rel in ("verifier/test-stdout.txt", "verifier/scorer.stdout", "verifier/verification.json"):
        text = _read(trial / rel, uncertainty)
        if re.search(
            r"stopped before force convergence|force.convergence.*(?:fail|not|unmet)", text, re.I
        ):
            failure_refs.append(
                {"source_file": rel, "source_pointer": "L1", "excerpt": text[:1000]}
            )
    if not failure_refs:
        return result
    verifier = _read(task / "tests/verify.py", uncertainty)
    norm = re.search(r"(?:np\.)?linalg\.norm\([^\n]*?(?:forces|force)[^\n]*?axis\s*=\s*1", verifier)
    stop = re.search(r"if\s+\w+\[-1\]\s*<=\s*[^\n]*force[^\n]*", verifier, re.I)
    if not norm or not stop:
        return result
    candidates, public = [], []
    writes = {}
    execution_events = []
    order = 0
    assignment = re.compile(
        r"(?P<metric>\w+)\s*=\s*(?:float\(\s*)?np\.abs\((?P<force>\w*[fF]\w*)\)\.max\(\)"
    )
    for step in trajectory.get("steps", [])[:5000]:
        ref = f"trajectory:step:{step.get('step_id')}"
        calls = {c.get("tool_call_id"): c for c in step.get("tool_calls", [])}
        observations = step.get("observation") or {}
        if isinstance(observations, dict):
            for obs in observations.get("results", []):
                call = calls.get(obs.get("source_call_id"), {})
                command = str((call.get("arguments") or {}).get("command", ""))
                text = str(obs.get("content", ""))
                # Public requirements must come from a saved read of supplied
                # assets, never the agent's own report or private verifier.
                if re.search(r"(?:cat|open\().*/(?:assets|inputs)/", command) and re.search(
                    r"max.atomic.force|max per.atom.*force", text, re.I
                ):
                    lines = [
                        line
                        for line in text.splitlines()
                        if re.search(
                            r"max.atomic.force|stopping|force_threshold|terminal_status", line, re.I
                        )
                    ]
                    public.append(
                        {
                            "source_ref": ref,
                            "source_pointer": "observation/results",
                            "excerpt": "\n".join(lines)[:1800],
                        }
                    )
        for index, call in enumerate(step.get("tool_calls", [])):
            args = call.get("arguments") or {}
            order += 1
            command = str(args.get("command", ""))
            source = command + "\n" + str(args.get("content", ""))
            target = heredoc_written_path(command) or args.get("file_path") or args.get("path")
            is_replacement = bool(heredoc_written_path(command) or (args.get("content") and target))
            if is_replacement:
                writes[str(target)] = order
                candidates = [c for c in candidates if c.get("script_target") != str(target)]
            event_ref = f"{ref}:tool:{index}"
            # Link a run to its own tool result, rather than any output from
            # a different call in the same turn.
            linked = (
                [
                    o
                    for o in observations.get("results", [])
                    if o.get("source_call_id") == call.get("tool_call_id")
                ]
                if isinstance(observations, dict) and call.get("tool_call_id")
                else []
            )
            shell_source = command
            if heredoc_written_path(command):
                body = _extract_heredoc_body(command)
                if body:
                    shell_source = command.replace(body, "", 1)
            for run in re.finditer(
                r"(?:^|[;\n&|])\s*python(?:3)?\s+([^\s;&|]+\.py)\b", shell_source
            ):
                for obs in linked:
                    content = str(obs.get("content", ""))
                    if "force_threshold" in content and re.search(
                        r"fmax|accepted|forward|reverse", content, re.I
                    ):
                        execution_events.append(
                            {
                                "order": order,
                                "script_target": run.group(1).strip("'\""),
                                "event_ref": event_ref,
                                "source_ref": ref,
                                "source_pointer": "observation/results",
                                "command_excerpt": command[:200],
                                "excerpt": content[:700],
                            }
                        )
            for match in assignment.finditer(source):
                metric = match.group("metric")
                tail = source[match.end() : match.end() + 5000]
                condition = re.search(
                    r"if\s+"
                    + re.escape(metric)
                    + r"\s*<=\s*(?P<threshold>[\w.]+)\s*:\s*\n?\s*(?P<body>[^\n]*(?:\n[^\n]*)?)",
                    tail,
                )
                if not condition:
                    continue
                body = condition.group("body")
                if "break" not in body or not re.search(r"force_threshold|converg", body, re.I):
                    continue
                if re.search(r"\b" + re.escape(metric) + r"\s*=(?!=)", tail[: condition.start()]):
                    continue
                candidates.append(
                    {
                        "event_ref": event_ref,
                        "script_target": str(target) if target else None,
                        "write_order": order,
                        "source_ref": ref,
                        "source_pointer": f"tool_calls/{index}/arguments",
                        "assignment": match.group(0),
                        "stopping_condition": condition.group(0),
                        "excerpt": source[
                            max(0, match.start() - 150) : match.end() + min(condition.end(), 1000)
                        ],
                    }
                )
    if not candidates:
        return result
    candidate = candidates[-1]
    target = candidate.get("script_target")
    execution_refs = [
        event
        for event in execution_events
        if target
        and event["order"] >= candidate["write_order"]
        and event["order"] >= writes.get(target, 0)
        and (
            event["script_target"] == target
            or (
                not Path(event["script_target"]).is_absolute()
                and Path(event["script_target"]).name == Path(target).name
            )
        )
    ]
    application_binding = "bound" if execution_refs else "unresolved"
    retained = []
    # Execution accounting is retained evidence of claimed stops, not a new
    # numerical verification of force values or an inferred replay result.
    path = trial / "artifacts/app/final/execution_trace.csv"
    trace = _read(path, uncertainty)
    if trace:
        for row in list(csv.DictReader(io.StringIO(trace)))[:1000]:
            if row.get("stop_status") == "force_threshold":
                retained.append(
                    {"source_file": "artifacts/app/final/execution_trace.csv", "row": row}
                )
    if not retained:
        uncertainty.append("No retained execution trace confirms a claimed force-threshold stop.")
    finding_uncertainty = [
        "Static code and retained stop records do not establish recomputed endpoint forces; the saved verifier failure supplies the outcome binding."
    ]
    if application_binding == "unresolved":
        finding_uncertainty.append(
            "Cannot bind the observed source version to a later saved execution of that script."
        )
    if not public:
        finding_uncertainty.append(
            "Public force definition unavailable in retained reads; requirement scope remains uncertain."
        )
    result["findings"].append(
        {
            "code": "AGENT_FORCE_STOP_METRIC_MISMATCH",
            "status": "candidate",
            "application_binding": application_binding,
            "category": "agent",
            "subtype": "scientific_definition_mismatch",
            "summary": "Submitted stopping code uses the maximum absolute force component; the failed convergence check uses the maximum per-atom Euclidean force norm.",
            "public_requirement": public[:6],
            "observed_code": candidates[-1],
            "stopping_condition": candidates[-1]["stopping_condition"],
            "verifier_definition": {
                "source_file": "tests/verify.py",
                "source_pointer": f"L{verifier[: norm.start()].count(chr(10)) + 1}",
                "excerpt": norm.group(0),
                "threshold_condition": stop.group(0),
            },
            "saved_failure": failure_refs,
            "retained_output": retained,
            "saved_execution": execution_refs[-6:],
            "scope": "public_requirement_observed" if public else "public_requirement_uncertain",
            "uncertainty": finding_uncertainty,
        }
    )
    return result
