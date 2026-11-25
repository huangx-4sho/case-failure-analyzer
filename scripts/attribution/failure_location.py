"""Relate a recorded rejection to static scorer code and explicitly named delivered fields."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

# Plain JSONPath fields and array offsets only. Unsupported paths stay unresolved.
_JSON_PATH = re.compile(r"\$(?:\.[\w-]+|\[\d+\])+(?![\w\[.])")
_COARSE_REJECTION = re.compile(
    r"candidate submission did not satisfy the public structured contract|submission (?:failed|invalid)|checks? failed",
    re.I,
)
_GENERIC = re.compile(r"(?:FAIL(?:ED)?[: ]*)?(?:reward\s*=\s*0(?:\.0)?)?\s*$", re.I)


def _message_parts(node, depth=0):
    if depth > 12:
        return [None]
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.JoinedStr):
        return [part for child in node.values for part in _message_parts(child, depth + 1)]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _message_parts(node.left, depth + 1) + _message_parts(node.right, depth + 1)
    return [None]


def _matches_message(parts, message):
    """Match ordered literal fragments separated by bounded synthetic placeholders."""
    if not parts or not any(parts):
        return False
    position = 0
    wildcard = False
    for index, part in enumerate(parts):
        if part is None:
            wildcard = True
            continue
        if wildcard and index == len(parts) - 1:
            match = len(message) - len(part) if message.endswith(part) else -1
        elif wildcard:
            match = message.find(part, position)
        else:
            match = position if message.startswith(part, position) else -1
        if match < position:
            return False
        position = match + len(part)
        wildcard = False
    return wildcard or position == len(message)


def _messages(tree, parents):
    """Inspect only rejection sites and explicit named test calls, without importing code."""
    for node in ast.walk(tree):
        predicate = label = message = None
        if isinstance(node, ast.Assert):
            message, predicate = node.msg, node.test
        elif isinstance(node, ast.Dict):
            fields = {
                k.value: v
                for k, v in zip(node.keys, node.values)
                if isinstance(k, ast.Constant) and isinstance(k.value, str)
            }
            named = fields.get("name") or fields.get("criterion")
            if isinstance(named, ast.Constant) and isinstance(named.value, str):
                label = named.value
                predicate = fields.get("ok") or fields.get("passed")
                message = fields.get("detail") or fields.get("reason")
        elif isinstance(node, ast.Call) and node.args:
            func = getattr(node.func, "id", None) or getattr(node.func, "attr", "")
            if (
                func in {"ck", "check"}
                and len(node.args) >= 3
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                label, predicate, message = node.args[0].value, node.args[1], node.args[2]
            elif func in {"require", "need", "check", "ensure"} and len(node.args) >= 2:
                predicate, message = node.args[:2]
            elif func == "fail" or isinstance(parents.get(node), ast.Raise):
                message = node.args[-1]
        elif isinstance(node, ast.Return) and isinstance(node.value, ast.Tuple):
            elements = node.value.elts
            if (
                len(elements) == 2
                and isinstance(elements[0], ast.Constant)
                and elements[0].value is False
            ):
                message = elements[1]
        elif isinstance(node, ast.Return) and isinstance(node.value, ast.List):
            if len(node.value.elts) == 1:
                message = node.value.elts[0]
        if message is None and not label:
            continue
        parts = _message_parts(message)
        literal_size = sum(len(part.strip()) for part in parts if part is not None)
        if not 8 <= literal_size <= 1200:
            parts = []
        if not label and not parts:
            continue
        if predicate is None:
            parent = parents.get(node)
            while parent is not None and not isinstance(parent, ast.stmt):
                parent = parents.get(parent)
            if parent is not None and not isinstance(parent, ast.If):
                parent = parents.get(parent)
            if isinstance(parent, ast.If):
                predicate = parent.test
        yield node, label, parts, predicate


def _submission_value(detail, submitted, trial):
    pointers = _JSON_PATH.findall(detail)
    if len(pointers) != 1 or trial is None:
        return None
    names = set(re.findall(r"[\w.-]+\.json\b", detail))
    files = [(p, v) for p, v in submitted if p.name in names]
    if len(files) != 1:
        return None
    return _field_value(*files[0], pointers[0], trial)


def _field_value(path, value, pointer, trial):
    found, value = _read_pointer(value, pointer)
    if not found:
        return None
    if not path.is_relative_to(trial):
        return None
    encoded = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else ""
    truncated = len(encoded) > 1600 or (isinstance(value, str) and len(value) > 160)
    if len(encoded) > 1600:
        value = encoded[:1600]
    elif isinstance(value, str):
        value = value[:160]
    return {
        "source": str(path.relative_to(trial)),
        "pointer": pointer,
        "value": value,
        **({"truncated": True} if truncated else {}),
    }


def _read_pointer(value, pointer):
    try:
        for key, index in re.findall(r"\.([\w-]+)|\[(\d+)\]", pointer):
            value = value[key] if key else value[int(index)]
    except (KeyError, IndexError, TypeError, ValueError):
        return False, None
    return True, value


def _scope(node, parents):
    while node in parents and not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        node = parents[node]
    return node


def _reader_binding(node, scope, before, parents, depth=0):
    """Resolve a unique local assignment rooted at a literal submitted JSON reader."""
    if depth > 8:
        return None
    if isinstance(node, ast.Subscript):
        child = node.slice
        source = node.value
    elif (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and len(node.args) == 1
    ):
        child = node.args[0]
        source = node.func.value
    else:
        child = source = None
    if isinstance(child, ast.Constant):
        base = _reader_binding(source, scope, before, parents, depth + 1)
        if base and isinstance(child.value, str) and re.fullmatch(r"[\w-]+", child.value):
            return base[0], base[1] + "." + child.value
        if base and type(child.value) is int and child.value >= 0:
            return base[0], base[1] + f"[{child.value}]"
    if isinstance(node, ast.Call):
        func = getattr(node.func, "id", None) or getattr(node.func, "attr", "")
        if func not in {
            "load",
            "loads",
            "read_json",
            "load_json",
            "jload",
            "json_read",
            "strict_json",
        }:
            return None
        files = {
            n.value
            for n in ast.walk(node)
            if isinstance(n, ast.Constant)
            and isinstance(n.value, str)
            and n.value.endswith(".json")
        }
        return (Path(files.pop()).name, "$") if len(files) == 1 else None
    if not isinstance(node, ast.Name):
        return None
    writes = [
        n
        for n in ast.walk(scope)
        if isinstance(n, ast.Name) and n.id == node.id and isinstance(n.ctx, ast.Store)
    ]
    if not writes and isinstance(scope, ast.FunctionDef):
        positional = [arg.arg for arg in scope.args.posonlyargs + scope.args.args]
        if node.id not in positional:
            return None
        index = positional.index(node.id)
        module = scope
        while module in parents:
            module = parents[module]
        calls = [
            call
            for call in ast.walk(module)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == scope.name
            and len(call.args) > index
            and not isinstance(call.args[index], ast.Starred)
            and _scope(call, parents) is not scope
        ]
        if len(calls) == 1:
            call = calls[0]
            return _reader_binding(
                call.args[index], _scope(call, parents), call.lineno, parents, depth + 1
            )
    if len(writes) != 1:
        return None
    assign = parents.get(writes[0])
    if not isinstance(assign, ast.Assign) or assign.lineno >= before:
        return None
    return _reader_binding(assign.value, scope, assign.lineno, parents, depth + 1)


def _bound_field(expression, predicate, parents, submitted, trial):
    bound = _bound_value(expression, predicate, parents, submitted)
    return _field_value(*bound, trial) if bound else None


def _bound_value(expression, predicate, parents, submitted):
    bound = _reader_binding(expression, _scope(predicate, parents), predicate.lineno, parents)
    if bound:
        file, pointer = bound
        candidates = [(path, val) for path, val in submitted if path.name == file]
        if len(candidates) == 1:
            path, value = candidates[0]
            return path, value, pointer
    return None


def _guard_field(predicate, parents, submitted, trial):
    """Show a submitted field named by a guard and a uniquely assigned JSON reader."""
    if predicate is None or trial is None:
        return None
    fields = {
        ast.unparse(node): node
        for node in ast.walk(predicate)
        if isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Name)
        and isinstance(node.slice, ast.Constant)
        and isinstance(node.slice.value, str)
    }
    if len(fields) != 1:
        return None
    return _bound_field(next(iter(fields.values())), predicate, parents, submitted, trial)


def _key_set_difference(predicate, rejection_node, parents, submitted, trial):
    if predicate is None or trial is None:
        return None
    required = isinstance(rejection_node, ast.Assert) or (
        isinstance(rejection_node, ast.Call)
        and getattr(rejection_node.func, "id", None) in {"require", "check", "need", "ensure"}
    )
    comparison = ast.Eq if required else ast.NotEq
    for node in ast.walk(predicate):
        if not (
            isinstance(node, ast.Compare)
            and len(node.ops) == 1
            and isinstance(node.ops[0], comparison)
            and isinstance(node.left, ast.Call)
            and isinstance(node.left.func, ast.Name)
            and node.left.func.id == "set"
            and len(node.left.args) == 1
            and isinstance(node.comparators[0], ast.Set)
        ):
            continue
        elements = node.comparators[0].elts
        if not all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in elements):
            continue
        actual = _bound_value(node.left.args[0], predicate, parents, submitted)
        if not actual:
            continue
        path, value, pointer = actual
        found, value = _read_pointer(value, pointer)
        if not found or not path.is_relative_to(trial) or not isinstance(value, dict):
            continue
        expected = {e.value for e in elements}
        missing = sorted(expected - value.keys())
        unexpected = sorted(value.keys() - expected)
        if not missing and not unexpected:
            continue
        return {
            "source": str(path.relative_to(trial)),
            "pointer": pointer,
            "expected_keys": sorted(expected)[:50],
            "submitted_keys": sorted(value)[:50],
            "missing_keys": missing[:50],
            "unexpected_keys": unexpected[:50],
        }
    return None


_JSON_NAMES = {
    "int": "integer",
    "float": "number",
    "bool": "boolean",
    "dict": "object",
    "list": "array",
    "str": "string",
    "NoneType": "null",
}


def _predicate_types(predicate, rejection_node, parents):
    """Extract an unambiguous shape constraint, leaving dynamic operands unresolved."""
    if predicate is None:
        return []
    candidates = set()
    for node in ast.walk(predicate):
        parent = parents.get(node)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "isinstance"
            and len(node.args) == 2
        ):
            flipped = isinstance(parent, ast.UnaryOp) and isinstance(parent.op, ast.Not)
            require = (
                isinstance(rejection_node, ast.Call)
                and getattr(rejection_node.func, "id", None)
                in {"require", "check", "need", "ensure"}
            ) or isinstance(rejection_node, ast.Assert)
            if flipped == require:
                continue
            types = node.args[1]
        elif (
            isinstance(node, ast.Compare)
            and isinstance(node.left, ast.Call)
            and isinstance(node.left.func, ast.Name)
            and node.left.func.id == "type"
            and len(node.ops) == 1
            and isinstance(node.ops[0], ast.NotIn)
        ):
            types = node.comparators[0]
        else:
            continue
        names = types.elts if isinstance(types, (ast.Tuple, ast.List, ast.Set)) else [types]
        if names and all(isinstance(name, ast.Name) and name.id in _JSON_NAMES for name in names):
            candidates.add(tuple(sorted({_JSON_NAMES[name.id] for name in names})))
    return list(next(iter(candidates))) if len(candidates) == 1 else []


def _recorded_site(location, path, node):
    recorded = location.get("reported_scorer_location") or {}
    line = recorded.get("line")
    source = recorded.get("source")
    if not isinstance(line, int) or not isinstance(source, str):
        return False
    source = source.removeprefix("/")
    if "/tests/" in source:
        source = "tests/" + source.rsplit("/tests/", 1)[1]
    return path.split("!", 1)[-1] == source and node.lineno <= line <= node.end_lineno


def _comparison_field(detail, predicate, parents, submitted, trial):
    """Resolve a recursive comparator's reported root label through its unique call site."""
    if predicate is None or trial is None:
        return None
    pointer = re.search(r"^([a-zA-Z_]\w*)((?:\.[\w-]+|\[\d+\])+)(?=\s|:|$)", detail)
    fn = _scope(predicate, parents)
    if not pointer or not isinstance(fn, ast.FunctionDef):
        return None
    if not any(
        isinstance(d, ast.Constant) and d.value == pointer.group(1) for d in fn.args.defaults
    ):
        return None
    module = fn
    while module in parents:
        module = parents[module]
    callers = [
        n
        for n in ast.walk(module)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == fn.name
        and n.args
        and _scope(n, parents) is not fn
    ]
    if len(callers) != 1:
        return None
    files = [
        n.value
        for n in ast.walk(callers[0].args[0])
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value.endswith(".json")
    ]
    if len(files) == 1:
        return _submission_value(f"{Path(files[0]).name} ${pointer.group(2)}", submitted, trial)
    return None


def _log_field(detail, evidence, trial):
    """Retain a named metric line even when the grader rejected its formatting."""
    match = re.search(r"([\w.-]+\.log) is missing numeric ([\w.-]+)", detail)
    if not match or trial is None:
        return None
    name, metric = match.groups()
    files = [
        a
        for a in evidence.get("artifacts") or []
        if a.get("scope") == "trial"
        and a.get("exists")
        and str(a.get("rel_path", "")).startswith("artifacts/")
        and Path(a["rel_path"]).name == name
    ]
    if len(files) != 1:
        return None
    rel = files[0]["rel_path"]
    src = trial / rel
    try:
        if (
            src.is_symlink()
            or not src.resolve().is_relative_to(trial.resolve())
            or src.stat().st_size > 131072
        ):
            return None
        with src.open(encoding="utf-8") as f:
            for i, line in enumerate(f, 1):
                if metric in line.split():
                    return {"source": rel, "pointer": f"L{i}", "value": line.strip()[:240]}
    except (OSError, UnicodeError):
        return None
    return None


def locate_failures(evidence, tree, parents, grader, submitted, logs, *, parsed_sources=()):
    """Collect observations about this trial. These records cannot select a peer cause."""
    if (evidence.get("runtime") or {}).get("verdict") not in ("failed", "errored", "unknown"):
        return []
    report = evidence.get("verifier_report") or {}
    checks = report.get("failed_checks") or []
    trial = Path(evidence["trial_dir"]) if evidence.get("trial_dir") else None
    locations = []
    for i, c in enumerate(checks[:32]):
        detail = str(c.get("detail") or c.get("name") or "")
        replay = report.get("candidate_replay_exception") or {}
        if (
            replay
            and replay["source"] == "verifier/" + (c.get("source") or "")
            and replay["pointer"] == c.get("source_pointer")
        ):
            path, number, statement, exception = replay["frame"]
            detail = f"{Path(path).name}:{number} · {statement} · {exception}"
        if (
            detail
            and not _GENERIC.fullmatch(detail)
            and (c.get("source") or report.get("primary_report"))
        ):
            locations.append(
                {
                    "ref": f"verifier_report.failed_checks[{i}]",
                    "name": c.get("name"),
                    "detail": detail[:600],
                    "source": "verifier/" + (c.get("source") or report["primary_report"]),
                    **({"reward_scope": c["reward_scope"]} if c.get("reward_scope") else {}),
                    "pointer": c.get("source_pointer"),
                    **(
                        {"reported_scorer_location": c["reported_scorer_location"]}
                        if c.get("reported_scorer_location")
                        else {}
                    ),
                }
            )
    if (
        not locations or all(_COARSE_REJECTION.fullmatch(x["detail"]) for x in locations)
    ) and trial is not None:
        logged = None
        for path, content in logs:
            if path.suffix == ".json":
                continue
            for i, line in enumerate(content.splitlines(), 1):
                if re.match(
                    r"^\s*(?:FAIL(?:ED)?\b|E\s+(?:AssertionError|ValueError)\b)", line
                ) and not _GENERIC.fullmatch(line.strip()):
                    logged = {
                        "source": str(path.relative_to(trial)),
                        "pointer": f"L{i}",
                        "detail": line.strip()[:600],
                    }
                    break
            if logged:
                locations.insert(0, logged)
                break
    if not locations:
        return []
    task = Path(evidence["task_dir"]) if evidence.get("task_dir") else None
    sources = [
        (
            source.path,
            source_parents,
            source.sha256,
            list(_messages(source_tree, source_parents)),
        )
        for source, source_tree, source_parents in parsed_sources
    ]
    if grader and task:
        sources.append(
            (str(grader.relative_to(task)), parents, None, list(_messages(tree, parents)))
        )
    for item in locations:
        detail = item["detail"]
        short = detail.removeprefix(str(item.get("name") or "") + ": ")
        short = re.sub(
            r"^(?:(?:\w*(?:Error|Failure)|FAIL(?:ED)?(?: L\d+)?)\s*:\s*)+", "", short, flags=re.I
        ).removeprefix("Scientific verification: ")
        matched = []
        for path, source_parents, digest, rules in sources:
            for node, label, parts, pred in rules:
                exact = any(_matches_message(parts, candidate) for candidate in {detail, short})
                if exact or label == item.get("name") and label is not None:
                    # A generic nested message ending in "expected type X" can
                    # match the full diagnostic. Prefer the locator that actually
                    # accounts for its concrete text, preserving exact ties.
                    literal_length = sum(len(part) for part in parts if part is not None)
                    quality = (
                        int(_recorded_site(item, path, node)),
                        int(exact),
                        int(exact and None not in parts),
                        literal_length if exact else 0,
                    )
                    matched.append((quality, path, digest, node, pred, source_parents))
        pred = None
        selected_parents = {}
        if matched:
            best = max(site[0] for site in matched)
            sites = [site for site in matched if site[0] == best]
            if len(sites) == 1:
                _, path, digest, node, pred, selected_parents = sites[0]
                rule = item["matching_rule"] = {"source": path, "line": node.lineno}
                if digest:
                    rule["source_sha256"] = digest
                if "!" in path:
                    rule["archive_path"], rule["archive_member"] = path.split("!", 1)
                    rule["source_ref"] = (
                        "art:refs_json"
                        if rule["archive_path"] == "tests/refs.json"
                        else "art:verify_py"
                    )
                if pred is not None:
                    rule["condition"] = ast.unparse(pred)[:240]
                expected_types = _predicate_types(pred, node, selected_parents)
                if expected_types:
                    item["scorer_expected_types"] = expected_types
                exact_keys = _key_set_difference(pred, node, selected_parents, submitted, trial)
                if exact_keys:
                    item["key_set_difference"] = exact_keys
        found = (
            _submission_value(detail, submitted, trial)
            or _guard_field(pred, selected_parents, submitted, trial)
            or _comparison_field(short, pred, selected_parents, submitted, trial)
            or _log_field(detail, evidence, trial)
        )
        if found:
            item["submitted_field"] = found
    return locations


def failure_detail(evidence, analysis):
    """Separate an earlier runtime cause from any downstream verification failure."""
    category = (analysis.get("primary_root_cause") or {}).get("category")
    code = (analysis.get("primary_root_cause") or {}).get("code")
    errors = [
        x
        for x in evidence.get("error_observations") or []
        if x.get("causal_candidate") and not x.get("recovered")
    ]
    runtime = next((x for x in errors if x.get("code") == code), None)
    if category == "verifier":
        runtime = next((x for x in errors if x.get("error_id") == "err:terminal_api_limit"), None)
    locs = (evidence.get("contract_review") or {}).get("failure_locations") or []
    check = locs[0] if locs else None
    if code == "AGENT_REPLAY_IMPLEMENTATION_ERROR":
        for contract in evidence.get("contract_observations") or []:
            if contract.get("item") == "candidate_replay_exit":
                check = {
                    "ref": contract["contract_id"],
                    "detail": contract["details"],
                    "source": contract["log_source"],
                    "pointer": contract["source_pointer"],
                    "writer_ref": contract["event_ref"],
                }
                break
    if category == "verifier":
        refs = set(analysis.get("evidence_refs") or [])
        obs = next(
            (
                o
                for o in evidence.get("verifier_observations") or []
                if o.get("type") == "verifier_internal_crash" and o.get("obs_id") in refs
            ),
            None,
        )
        if (
            obs
            and (not check or obs.get("scoring_reached") is False)
            and (obs.get("source_file") or obs.get("source_ref"))
        ):
            check = {
                "ref": obs["obs_id"],
                "detail": obs.get("matched_text") or obs.get("summary") or code,
                "source": obs.get("source_file") or obs.get("source_ref"),
                "pointer": obs.get("source_pointer"),
                **({"stage": "preflight"} if obs.get("scoring_reached") is False else {}),
            }
    if check and (evidence.get("runtime") or {}).get("reward_scope") == "submission_checks_only":
        check = {**check, "reward_scope": "submission_checks_only"}
    return {
        "runtime_event": {
            k: runtime.get(k) for k in ("error_id", "source_file", "source_pointer", "matched_text")
        }
        if runtime
        else None,
        "scoring_event": check,
    }


def recorded_event(evidence, analysis):
    """Compact, source-located diagnosis for a job row, independent of who owns it."""
    runtime = evidence.get("runtime") or {}
    if analysis.get("verdict") == "pending":
        receipts = runtime.get("review_receipts") or []
        if receipts:
            receipt = receipts[0]
            return {
                "kind": "review",
                "detail": receipt.get("reason") or receipt["status"],
                "source": receipt["source_file"],
                "pointer": receipt["source_pointer"],
            }
    location = (analysis.get("causal_assessment") or {}).get("failure_detail") or {}
    stop = location.get("runtime_event")
    check = location.get("scoring_event")
    if stop and stop.get("matched_text"):
        event = {
            "kind": "interruption",
            "detail": stop["matched_text"],
            "source": stop.get("source_file"),
            "pointer": stop.get("source_pointer"),
        }
        if check:
            event["downstream_check"] = check["detail"][:300]
        triage = (analysis.get("failure_manifestation") or {}).get("runtime_triage") or {}
        last = (triage.get("last_tool_events") or [])[-1:]
        if last:
            event["last_tool_event"] = last[0]
            if "last_tool_to_agent_finish_seconds" in triage:
                event["last_tool_to_finish_seconds"] = triage["last_tool_to_agent_finish_seconds"]
        return event
    if check:
        event = {
            k: check[k]
            for k in ("detail", "source", "pointer", "name", "writer_ref")
            if check.get(k)
        }
        event["kind"] = check.get("stage", "rejection")
        difference = check.get("key_set_difference")
        if difference:
            event["field_difference"] = {
                k: difference[k] for k in ("source", "pointer", "unexpected_keys", "missing_keys")
            }
        field = check.get("submitted_field")
        if field:
            event["submitted_field"] = {
                k: field[k] for k in ("source", "pointer", "value", "truncated") if k in field
            }
        if check.get("matching_rule"):
            event["matching_rule"] = check["matching_rule"]
        return event
    return None


def render_failure_detail(assessment, lang):
    from report_style import clean_prose, source_quotation

    detail = assessment.get("failure_detail") or {}
    event, check = detail.get("runtime_event"), detail.get("scoring_event")
    en = lang == "en"
    lines = []
    if event:
        quote = source_quotation(clean_prose(event.get("matched_text") or "", 240), lang)
        lines.append(
            f"> **{'Runtime interruption' if en else '运行中断'}** · {quote} (`{event.get('source_file')} {event.get('source_pointer') or ''}`)"
        )
    if check:
        name = check.get("name") or ""
        msg = check["detail"]
        if name and name not in msg and name != "unnamed_check":
            msg = f"{name} · {msg}"
        label = "Recorded rejection" if en else "评分拒绝"
        if check.get("stage") == "preflight":
            label = "Preflight interruption" if en else "预检中断"
        lines.append(
            f"> **{label}** · {source_quotation(clean_prose(msg, 350), lang)} (`{check['source']}{' ' + check['pointer'] if check.get('pointer') else ''}`)"
        )
        if check.get("reward_scope") == "evidence_completeness_only":
            lines.append(
                "> Scoring scope · evidence completeness only; scientific review is pending."
                if en
                else "> 评分范围 · 记录完整性。科学结论仍待审查。"
            )
        elif check.get("reward_scope") == "submission_checks_only":
            lines.append(
                "> Scoring scope · submission checks only. No scientific verdict was recorded."
                if en
                else "> 评分范围 · 提交检查。科学结论尚无裁定。"
            )
        reported_site = check.get("reported_scorer_location")
        if reported_site and reported_site.get("source") and reported_site.get("line"):
            lines.append(
                f"> **{'Recorded scorer location' if en else '运行日志记录的评分位置'}** · `{reported_site['source']}:L{reported_site['line']}`"
            )
        rule = check.get("matching_rule")
        if rule:
            lines.append(
                f"> **{'Matching scorer code' if en else '对应评分代码'}** · `{rule['source']}:L{rule['line']}`"
                + (f" · `{rule['condition']}`" if rule.get("condition") else "")
            )
        field = check.get("submitted_field")
        if field:
            lines.append(
                f"> **{'Delivered value' if en else '已采集的提交值'}** · `{field['source']} {field['pointer']}` = `{json.dumps(field['value'], ensure_ascii=False)}`{'…' if field.get('truncated') else ''}"
            )
        key_diff = check.get("key_set_difference")
        if key_diff:
            label = "Scorer key-set difference" if en else "评分键集合差异"
            missing = ", ".join(key_diff["missing_keys"]) or "∅"
            unexpected = ", ".join(key_diff["unexpected_keys"]) or "∅"
            lines.append(
                f"> **{label}** · `{key_diff['source']} {key_diff['pointer']}`"
                f" · {'missing' if en else '缺少'} `{missing}`"
                f" · {'extra' if en else '额外'} `{unexpected}`"
            )
        for schema in check.get("public_schema_comparison") or []:
            label = "Public contract field" if en else "公开契约字段"
            source = schema["source"]
            if schema.get("declared_types"):
                expected = ", ".join(schema["declared_types"])
                actual = schema["submitted_type"]
                lines.append(
                    f"> **{label}** · `{source} {schema['pointer']}`"
                    f" · {'declared' if en else '规定'} `{expected}`"
                    f" · {'submitted' if en else '提交'} `{actual}`"
                )
            else:
                lines.append(f"> **{label}** · `{source} {schema['pointer']}`")
        if assessment.get("level") == "observed_failure" and (
            assessment.get("deep_review") or {}
        ).get("status") not in {"traced", "open"}:
            if field:
                gap = (
                    "Trace the submitted field to its writer and compare the guard with the public contract."
                    if en
                    else "核对这个提交字段的写入步骤与公开要求，区分生成错误和评分约定。"
                )
            elif rule:
                gap = (
                    "Identify the value actually read by this guard and the corresponding public requirement."
                    if en
                    else "查明这条评分条件当时读到的值，以及任务对该值的公开要求。"
                )
            else:
                gap = (
                    "Locate the scoring condition and the effective input for this rejection."
                    if en
                    else "定位产生这条拒绝的评分条件及其实际读取的输入。"
                )
            lines.append("> " + gap)
    if not event and not check and detail:
        lines.append(
            "> The saved records do not localize the rejection. Inspect the trial-specific exception, scorer output and collection record."
            if en
            else "> 现存记录无法定位拒绝项。需核对该试次的异常、评分输出和产物采集记录。"
        )
    return "\n".join(lines)
