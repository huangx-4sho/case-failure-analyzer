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


def _messages(tree, parents):
    """Inspect only rejection sites and explicit named test calls, without importing code."""
    for node in ast.walk(tree):
        predicate = None
        label = None
        if isinstance(node, ast.Assert):
            message, predicate = node.msg, node.test
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
            else:
                continue
        else:
            continue
        if isinstance(message, ast.Constant) and isinstance(message.value, str):
            pieces = [message.value]
        elif isinstance(message, ast.JoinedStr):
            pieces = [
                x.value if isinstance(x, ast.Constant) and isinstance(x.value, str) else None
                for x in message.values
            ]
        else:
            continue
        # Match a diagnostic literally, with at most two short dynamic substitutions.
        if pieces.count(None) > 2 or sum(len(x) for x in pieces if x) > 1200:
            continue
        if not label and sum(len(x.strip()) for x in pieces if x) < 8:
            continue
        pattern = re.compile(
            "".join(re.escape(x) if x is not None else ".{1,140}?" for x in pieces)
        )
        if predicate is None:
            parent = parents.get(node)
            while parent is not None and not isinstance(parent, ast.stmt):
                parent = parents.get(parent)
            if parent is not None and not isinstance(parent, ast.If):
                parent = parents.get(parent)
            if isinstance(parent, ast.If):
                predicate = parent.test
        yield node.lineno, label, pattern, predicate


def _submission_value(detail, submitted, trial):
    pointers = _JSON_PATH.findall(detail)
    if len(pointers) != 1 or trial is None:
        return None
    names = set(re.findall(r"[\w.-]+\.json\b", detail))
    files = [(p, v) for p, v in submitted if p.name in names]
    if len(files) != 1:
        return None
    path, value = files[0]
    try:
        for key, index in re.findall(r"\.([\w-]+)|\[(\d+)\]", pointers[0]):
            value = value[key] if key else value[int(index)]
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    if isinstance(value, (dict, list)) or not path.is_relative_to(trial):
        return None
    return {
        "source": str(path.relative_to(trial)),
        "pointer": pointers[0],
        "value": value[:160] if isinstance(value, str) else value,
        **({"truncated": True} if isinstance(value, str) and len(value) > 160 else {}),
    }


def _guard_field(predicate, parents, submitted, trial):
    """Show a submitted scalar named by a guard and a uniquely assigned JSON reader."""
    if predicate is None or trial is None:
        return None
    fields = [
        (node.value.id, node.slice.value)
        for node in ast.walk(predicate)
        if isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Name)
        and isinstance(node.slice, ast.Constant)
        and isinstance(node.slice.value, str)
    ]
    if len(fields) != 1:
        return None
    name, key = fields[0]
    scope = predicate
    while scope in parents and not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
        scope = parents[scope]
    writes = [
        n
        for n in ast.walk(scope)
        if isinstance(n, ast.Name) and n.id == name and isinstance(n.ctx, ast.Store)
    ]
    if len(writes) != 1:
        return None
    assign = parents.get(writes[0])
    if (
        not isinstance(assign, ast.Assign)
        or assign.lineno >= predicate.lineno
        or not isinstance(assign.value, ast.Call)
    ):
        return None
    files = [
        n.value
        for n in ast.walk(assign.value)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value.endswith(".json")
    ]
    if len(files) != 1 or not re.fullmatch(r"[\w-]+", key):
        return None
    return _submission_value(f"{Path(files[0]).name} $.{key}", submitted, trial)


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


def locate_failures(evidence, tree, parents, grader, submitted, logs):
    """Collect observations about this trial. These records cannot select a peer cause."""
    if (evidence.get("runtime") or {}).get("verdict") not in ("failed", "errored", "unknown"):
        return []
    report = evidence.get("verifier_report") or {}
    checks = report.get("failed_checks") or []
    trial = Path(evidence["trial_dir"]) if evidence.get("trial_dir") else None
    locations = []
    for i, c in enumerate(checks[:32]):
        detail = str(c.get("detail") or c.get("name") or "")
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
    rules = list(_messages(tree, parents))
    task = Path(evidence["task_dir"]) if evidence.get("task_dir") else None
    for item in locations:
        detail = item["detail"]
        short = re.sub(
            r"^(?:(?:\w*(?:Error|Failure)|FAIL(?:ED)?(?: L\d+)?)\s*:\s*)+", "", detail, flags=re.I
        )
        sites = [
            (line, pred)
            for line, label, pat, pred in rules
            if (label and label == item.get("name")) or pat.fullmatch(short)
        ]
        pred = None
        if grader and task and len(sites) == 1:
            line, pred = sites[0]
            item["matching_rule"] = {"source": str(grader.relative_to(task)), "line": line}
            if pred is not None:
                item["matching_rule"]["condition"] = ast.unparse(pred)[:240]
        found = (
            _submission_value(detail, submitted, trial)
            or _guard_field(pred, parents, submitted, trial)
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
    if not check and category == "verifier":
        refs = set(analysis.get("evidence_refs") or [])
        obs = next(
            (
                o
                for o in evidence.get("verifier_observations") or []
                if o.get("type") == "verifier_internal_crash" and o.get("obs_id") in refs
            ),
            None,
        )
        if obs and (obs.get("source_file") or obs.get("source_ref")):
            check = {
                "ref": obs["obs_id"],
                "detail": obs.get("summary") or obs.get("matched_text") or code,
                "source": obs.get("source_file") or obs.get("source_ref"),
                "pointer": obs.get("source_pointer"),
            }
    return {
        "runtime_event": {
            k: runtime.get(k) for k in ("error_id", "source_file", "source_pointer", "matched_text")
        }
        if runtime
        else None,
        "scoring_event": check,
    }


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
        lines.append(
            f"> **{'Recorded rejection' if en else '评分拒绝'}** · {source_quotation(clean_prose(msg, 350), lang)} (`{check['source']}{' ' + check['pointer'] if check.get('pointer') else ''}`)"
        )
        if check.get("reward_scope") == "evidence_completeness_only":
            lines.append(
                "> Scoring scope · evidence completeness only; scientific review is pending."
                if en
                else "> 评分范围 · 记录完整性。科学结论仍待审查。"
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
        if assessment.get("level") == "observed_failure":
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
