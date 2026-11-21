"""Bounded evidence slices for reviewing an existing single-trial audit."""

from __future__ import annotations

import ast
import csv
import hashlib
import io
import json
import re
from pathlib import Path, PurePosixPath

from normalize_trajectory import truncate_with_head_tail
from trajectory_source import SOURCE_SUFFIXES, trace_sources

_MAX_FILE_BYTES = 384 * 1024
_MAX_STRUCTURED_FILE_BYTES = 4 * 1024 * 1024
_MAX_PACK_CHARS = 38_000
_JSON_PATH = re.compile(r"\$(?:\.[\w-]+|\[\d+\])+")
_STRUCTURED_NAME = re.compile(r"[\w.-]+\.(?:csv|tsv|json)\b", re.I)
_WORDS = re.compile(r"[a-z][a-z_0-9.-]{2,}", re.I)
_STOP = set(
    "the and from check that with should must failed fail error expected verify verifier"
    " result required output score not name value passed scientific false true data"
    " tests runtime native reference file task check does for are have could".split()
)


def _read(base: Path, rel: str, expected_sha=None, *, limit=_MAX_FILE_BYTES):
    path = base / rel
    try:
        if (
            path.is_symlink()
            or not path.resolve().is_relative_to(base.resolve())
            or not path.is_file()
        ):
            return None
        with path.open("rb") as src:
            data = src.read(limit + 1)
        if len(data) > limit:
            return None
        if expected_sha and hashlib.sha256(data).hexdigest() != expected_sha.removeprefix(
            "sha256:"
        ):
            return None
        return data.decode("utf-8")
    except (OSError, UnicodeError):
        return None


def _tokens(text):
    return set(_WORDS.findall(text.lower())) - _STOP


def _terms(failures, anchors, checks):
    text = " ".join(
        str(k or "")
        for item in [*failures, *checks]
        for k in (item.get("name"), item.get("detail"))
    )
    paths = set(re.findall(r"[\w./-]+\.(?:csv|tsv|json|py|sh)\b", text))
    for name in ("algorithm_or_param_definition", "deliverable_generation"):
        anchor = anchors.get(name) or {}
        if anchor.get("target_path"):
            paths.add(anchor["target_path"])
    for location in failures:
        field = location.get("submitted_field") or {}
        if field.get("source"):
            paths.add(field["source"])
    terms = _tokens(text)
    terms.update(part for term in terms.copy() for part in re.split(r"[_/]", term) if len(part) > 3)
    return text, terms - _STOP, {PurePosixPath(path).name for path in paths}


def _excerpt(text, limit):
    return truncate_with_head_tail(text, limit)[0] or ""


def _matched_sections(text, terms, *, max_windows=3):
    lines = text.splitlines()
    scores = sorted(
        ((len(_tokens(line) & terms), -i) for i, line in enumerate(lines)), reverse=True
    )
    windows = []
    for score, negative_i in scores:
        i = -negative_i
        if score == 0 or any(start <= i < stop for start, stop in windows):
            continue
        windows.append((max(0, i - 2), min(len(lines), i + 4)))
        if len(windows) == max_windows:
            break
    return [
        {"line": start + 1, "text": _excerpt("\n".join(lines[start:stop]), 850)}
        for start, stop in sorted(windows)
    ]


def _verifier_functions(task, failures, artifacts):
    if task is None or not any(
        item.get("matching_rule") or item.get("reported_scorer_location") for item in failures
    ):
        return []
    from verifier_source import collect_verifier_sources

    sources = {source.path: source for source in collect_verifier_sources(task).sources}
    by_path = {
        artifact["rel_path"]: artifact.get("artifact_id")
        for artifact in artifacts
        if artifact.get("scope") == "task"
    }
    records = []
    seen = set()
    for location in failures:
        match = location.get("matching_rule") or {}
        status = "current_task_source_match"
        if not match and (reported := location.get("reported_scorer_location")):
            recorded_path = str(reported.get("path") or reported.get("source") or "")
            if "/tests/" in recorded_path:
                recorded_path = "tests/" + recorded_path.rsplit("/tests/", 1)[-1]
            matches = [
                src.path
                for src in sources.values()
                if recorded_path in {src.path, src.member} and recorded_path
            ]
            if len(matches) == 1:
                match = {"source": matches[0], "line": reported.get("line")}
                status = "recorded_location_current_source"
        source = sources.get(match.get("source"))
        line = match.get("line")
        if (
            source is None
            or not isinstance(line, int)
            or match.get("source_sha256") not in (None, source.sha256)
        ):
            continue
        try:
            nodes = [
                node
                for node in ast.walk(ast.parse(source.text))
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.lineno <= line <= node.end_lineno
            ]
        except (SyntaxError, ValueError, RecursionError):
            continue
        function = min(nodes, key=lambda node: node.end_lineno - node.lineno) if nodes else None
        lines = source.text.splitlines()
        start, stop = (
            (function.lineno, function.end_lineno)
            if function
            else (max(1, line - 5), min(len(lines), line + 5))
        )
        if (source.path, start) in seen:
            continue
        seen.add((source.path, start))
        raw = "\n".join(lines[start - 1 : stop])
        file_names = sorted(set(_STRUCTURED_NAME.findall(raw)))[:24]
        if len(raw.encode("utf-8")) > 7200:
            start, stop = max(start, line - 10), min(stop, line + 10)
            raw = "\n".join(lines[start - 1 : stop])
            complete = False
        else:
            complete = function is not None
        records.append(
            {
                "source": source.path,
                "source_sha256": source.sha256,
                "container": source.container,
                "source_ref": by_path.get(source.container or source.path),
                "check_ref": location.get("ref"),
                "matched_line": line,
                "start_line": start,
                "end_line": stop,
                "function": function.name if function else None,
                "complete_function": complete,
                "source_status": status,
                "mentioned_structured_files": file_names,
                "text": _excerpt(raw, 7200),
            }
        )
        if len(records) == 3:
            break
    return records


def _public_requirements(task, artifacts, terms):
    if task is None:
        return []
    eligible = [
        entry
        for entry in artifacts
        if entry.get("scope") == "task"
        and (
            entry.get("rel_path") in {"instruction.md", "task.toml"}
            or entry.get("rel_path", "").startswith("environment/assets/")
            and Path(entry["rel_path"]).suffix.lower() in {".md", ".json"}
            and "solution" not in Path(entry["rel_path"]).parts
        )
    ]
    eligible.sort(
        key=lambda entry: (
            not entry["rel_path"].startswith("instruction."),
            "contract" not in entry["rel_path"].lower()
            and "schema" not in entry["rel_path"].lower(),
            entry["rel_path"],
        )
    )
    records = []
    for entry in eligible[:24]:
        rel = entry["rel_path"]
        text = _read(task, rel, entry.get("sha256"))
        if not text:
            continue
        if ("schema" in rel.lower() or "contract" in rel.lower()) and len(text.encode()) <= 5500:
            slices = [{"line": 1, "text": text}]
        else:
            slices = _matched_sections(text, terms)
        if rel == "instruction.md" and not slices:
            slices = [{"line": 1, "text": _excerpt(text, 900)}]
        if slices:
            records.append({"source": rel, "ref": entry.get("artifact_id"), "excerpts": slices})
        if len(records) == 5:
            break
    return records


def _json_value(payload, path):
    for key, index in re.findall(r"\.([\w-]+)|\[(\d+)\]", path):
        payload = payload[key] if key else payload[int(index)]
    return payload


def _submission_slices(
    trial,
    artifacts,
    failures,
    diagnostic,
    names,
    scorer_names,
    agent_names,
    terms,
    cited_rows,
    declared_paths,
):
    bound_paths = {
        field["source"]
        for failure in failures
        for key in ("submitted_field", "key_set_difference", "type_difference")
        if isinstance(field := failure.get(key), dict) and field.get("source")
    }
    structured = [
        entry
        for entry in artifacts
        if entry.get("scope") == "trial"
        and str(entry.get("rel_path") or "").startswith("artifacts/")
        and PurePosixPath(entry["rel_path"]).name != "manifest.json"
        and PurePosixPath(entry["rel_path"]).suffix.lower() in {".json", ".csv", ".tsv"}
    ]
    ranked = []
    for entry in structured:
        rel = entry["rel_path"]
        filename = PurePosixPath(rel).name
        if cited_rows.get(entry.get("artifact_id")) or rel in bound_paths:
            origin = "bound_in_audit"
        elif filename in names:
            origin = "named_by_rejection_or_anchor"
        elif filename in scorer_names:
            origin = "mentioned_in_scorer"
        elif filename in agent_names:
            origin = "mentioned_in_invoked_source"
        elif rel in declared_paths:
            origin = "declared_output_preview"
        elif len(structured) <= 4:
            origin = "small_inventory_preview"
        elif _tokens(re.sub(r"[_\-.]", " ", PurePosixPath(filename).stem)) & terms:
            origin = "filename_term_preview"
        else:
            continue
        priority = (
            "bound_in_audit",
            "named_by_rejection_or_anchor",
            "mentioned_in_scorer",
            "mentioned_in_invoked_source",
            "declared_output_preview",
            "small_inventory_preview",
            "filename_term_preview",
        ).index(origin)
        filename_terms = _tokens(re.sub(r"[_\-.]", " ", PurePosixPath(filename).stem))
        rank = (priority, -len(filename_terms & terms), not filename.endswith(".json"), rel)
        ranked.append((rank, rel, origin, entry))

    records = []
    for _, rel, origin, entry in sorted(ranked, key=lambda entry: entry[0]):
        filename = PurePosixPath(rel).name
        ext = PurePosixPath(rel).suffix.lower()
        cited = cited_rows.get(entry.get("artifact_id"), set())
        text = _read(trial, rel, entry.get("sha256"), limit=_MAX_STRUCTURED_FILE_BYTES)
        if text is None:
            continue
        fields = []
        if ext == ".json":
            try:
                payload = json.loads(text)
            except (ValueError, RecursionError):
                continue
            pointers = {}
            for item in failures:
                field = item.get("submitted_field") or {}
                if field.get("source") == rel and _JSON_PATH.fullmatch(field.get("pointer") or ""):
                    pointers[field["pointer"]] = None
                detail = item.get("detail") or ""
                if filename in detail:
                    pointers.update(dict.fromkeys(_JSON_PATH.findall(detail)))
            for pointer in list(pointers)[:4]:
                try:
                    value = _json_value(payload, pointer)
                    rendered = json.dumps(value, ensure_ascii=False, indent=2)
                except (TypeError, KeyError, ValueError, IndexError, RecursionError):
                    continue
                fields.append({"pointer": pointer, "text": _excerpt(rendered, 1550)})
            if not fields and isinstance(payload, dict):
                fields = [{"pointer": "$", "keys": list(payload)[:40]}]
                if len(text) <= 1200:
                    fields[0].update(text=text.strip(), sample_only=True)
                if isinstance(payload.get("rows"), list) and payload["rows"]:
                    first_row = payload["rows"][0]
                    if isinstance(first_row, dict):
                        fields.append(
                            {
                                "pointer": "$.rows[0]",
                                "sample_only": True,
                                "text": _excerpt(
                                    json.dumps(first_row, ensure_ascii=False, indent=2), 1250
                                ),
                            }
                        )
            elif not fields and isinstance(payload, list):
                fields = [{"pointer": "$", "length": len(payload)}]
                if payload and isinstance(payload[0], dict):
                    fields.append(
                        {"pointer": "$[0]", "keys": list(payload[0])[:40], "sample_only": True}
                    )
        else:
            try:
                table = csv.DictReader(io.StringIO(text), delimiter="\t" if ext == ".tsv" else ",")
                if table.fieldnames:
                    fields.append({"line": 1, "columns": table.fieldnames[:30]})
                # Match literal, nontrivial row identifiers in the rejection.
                for row in table:
                    ids = [
                        value
                        for value in row.values()
                        if isinstance(value, str)
                        and len(value) >= 3
                        and re.search(r"[A-Za-z]", value)
                        and value.lower() in terms
                        and value in diagnostic
                    ]
                    if table.line_num in cited or (not cited and ids):
                        fields.append(
                            {
                                "line": table.line_num,
                                "linked_in_audit": table.line_num in cited,
                                "matched_ids": ids[:5],
                                "text": _excerpt(json.dumps(row, ensure_ascii=False), 1200),
                            }
                        )
                    if len(fields) >= 4:
                        break
            except (csv.Error, RecursionError):
                continue
        if fields:
            records.append(
                {
                    "source": rel,
                    "ref": entry.get("artifact_id"),
                    "selection": origin,
                    "slices": fields,
                }
            )
        if len(records) == 4:
            break
    return records


def _agent_sources(trial, artifacts, timeline, names, terms):
    rebuilt, executions = trace_sources(timeline)
    candidates = {}
    for path, item in rebuilt.items():
        if isinstance(item.get("text"), str):
            candidates[path] = {
                "path": path,
                "source_status": "literal_trace_reconstruction",
                **item,
            }
    source_entries = [
        a
        for a in artifacts
        if a.get("scope") == "trial"
        and a.get("rel_path", "").startswith("artifacts/")
        and PurePosixPath(a["rel_path"]).suffix.lower() in SOURCE_SUFFIXES
    ]
    source_entries.sort(
        key=lambda a: (PurePosixPath(a["rel_path"]).name not in names, a["rel_path"])
    )
    for entry in source_entries[:128]:
        rel = entry.get("rel_path", "")
        path = "/" + rel.removeprefix("artifacts/")
        text = _read(trial, rel, entry.get("sha256"))
        if text is not None:
            reconstructed = rebuilt.get(path, {})
            candidates[path] = {
                "path": path,
                "source": rel,
                "ref": entry.get("artifact_id"),
                "source_status": "captured_submission",
                "text": text,
                "write_event_refs": reconstructed.get("write_event_refs", [])
                if reconstructed.get("text") == text
                else [],
            }
    records = []
    ranked = sorted(
        candidates.values(),
        key=lambda item: (
            PurePosixPath(item["path"]).name not in names,
            not executions.get(item["path"]),
            -len(_tokens(item["text"]) & terms),
            item["source_status"] != "captured_submission",
            item["path"],
        ),
    )
    seen = set()
    for item in ranked:
        path = item["path"]
        name = PurePosixPath(path).name
        if name in seen:
            continue
        seen.add(name)
        raw = item["text"]
        digest = "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()
        sections = _matched_sections(raw, terms)
        records.append(
            {
                **{k: v for k, v in item.items() if k not in {"text", "write_event_refs"}},
                "write_event_refs": item.get("write_event_refs", [])[-12:],
                "sha256": digest,
                "invocations_matching_traced_text": [
                    invocation
                    for invocation in executions.get(path, [])
                    if invocation.get("sha256") == digest
                ][-6:],
                "text": _excerpt(raw, 2000),
                "excerpts": sections,
                "mentioned_output_files": sorted(
                    set(re.findall(r"[\w.-]+\.(?:csv|tsv|json)\b", raw))
                )[:24],
            }
        )
        if len(records) == 4:
            break
    return records


def _decision_context(timeline, anchors, agent_sources, runtime_triage):
    ids = {
        anchor.get("event_id")
        for key in ("algorithm_or_param_definition", "deliverable_generation")
        if (anchor := anchors.get(key))
    }
    ids.update(item.get("event_id") for item in anchors.get("self_check_executions", [])[-4:])
    ids.update(item.get("event_ref") for item in runtime_triage.get("last_tool_events", []))
    priority = ids.copy()
    for item in agent_sources:
        ids.update(item["write_event_refs"][-3:])
        ids.update(
            invocation["event_ref"] for invocation in item["invocations_matching_traced_text"]
        )
    preferred = [event for event in timeline if event.get("event_id") in priority]
    optional = [event for event in timeline if event.get("event_id") in ids - priority]
    selected = {event["event_id"] for event in (preferred[-8:] + optional[-4:])}
    tool_events = [event for event in timeline if event.get("event_id") in selected]
    thoughts = {
        event["event_id"].rsplit(":", 1)[0]: event
        for event in timeline
        if event.get("event_type") == "agent_thought" and event.get("event_id")
    }
    records = []
    for event in tool_events:
        thought = thoughts.get(event["event_id"].split(":tool:", 1)[0])
        record = {
            "ref": event["event_id"],
            "source_pointer": event.get("source_pointer"),
            "tool": event.get("tool_name"),
            "command": _excerpt(event.get("command") or "", 480),
            "result": _excerpt(event.get("observation") or "", 600),
            "tool_succeeded": event.get("tool_succeeded"),
        }
        if thought:
            record["recorded_reasoning"] = {
                "ref": thought["event_id"],
                "source_pointer": thought.get("source_pointer"),
                "text": _excerpt(thought.get("observation") or "", 750),
            }
        records.append(record)
    return records


def _compact_text_fields(record, limit):
    """Shrink display excerpts while retaining their evidence identifiers."""
    if isinstance(record, list):
        for value in record:
            _compact_text_fields(value, limit)
    elif isinstance(record, dict):
        for key, value in list(record.items()):
            if (
                key in {"text", "command", "result"}
                and isinstance(value, str)
                and len(value) > limit
            ):
                start = (limit - 18) * 2 // 3
                end = limit - 18 - start
                record[key] = value[:start] + "\n… [shortened] …\n" + value[-end:]
                record["excerpt_shortened"] = True
                if "complete_function" in record:
                    record["complete_function"] = False
            elif isinstance(value, (list, dict)):
                _compact_text_fields(value, limit)


def _fit_pack(pack):
    def oversized():
        return len(json.dumps(pack, ensure_ascii=False)) > _MAX_PACK_CHARS

    if not oversized():
        return
    pack["limits"].append(
        "Some excerpts or lower-priority records exceeded the dossier size budget."
    )
    for key, size in (
        ("agent_sources", 700),
        ("public_requirements", 950),
        ("verifier_functions", 2100),
        ("decision_context", 360),
        ("submission_slices", 750),
        ("oracle", 750),
    ):
        _compact_text_fields(pack.get(key), size)
        if not oversized():
            return
    for key in (
        "public_requirements",
        "agent_sources",
        "verifier_functions",
        "submission_slices",
        "contract_review_items",
        "failure_locations",
    ):
        while len(pack[key]) > 1 and oversized():
            pack[key].pop()
    if oversized():
        _compact_text_fields(pack["decision_context"], 180)


def build_dossier(evidence, analysis, *, with_oracle=False):
    trial = Path(evidence["trial_dir"])
    task = Path(evidence["task_dir"]) if evidence.get("task_dir") else None
    artifacts = evidence.get("artifacts") or []
    timeline = evidence.get("timeline") or []
    contract = evidence.get("contract_review") or {}
    failures = (contract.get("failure_locations") or [])[:8]
    report = evidence.get("verifier_report") or {}
    checks = (report.get("failed_checks") or [])[:6]
    anchors = evidence.get("causal_anchors") or {}
    diagnostic, terms, names = _terms(failures, anchors, checks)
    agent_sources = _agent_sources(trial, artifacts, timeline, names, terms)
    verifier_functions = _verifier_functions(task, failures, artifacts)
    scorer_names = {
        name for entry in verifier_functions for name in entry["mentioned_structured_files"]
    }
    declared_paths = {
        "artifacts/" + path.lstrip("/")
        for path in (evidence.get("metadata") or {}).get("declared_deliverables") or []
        if isinstance(path, str) and path.startswith("/")
    }
    artifact_names = {
        PurePosixPath(a["rel_path"]).name
        for a in artifacts
        if a.get("scope") == "trial" and a.get("rel_path", "").startswith("artifacts/")
    }
    candidate_files = sorted(
        {
            name
            for entry in agent_sources
            if entry["invocations_matching_traced_text"]
            for name in entry["mentioned_output_files"]
            if name in artifact_names
        }
        - names,
        key=lambda name: (-len(_tokens(re.sub(r"[_\-.]", " ", name)) & terms), name),
    )
    cited_rows = {}
    for item in evidence.get("scientific_observations") or []:
        ref = item.get("agent_artifact_ref") or ""
        match = re.fullmatch(r"(art:.*):L([1-9]\d*)", ref)
        if match:
            cited_rows.setdefault(match[1], set()).add(int(match[2]))
    assessment = analysis.get("causal_assessment") or {}
    runtime_triage = (analysis.get("failure_manifestation") or {}).get("runtime_triage") or {}
    lock = next((a for a in artifacts if a.get("artifact_id") == "art:trial_lock"), {})
    lock_text = _read(trial, "lock.json", lock.get("sha256")) if lock.get("exists") else None
    try:
        locked_task = json.loads(lock_text).get("task") if lock_text else None
    except (ValueError, AttributeError):
        locked_task = None
    pack = {
        "schema_version": "cfa-dossier-v1",
        "trial_name": analysis.get("trial_name") or evidence.get("trial_name"),
        "verdict": analysis.get("verdict"),
        "rule_candidate": analysis.get("primary_root_cause"),
        "recorded_task": {
            "ref": "art:trial_lock",
            **{k: locked_task[k] for k in ("name", "path", "digest") if k in locked_task},
        }
        if isinstance(locked_task, dict)
        else None,
        "failure": assessment.get("failure_detail"),
        "failed_checks": [
            {k: item.get(k) for k in ("name", "detail", "layer", "source", "source_pointer")}
            for item in checks
        ],
        "failure_locations": failures,
        "verifier_functions": verifier_functions,
        "public_requirements": _public_requirements(task, artifacts, terms),
        "submission_slices": _submission_slices(
            trial,
            artifacts,
            failures,
            diagnostic,
            names,
            scorer_names,
            set(candidate_files[:3]),
            terms,
            cited_rows,
            declared_paths,
        ),
        "contract_review_items": (contract.get("review_items") or [])[:6],
        "agent_sources": agent_sources,
        "self_checks": anchors.get("self_check_executions", [])[-6:],
        "runtime_triage": runtime_triage,
        "decision_context": _decision_context(timeline, anchors, agent_sources, runtime_triage),
        "oracle_assisted": False,
        "limits": [
            "Audit sources and bounded text slices are leads. Match the effective input and recorded output before assigning responsibility.",
            "Captured code represents submission bytes. A traced invocation matches a literal version only where the recorded writes resolve.",
            "Recorded reasoning gives context for a decision. Cite an execution event as evidence of the action.",
        ],
    }
    if with_oracle and task:
        reference = _read(task, "solution/solve.sh")
        if reference:
            pack["oracle_assisted"] = True
            pack["oracle"] = {
                "source": "solution/solve.sh",
                "role": "private comparison; public requirements determine responsibility",
                "excerpts": _matched_sections(reference, terms),
                "text": _excerpt(reference, 3000),
            }
    _fit_pack(pack)
    return pack


def render_dossier(pack):
    headings = {
        "recorded_task": "试次任务版本",
        "failure": "终止记录",
        "failed_checks": "评分记录",
        "failure_locations": "拒绝条件与位置",
        "verifier_functions": "评分函数",
        "public_requirements": "公开要求",
        "submission_slices": "提交物切片",
        "contract_review_items": "契约对照",
        "agent_sources": "Agent 源码",
        "self_checks": "已记录的自检",
        "runtime_triage": "运行中断线索",
        "decision_context": "决策和执行记录",
        "oracle": "私有参考对照",
        "limits": "证据边界",
    }
    lines = [f"# 归因调查包 · {pack['trial_name']}", "", f"评测结果 `{pack['verdict']}`"]
    for key, title in headings.items():
        if key in pack:
            data = json.dumps(pack[key], ensure_ascii=False, indent=2)
            fence = "`" * max(3, 1 + max((len(s) for s in re.findall(r"`+", data)), default=0))
            lines.extend(["", f"## {title}", "", fence + "json", data, fence])
    return "\n".join(lines) + "\n"
