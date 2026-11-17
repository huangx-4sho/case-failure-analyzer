"""Bounded, read-only audit of declared outputs in a collected Harbor trial.

Statuses describe the collected tree, never whether the agent produced an output.
No verifier, container, shell, or trajectory command is executed by this module.
"""

from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path, PurePosixPath
from typing import Any


def _declarations(evidence: dict) -> tuple[list, str, list[str]]:
    metadata = evidence.get("metadata") or {}
    task = metadata.get("task_toml")
    if isinstance(task, dict) and "_parse_error" in task:
        return [], "unreadable", ["task.toml could not be parsed"]
    if isinstance(task, dict) and "artifacts" in task:
        raw = task["artifacts"]
        origin = "task_toml"
    else:
        raw = metadata.get("declared_deliverables")
        origin = "metadata_fallback"
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list) or not raw:
        return [], "absent_or_empty", ["No usable deliverable declaration is available"]
    return (
        raw,
        origin,
        (
            ["Declaration is taken from metadata; original task.toml declaration is unavailable"]
            if origin == "metadata_fallback"
            else []
        ),
    )


def extract_verified_missing_outputs(evidence: dict) -> list[dict]:
    """Bind explicit saved verifier absence statements to exact declarations.

    Conservative extraction deliberately misses ambiguous wording. It does not
    turn a reported missing child of a declared directory into a missing output.
    Verifier assertions describe verifier observations, not agent production.
    """
    declarations, _, _ = _declarations(evidence)
    sources = []
    for item in declarations:
        source = item.get("source") if isinstance(item, dict) else item
        if isinstance(item, dict) and item.get("service") is not None:
            continue
        if not isinstance(source, str):
            continue
        source = source.strip()
        pure = PurePosixPath(source)
        if (
            not pure.is_absolute()
            or ".." in pure.parts
            or str(pure) == "/"
            or "\\" in source
            or "\x00" in source
        ):
            continue
        if source not in sources:
            sources.append(source)
    candidates = []
    report = evidence.get("verifier_report") or {}
    for index, check in enumerate(report.get("failed_checks") or []):
        if not isinstance(check, dict):
            continue
        status = str(check.get("status") or check.get("verdict") or "").lower()
        if status in {"pass", "passed", "ok", "success", "true"} or check.get("passed") is True:
            continue
        detail = check.get("detail")
        if isinstance(detail, str):
            candidates.append(
                (
                    detail,
                    {
                        "source_ref": check.get("source"),
                        "source_pointer": check.get("source_pointer"),
                        "check_ref": check.get("check_id") or check.get("name"),
                        "evidence_refs": ["verifier_report.failed_checks[" + str(index) + "]"],
                    },
                )
            )
    for observation in evidence.get("verifier_observations") or []:
        if not isinstance(observation, dict) or observation.get("type") != "verifier_fail_message":
            continue
        text = observation.get("matched_text")
        if isinstance(text, str):
            candidates.append(
                (
                    text,
                    {
                        "source_ref": observation.get("source_file"),
                        "source_pointer": observation.get("source_pointer"),
                        "check_ref": None,
                        "evidence_refs": [observation["obs_id"]]
                        if observation.get("obs_id")
                        else [],
                    },
                )
            )
    # These contexts can quote an absence message without asserting absence.
    excluded = re.compile(
        r"\b(?:not\s+(?:missing|absent|not\s+found)|no\s+missing|never\s+missing|"
        r"must|should|would|could|might|if|unless|example|requirement|"
        r"pass|passed|success|successful|ok|expected\s+(?:message|error))\b"
        r"|(?:并非|不是|没有|无)(?:不存在|缺失)|(?:示例|应当|应该|如果|成功)",
        re.I,
    )
    found = []
    seen = set()
    for text, context in candidates:
        for line_number, line in enumerate(text[:120000].splitlines(), 1):
            if excluded.search(line):
                continue
            for source in sources:
                path_start = r"(?<![\w./~%+@=\-])" + re.escape(source)
                path = path_start + r"(?![\w./~%+@=\-])"
                # Require the absence phrase and path to be directly linked.
                punctuation = r"[\s:`\"'，,：]*"
                before = (
                    r"(?i:\bmissing\b(?:\s+(?:the|required|file|output|deliverable|directory))*"
                    r"|\bnot found\b|\bno such file(?: or directory)?\b|不存在|缺失)"
                    + punctuation
                    + path
                )
                after = (
                    path_start + punctuation + r"(?i:(?:(?:is|was)\s+)?"
                    r"(?:missing\b|not found\b|no such file(?: or directory)?\b|不存在(?!\w)|缺失(?!\w)))"
                )
                if not (re.search(before, line) or re.search(after, line)):
                    continue
                key = (source, context["source_ref"], context["check_ref"], line)
                if key in seen:
                    continue
                seen.add(key)
                found.append(
                    {
                        **context,
                        "declared_source": source,
                        "collected_rel_path": "artifacts/" + source.lstrip("/"),
                        "observed_text": line,
                        "text_line": line_number,
                        "observation_kind": "verifier_reported_missing_declared_output",
                    }
                )
    return found


def _safe_target(root: Path, relative: PurePosixPath) -> tuple[Path | None, str | None]:
    """Reject traversal and symlinks, including symlinked parent directories."""
    if ".." in relative.parts or relative.is_absolute() or not relative.parts:
        return None, "Unsafe or empty collected path"
    target = root
    for part in relative.parts:
        target = target / part
        try:
            if target.is_symlink():
                return None, "Symlink mapping is not followed"
        except OSError as exc:
            return None, f"Cannot inspect mapped path: {exc}"
    return target, None


def _inspect_file(path: Path, root: Path, max_read_bytes: int) -> dict:
    record: dict[str, Any] = {
        "path": str(path.relative_to(root)),
        "kind": "file",
        "read_status": "not_attempted",
        "parse_status": "not_attempted",
    }
    try:
        record["size_bytes"] = path.stat().st_size
        record["empty"] = record["size_bytes"] == 0
        if path.suffix.lower() == ".json":
            if record["size_bytes"] > max_read_bytes:
                record["read_status"] = "size_limit"
            else:
                with path.open("rb") as stream:
                    data = stream.read(max_read_bytes + 1)
                if len(data) > max_read_bytes:
                    record["read_status"] = "size_limit"
                else:
                    record["read_status"] = "read"
                    try:
                        json.loads(data)
                        record["parse_status"] = "valid_json"
                    except (ValueError, UnicodeError):
                        record["parse_status"] = "invalid_json"
    except OSError as exc:
        record["read_status"] = "failed"
        record["error"] = str(exc)
    return record


def _scan(root: Path, max_entries: int, max_depth: int, max_read_bytes: int) -> dict:
    result: dict[str, Any] = {
        "complete": True,
        "entries_scanned": 0,
        "observed_files": [],
        "directories": {},
        "limits": [],
    }
    pending = [(root, 0)]
    while pending:
        directory, depth = pending.pop()
        relative = str(directory.relative_to(root))
        try:
            # scandir is consumed incrementally: a huge directory cannot defeat the bound.
            with os.scandir(directory) as entries:
                empty = True
                for entry in entries:
                    empty = False
                    if result["entries_scanned"] >= max_entries:
                        result["complete"] = False
                        result["limits"].append("entry_limit")
                        return result
                    result["entries_scanned"] += 1
                    path = Path(entry.path)
                    if entry.is_symlink():
                        result["complete"] = False
                        result["limits"].append(f"symlink_not_followed:{path.relative_to(root)}")
                    elif entry.is_dir(follow_symlinks=False):
                        if depth >= max_depth:
                            result["complete"] = False
                            result["limits"].append(f"depth_limit:{path.relative_to(root)}")
                        else:
                            pending.append((path, depth + 1))
                    elif entry.is_file(follow_symlinks=False):
                        result["observed_files"].append(_inspect_file(path, root, max_read_bytes))
                result["directories"][relative] = {"empty": empty, "listing_status": "complete"}
        except OSError as exc:
            result["complete"] = False
            result["limits"].append(f"inaccessible:{relative}:{exc}")
    return result


def audit_deliverables(
    evidence: dict, *, max_entries: int = 1000, max_depth: int = 8, max_read_bytes: int = 65536
) -> dict:
    """Audit saved artifacts only; the return value is JSON serializable.

    ``records`` contains one record per declaration, including malformed items.
    ``not_found_in_collected_tree`` requires a usable mapping and complete scan;
    it is not evidence that an output never existed in the execution environment.
    Service declarations have no assumed correspondence with the main container.
    """
    if max_entries < 1 or max_depth < 0 or max_read_bytes < 0:
        raise ValueError("Audit bounds must be positive (depth/read size may be zero)")
    declarations, declaration_status, uncertainty = _declarations(evidence)
    refs = [
        a.get("artifact_id")
        for a in evidence.get("artifacts", [])
        if isinstance(a, dict) and a.get("artifact_id") == "art:task_toml"
    ]
    failures = [
        o
        for o in evidence.get("error_observations", [])
        if isinstance(o, dict)
        and (
            o.get("code") == "INFRA_ARTIFACT_COLLECTION_CRASH"
            or o.get("subtype") == "artifact_collection_crash"
            or (o.get("code") == "INFRA_RUNNER_INTERRUPTED" and o.get("stage") == "runner")
        )
    ]
    failure_refs = [o.get("error_id") for o in failures if o.get("error_id")]
    trial = evidence.get("trial_dir")
    root = Path(trial).resolve() / "artifacts" if trial else None
    root_status = "unavailable"
    scan = {
        "complete": False,
        "entries_scanned": 0,
        "observed_files": [],
        "directories": {},
        "limits": [],
    }
    if root is not None:
        try:
            # The supplied trial directory is the trust boundary; reject a redirected capture root.
            if root.is_symlink():
                root_status = "unsafe_symlink"
            elif root.is_dir():
                root_status = "available"
                scan = _scan(root, max_entries, max_depth, max_read_bytes)
            elif root.exists():
                root_status = "not_a_directory"
            else:
                root_status = "missing"
        except OSError as exc:
            root_status = "inaccessible"
            scan["limits"].append(str(exc))
    if root_status != "available":
        uncertainty.append(f"Collected artifact root is {root_status}")
    if not scan["complete"]:
        uncertainty.append("Collected tree coverage is incomplete")
    if failures:
        uncertainty.append("Existing runner evidence reports interrupted or failed collection")
    records = []
    checked_paths = []
    for index, item in enumerate(declarations):
        source = item.get("source") if isinstance(item, dict) else item
        service = item.get("service") if isinstance(item, dict) else None
        record = {
            "declaration_index": index,
            "declaration": item,
            "source": source,
            "service": service,
            "status": "unresolved",
            "mapping_status": "unresolved",
            "checked_paths": [],
            "observed_files": [],
            "evidence_refs": list(refs),
            "uncertainty": [],
            "captured_kind": None,
            "empty": None,
        }
        records.append(record)
        if not isinstance(source, str) or not source.strip():
            record["uncertainty"].append("Malformed declaration: source must be a nonempty string")
            continue
        source = source.strip()
        if service is not None:
            record["uncertainty"].append(
                "Service collection layout is not established by available evidence"
            )
            continue
        pure = PurePosixPath(source)
        if (
            not pure.is_absolute()
            or ".." in pure.parts
            or str(pure) == "/"
            or "\\" in source
            or "\x00" in source
        ):
            record["uncertainty"].append("Declaration has an ambiguous or unsafe container path")
            continue
        record["mapping_status"] = "main_container_absolute_path"
        record["mapping_basis"] = (
            "Harbor main-service capture layout: artifacts/<absolute source without leading slash>"
        )
        if root_status != "available":
            record["status"] = "collection_failed" if failures else "unresolved"
            record["uncertainty"].append(f"Collected artifact root is {root_status}")
            record["evidence_refs"].extend(failure_refs)
            continue
        target, problem = _safe_target(root, PurePosixPath(source.lstrip("/")))
        if problem:
            record["uncertainty"].append(problem)
            continue
        mapped = str(target)
        record["checked_paths"].append(mapped)
        checked_paths.append(mapped)
        try:
            mode = target.lstat().st_mode
        except FileNotFoundError:
            relevant_failures = [
                o
                for o in failures
                if not o.get("missing_deliverable") or o.get("missing_deliverable") == source
            ]
            if relevant_failures:
                record["status"] = "collection_failed"
                record["evidence_refs"].extend(
                    o["error_id"] for o in relevant_failures if o.get("error_id")
                )
            elif scan["complete"]:
                record["status"] = "not_found_in_collected_tree"
            record["uncertainty"].append(
                "Absence from capture does not establish whether the agent produced this output"
            )
            if not scan["complete"]:
                record["uncertainty"].append("Collected tree scan is incomplete")
            continue
        except OSError as exc:
            record["uncertainty"].append(f"Mapped path cannot be inspected: {exc}")
            continue
        if stat.S_ISREG(mode):
            record["captured_kind"] = "file"
            observation = _inspect_file(target, root, max_read_bytes)
            record["observed_files"] = [observation]
            record["empty"] = observation.get("empty")
        elif stat.S_ISDIR(mode):
            record["captured_kind"] = "directory"
            rel = str(target.relative_to(root))
            listing = scan["directories"].get(rel, {})
            record["empty"] = listing.get("empty")
            record["observed_files"] = [
                f for f in scan["observed_files"] if PurePosixPath(f["path"]).is_relative_to(rel)
            ]
            if record["empty"] is None or not scan["complete"]:
                record["uncertainty"].append("Directory contents have incomplete scan coverage")
        else:
            record["uncertainty"].append("Mapped path is not a regular file or directory")
            continue
        record["status"] = "present"
        record["evidence_refs"].append("art:artifacts/" + source.lstrip("/"))
    verified_missing = extract_verified_missing_outputs(evidence)
    for record in records:
        related = [
            finding
            for finding in verified_missing
            if finding["declared_source"] == record.get("source")
        ]
        record["verifier_reported_missing"] = bool(related)
        record["capture_verifier_conflict"] = record["status"] == "present" and bool(related)
        if record["capture_verifier_conflict"]:
            record["uncertainty"].append(
                "Saved verifier reported absence, but the current collected path exists; inspect timing and path mapping"
            )
    counts = {
        status: sum(r["status"] == status for r in records)
        for status in ("present", "not_found_in_collected_tree", "collection_failed", "unresolved")
    }
    return {
        "schema_version": "1.0",
        "mode": "read_only_existing_artifacts",
        "declaration_status": declaration_status,
        "declaration_validation": (
            "unavailable"
            if not records
            else "partial_or_malformed"
            if any(not isinstance(r["source"], str) or not r["source"].strip() for r in records)
            else "parsed"
        ),
        "records": records,
        "counts": counts,
        "verified_missing_outputs": verified_missing,
        "checked_paths": checked_paths,
        "observed_files": scan["observed_files"],
        "coverage": {
            "collection_root": str(root) if root else None,
            "root_status": root_status,
            "scan_complete": scan["complete"],
            "entries_scanned": scan["entries_scanned"],
            "max_entries": max_entries,
            "max_depth": max_depth,
            "max_read_bytes": max_read_bytes,
            "limits": scan["limits"],
        },
        "evidence_refs": list(dict.fromkeys(refs + failure_refs)),
        "uncertainty": uncertainty,
        "interpretation_limits": [
            "Presence describes captured output, not scientific correctness",
            "Absence from capture does not prove non-production",
            "Directory declarations do not imply required filenames",
            "Trajectory write commands are not filesystem evidence",
        ],
    }
