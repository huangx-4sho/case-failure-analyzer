"""Reconstruct literal source writes in a saved normalized trajectory."""

from __future__ import annotations

import posixpath
import re
from pathlib import PurePosixPath

from normalize_trajectory import _extract_script_write_targets, sha256_text
from self_check_execution import _RESET_CWD, _shell_commands

SOURCE_SUFFIXES = {".py", ".sh", ".bash", ".jl", ".r", ".cpp", ".c", ".f90"}
_MUTATING_SHELL = re.compile(
    r"(?m)(?:^|[;&|])\s*(?:mv|cp|rm|tee|sed\s+-i|install)\s|"
    r"(?<![<>&])>>?(?![>&])|\.(?:write_text|write_bytes)\s*\(|\bopen\([^\n]+[\x27\x22][wax]"
)
_MAX_SOURCE_CHARS = 96_000
_SHELL_TOOLS = {"bash", "shell", "exec", "exec_command"}
_FLOW_KEYWORDS = {
    "if",
    "then",
    "else",
    "elif",
    "fi",
    "for",
    "while",
    "until",
    "do",
    "done",
    "case",
    "esac",
    "function",
    "!",
    "{",
    "}",
}


def _literal_path(raw):
    return (
        isinstance(raw, str)
        and bool(raw)
        and not any(char in raw for char in "\n\r$`~*?[]{}()<>|;&")
    )


def _source_path(raw, cwd=None):
    if not _literal_path(raw):
        return None
    if not raw.startswith("/"):
        if not isinstance(cwd, str) or not cwd.startswith("/"):
            return None
        raw = posixpath.join(cwd, raw)
    path = posixpath.normpath(raw)
    return path if PurePosixPath(path).suffix.lower() in SOURCE_SUFFIXES else None


def _apply_edits(source, edits):
    if source is None or not edits:
        return None
    for edit in edits:
        if not isinstance(edit, dict):
            return None
        old = edit.get("old_string", edit.get("oldString"))
        new = edit.get("new_string", edit.get("newString"))
        if not isinstance(old, str) or not old or not isinstance(new, str) or old not in source:
            return None
        all_matches = edit.get("replace_all") is True
        if not all_matches and source.count(old) != 1:
            return None
        source = source.replace(old, new, -1 if all_matches else 1)
        if len(source) > _MAX_SOURCE_CHARS:
            return None
    return source


def _invoked_path(words, cwd):
    """Recognize literal script arguments after explicit cwd and interpreter options."""
    executable = posixpath.basename(words[0])
    if re.fullmatch(r"python(?:\d+(?:\.\d+)?)?|bash|sh|zsh", executable):
        arguments = iter(words[1:])
        for arg in arguments:
            if arg in {"-m", "-c", "-s"}:
                break
            if arg in {"-W", "-X"}:
                next(arguments, None)
            elif not arg.startswith("-"):
                return _source_path(arg, cwd)
    elif words[0].startswith(("./", "/")):
        return _source_path(words[0], cwd)
    return None


def _shell_trace(event, cwd, sources, executions):
    """Follow only static sequential shell segments. This never runs a command."""
    command = str(event.get("command") or "")
    ref = event["event_id"]
    succeeded = event.get("tool_succeeded") is True
    if event.get("result_recorded") is not True:
        if _MUTATING_SHELL.search(command):
            for path, record in sources.items():
                if path in command or (cwd and posixpath.relpath(path, cwd) in command):
                    record["text"] = None
        return None

    segments = list(_shell_commands(command, literal_heredocs_only=True))
    previous_separator = ""
    last_literal_status = None
    for index, (words, body, separator) in enumerate(segments):
        raw_verb = words[0]
        # Multi-branch control flow can leave cwd or a redirected script
        # ambiguous, so only the recognized leading sequence is attested.
        if (
            raw_verb in _FLOW_KEYWORDS
            or raw_verb.startswith(("(", ")"))
            or "|" in separator
            or separator == "&"
            or (previous_separator == "||" and last_literal_status is None)
        ):
            if any("cd " in " ".join(part) for part, _, _ in segments[index:]):
                cwd = None
            break
        if (previous_separator, last_literal_status) in {("&&", False), ("||", True)}:
            previous_separator = separator
            continue

        previous_separator = separator
        verb = posixpath.basename(raw_verb)
        last_literal_status = {"false": False, "true": True, ":": True}.get(verb)
        if verb == "exit":
            break
        if verb == "cd":
            destination = words[1] if len(words) == 2 else None
            cd_succeeded = succeeded and (separator == "&&" or index == len(segments) - 1)
            cwd = (
                posixpath.normpath(posixpath.join(cwd or "", destination))
                if cd_succeeded
                and _literal_path(destination)
                and (cwd or destination.startswith("/"))
                else None
            )
            continue

        redirects = [
            _source_path(words[i + 1], cwd)
            for i, word in enumerate(words[:-1])
            if word in {">", ">>"}
        ]
        cat_literal = (
            verb == "cat"
            and body is not None
            and len(words) == 5
            and (words[1] == ">" and words[3] == "<<" or words[1] == "<<" and words[3] == ">")
            and len(redirects) == 1
        )
        for changed in redirects:
            if cat_literal and changed and succeeded:
                sources[changed] = {
                    "text": body if len(body) <= _MAX_SOURCE_CHARS else None,
                    "write_event_refs": [ref],
                }
            elif changed in sources:
                sources[changed]["text"] = None
        if body and not cat_literal and re.fullmatch(r"python(?:\d+(?:\.\d+)?)?", verb):
            # A retained Python heredoc can overwrite a previously reconstructed
            # file. Static detection invalidates it without simulating the write.
            for target in _extract_script_write_targets(body):
                if (changed := _source_path(target, cwd)) in sources:
                    sources[changed]["text"] = None
        if verb in {"mv", "cp", "rm", "tee", "install"} or (
            verb == "sed" and any(word.startswith("-i") for word in words[1:])
        ):
            targets = words[1:] if verb in {"mv", "rm"} else words[-1:]
            for raw_path in targets:
                if (changed := _source_path(raw_path, cwd)) in sources:
                    sources[changed]["text"] = None

        if (path := _invoked_path(words, cwd)) in sources:
            record = sources[path]
            executions.setdefault(path, []).append(
                {
                    "event_ref": ref,
                    "result_recorded": True,
                    "sha256": sha256_text(record["text"]) if record.get("text") else None,
                }
            )
    reset = _RESET_CWD.search(str(event.get("observation") or ""))
    return reset.group(1) if reset else cwd if succeeded else None


def trace_sources(timeline):
    """Return per-path literal versions and observed invocation leads.

    Unknown commands, indirect imports and paths without known cwd stay
    unbound. A text reconstruction is limited to recognized operations.
    """
    sources = {}
    executions = {}
    bash_workdir = None
    for event in timeline:
        args = event.get("arguments") or {}
        tool = str(event.get("tool_name") or "").lower().rsplit(".", 1)[-1]
        ref = event.get("event_id")
        if not ref or not isinstance(args, dict):
            continue
        succeeded = event.get("tool_succeeded") is True
        path = _source_path(
            args.get("file_path") or args.get("path"), args.get("workdir") or args.get("cwd")
        )
        content = None
        reset = False
        if path and tool in {"write", "edit", "multiedit", "replace"}:
            previous = sources.get(path, {})
            if succeeded and tool == "write":
                content, reset = args.get("content"), True
            elif succeeded:
                edits = args.get("edits") if tool == "multiedit" else [args]
                if isinstance(edits, list):
                    content = _apply_edits(previous.get("text"), edits)
            elif event.get("result_recorded"):
                continue
        elif tool in _SHELL_TOOLS:
            cwd = args.get("workdir") or args.get("cwd")
            if not (isinstance(cwd, str) and cwd.startswith("/")):
                cwd = bash_workdir if tool == "bash" else None
            end = _shell_trace(event, cwd, sources, executions)
            if tool == "bash":
                bash_workdir = end
            continue
        else:
            continue
        if path:
            previous = sources.get(path, {})
            sources[path] = {
                "text": content
                if isinstance(content, str) and len(content) <= _MAX_SOURCE_CHARS
                else None,
                "write_event_refs": [ref]
                if reset
                else [*previous.get("write_event_refs", []), ref],
            }
    return sources, executions
