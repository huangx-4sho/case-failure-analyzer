#!/usr/bin/env python3
"""
Phase 3: Runtime and Infra Error Extraction (`scripts/extract_runtime_errors.py`)

Extracts infrastructure, container build, network, API, timeout, and resource
errors from `result.json`, `exception.txt`, `trial.log`, and `job.log`.
Crucially distinguishes fatal causal candidates (`causal_candidate=True`) from
recovered transient warnings (`transient=True`).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from cli_args import positive_int
from discover_artifacts import safe_load_json
from runtime_state import compute_runtime_state


API_LIMIT_CODE = "INFRA_API_RATE_LIMIT"
API_LIMIT_EXCEPTION_TYPES = {"apiusagelimiterror", "ratelimiterror"}
SDK_API_LIMIT_ERRORS = {"api_usage_limit", "rate_limit", "rate_limit_error", "usage_limit"}
SDK_API_LIMIT_MESSAGE = re.compile(
    r"^API Error:\s*(?:Request rejected\s*\(429\)|429\b|(?:API )?usage limit\b|"
    r"rate limit (?:exceeded|reached)|exceeded your current quota)",
    re.IGNORECASE,
)
HARBOR_API_LIMIT_EXCEPTION = re.compile(
    r"^harbor\.agents\.[\w.]*\.(ApiUsageLimitError|RateLimitError):", re.MULTILINE
)
BUILD_ERROR_LINE = re.compile(
    r"^(?:failed to solve:|#\d+(?:\s+[\d.]+)?\s+ERROR:|Error response from daemon:)", re.I
)
BUILDKIT_STAGE = re.compile(r"^#(\d+)\s+(.+)")
BUILD_DEPENDENCY_FAILURES = (
    (
        "registry_token_request_failed",
        re.compile(r"failed to fetch (?:anonymous |oauth )?token", re.I),
    ),
    (
        "registry_access_denied",
        re.compile(
            r"pull access denied|insufficient_scope|authorization failed|unauthorized:|denied: requested access",
            re.I,
        ),
    ),
    (
        "image_not_found",
        re.compile(
            r"manifest unknown|no matching manifest|(?:source metadata|manifest|image).+not found\b|"
            r"^failed to solve:\s+\S+:\s+not found\b",
            re.I,
        ),
    ),
    (
        "missing_local_build_dependency",
        re.compile(
            r"could not find [\w.+-]+ (?:for |executable\b)|"
            r"[\w.+-]+: command not found\b|fatal error:.+no such file or directory",
            re.I,
        ),
    ),
    (
        "download_or_connection_failed",
        re.compile(
            r"failed to fetch|could not resolve|TLS handshake timeout|i/o timeout|connection timed out",
            re.I,
        ),
    ),
)


INFRA_RULES: List[Tuple[str, str, str, re.Pattern[str]]] = [
    (
        "INFRA_EXTERNAL_NETWORK",
        "external_network",
        "environment_build",
        re.compile(
            r"(?:Failed to fetch|Temporary failure resolving|Could not resolve host|Connection timed out|"
            r"unable to access|net/http: TLS handshake timeout|dial tcp .* i/o timeout|"
            r"lookup .* no such host|pull token for registry)",
            re.IGNORECASE,
        ),
    ),
    (
        "INFRA_CONTAINER_BUILD",
        "container_build",
        "environment_build",
        re.compile(
            r"(?:Docker compose command failed|failed to solve: process|executor failed running|"
            r"dockerfile parse error|buildx failed)",
            re.IGNORECASE,
        ),
    ),
    (
        API_LIMIT_CODE,
        "api_rate_limit",
        "agent_execution",
        re.compile(
            r"(?:429 Too Many Requests|Request rejected\s*\(429\)|RateLimitError|"
            r"ApiUsageLimitError|rate_limit_error|exceeded your current quota)",
            re.IGNORECASE,
        ),
    ),
    (
        "INFRA_API_ERROR",
        "api_error",
        "agent_execution",
        re.compile(
            r"(?:APIConnectionError|InternalServerError|502 Bad Gateway|503 Service Unavailable|"
            r"504 Gateway Time-out|ANTHROPIC_BASE_URL.*connection refused)",
            re.IGNORECASE,
        ),
    ),
    (
        "INFRA_AGENT_TIMEOUT",
        "agent_timeout",
        "agent_execution",
        re.compile(
            r"(?:AgentTimeoutError|Agent execution timed out|TimeoutError.*agent)",
            re.IGNORECASE,
        ),
    ),
    (
        "INFRA_VERIFIER_TIMEOUT",
        "verifier_timeout",
        "verifier_execution",
        re.compile(
            r"(?:VerifierTimeoutError|Verifier execution timed out|TimeoutError.*verifier)",
            re.IGNORECASE,
        ),
    ),
    (
        "INFRA_OOM",
        "oom",
        "environment_runtime",
        re.compile(
            r"(?:Out of memory: Killed process|oom-kill|exit code 137|MemoryError)",
            re.IGNORECASE,
        ),
    ),
    (
        "INFRA_DISK",
        "disk",
        "environment_runtime",
        re.compile(r"(?:No space left on device|Disk quota exceeded)", re.IGNORECASE),
    ),
    (
        "INFRA_VOLUME_MOUNT",
        "volume_mount",
        "environment_runtime",
        re.compile(
            r"(?:error while creating mount source path|bind source path does not exist|mount denied)",
            re.IGNORECASE,
        ),
    ),
    (
        "INFRA_PERMISSION",
        "permission",
        "environment_runtime",
        re.compile(
            r"(?:PermissionError: \[Errno 13\]|permission denied while trying to connect to the Docker daemon)",
            re.IGNORECASE,
        ),
    ),
    (
        "INFRA_CONTAINER_RUNTIME",
        "container_runtime",
        "environment_runtime",
        re.compile(
            r"(?:Error response from daemon|OCI runtime create failed|container .* is not running)",
            re.IGNORECASE,
        ),
    ),
    (
        "INFRA_RUNNER_LIFECYCLE",
        "runner_lifecycle",
        "runner",
        re.compile(
            r"(?:KeyboardInterrupt|CancelledError|Harbor lock conflict)",
            re.IGNORECASE,
        ),
    ),
]

#: Harbor collects the agent's declared deliverables with `docker cp` *after*
#: agent execution. A missing `/app/...` path aborts the whole trial before the
#: verifier ever starts, so this is a runner-stage failure — not
#: `environment_build` / `container_build`. Treating it as a first-class
#: observation is what stops the engine from blaming the agent for "premature
#: termination" on trials that were never scored.
ARTIFACT_COLLECTION_CRASH_RE = re.compile(
    r"RuntimeError:\s*Engine cp failed[^\n]*?"
    r"(?:Could not find the file (?P<path>/\S+) in container|Error response from daemon)",
    re.IGNORECASE,
)
#: A runner that stops logging right at this marker died (or was killed) during
#: collection without emitting a traceback.
RUNNER_INTERRUPTION_MARKER = "Collecting main service artifacts"

#: Deliberately NOT an error rule: harbor trial logs contain a *benign telemetry*
#: warning on every agent turn —
#:   `Cannot estimate Claude cost for model '...': litellm.BadRequestError:
#:    LLM Provider NOT provided. Pass in the LLM provider ...`
#: It appears in fully successful 50+ step trajectories and in passing trials, so
#: matching `litellm.BadRequestError` / `LLM Provider NOT provided` as a causal
#: infrastructure error produces mass false positives. A genuine model-gateway
#: failure is only observable through `exception_info` or a missing trajectory.


def _runner_collection_observations(
    sources_to_scan: List[Tuple[str, str, str]],
    agent_started: bool,
    verifier_started: bool,
    start_counter: int,
) -> Tuple[List[Dict[str, Any]], int, set]:
    """
    Detect post-agent artifact-collection crashes / silent interruptions.

    Returns `(observations, next_error_counter, suppressed_rule_keys)`. The
    suppressed keys stop the generic `Error response from daemon` rule from
    double-reporting the same `Engine cp failed` line as an environment error.
    """
    observations: List[Dict[str, Any]] = []
    suppressed: set = set()
    counter = start_counter
    seen_codes: set = set()
    is_causal = bool(agent_started and not verifier_started)

    for src_file, src_ptr, text in sources_to_scan:
        if not text:
            continue
        crash = ARTIFACT_COLLECTION_CRASH_RE.search(text)
        marker_tail = text.rstrip().endswith(RUNNER_INTERRUPTION_MARKER)
        if crash:
            code = "INFRA_ARTIFACT_COLLECTION_CRASH"
            subtype = "artifact_collection_crash"
            snippet = text[max(0, crash.start() - 60) : min(len(text), crash.end() + 160)]
            path_match = re.search(
                r"Could not find the file\s+(/\S+)\s+in container", text, re.IGNORECASE
            )
            missing_path = path_match.group(1) if path_match else ""
        elif marker_tail and RUNNER_INTERRUPTION_MARKER in text:
            code = "INFRA_RUNNER_INTERRUPTED"
            subtype = "runner_interrupted"
            snippet = (
                "Runner log ends at 'Collecting main service artifacts' with no "
                "verifier output or completion record (silent interruption)."
            )
            missing_path = ""
        else:
            continue
        if code in seen_codes:
            # trial.log and job.log usually carry the same traceback.
            suppressed.add((src_file, "INFRA_CONTAINER_RUNTIME"))
            continue
        seen_codes.add(code)
        suppressed.add((src_file, "INFRA_CONTAINER_RUNTIME"))
        observations.append(
            {
                "error_id": f"err:{counter}",
                "code": code,
                "subtype": subtype,
                "stage": "runner",
                "runner_phase": "post_agent_execution",
                "matched_text": " ".join(snippet.split())[:300],
                "causal_candidate": is_causal,
                "transient": not is_causal,
                "source_file": src_file,
                "source_pointer": src_ptr,
                "missing_deliverable": missing_path,
                "scoring_reached": verifier_started,
            }
        )
        counter += 1
    return observations, counter, suppressed


def read_text_head_tail(path: Path, max_bytes: int = 120000) -> str:
    if max_bytes <= 0:
        raise ValueError(f"max_bytes must be a positive integer, got {max_bytes!r}")
    if not path.is_file():
        return ""
    try:
        file_size = path.stat().st_size
        if file_size <= max_bytes:
            return path.read_bytes().decode("utf-8", errors="replace")
        half = max_bytes // 2
        with path.open("rb") as f:
            head = f.read(half)
            f.seek(max(0, file_size - half))
            tail = f.read(half)
        return (
            head.decode("utf-8", errors="replace")
            + "\n... [TRUNCATED] ...\n"
            + tail.decode("utf-8", errors="replace")
        )
    except OSError:
        return ""


def _terminal_sdk_result(path: Path, max_log_bytes: int) -> Optional[Dict[str, Any]]:
    try:
        with path.open("rb") as source:
            size = source.seek(0, 2)
            start = max(0, size - max_log_bytes)
            source.seek(max(0, start - 1))
            at_line_start = start == 0 or source.read(1) == b"\n"
            raw = source.read(max_log_bytes)
    except OSError:
        return None
    # SDK terminal turns can end with a large assistant payload. Use the
    # whole bounded read for the end of the stream rather than sampling its head.
    if not at_line_start:
        raw = raw.partition(b"\n")[2]
    terminal = None
    thinking = None
    for line in raw.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            if event.get("type") == "result":
                terminal = dict(event)
                if thinking:
                    terminal["terminal_thinking_estimate"] = thinking
            elif event.get("type") == "system" and event.get("subtype") == "thinking_tokens":
                estimate = event.get("estimated_tokens")
                if type(estimate) is int and estimate > 0:
                    thinking = {"estimated_tokens": estimate, "event_uuid": event.get("uuid")}
            elif event.get("type") in ("assistant", "user") or (
                event.get("type") == "system" and event.get("subtype") == "init"
            ):
                terminal = None
                if event.get("type") != "assistant":
                    thinking = None
    return terminal


def _build_dependency_excerpt(text: str, source_pointer: str) -> Dict[str, Any]:
    """Find a BuildKit diagnostic and point to its original source location."""
    details: Dict[str, Any] = {}
    in_tail = False
    priority = 0
    selected_step = None
    for number, line in enumerate(text.splitlines(), 1):
        if line == "... [TRUNCATED] ...":
            in_tail = True
        line = line.strip()
        step_match = BUILDKIT_STAGE.match(line)
        step = step_match.group(1) if step_match else None
        if step_match and selected_step is not None and priority == 1:
            status = step_match.group(2)
            if (step == selected_step and status.startswith("DONE")) or (
                step != selected_step and status.startswith("ERROR:")
            ):
                details = {}
                priority = 0
        if not BUILD_ERROR_LINE.match(line) or line.lower().startswith("failed to solve: process "):
            continue
        line_priority = 2 if line.lower().startswith("failed to solve:") else 1
        if line_priority < priority:
            continue
        for kind, pattern in BUILD_DEPENDENCY_FAILURES:
            if pattern.search(line):
                pointer = source_pointer
                if source_pointer == "L1":
                    pointer = "bounded tail excerpt" if in_tail else f"L{number}"
                details = {
                    "matched_text": line[:300],
                    "source_pointer": pointer,
                    "build_failure_kind": kind,
                }
                priority = line_priority
                selected_step = step
                break
    return details


def _terminal_api_limit_observation(
    runtime: Dict[str, Any],
    exception: Dict[str, Any],
    traceback_text: str,
    sdk_result: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    exception_type = str(exception.get("exception_type") or "")
    typed = exception_type.casefold() in API_LIMIT_EXCEPTION_TYPES
    trace_exception = (
        HARBOR_API_LIMIT_EXCEPTION.search(
            traceback_text.rsplit("Traceback (most recent call last):", 1)[-1]
        )
        if not exception_type
        else None
    )
    sdk = sdk_result or {}
    sdk_message = str(sdk.get("result") or "").strip()
    sdk_limit = sdk.get("is_error") is True and (
        str(sdk.get("api_error") or "").casefold() in SDK_API_LIMIT_ERRORS
        or SDK_API_LIMIT_MESSAGE.match(sdk_message) is not None
    )
    if typed:
        source_file, source_pointer = "result.json", "/exception_info/exception_type"
        diagnostic = str(exception.get("exception_message") or "")
    elif trace_exception:
        source_file, source_pointer = "exception.txt", "terminal agent exception"
        diagnostic = trace_exception.string[trace_exception.end() :]
    elif sdk_limit:
        source_file, source_pointer = "agent/claude-code.txt", "terminal_result"
        diagnostic = sdk_message
    else:
        return None

    # Keep the typed interruption distinct from incidental HTTP errors in tool logs.
    # Do not copy API account identifiers or a shell wrapper's command into reports.
    if sdk_limit and typed:
        diagnostic = sdk_message
    status_429 = re.search(r"(?:\(429\)|HTTP\s*429|429 Too Many Requests)", diagnostic, re.I)
    if re.search(r"\bbudget (?:has been )?exceeded\b", diagnostic, re.I):
        limit_kind, detail = "budget", "the API usage budget was exceeded"
    elif re.search(r"\b(?:usage limit|quota)\b", diagnostic, re.I):
        limit_kind, detail = "quota", "the API usage quota was reached"
    else:
        limit_kind, detail = "usage_limit", "the model API reported a usage limit"
    causal = (
        runtime["verification_status"] not in ("passed", "pending")
        and sdk.get("is_error") is not False
        and (
            typed
            or bool(trace_exception)
            or exception_type in ("", "NonZeroAgentExitCodeError", "RewardFileNotFoundError")
        )
    )
    return {
        "error_id": "err:terminal_api_limit",
        "code": API_LIMIT_CODE,
        "subtype": "api_rate_limit",
        "stage": "agent_execution" if runtime["agent_started"] else "agent_setup",
        "matched_text": f"{'HTTP 429: ' if status_429 else ''}{detail}",
        "causal_candidate": causal,
        "transient": not causal,
        "source_file": source_file,
        "source_pointer": source_pointer,
        "limit_kind": limit_kind,
        "http_status": 429 if status_429 else None,
    }


def extract_runtime_errors(
    job_dir: Path,
    trial_dir: Optional[Path] = None,
    max_log_bytes: int = 120000,
) -> Dict[str, Any]:
    if max_log_bytes <= 0:
        raise ValueError(f"max_log_bytes must be a positive integer, got {max_log_bytes!r}")
    error_observations: List[Dict[str, Any]] = []

    trial_res_path = trial_dir / "result.json" if trial_dir else None
    job_res_path = job_dir / "result.json"
    loaded_trial = safe_load_json(trial_res_path) if trial_res_path else None
    trial_res: Dict[str, Any] = (
        loaded_trial if isinstance(loaded_trial, dict) and "_value" not in loaded_trial else {}
    )
    loaded_job = safe_load_json(job_res_path)
    job_res: Dict[str, Any] = (
        loaded_job if isinstance(loaded_job, dict) and "_value" not in loaded_job else {}
    )

    rt_state = compute_runtime_state(
        job_result=job_res,
        trial_result=trial_res,
        trial_dir=trial_dir,
    )
    agent_started = bool(rt_state["agent_started"])
    verifier_started = bool(rt_state["verifier_started"])
    exc_info = trial_res.get("exception_info")
    has_unrecovered_runner_failure = bool(
        rt_state["verification_status"] != "passed"
        and (rt_state["has_trial_exception"] or not agent_started)
    )

    sources_to_scan: List[Tuple[str, str, str]] = []
    if isinstance(exc_info, dict) and exc_info.get("exception_message"):
        sources_to_scan.append(
            (
                "result.json",
                "/exception_info/exception_message",
                f"{exc_info.get('exception_type', 'Exception')}: {exc_info.get('exception_message', '')}",
            )
        )
    if trial_dir and (trial_dir / "exception.txt").is_file():
        sources_to_scan.append(
            (
                "exception.txt",
                "L1",
                read_text_head_tail(trial_dir / "exception.txt", max_log_bytes),
            )
        )
    if trial_dir and (trial_dir / "trial.log").is_file():
        sources_to_scan.append(
            (
                "trial.log",
                "L1",
                read_text_head_tail(trial_dir / "trial.log", max_log_bytes),
            )
        )
    if (job_dir / "job.log").is_file():
        sources_to_scan.append(
            (
                "job.log",
                "L1",
                read_text_head_tail(job_dir / "job.log", max_log_bytes),
            )
        )

    terminal = None
    # Scoring shell output is distinct from agent startup diagnostics.
    if trial_dir:
        stdout_path = trial_dir / "verifier" / "test-stdout.txt"
        stdout = read_text_head_tail(stdout_path, max_log_bytes)
        match = re.search(
            r"(?:exec:\s*)?(python(?:3(?:\.\d+)?)?): (?:not found|command not found)", stdout
        )
        if match:
            causal = rt_state["reward"] is None and rt_state["verification_status"] != "passed"
            error_observations.append(
                {
                    "error_id": "err:verifier_interpreter",
                    "code": "INFRA_VERIFIER_INTERPRETER_MISSING",
                    "subtype": "verifier_interpreter_missing",
                    "stage": "verifier_execution",
                    "matched_text": stdout.strip()[:300],
                    "causal_candidate": causal,
                    "transient": not causal,
                    "source_file": "verifier/test-stdout.txt",
                    "source_pointer": "L1",
                    "scoring_reached": False,
                }
            )
        # Only a terminal SDK result with is_error=true binds an SDK failure.
        terminal = _terminal_sdk_result(trial_dir / "agent" / "claude-code.txt", max_log_bytes)
        sdk_codes = {
            "autocompact_thrashing": ("INFRA_AGENT_AUTOCOMPACT_THRASHING", "autocompact_thrashing"),
            "max_output_tokens": ("INFRA_AGENT_OUTPUT_TOKEN_LIMIT", "output_token_limit"),
        }
        if terminal and terminal.get("is_error") and terminal.get("api_error") in sdk_codes:
            code, subtype = sdk_codes[terminal["api_error"]]
            causal = bool(
                rt_state["has_trial_exception"] and rt_state["verification_status"] != "passed"
            )
            diagnostic = str(terminal.get("result") or terminal["api_error"])
            ceiling = re.search(r"\b(\d[\d,]*) output token maximum\b", diagnostic, re.I)
            error_observations.append(
                {
                    "error_id": "err:terminal_sdk",
                    "code": code,
                    "subtype": subtype,
                    "stage": "agent_execution",
                    "matched_text": diagnostic[:300],
                    "causal_candidate": causal,
                    "transient": not causal,
                    "source_file": "agent/claude-code.txt",
                    "source_pointer": "terminal_result",
                    **(
                        {"terminal_thinking_estimate": terminal["terminal_thinking_estimate"]}
                        if terminal.get("terminal_thinking_estimate")
                        else {}
                    ),
                    **(
                        {"output_token_ceiling": int(ceiling.group(1).replace(",", ""))}
                        if ceiling
                        else {}
                    ),
                }
            )

    api_limit = _terminal_api_limit_observation(
        rt_state,
        exc_info if isinstance(exc_info, dict) else {},
        next((text for source, _, text in sources_to_scan if source == "exception.txt"), ""),
        terminal,
    )
    seen_codes_per_file = set()
    if api_limit:
        error_observations.append(api_limit)
        seen_codes_per_file.add((api_limit["source_file"], API_LIMIT_CODE))
    err_counter = 1

    # Runner-stage collection failures are detected before the generic rules so
    # `Error response from daemon` on an `Engine cp failed` line is not also
    # reported as an environment/container-build error.
    runner_obs, err_counter, _ = _runner_collection_observations(
        sources_to_scan,
        agent_started=agent_started,
        verifier_started=verifier_started,
        start_counter=err_counter,
    )
    error_observations.extend(runner_obs)
    if runner_obs:
        for src_file, _, _ in sources_to_scan:
            seen_codes_per_file.add((src_file, "INFRA_CONTAINER_RUNTIME"))

    build_excerpts = {
        source: _build_dependency_excerpt(text, pointer)
        for source, pointer, text in sources_to_scan
        if not agent_started
        and "Docker compose command failed" in text
        and "build" in text
        and not (source == "job.log" and trial_dir is not None)
    }
    terminal_build_kind = next(
        (detail["build_failure_kind"] for detail in build_excerpts.values() if detail), None
    )
    local_build_failure = terminal_build_kind == "missing_local_build_dependency"

    for src_file, src_ptr, text in sources_to_scan:
        if not text:
            continue
        build_excerpt = build_excerpts.get(src_file, {})
        # Build commands can mention apt or a registry without either one failing.
        if (
            build_excerpt
            and build_excerpt["build_failure_kind"] != "missing_local_build_dependency"
            and not local_build_failure
        ):
            key = (src_file, "INFRA_EXTERNAL_NETWORK")
            if key not in seen_codes_per_file:
                seen_codes_per_file.add(key)
                error_observations.append(
                    {
                        "error_id": f"err:{err_counter}",
                        "code": "INFRA_EXTERNAL_NETWORK",
                        "subtype": "external_network",
                        "stage": "environment_build",
                        "matched_text": "Docker compose build failed during external image/package download before agent start",
                        "causal_candidate": has_unrecovered_runner_failure,
                        "transient": not has_unrecovered_runner_failure,
                        "source_file": src_file,
                        "source_pointer": src_ptr,
                        **build_excerpt,
                    }
                )
                err_counter += 1

        for code, subtype, stage, pattern in INFRA_RULES:
            m = pattern.search(text)
            if m:
                key = (src_file, code)
                if key in seen_codes_per_file:
                    continue
                seen_codes_per_file.add(key)
                snippet_start = max(0, m.start() - 60)
                snippet_end = min(len(text), m.end() + 140)
                snippet = (
                    m.group(0)
                    if code == API_LIMIT_CODE
                    else text[snippet_start:snippet_end].replace("\n", " ").strip()
                )

                # A post-agent collection crash proves environment build already
                # succeeded. The harbor fallback line "artifacts compose cp
                # failed; retrying download with engine cp: Docker compose
                # command failed" otherwise matches a build pattern and gets
                # mislabeled `environment_build` / `container_build`.
                effective_stage = stage
                if runner_obs and agent_started and stage == "environment_build":
                    effective_stage = "runner"

                # Determine whether this error actually blocked execution or was recovered
                if effective_stage == "environment_build":
                    is_causal = not agent_started or has_unrecovered_runner_failure
                elif code == API_LIMIT_CODE:
                    is_causal = False
                elif code in ("INFRA_AGENT_TIMEOUT", "INFRA_API_ERROR"):
                    is_causal = has_unrecovered_runner_failure or not verifier_started
                elif code == "INFRA_VERIFIER_TIMEOUT":
                    is_causal = has_unrecovered_runner_failure
                else:
                    is_causal = has_unrecovered_runner_failure

                # An earlier recovered error in a log does not become causal merely
                # because a later, different terminal exception exists.
                terminal_text = str(exc_info or {})
                if (
                    src_file in ("trial.log", "job.log")
                    and exc_info
                    and not pattern.search(terminal_text)
                ):
                    is_causal = False
                if src_file == "job.log" and trial_dir is not None:
                    # Shared job logs contain other trials; local terminal evidence
                    # must establish causality instead of aggregate log matches.
                    is_causal = False
                if (
                    code == "INFRA_RUNNER_LIFECYCLE"
                    and isinstance(exc_info, dict)
                    and exc_info.get("exception_type")
                    not in ("KeyboardInterrupt", "CancelledError")
                ):
                    is_causal = False
                if rt_state["verification_status"] == "passed":
                    is_causal = False
                if code == "INFRA_EXTERNAL_NETWORK" and local_build_failure:
                    is_causal = False

                observation: Dict[str, Any] = {
                    "error_id": f"err:{err_counter}",
                    "code": code,
                    "subtype": subtype,
                    "stage": effective_stage,
                    "matched_text": snippet[:300],
                    "causal_candidate": is_causal,
                    "transient": not is_causal,
                    "source_file": src_file,
                    "source_pointer": src_ptr,
                }
                if code == "INFRA_AGENT_TIMEOUT":
                    limit = re.search(
                        r"\bAgent execution timed out after (\d+(?:\.\d+)?) seconds\b",
                        text,
                        re.I,
                    )
                    if limit:
                        observation["timeout_seconds"] = float(limit.group(1))
                if code == "INFRA_CONTAINER_BUILD" or (
                    code == "INFRA_EXTERNAL_NETWORK"
                    and build_excerpt.get("build_failure_kind") != "missing_local_build_dependency"
                ):
                    observation.update(build_excerpt)
                if effective_stage != stage:
                    observation["stage_reclassified_from"] = stage
                    observation["runner_phase"] = "post_agent_execution"
                error_observations.append(observation)
                err_counter += 1

    return {
        "agent_started": agent_started,
        "verifier_started": verifier_started,
        "has_unrecovered_runner_failure": has_unrecovered_runner_failure,
        "error_observations": error_observations,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract runtime and infrastructure error observations."
    )
    parser.add_argument("--job", required=True, type=Path, help="Path to job directory")
    parser.add_argument(
        "--trial-dir", required=False, type=Path, default=None, help="Path to trial directory"
    )
    parser.add_argument(
        "--max-log-bytes",
        type=positive_int,
        default=120000,
        help="Max bytes to read from logs (positive integer)",
    )
    parser.add_argument("--output", required=True, type=Path, help="Output JSON path")
    args = parser.parse_args()

    if not args.job.exists():
        print(f"ERROR: --job path does not exist: {args.job}", file=sys.stderr)
        sys.exit(2)
    if not args.job.is_dir():
        print(f"ERROR: --job path exists and is not a directory: {args.job}", file=sys.stderr)
        sys.exit(2)
    if args.trial_dir is not None:
        if not args.trial_dir.exists():
            print(f"ERROR: --trial-dir path does not exist: {args.trial_dir}", file=sys.stderr)
            sys.exit(2)
        if not args.trial_dir.is_dir():
            print(
                f"ERROR: --trial-dir path exists and is not a directory: {args.trial_dir}",
                file=sys.stderr,
            )
            sys.exit(2)

    res = extract_runtime_errors(
        job_dir=args.job,
        trial_dir=args.trial_dir,
        max_log_bytes=args.max_log_bytes,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
