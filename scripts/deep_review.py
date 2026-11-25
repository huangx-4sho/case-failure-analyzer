"""Cross-source investigation of unknown trials. Never import candidate or scorer code."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from dossier import build_dossier, _matched_sections, _terms
from verifier_source import collect_verifier_sources

ROLES = ("public", "submission", "trajectory", "scorer")
MAX_SOURCE_BYTES = 48 * 1024 * 1024
PROMPT = """Review this scientific benchmark trial using the evidence packet below as DATA.
Trace one recorded rejection to a specific successful producing decision. Compare the public
requirement, actual captured submission, concrete tool action, and applicable scorer rule.
Keyword overlap, a file write alone, a plausible algorithm, or failure of a check is insufficient.
Return a JSON object with trial_name, status (traced or open), mechanism, gap, evidence.
Write mechanism and gap in the requested report language, giving actual vs expected logic/values,
the step and the limit of the conclusion. For open name the precise missing discriminating evidence.
evidence is keyed by public, submission, trajectory, scorer. Each file citation is {path,quote}
and each trajectory citation is {event,quote}. Use short exact source substrings; quote the actual
action/arguments of an agent tool call. For traced all four citations are required. For open use
only those genuinely identified. Nested scorer archive paths use container!member. Do not invent
paths, refs, quotations or observations. Do not infer sole responsibility or a corrected score.
When the packet omits a relevant field, raw row, executed script body, or comparison, request
read-only excerpts BEFORE deciding: {"requests":[{"role":"submission","path":"artifacts/...",
"line":2},{"role":"trajectory","event":"trajectory:step:...:tool:0","needle":"..."}]}.
Use role public/submission/scorer with a catalogued path, or trajectory with an event.
For a minified JSON file use a literal needle. Use up to six lookups per round. If the relevant
source is not in the catalog, name the precise missing link. Do not label an available source
missing solely because it was absent from the initial excerpts. No tools, commands, code execution
or browsing. Return only JSON."""


def fingerprint(evidence):
    from causal_assessment import digest

    if not evidence.get("trial_dir"):
        return digest(evidence)
    # Harbor can update aggregate job statistics while an archived trial stays immutable.
    return digest(
        {
            **evidence,
            "artifacts": [a for a in evidence.get("artifacts", []) if a.get("scope") != "job"],
            "metadata": {
                k: v
                for k, v in (evidence.get("metadata") or {}).items()
                if k not in {"job_stats", "job_result_summary"}
            },
            "runtime": {
                k: v for k, v in (evidence.get("runtime") or {}).items() if k != "job_stats"
            },
        }
    )


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _strings(child)


def _sha(raw):
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _safe_read(root, rel):
    path = root / rel
    if Path(rel).is_absolute() or ".." in Path(rel).parts:
        raise ValueError(f"invalid relative source path {rel}")
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError(f"source unavailable: {rel}")
    with path.open("rb") as stream:
        raw = stream.read(MAX_SOURCE_BYTES + 1)
    if len(raw) > MAX_SOURCE_BYTES:
        raise ValueError(f"source exceeds inspection budget: {rel}")
    return raw


def _file_citation(evidence, role, ref, sources):
    rel, quote = ref.get("path"), ref["quote"]
    if not isinstance(rel, str) or not rel or len(rel) > 500:
        raise ValueError(f"missing {role} source")
    scope = "trial" if role == "submission" else "task"
    physical = rel.split("!", 1)[0] if role == "scorer" else rel
    if role == "submission" and not rel.startswith("artifacts/"):
        raise ValueError("submission citation must identify a captured artifact")
    if role == "public" and not _public_path(rel):
        raise ValueError(f"source outside the public task: {rel}")
    if role == "scorer" and not (
        rel.startswith("tests/") or rel in {"verify.py", "test.sh"} or rel in sources
    ):
        raise ValueError(f"source outside the scorer: {rel}")
    root = evidence.get(scope + "_dir")
    if not root:
        raise ValueError(f"missing {scope} root")
    raw = _safe_read(Path(root), physical)
    actual_hash = _sha(raw)
    artifact = next(
        (
            a
            for a in evidence.get("artifacts", [])
            if a.get("scope") == scope and a.get("rel_path") == physical
        ),
        {},
    )
    saved_hash = artifact.get("sha256")
    if saved_hash and actual_hash.removeprefix("sha256:") != saved_hash.removeprefix("sha256:"):
        raise ValueError(f"source changed since collection: {physical}")
    if role == "scorer" and "!" in rel:
        if rel not in sources:
            raise ValueError(f"scorer member unavailable: {rel}")
        content = sources[rel].text
    else:
        content = raw.decode("utf-8")
    content = content.replace("\r\n", "\n")
    pos = content.find(quote)
    citation = {"path": rel, "quote": quote, "sha256": actual_hash}
    if ref.get("sha256") not in (None, actual_hash):
        raise ValueError(f"source changed since review: {physical}")
    if pos >= 0:
        citation["line"] = 1 + content.count("\n", 0, pos)
    elif physical.endswith(".json") and "!" not in rel:
        data = json.loads(content)
        if not any(quote in value for value in _strings(data)):
            raise ValueError(f"quote absent from {rel}: {quote[:75]}")
        citation["locator"] = "decoded JSON string"
    else:
        raise ValueError(f"quote absent from {rel}: {quote[:75]}")
    return citation


def verify_finding(row, evidence, sources):
    if not isinstance(row, dict) or row.get("trial_name") != evidence["trial_name"]:
        raise ValueError("trial identity mismatch")
    status = row.get("status")
    refs = row.get("evidence")
    if status not in {"traced", "open"} or not isinstance(refs, dict) or set(refs) - set(ROLES):
        raise ValueError("invalid deep review status or evidence roles")
    if status == "traced" and set(refs) != set(ROLES):
        raise ValueError("a traced step needs all four evidence roles")
    if not all(
        isinstance(row.get(k), str) and 0 < len(row[k].strip()) <= 4000
        for k in ("mechanism", "gap")
    ):
        raise ValueError("deep review needs a concrete mechanism and remaining uncertainty")
    citations = {}
    for role, ref in refs.items():
        if (
            not isinstance(ref, dict)
            or not isinstance(ref.get("quote"), str)
            or not 2 <= len(ref["quote"].strip()) <= 1500
        ):
            raise ValueError(f"invalid {role} quote")
        if role != "trajectory":
            citations[role] = _file_citation(evidence, role, ref, sources)
            continue
        event = next(
            (e for e in evidence.get("timeline", []) if e.get("event_id") == ref.get("event")), None
        )
        if not event or not event.get("tool_name") or event.get("actor") != "agent":
            raise ValueError("trajectory citation does not identify an agent tool action")
        if status == "traced" and (
            event.get("tool_succeeded") is not True
            or event.get("tool_name", "").lower()
            in {"read", "grep", "glob", "webfetch", "websearch"}
        ):
            raise ValueError("traced decision needs a successful producing or checking action")
        args = event.get("arguments") or {}
        action = [event.get("command"), args]
        if status == "traced":
            tool = event["tool_name"].lower().split(".")[-1]
            if tool == "write":
                action = args.get("content")
            elif tool in {"edit", "replace", "multiedit"}:
                edits = args.get("edits") or [args]
                action = [
                    edit.get("new_string", edit.get("newString"))
                    for edit in edits
                    if isinstance(edit, dict)
                ]
            elif tool in {"bash", "shell", "exec_command"}:
                action = args.get("command") or args.get("cmd") or event.get("command")
                if isinstance(action, str) and re.fullmatch(
                    r"\s*(?:cat|ls|head|tail|pwd|grep|rg|wc)\b[^;&|<>\n]*", action
                ):
                    raise ValueError(
                        "an inspection command does not establish a producing decision"
                    )
        if not any(ref["quote"] in s for s in _strings(action)):
            raise ValueError(f"quote absent from action {ref.get('event')}: {ref['quote'][:75]}")
        citations[role] = {
            "event": event["event_id"],
            "tool": event["tool_name"],
            "pointer": event.get("source_pointer"),
            "quote": ref["quote"],
        }
    return {
        "status": status,
        "mechanism": row["mechanism"].strip(),
        "gap": row["gap"].strip(),
        "evidence": citations,
    }


def _source_packet(evidence, analysis, sources):
    pack = build_dossier(evidence, analysis)
    failures = pack["failure_locations"]
    _, terms, _ = _terms(failures, evidence.get("causal_anchors") or {}, pack["failed_checks"])
    if not pack["verifier_functions"]:
        options = []
        for name, source in sources.items():
            excerpts = _matched_sections(source.text, terms, max_windows=1)
            if excerpts:
                options.append(
                    (
                        len(
                            terms.intersection(re.findall(r"[\w.-]+", excerpts[0]["text"].lower()))
                        ),
                        name,
                        excerpts,
                    )
                )
        pack["scorer_search"] = [
            {"source": n, "excerpts": x} for _, n, x in sorted(options, reverse=True)[:3]
        ]
    # Include the actual successful writes, not just a tool name or a filename.
    actions = []
    for event in evidence.get("timeline") or []:
        if not event.get("tool_succeeded"):
            continue
        args = event.get("arguments") or {}
        code = (
            args.get("new_string")
            or args.get("content")
            or args.get("command")
            or event.get("command")
        )
        if not isinstance(code, str) or len(code) < 15:
            continue
        excerpts = _matched_sections(code, terms, max_windows=2)
        if excerpts:
            hits = sum(
                len(terms.intersection(re.findall(r"[\w.-]+", x["text"].lower()))) for x in excerpts
            )
            actions.append((hits, event["event_id"], event.get("tool_name"), excerpts))
    pack["action_excerpts"] = [
        {"event": ref, "tool": tool, "excerpts": x}
        for _, ref, tool, x in sorted(actions, key=lambda row: (-row[0], row[1]))[:6]
    ]
    return pack


def _gap(pack):
    check = (pack.get("failure") or {}).get("scoring_event") or next(
        iter(pack["failed_checks"]), {}
    )
    target = str(check.get("detail") or check.get("name") or pack["trial_name"])[:240]
    rule = check.get("matching_rule") or {}
    field = check.get("submitted_field") or {}
    missing = []
    if not pack["public_requirements"]:
        missing.append("the public requirement applicable to this rejection")
    if field:
        target_field = f"{field['source']} {field['pointer']}"
    else:
        target_field = target
        if not pack["submission_slices"]:
            missing.append("the captured field or row actually read")
    if not pack["verifier_functions"]:
        missing.append("the executed scoring condition")
    if not pack["decision_context"] and not pack["action_excerpts"]:
        missing.append("an observed tool action producing the rejected value")
    if not missing:
        missing.append("the link from the effective writer to the value consumed by the check")
    at = f" at {rule['source']}:L{rule['line']}" if rule.get("line") else ""
    return target, f"{target_field}{at} — still needed: " + ", ".join(missing)


def _catalog(evidence, sources):
    result = {"public": [], "submission": [], "scorer": list(sources)[:150], "trajectory": []}
    for a in evidence.get("artifacts", []):
        rel = a.get("rel_path") or ""
        if a.get("exists") and a.get("scope") == "trial" and rel.startswith("artifacts/"):
            result["submission"].append(rel)
        elif a.get("exists") and a.get("scope") == "task" and _public_path(rel):
            result["public"].append(rel)
    for role in ("public", "submission"):
        result[role] = result[role][:350]
    for ev in evidence.get("timeline", []):
        if ev.get("actor") == "agent" and ev.get("tool_name"):
            result["trajectory"].append(
                [ev["event_id"], ev["tool_name"], str(ev.get("command") or "")[:100]]
            )
    result["trajectory"] = result["trajectory"][:500]
    return result


def _public_path(rel):
    parts = Path(rel).parts
    return (
        rel in {"instruction.md", "task.toml", "environment/Dockerfile"}
        or rel.startswith("environment/")
    ) and not set(parts).intersection({"tests", "protected", "solution", "trusted_reviews"})


def _lookup(request, evidence, catalog, sources):
    if not isinstance(request, dict):
        raise ValueError("invalid lookup object")
    role = request.get("role")
    if role == "trajectory":
        event = next(
            (
                e
                for e in evidence.get("timeline", [])
                if e.get("event_id") == request.get("event") and e.get("tool_name")
            ),
            None,
        )
        if not event:
            raise ValueError("unknown tool event")
        content = "\n".join(
            _strings([event.get("arguments"), event.get("command"), event.get("observation")])
        )
    else:
        rel = request.get("path")
        if role not in {"scorer", "submission", "public"} or rel not in catalog[role]:
            raise ValueError("lookup source outside this trial catalog")
        if role == "scorer":
            content = sources[rel].text
        else:
            root = evidence["task_dir" if role == "public" else "trial_dir"]
            content = _safe_read(Path(root), rel).decode("utf-8")
    needle = request.get("needle")
    if needle:
        if not isinstance(needle, str) or len(needle) > 400:
            raise ValueError("invalid search literal")
        pos = content.casefold().find(needle.casefold())
        if pos < 0:
            raise ValueError("literal not found in requested source")
    else:
        number = request.get("line", 1)
        if type(number) is not int or not 1 <= number <= 1000000:
            raise ValueError("invalid line number")
        pos = 0
        for _ in range(number - 1):
            pos = content.find("\n", pos)
            if pos == -1:
                raise ValueError("line outside requested source")
            pos += 1
    return content[max(0, pos - 350) : pos + 2200]


def _codex_answer(prompt):
    exe = shutil.which("codex")
    if not exe:
        raise ValueError("Codex CLI is unavailable for standalone semantic review")
    with tempfile.TemporaryDirectory(prefix="cfa-reviewer-") as tmp:
        answer = Path(tmp) / "answer.json"
        cmd = [
            exe,
            "exec",
            "--ephemeral",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "-c",
            'approval_policy="never"',
            "-c",
            'web_search="disabled"',
            "-c",
            "mcp_servers={}",
            "-C",
            tmp,
        ]
        for feature in ("shell_tool", "unified_exec", "apps", "plugins", "hooks", "multi_agent"):
            cmd.extend(["--disable", feature])
        cmd.extend(["-o", str(answer), "-"])
        with tempfile.TemporaryFile(mode="w+b") as diagnostics:
            run = subprocess.run(
                cmd,
                input=prompt,
                text=True,
                cwd=tmp,
                stdout=subprocess.DEVNULL,
                stderr=diagnostics,
                timeout=180,
                check=False,
            )
            if run.returncode:
                from cfa import redact_sensitive

                diagnostics.seek(max(0, diagnostics.tell() - 1800))
                tail = diagnostics.read().decode("utf-8", errors="replace").splitlines()
                note = next((line for line in reversed(tail) if "error" in line.lower()), "")
                raise ValueError(
                    f"standalone reviewer exit {run.returncode}: {redact_sensitive(note)[:220]}"
                )
        if run.returncode or not answer.is_file() or answer.stat().st_size > 64000:
            raise ValueError(
                f"standalone reviewer returned no usable decision (exit {run.returncode})"
            )
        text = answer.read_text().strip()
        if text.startswith("```json\n") and text.endswith("\n```"):
            text = text[8:-4]
        return json.loads(text)


def _codex_review(packet, evidence, sources, lang):
    from cfa import redact_sensitive

    catalog = _catalog(evidence, sources)
    history = []
    for attempt in range(4):
        prompt = (
            PROMPT
            + "\nReport language: "
            + lang
            + "\n"
            + redact_sensitive(
                json.dumps(
                    {"packet": packet, "catalog": catalog, "lookups": history}, ensure_ascii=False
                )
            )
        )
        if attempt == 3:
            prompt += (
                "\nFinal response required. Return a traced or open decision with exact citations."
            )
        answer = _codex_answer(prompt)
        if not isinstance(answer, dict):
            raise ValueError("reviewer returned invalid JSON object")
        if "requests" not in answer:
            try:
                verified = verify_finding(answer, evidence, sources)
                if attempt == 0 and verified["status"] == "open":
                    history.append(
                        {
                            "provisional_gap": verified["gap"],
                            "instruction": "Check whether this gap can be resolved from the catalog. Request specific source excerpts or explain why the archived record cannot supply them.",
                        }
                    )
                    continue
                return verified
            except ValueError as exc:
                if attempt == 3:
                    raise
                history.append({"invalid_citation": str(exc), "previous_decision": answer})
                continue
        queries = answer["requests"]
        if not isinstance(queries, list) or not 1 <= len(queries) <= 6:
            raise ValueError("invalid number of excerpt requests")
        for request in queries:
            try:
                excerpt = _lookup(request, evidence, catalog, sources)
            except (OSError, ValueError) as exc:
                excerpt = "Lookup unavailable: " + str(exc)
            history.append({"request": request, "excerpt": excerpt})
    raise ValueError("reviewer exhausted evidence lookups without a final decision")


def load_reviews(path):
    if not path:
        return {}
    rows = json.loads(Path(path).read_text())
    if not isinstance(rows, list):
        raise ValueError("deep review file must contain a list of decisions")
    decisions = {}
    for row in rows:
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("trial_name"), str)
            or not isinstance(row.get("audit_binding"), str)
        ):
            raise ValueError("each deep review needs its trial name and audit_binding")
        key = row["trial_name"], row["audit_binding"]
        if key in decisions:
            raise ValueError(f"duplicate deep review: {key[0]}")
        decisions[key] = row
    return decisions


def investigate(evidence, analysis, decisions, *, mode, lang, cached=None):
    if (analysis.get("primary_root_cause") or {}).get("category") != "unknown":
        return None
    binding = fingerprint(evidence)
    if not evidence.get("trial_dir"):
        return {
            "schema_version": "cfa-deep-review-v1",
            "trial_name": evidence.get("trial_name"),
            "audit_binding": binding,
            "status": "open",
            "method": "missing_trial",
            "mechanism": "No trial directory was retained.",
            "gap": "Missing the trial-specific submission, tool trajectory and verifier receipt.",
            "evidence": {},
        }
    sources = {
        s.path: s
        for s in collect_verifier_sources(
            Path(evidence["task_dir"]) if evidence.get("task_dir") else None
        ).sources
    }
    packet = _source_packet(evidence, analysis, sources)
    target, gap = _gap(packet)
    state = {
        "schema_version": "cfa-deep-review-v1",
        "trial_name": evidence["trial_name"],
        "audit_binding": binding,
        "recorded_rejection": target,
        "comparison": packet,
    }
    row = decisions.get((evidence["trial_name"], binding))
    if row is None and any(name == evidence["trial_name"] for name, _ in decisions):
        raise ValueError(f"stale deep review for {evidence['trial_name']}")
    if row is not None:
        # Explicit inputs fail closed so a stale or fabricated quote cannot enter a batch.
        return {**state, **verify_finding(row, evidence, sources), "method": "host_review"}
    if (
        cached
        and cached.get("audit_binding") == binding
        and cached.get("status") in {"open", "traced"}
    ):
        try:
            verified = verify_finding(cached, evidence, sources)
        except (OSError, ValueError):
            pass
        else:
            return {**state, **verified, "method": cached["method"]}
    if mode == "auto":
        try:
            verified = _codex_review(packet, evidence, sources, lang)
            return {**state, **verified, "method": "standalone_reviewer"}
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            return {
                **state,
                "status": "review_incomplete",
                "method": "standalone_reviewer",
                "mechanism": target,
                "gap": gap,
                "review_error": str(exc),
            }
    return {
        **state,
        "status": "review_pending",
        "method": "host_requested",
        "mechanism": target,
        "gap": gap,
    }


def compact_review(result):
    return {k: v for k, v in result.items() if k != "comparison"}


def validation_error(deep, evidence, reread):
    if not isinstance(deep, dict) or deep.get("schema_version") != "cfa-deep-review-v1":
        return "invalid deep review structure"
    if deep.get("trial_name") != evidence.get("trial_name") or deep.get(
        "audit_binding"
    ) != fingerprint(evidence):
        return "deep review belongs to a different evidence snapshot"
    if deep.get("status") in ("review_incomplete", "review_pending"):
        return None
    if deep.get("status") not in ("open", "traced"):
        return "invalid deep review status"
    refs = deep.get("evidence")
    if not isinstance(refs, dict) or (deep["status"] == "traced" and set(refs) != set(ROLES)):
        return "deep decision lacks four evidence roles"
    if reread:
        try:
            sources = collect_verifier_sources(
                Path(evidence["task_dir"]) if evidence.get("task_dir") else None
            )
            checked = verify_finding(deep, evidence, {s.path: s for s in sources.sources})
            if checked["evidence"] != refs:
                return "deep review citations differ from source"
        except (ValueError, OSError) as exc:
            return str(exc)
    return None


def render_review(result, lang):
    if not result:
        return ""
    en = lang == "en"
    labels = {
        "traced": ("已追到具体步骤", "Decision traced"),
        "open": ("已深查，因果链仍有缺口", "Reviewed, causal link open"),
        "review_pending": ("待本轮宿主完成深查", "Host review pending"),
        "review_incomplete": ("深查未完成", "Review incomplete"),
    }
    status = labels[result["status"]][int(en)]
    lines = [
        f"> **{status}** · {result['mechanism']}",
        f"> {'Scope / missing evidence' if en else '结论边界或证据缺口'} · {result['gap']}",
    ]
    if result.get("review_error"):
        lines.append("> " + result["review_error"])
    for role, ref in result.get("evidence", {}).items():
        source = ref.get("event") or ref["path"]
        line = f":L{ref['line']}" if ref.get("line") else ""
        lines.append(f"> {role} · \x60{source}{line}\x60 · " + " ".join(ref["quote"].split())[:200])
    return "\n".join(lines) + "\n"
