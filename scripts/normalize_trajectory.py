#!/usr/bin/env python3
"""
Phase 2: Trajectory Normalization (`scripts/normalize_trajectory.py`)

Normalizes ATIF-v1.7 (and compatible) `agent/trajectory.json` files into a
unified event timeline and derives objective behavioral signals with exact
JSON pointers back to raw trajectory steps.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


SUPPORTED_SCHEMAS = {"ATIF-v1.7", "ATIF-v1.6", "ATIF-v1.5"}

#: Shell tools whose *final* argument is the write destination (`cp src dst`,
#: `mv src dst`, `rsync src dst/`, `tee file`, `sed -i file`).
_SHELL_WRITE_TOOL_RE = re.compile(r"(?:\bcp\b|\bmv\b|\brsync\b|\btee\b|\binstall\b|\bsed\s+-i)")

#: Function-style or path-taking writers where the deliverable appears among the
#: keyword's arguments rather than as the command's last token.
_FUNC_WRITE_RE = re.compile(
    r"(?:write_text|json\.dump|\.write\s*\(|\bsave\b|\bcopy\b|\bopen\s*\("
    r"|\bmkdir\b|\bmakedirs\b|\btouch\b|\btar\b|\bscp\b)"
)


def _path_covers(path: str, deliverable: str) -> bool:
    """True when `path` is `deliverable` itself or lives inside it."""
    p = (path or "").strip().rstrip("/")
    d = (deliverable or "").strip().rstrip("/")
    if not p or not d:
        return False
    return p == d or p.startswith(d + "/")


def _command_writes_path(cmd: str, deliverable: str) -> bool:
    """
    True when a shell command appears to *write* `deliverable` rather than merely
    read, list, or redirect stderr past it.

    Three accepted forms:
      1. a redirect whose immediate target is the path (`... > /app/final/x`);
      2. a shell copy/move tool whose final argument is the path
         (`cp /tmp/x /app/final/`);
      3. a function-style or directory-creating writer with the path among its
         arguments (`open('/app/final/x.json','w')`, `mkdir -p /app/final`).
    `2>&1` fd duplication and `ls /app/final 2>/dev/null` never match.
    """
    d = (deliverable or "").strip().rstrip("/")
    if not d or not cmd:
        return False

    # 1. Redirect directly into the deliverable.
    if re.search(r">>?\s*['\"]?" + re.escape(d) + r"[/'\"]?", cmd):
        return True

    # 2. Copy/move tools: the deliverable must be the final argument.
    if _SHELL_WRITE_TOOL_RE.search(cmd):
        last_token = cmd.strip().split()[-1].strip("'\"")
        if _path_covers(last_token, d):
            return True

    # 3. Function-style writers: the path appears among the arguments.
    for m in _FUNC_WRITE_RE.finditer(cmd):
        if d in cmd[m.end() : m.end() + 200]:
            return True
    return False


def _deliverable_written(
    deliverable: str,
    written_files: List[str],
    commands: List[str],
) -> bool:
    """
    Best-effort check that the agent produced `deliverable` somewhere in the
    trajectory: an explicit file write, or a shell command that writes to the
    path.
    """
    for path in written_files:
        if _path_covers(path, deliverable):
            return True
    for cmd in commands:
        if deliverable in cmd and _command_writes_path(cmd, deliverable):
            return True
    return False


#: Input roots the agent must never write to.
_PROTECTED_INPUT_RE = re.compile(
    r"(?:/workspace/assets|/inputs\b|/submission\b|/opt/[\w.-]*public/inputs)"
)
#: fd duplication / discard redirects that are not file writes.
_FD_REDIRECT_RE = re.compile(r"\d*>&\d+|&>\s*/dev/null|\d*>\s*/dev/null|\d*>>\s*/dev/null")
#: Shell-level mutation of the workspace (used for edit-aware retry detection).
_SHELL_MUTATION_RE = re.compile(
    r"(?:(?<![\d&-])>>?\s*[^\s&|;<>]|\btee\b|\bsed\s+-i|\bcp\b|\bmv\b|\brm\b"
    r"|\bpatch\b|\bgit\s+(?:apply|checkout)\b)"
)
_HEREDOC_TARGET_RE = re.compile(
    r"cat\s*>>?\s*(?P<p>[^\s<]+)\s*<<-?\s*['\"]?\w+['\"]?"
    r"|<<-?\s*['\"]?\w+['\"]?\s*>>?\s*(?P<p2>[^\s]+)"
)
_DEP_SEARCH_RE = re.compile(
    r"(?:\bwhich\s+\S|\bcommand\s+-v\b|\blocate\s+\S|\bfind\s+/(?:opt|usr|workspace/assets)\b"
    r"|\bls\s+/opt\b|\bls\s+/workspace/assets\b|\bpip\s+(?:list|show)\b)"
)
#: Canonical agent-authored self-check script names. Deliberately excludes the
#: benchmark's own `tests/verify.py` (a bare `verify.py` / `check.py` is not an
#: agent self-check). Shared with `trace_causal_anchors.py` so the behavioral
#: signal and the causal anchor never disagree about what counts as a self-check.
SELF_CHECK_SCRIPT_RE = re.compile(
    r"(?:verify_outputs|check_outputs|validate_outputs|verify_deliverables|"
    r"check_results|run_checks|self[_-]?check|selfcheck|sanity_check)"
    r"[a-z0-9_.-]*\.(?:py|sh)\b",
    re.IGNORECASE,
)
#: A self-check must actually be *executed*, not merely mentioned in a `cat`
#: / `Read` command.
SELF_CHECK_RUN_RE = re.compile(r"(?:\bpython3?\b|\b(?:ba)?sh\b|\bpytest\b|\bnode\b|\./)")


def strip_fd_redirects(cmd: str) -> str:
    """Remove `2>&1` / `>/dev/null` style redirects so they never read as file writes."""
    return _FD_REDIRECT_RE.sub(" ", cmd or "")


def shell_writes_protected_input(cmd: str) -> bool:
    """True only when a write (redirect/tee/sed -i/cp/mv) targets a protected input root."""
    c = strip_fd_redirects(cmd)
    for m in re.finditer(
        r"(?:(?<![\d&-])>>?\s*(?P<r>[^\s&|;<>]+)|\btee\s+(?:-a\s+)?(?P<t>\S+)"
        r"|\bsed\s+-i\S*\s+(?:-e\s+)?(?:'[^']*'|\"[^\"]*\"|\S+)\s+(?P<s>\S+)"
        r"|\b(?:cp|mv)\s+[^;&|]*?\s(?P<d>\S+)\s*(?:$|[;&|]))",
        c,
    ):
        target = next((g for g in m.groupdict().values() if g), "")
        if _PROTECTED_INPUT_RE.search(target.strip("'\"")):
            return True
    return False


def heredoc_written_path(cmd: str) -> Optional[str]:
    m = _HEREDOC_TARGET_RE.search(cmd or "")
    if not m:
        return None
    return (m.group("p") or m.group("p2") or "").strip("'\"") or None


def summarize_code_payload(args: Dict[str, Any], limit: int = 600) -> Optional[str]:
    """Compact, salience-aware snapshot of what a Write/Edit tool put into a file."""
    new = args.get("content") or args.get("new_string") or args.get("newString") or ""
    if not new and isinstance(args.get("edits"), list):
        new = "\n".join(str(e.get("new_string", "")) for e in args["edits"] if isinstance(e, dict))
    if not isinstance(new, str) or not new.strip():
        return None
    code_text = new
    # Skip leading module docstring to surface real parameter and code statements
    m_doc = re.match(r"^\s*(?:#.*?\n)*\s*(?:\"\"\"[\s\S]*?\"\"\"|'''[\s\S]*?''')\s*\n?", code_text)
    if m_doc and len(code_text) > m_doc.end():
        code_text = code_text[m_doc.end() :]
    keep = re.compile(
        r"(?:=\s*[-+0-9.eE]+|tol|cutoff|range|eta|alpha|spin|mult|charge|basis|conv|max_|"
        r"\bdef\s|\bclass\s|import\s|open\(|json\.dump|to_csv|argparse)",
        re.IGNORECASE,
    )
    lines = [ln.rstrip() for ln in code_text.splitlines() if ln.strip()]
    picked = [ln for ln in lines if keep.search(ln)] or lines
    out = "\n".join(picked[:12])
    return out[:limit]


def extract_thought(step: Dict[str, Any]) -> Optional[str]:
    for key in ("reasoning_content", "thinking", "thought", "reasoning"):
        val = step.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def truncate_with_head_tail(
    text: Optional[str], max_bytes: int = 2000
) -> Tuple[Optional[str], Optional[str], bool]:
    if text is None:
        return None, None, False
    digest = sha256_text(text)
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return text, digest, False
    half = max(200, max_bytes // 2)
    head = encoded[:half].decode("utf-8", errors="replace")
    tail = encoded[-half:].decode("utf-8", errors="replace")
    truncated = f"{head}\n... [TRUNCATED {len(encoded)} bytes, sha256={digest}] ...\n{tail}"
    return truncated, digest, True


def extract_exit_code_and_error(obs_text: str) -> Tuple[Optional[int], bool]:
    if not obs_text:
        return None, False
    m = re.search(r"(?:Exit code|Return code|exit status)[:\s]+(\d+)", obs_text, re.IGNORECASE)
    if m:
        code = int(m.group(1))
        return code, code != 0
    error_indicators = (
        "Traceback (most recent call last):",
        "command not found",
        "No such file or directory",
        "Permission denied",
        "ERROR:",
        "Fatal Error",
        "ABORT",
        "Segmentation fault",
        "convergence NOT achieved",
        "NOT converged",
        "SCF NOT CONVERGED",
        "cannot be opened",
        "Lost atoms",
        "CUDA out of memory",
        "RuntimeError:",
        "LinAlgError:",
        "ValueError:",
        "KeyError:",
        "Error EDDDAV",
        "ZBRENT:",
        "LINCS WARNING",
        "link 9999",
        "l502.exe",
    )
    if any(ind in obs_text for ind in error_indicators):
        return 1, True
    return 0, False


def classify_tool_event(
    tool_name: str, args: Dict[str, Any], has_error: bool
) -> Tuple[str, Optional[str]]:
    t_lower = (tool_name or "").lower()
    if t_lower in ("read", "view", "cat"):
        return "file_read", args.get("file_path") or args.get("path")
    if t_lower in ("write", "edit", "multiedit", "replace"):
        return "file_write", args.get("file_path") or args.get("path")
    if t_lower in ("bash", "shell", "exec"):
        cmd = args.get("command") or ""
        if has_error:
            return "shell_error", cmd
        return "tool_call", cmd
    return "tool_call", json.dumps(args, ensure_ascii=False)[:200]


def _parse_trajectory_payload(text: str) -> Dict[str, Any]:
    """Parse standard ATIF JSON, JSON array of messages, or JSONL stream into a normalized dict."""
    stripped = text.strip()
    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict):
            if "steps" not in parsed and isinstance(parsed.get("messages"), list):
                parsed["steps"] = _convert_messages_to_steps(parsed["messages"])
                parsed.setdefault("schema_version", "ATIF-v1.7")
            return parsed
        if isinstance(parsed, list):
            return {"schema_version": "ATIF-v1.7", "steps": _convert_messages_to_steps(parsed)}
    except Exception:
        pass

    # Fallback: try line-delimited JSONL
    lines = [ln.strip() for ln in stripped.splitlines() if ln.strip()]
    if lines:
        objs = []
        for ln in lines:
            objs.append(json.loads(ln))
        return {"schema_version": "ATIF-v1.7", "steps": _convert_messages_to_steps(objs)}
    raise ValueError("Unable to parse trajectory file as JSON or JSONL")


def _convert_messages_to_steps(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert generic OpenAI/Anthropic/JSONL message or step records into ATIF-compatible step dicts."""
    steps: List[Dict[str, Any]] = []
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        if "tool_calls" in item or "observation" in item or "step_id" in item:
            steps.append(item)
            continue
        role = item.get("role") or item.get("source") or "agent"
        content = item.get("content")
        if isinstance(content, list):
            text_parts = [
                p.get("text", "")
                for p in content
                if isinstance(p, dict) and p.get("type") == "text"
            ]
            msg_str = "\n".join(t for t in text_parts if t)
        else:
            msg_str = str(content or item.get("message") or "")
        steps.append(
            {
                "step_id": idx + 1,
                "timestamp": item.get("timestamp") or item.get("created_at"),
                "source": "user" if role in ("user", "system") else "agent",
                "message": msg_str,
                "tool_calls": item.get("tool_calls") or [],
            }
        )
    return steps


def normalize_trajectory(
    trajectory_path: Optional[Path],
    max_obs_bytes: int = 2000,
    max_file_bytes: int = 50_000_000,
    expected_deliverables: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Normalize an ATIF agent trajectory into a timeline plus behavioral signals.

    `expected_deliverables` is the harbor task's declared artifact set
    (`task.toml` `artifacts`, e.g. `["/app/final"]`). When provided, the
    `premature_completion` signal fires only if a *declared* deliverable is
    absent from the trajectory — the hardcoded `results.json` heuristic below is
    kept only as a fallback for tasks that declare nothing.
    """
    if trajectory_path is None or not trajectory_path.is_file():
        return {
            "exists": False,
            "schema_version": None,
            "warnings": ["agent/trajectory.json is missing"],
            "events": [],
            "behavioral_signals": [],
            "written_files": [],
            "read_files": [],
        }

    try:
        file_size = trajectory_path.stat().st_size
        if file_size > max_file_bytes:
            return {
                "exists": True,
                "schema_version": None,
                "warnings": [
                    f"trajectory.json size ({file_size} bytes) exceeds max_file_bytes ({max_file_bytes})"
                ],
                "events": [],
                "behavioral_signals": [],
                "written_files": [],
                "read_files": [],
            }
        raw = _parse_trajectory_payload(
            trajectory_path.read_text(encoding="utf-8", errors="replace")
        )
    except Exception as exc:
        return {
            "exists": True,
            "schema_version": None,
            "warnings": [f"Failed to parse trajectory.json: {exc}"],
            "events": [],
            "behavioral_signals": [],
            "written_files": [],
            "read_files": [],
        }

    schema_version = raw.get("schema_version")
    warnings: List[str] = []
    if schema_version not in SUPPORTED_SCHEMAS:
        warnings.append(
            f"Unknown trajectory schema_version={schema_version!r}; preserving raw step pointers."
        )

    steps = raw.get("steps") or []
    events: List[Dict[str, Any]] = []
    signals: List[Dict[str, Any]] = []
    written_files: List[str] = []
    read_files: List[str] = []

    prev_commands: List[Tuple[str, bool, str, int]] = []  # (cmd, failed, event_id, epoch)
    mutation_epoch = 0  # bumps whenever the agent edits/writes anything
    results_json_written = False
    results_json_verified_after_write = False
    ran_simulation = False
    inspected_sim_log = False

    for idx, step in enumerate(steps):
        step_id = step.get("step_id", idx + 1)
        ts = step.get("timestamp")
        source = step.get("source", "agent")
        msg = step.get("message") or ""
        step_ptr = f"/steps/{idx}"

        if source == "user":
            trunc_msg, msg_sha, _ = truncate_with_head_tail(msg, max_obs_bytes)
            events.append(
                {
                    "event_id": f"trajectory:step:{step_id}",
                    "timestamp": ts,
                    "actor": "user",
                    "event_type": "user_instruction",
                    "tool_name": None,
                    "command": None,
                    "observation": trunc_msg,
                    "observation_sha256": msg_sha,
                    "exit_code": None,
                    "source_file": "agent/trajectory.json",
                    "source_pointer": step_ptr,
                }
            )
            continue

        thought = extract_thought(step)
        if thought:
            t_trunc, t_sha, _ = truncate_with_head_tail(thought, max_obs_bytes)
            events.append(
                {
                    "event_id": f"trajectory:step:{step_id}:thought",
                    "timestamp": ts,
                    "actor": "agent",
                    "event_type": "agent_thought",
                    "tool_name": None,
                    "command": None,
                    "observation": t_trunc,
                    "observation_sha256": t_sha,
                    "exit_code": None,
                    "source_file": "agent/trajectory.json",
                    "source_pointer": f"{step_ptr}/thinking",
                }
            )

        # Agent reasoning / message event
        if msg:
            is_final = idx == len(steps) - 1 and not step.get("tool_calls")
            trunc_msg, msg_sha, _ = truncate_with_head_tail(msg, max_obs_bytes)
            events.append(
                {
                    "event_id": f"trajectory:step:{step_id}:msg",
                    "timestamp": ts,
                    "actor": "agent",
                    "event_type": "final_response" if is_final else "agent_reasoning",
                    "tool_name": None,
                    "command": None,
                    "observation": trunc_msg,
                    "observation_sha256": msg_sha,
                    "exit_code": None,
                    "source_file": "agent/trajectory.json",
                    "source_pointer": f"{step_ptr}/message",
                }
            )

        # Map tool observations by source_call_id
        obs_by_call_id: Dict[str, str] = {}
        obs_block = step.get("observation") or {}
        for res_item in obs_block.get("results") or []:
            cid = res_item.get("source_call_id")
            content = res_item.get("content")
            if isinstance(content, list):
                content = "\n".join(str(x) for x in content)
            elif content is not None and not isinstance(content, str):
                content = json.dumps(content, ensure_ascii=False)
            if cid:
                obs_by_call_id[cid] = content or ""

        tool_calls = step.get("tool_calls") or []
        for t_idx, tc in enumerate(tool_calls):
            tc_id = tc.get("tool_call_id") or f"call_{t_idx}"
            fn_name = tc.get("function_name") or tc.get("name") or "unknown"
            args = tc.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {"raw": args}

            obs_text = obs_by_call_id.get(tc_id, "")
            exit_code, has_error = extract_exit_code_and_error(obs_text)
            ev_type, cmd_or_path = classify_tool_event(fn_name, args, has_error)
            trunc_obs, obs_sha, _ = truncate_with_head_tail(obs_text, max_obs_bytes)
            ev_id = f"trajectory:step:{step_id}:tool:{t_idx}"
            tc_ptr = f"{step_ptr}/tool_calls/{t_idx}"

            # Check for repeated command
            norm_cmd = (cmd_or_path or "").strip()
            if fn_name.lower() == "bash" and norm_cmd:
                for prev_cmd, prev_failed, prev_ev, prev_epoch in prev_commands:
                    if prev_cmd == norm_cmd:
                        ev_type = "repeated_command"
                        if prev_failed and prev_epoch != mutation_epoch:
                            signals.append(
                                {
                                    "signal_id": f"sig:iter_debug:{step_id}:{t_idx}",
                                    "signal_type": "iterative_debugging_failure",
                                    "description": (
                                        f"Agent edited files then re-ran a still-failing command "
                                        f"({norm_cmd[:100]}) after {prev_ev}"
                                    ),
                                    "event_ref": ev_id,
                                    "source_pointer": tc_ptr,
                                }
                            )
                        elif prev_failed:
                            signals.append(
                                {
                                    "signal_id": f"sig:repeated_fail:{step_id}:{t_idx}",
                                    "signal_type": "repeated_failed_action",
                                    "description": f"Agent re-executed identical failing command ({norm_cmd[:100]}) after {prev_ev}",
                                    "event_ref": ev_id,
                                    "source_pointer": tc_ptr,
                                }
                            )
                        break
                prev_commands.append((norm_cmd, has_error, ev_id, -1))  # epoch finalized below

                # Behavioral heuristics on Bash commands
                if _DEP_SEARCH_RE.search(strip_fd_redirects(norm_cmd)):
                    signals.append(
                        {
                            "signal_id": f"sig:dep_search:{step_id}:{t_idx}",
                            "signal_type": "dependency_search_attempted",
                            "description": f"Agent searched for binaries or data files: {norm_cmd[:120]}",
                            "event_ref": ev_id,
                            "source_pointer": tc_ptr,
                        }
                    )

                if (
                    SELF_CHECK_SCRIPT_RE.search(norm_cmd)
                    and SELF_CHECK_RUN_RE.search(norm_cmd)
                    and not has_error
                ):
                    signals.append(
                        {
                            "signal_id": f"sig:self_check:{step_id}:{t_idx}",
                            "signal_type": "self_verification_executed",
                            "description": f"Agent ran a self-check script: {norm_cmd[:120]}",
                            "event_ref": ev_id,
                            "source_pointer": tc_ptr,
                        }
                    )

                hd_path = heredoc_written_path(norm_cmd)
                if hd_path:
                    written_files.append(hd_path)

                if _SHELL_MUTATION_RE.search(strip_fd_redirects(norm_cmd)) or hd_path:
                    mutation_epoch += 1
                # A command's own output redirects must not count as an edit between
                # it and its identical retry; finalize its epoch after its own bump.
                if prev_commands and prev_commands[-1][2] == ev_id:
                    c0, f0, e0, _ = prev_commands[-1]
                    prev_commands[-1] = (c0, f0, e0, mutation_epoch)

                if any(
                    bin_k in norm_cmd
                    for bin_k in (
                        "cp2k",
                        "pw.x",
                        "bands.x",
                        "lmp",
                        "lammps",
                        "xtb",
                        "vasp",
                        "abacus",
                        "orca",
                        "g16",
                        "g09",
                        "pyscf",
                        "psi4",
                        "nwchem",
                        "gmx",
                        "mdrun",
                        "pmemd",
                        "sander",
                        "openmm",
                        "mace",
                        "nequip",
                        "deepmd",
                        "dp ",
                        "chgnet",
                        "sevennet",
                        "openfoam",
                        "fenics",
                    )
                ):
                    ran_simulation = True

                if any(
                    log_k in norm_cmd
                    for log_k in (
                        ".out",
                        ".log",
                        "log.lammps",
                        ".ener",
                        "pwscf.xml",
                        "OUTCAR",
                        "OSZICAR",
                        "running_scf.log",
                        "md.log",
                    )
                ) and any(r_k in norm_cmd for r_k in ("cat ", "head ", "tail ", "grep ", "python")):
                    inspected_sim_log = True

                if "results.json" in norm_cmd and any(
                    w in norm_cmd for w in (">", "tee", "json.dump", "write_text", "open(")
                ):
                    results_json_written = True
                    results_json_verified_after_write = False
                elif (
                    results_json_written
                    and "results.json" in norm_cmd
                    and any(r in norm_cmd for r in ("cat ", "json.load", "head "))
                ):
                    results_json_verified_after_write = True

                # Check if agent modified protected inputs (fd redirects excluded)
                if shell_writes_protected_input(norm_cmd):
                    signals.append(
                        {
                            "signal_id": f"sig:asset_mod:{step_id}:{t_idx}",
                            "signal_type": "asset_modified",
                            "description": f"Agent command potentially modified `/workspace/assets/`: {norm_cmd[:120]}",
                            "event_ref": ev_id,
                            "source_pointer": tc_ptr,
                        }
                    )

                # Check if scientific parameters were edited
                if any(
                    p in norm_cmd
                    for p in (
                        "TIMESTEP",
                        "ecutwfc",
                        "MAX_SCF",
                        "mixing_beta",
                        "reset_timestep",
                        "thermo_style",
                    )
                ):
                    signals.append(
                        {
                            "signal_id": f"sig:sci_param:{step_id}:{t_idx}",
                            "signal_type": "scientific_parameter_changed",
                            "description": f"Agent set or modified scientific simulation parameter in command: {norm_cmd[:120]}",
                            "event_ref": ev_id,
                            "source_pointer": tc_ptr,
                        }
                    )

            code_payload = None
            if ev_type == "file_write" and cmd_or_path:
                mutation_epoch += 1
                code_payload = summarize_code_payload(args)
                written_files.append(cmd_or_path)
                if "/workspace/assets/" in cmd_or_path:
                    signals.append(
                        {
                            "signal_id": f"sig:asset_write:{step_id}:{t_idx}",
                            "signal_type": "asset_modified",
                            "description": f"Agent directly wrote/edited asset file: {cmd_or_path}",
                            "event_ref": ev_id,
                            "source_pointer": tc_ptr,
                        }
                    )
                if cmd_or_path.endswith("results.json"):
                    results_json_written = True
                    results_json_verified_after_write = False
                    if (
                        not cmd_or_path.startswith("/workspace/results.json")
                        and cmd_or_path != "results.json"
                    ):
                        signals.append(
                            {
                                "signal_id": f"sig:wrong_path:{step_id}:{t_idx}",
                                "signal_type": "wrong_output_path",
                                "description": f"results.json written to non-standard path: {cmd_or_path}",
                                "event_ref": ev_id,
                                "source_pointer": tc_ptr,
                            }
                        )

            if ev_type == "file_read" and cmd_or_path:
                read_files.append(cmd_or_path)
                if any(k in cmd_or_path for k in (".out", "log.lammps", ".ener")):
                    inspected_sim_log = True
                if cmd_or_path.endswith("results.json") and results_json_written:
                    results_json_verified_after_write = True

            events.append(
                {
                    "event_id": ev_id,
                    "timestamp": ts,
                    "actor": "agent",
                    "event_type": ev_type,
                    "tool_name": fn_name,
                    "command": cmd_or_path,
                    "observation": trunc_obs,
                    "observation_sha256": obs_sha,
                    "exit_code": exit_code,
                    "source_file": "agent/trajectory.json",
                    "source_pointer": tc_ptr,
                    **({"code_payload": code_payload} if code_payload else {}),
                }
            )

    # Post-trajectory aggregate behavioral signals
    if ran_simulation and not inspected_sim_log:
        signals.append(
            {
                "signal_id": "sig:missing_log_inspection",
                "signal_type": "missing_log_inspection",
                "description": "Agent executed simulation binary without inspecting output log files.",
                "event_ref": events[-1]["event_id"] if events else "trajectory:root",
                "source_pointer": "/steps",
            }
        )

    declared_deliverables = [
        d for d in (expected_deliverables or []) if isinstance(d, str) and d.strip()
    ]
    # Shell commands that ran (including repeated/failed ones); file_write events
    # are already collected in `written_files`.
    ran_commands = [
        ev["command"]
        for ev in events
        if ev.get("event_type") in ("tool_call", "shell_error", "repeated_command")
        and ev.get("command")
    ]

    if declared_deliverables and events:
        # Harbor task.toml declares the authoritative deliverable set. "Premature
        # completion" therefore means a declared artifact was never produced -
        # not the absence of a hardcoded `results.json` most harbor tasks never
        # write. When every declared artifact is present, completion heuristics
        # fall back to the results.json verification check below.
        missing_deliverables = [
            d
            for d in declared_deliverables
            if not _deliverable_written(d, written_files, ran_commands)
        ]
        if missing_deliverables:
            signals.append(
                {
                    "signal_id": "sig:premature_completion",
                    "signal_type": "premature_completion",
                    "description": (
                        "Agent trajectory ended without producing declared deliverable(s): "
                        f"{', '.join(missing_deliverables)} (task.toml `artifacts`)."
                    ),
                    "event_ref": events[-1]["event_id"],
                    "source_pointer": f"/steps/{len(steps) - 1}" if steps else "/steps",
                    "declared_deliverables": declared_deliverables,
                    "missing_deliverables": missing_deliverables,
                }
            )
        elif results_json_written and not results_json_verified_after_write:
            signals.append(
                {
                    "signal_id": "sig:unverified_output",
                    "signal_type": "unverified_output",
                    "description": "Agent wrote `results.json` without reading/validating its final schema.",
                    "event_ref": events[-1]["event_id"],
                    "source_pointer": f"/steps/{len(steps) - 1}" if steps else "/steps",
                }
            )
    elif not results_json_written and events:
        # Legacy fallback: no declared deliverables are known, so the only
        # observable completion contract is `results.json`.
        signals.append(
            {
                "signal_id": "sig:premature_completion",
                "signal_type": "premature_completion",
                "description": "Agent trajectory ended without writing `results.json`.",
                "event_ref": events[-1]["event_id"],
                "source_pointer": f"/steps/{len(steps) - 1}" if steps else "/steps",
            }
        )
    elif results_json_written and not results_json_verified_after_write and events:
        signals.append(
            {
                "signal_id": "sig:unverified_output",
                "signal_type": "unverified_output",
                "description": "Agent wrote `results.json` without reading/validating its final schema.",
                "event_ref": events[-1]["event_id"],
                "source_pointer": f"/steps/{len(steps) - 1}" if steps else "/steps",
            }
        )

    result: Dict[str, Any] = {
        "exists": True,
        "schema_version": schema_version,
        "session_id": raw.get("session_id"),
        "warnings": warnings,
        "events": events,
        "behavioral_signals": signals,
        "written_files": sorted(set(written_files)),
        "read_files": sorted(set(read_files)),
    }
    if declared_deliverables:
        # Additive: lets gates distinguish "declared deliverable missing" from a
        # generic completion heuristic without re-parsing task.toml.
        result["declared_deliverables"] = declared_deliverables
        result["missing_deliverables"] = [
            d
            for d in declared_deliverables
            if not _deliverable_written(d, written_files, ran_commands)
        ]
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Normalize ATIF agent trajectory into timeline events."
    )
    parser.add_argument(
        "--trajectory", required=True, type=Path, help="Path to agent/trajectory.json"
    )
    parser.add_argument(
        "--max-obs-bytes", type=int, default=2000, help="Max bytes per observation snippet"
    )
    parser.add_argument("--output", required=True, type=Path, help="Output JSON path")
    args = parser.parse_args()

    res = normalize_trajectory(args.trajectory, max_obs_bytes=args.max_obs_bytes)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
