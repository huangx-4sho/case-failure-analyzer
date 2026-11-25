"""Identify a candidate subprocess traceback and bind its last frame to a saved write."""

import re
from pathlib import Path

from deliverable_audit import captured_container_path


def candidate_tracebacks(text):
    """Index only explicitly labelled nonzero candidate exits, by terminal log line."""
    lines = text.splitlines()
    frame = None
    in_candidate = False
    errors = {}
    for number, line in enumerate(lines, 1):
        if "Traceback (most recent call last):" in line:
            in_candidate = bool(
                re.search(
                    r"\bcandidate exit [1-9]\d*:\s*Traceback \(most recent call last\):\s*$",
                    line,
                    re.I,
                )
            )
            frame = None
        elif in_candidate:
            match = re.fullmatch(r'  File "([^"]+)", line (\d{1,9}), in .+', line)
            if match:
                statement = (
                    lines[number].strip()
                    if number < len(lines) and lines[number].startswith("    ")
                    else ""
                )
                frame = (match[1], int(match[2]), statement)
            elif line and not line[0].isspace():
                if frame and re.match(r"^[\w.]+(?:Error|Exception):", line):
                    errors[number] = (*frame, line.strip())
                in_candidate = False
    return errors


def bound_replay_contract(frame, timeline, trial, instruction, log_source, pointer):
    """Require a public entry point, the captured statement and a successful matching writer."""
    path, number, statement, exception = frame
    if not trial or not statement or not number:
        return None
    name = Path(path).name
    public_paths = set(re.findall(r"(?<![\w./-])/[\w./-]+\.py\b", instruction))
    paths = [p for p in public_paths if Path(p).name == name]
    if len(paths) != 1:
        return None
    capture = captured_container_path(trial, paths[0])
    if not capture:
        return None
    try:
        source = trial / capture
        if source.stat().st_size > 2_000_000:
            return None
        lines = source.read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError):
        return None
    if number > len(lines) or lines[number - 1].strip() != statement:
        return None
    for event in timeline:
        args = event.get("arguments") or {}
        if (
            event.get("tool_name") not in {"Write", "Edit"}
            or event.get("tool_succeeded") is not True
            or not isinstance(args, dict)
            or args.get("file_path") != paths[0]
        ):
            continue
        payload = args.get("content") if event["tool_name"] == "Write" else args.get("new_string")
        if isinstance(payload, str) and any(
            line.strip() == statement for line in payload.splitlines()
        ):
            return {
                "contract_id": "contract:candidate_replay_exit",
                "item": "candidate_replay_exit",
                "alignment": "agent_mismatch",
                "prompt_requirement": paths[0],
                "verifier_requirement": "execute the submitted entry point",
                "agent_output_status": "raised_exception",
                "event_ref": event["event_id"],
                "log_source": log_source,
                "source_refs": ["task:instruction.md", capture, log_source, event["event_id"]],
                "source_pointer": pointer,
                "details": f"{name}:{number} · {statement} · {exception}",
            }
    return None
