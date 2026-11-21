"""Observe agent-authored checks in saved tool calls, without assessing coverage."""

from __future__ import annotations

import ast
import posixpath
import re
import shlex
from dataclasses import dataclass
from typing import Any, Callable, Iterator, Optional


_PYTHON = re.compile(r"python(?:[23](?:\.\d+)?)?$")
_SHELL_TOOLS = {"bash", "exec", "exec_command", "shell"}
_SHELL_SCRIPTS = {"bash", "sh"}
_CHECK_CALL = re.compile(r"(?:chk|check|require|assert|verify)(?:_\w+)?$")
_CHECK_LABEL = re.compile(r"(?:^|[_-])(?:audit|accept|check|validat\w*|verif\w*)(?:[_-]|\.|$)")
_RESET_CWD = re.compile(r"(?m)^Shell cwd was reset to (/[^\n\r]+)$")
_SHELL_SEPARATOR = re.compile(r"[;&|\n]+$")
_QUOTED_OPERATOR = re.compile(r"""(?<!\\)(['"])([;&|<>\n]+)\1""")
_VAR_PATH = re.compile(r"(?m)^\s*(\w+)\s*=\s*(?:pathlib\.)?Path\(['\"]([^'\"\n]+)['\"]\)")
_FULL_PASS = re.compile(
    r"^(?:RESULT:\s*)?(?:ALL CHECKS PASSED|ALL GOOD|VERIFY OK|SELF[-_ ]?CHECK (?:OK|PASSED))$"
    r"|^ERRORS:\s*0$|^=*\s*\d+ checks? passed, 0 failed\s*=*$"
    r"|^0 CHECK\(S\) FAILED$",
    re.IGNORECASE,
)
_FULL_FAILURE = re.compile(
    r"^(?:RESULT:\s*)?FAILURES PRESENT$|^ERRORS:\s*[1-9]\d*$"
    r"|^=*\s*\d+ checks? passed, [1-9]\d* failed\s*=*$",
    re.IGNORECASE,
)
_PASS_LINE = re.compile(
    r"^(?:PASS\b|ok\s*:)|\b(?:passes|match)\s*[=:]\s*True\b|\brun ok\b",
    re.IGNORECASE,
)
_FAIL_LINE = re.compile(
    r"^(?:FAIL(?:ED)?\b|Traceback \()|\b(?:passes|match)\s*[=:]\s*False\b",
    re.IGNORECASE,
)


def _tokens(command: str) -> list[str]:
    # shlex removes quotes. Keep a quoted operator distinct from a separator
    # so arguments to echo/grep cannot be mistaken for a following invocation.
    command = _QUOTED_OPERATOR.sub(r"\1CFA_QUOTED_OPERATOR\2\1", command)
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>\n")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    return list(lexer)


def _shell_commands(
    command: str, *, literal_heredocs_only: bool = False
) -> Iterator[tuple[list[str], Optional[str], str]]:
    """Yield static shell segments, keeping literal heredoc bodies out of shell tokens."""
    lines = iter(command.splitlines(keepends=True))
    shell: list[str] = []
    bodies: dict[str, Optional[str]] = {}
    for line in lines:
        if "<<" in line:
            try:
                tokens = _tokens(line)
            except ValueError:
                tokens = []
            for i, token in enumerate(tokens[:-1]):
                if token != "<<":
                    continue
                next_token = (
                    tokens[i + 2] if tokens[i + 1] == "-" and i + 2 < len(tokens) else tokens[i + 1]
                )
                if not re.fullmatch(r"-?\w+", next_token):
                    continue
                delimiter = next_token.lstrip("-")
                marker = re.search(
                    rf"(<<(?P<strip>-)?[ \t]*)(?P<quote>['\"]?){re.escape(delimiter)}"
                    rf"(?P=quote)(?=[\s;&|<>]|$)",
                    line,
                )
                if not marker:
                    return
                body = []
                for text in lines:
                    content = text.lstrip("\t") if marker["strip"] else text
                    if content.rstrip("\r\n") == delimiter:
                        break
                    body.append(content)
                else:
                    return  # No closing delimiter in the retained command.
                placeholder = f"CFA_HEREDOC_{len(bodies)}"
                line = line[: marker.start()] + "<< " + placeholder + line[marker.end() :]
                bodies[placeholder] = (
                    None if literal_heredocs_only and not marker["quote"] else "".join(body)
                )
        shell.append(line)
    try:
        tokens = _tokens("".join(shell))
    except ValueError:
        return
    part: list[str] = []
    for token in [*tokens, "\n"]:
        if not _SHELL_SEPARATOR.fullmatch(token):
            part.append(token)
            continue
        if part:
            body = next((bodies[item] for item in part if item in bodies), None)
            yield part, body, token.strip()
            part = []


def _script_path(path: str, cwd: Optional[str]) -> Optional[str]:
    if not path.endswith((".py", ".sh")) or any(c in path for c in "$`*?{}()"):
        return None
    if cwd is None and not posixpath.isabs(path):
        return None
    path = posixpath.normpath(posixpath.join(cwd or "", path))
    if any(part in ("tests", "verifier", "grader") for part in path.split("/")):
        return None
    return path


def _checks_in_source(source: str, *, shell: bool = False) -> bool:
    if shell:
        return bool(re.search(r"(?:\btest\s|\[\[?\s).*(?:&&|\|\||then)", source))
    try:
        nodes = ast.walk(ast.parse(source))
    except (SyntaxError, ValueError, RecursionError):
        return False
    return any(
        isinstance(node, ast.Assert)
        or (
            isinstance(node, ast.Call)
            and isinstance(node.func, (ast.Name, ast.Attribute))
            and _CHECK_CALL.fullmatch(
                node.func.id if isinstance(node.func, ast.Name) else node.func.attr
            )
        )
        or (
            isinstance(node, ast.If)
            and any(
                isinstance(stmt, ast.Expr)
                and isinstance(stmt.value, ast.Call)
                and isinstance(stmt.value.func, ast.Name)
                and stmt.value.func.id in {"err", "error", "fail"}
                for stmt in node.body
            )
        )
        for node in nodes
    )


def _observed_result(observation: str) -> tuple[str, list[str]]:
    lines = list(dict.fromkeys(line.strip() for line in observation.splitlines() if line.strip()))
    complete = [line for line in lines if _FULL_PASS.search(line)]
    failed = [line for line in lines if _FULL_FAILURE.search(line) or _FAIL_LINE.search(line)]
    passed = [line for line in lines if _PASS_LINE.search(line)]
    if failed:
        status = "partial" if complete or passed else "failed"
    elif complete:
        status = "passed"
    elif passed:
        status = "partial"
    else:
        status = "unknown"
    return status, [line[:240] for line in (complete[:2] + failed[:3] + passed[:3])]


@dataclass
class _Script:
    source: str
    event_refs: list[str]
    checks: bool
    complete: bool = True


class SelfCheckTracker:
    """Follow saved local script writes and actual checking output in trajectory order."""

    def __init__(self) -> None:
        self.scripts: dict[str, _Script] = {}
        self.bash_workdir: Optional[str] = None
        self.bash_workdir_event: Optional[str] = None

    def _write(self, path: str, source: str, event_id: str) -> None:
        self.scripts[path] = _Script(
            source, [event_id], _checks_in_source(source, shell=path.endswith(".sh"))
        )

    def _edit(self, path: str, event_id: str, edits: list[dict[str, Any]]) -> None:
        previous = self.scripts.get(path)
        if not previous:
            return
        source = previous.source
        complete = previous.complete and bool(edits)
        for edit in edits:
            if not isinstance(edit, dict):
                complete = False
                break
            old = edit.get("old_string") or edit.get("oldString")
            new = edit.get("new_string", edit.get("newString"))
            count = source.count(old) if isinstance(old, str) and old else 0
            replace_all = edit.get("replace_all") or edit.get("replaceAll")
            if not count or not isinstance(new, str) or (count != 1 and not replace_all):
                complete = False
                break
            source = source.replace(old, new, -1 if replace_all else 1)
        self.scripts[path] = _Script(
            source,
            list(dict.fromkeys([*previous.event_refs, event_id])),
            _checks_in_source(source, shell=path.endswith(".sh")) if complete else previous.checks,
            complete,
        )

    def observe(
        self,
        event: dict[str, Any],
        observation: str,
        observation_truncated: bool,
        script_write_targets: Callable[[str], list[str]],
    ) -> list[dict[str, Any]]:
        tool = str(event.get("tool_name") or "").lower().rsplit(".", 1)[-1]
        event_id = event["event_id"]
        args = event.get("arguments") or {}
        command = str(event.get("command") or "")
        if event.get("event_type") == "file_write":
            path = _script_path(command, None)
            if not path:
                return []
            if not event.get("result_recorded"):
                self.scripts.pop(path, None)
                return []
            if not event.get("tool_succeeded"):
                return []
            if tool == "write" and isinstance(args.get("content"), str):
                self._write(path, args["content"], event_id)
            else:
                edits = args.get("edits")
                self._edit(path, event_id, edits if isinstance(edits, list) else [args])
            return []
        if tool not in _SHELL_TOOLS:
            return []
        if not event.get("result_recorded"):
            # A missing shell result can hide writes or a working-directory change.
            self.bash_workdir = self.bash_workdir_event = None
            return []

        workdir = args.get("workdir") or args.get("cwd")
        cwd = workdir if isinstance(workdir, str) and workdir.startswith("/") else None
        cwd_ref = event_id if cwd else None
        if cwd is None and tool == "bash":
            cwd, cwd_ref = self.bash_workdir, self.bash_workdir_event
        candidates: list[dict[str, Any]] = []
        command_count = 0
        status, markers = _observed_result(observation)
        last_separator = ""
        literal_success = None
        for tokens, body, separator in _shell_commands(command):
            if tokens[0] == "env" or tokens[0] == "time":
                tokens = tokens[1:]
            elif tokens[:2] == ["uv", "run"]:
                tokens = tokens[2:]
            while tokens and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[0]):
                tokens = tokens[1:]
            if (
                not tokens
                or (literal_success is False and last_separator == "&&")
                or (literal_success is True and last_separator == "||")
            ):
                last_separator = separator
                continue
            verb = posixpath.basename(tokens[0])
            literal_success = {"false": False, "true": True, ":": True}.get(verb)
            last_separator = separator
            if verb == "exit":
                break
            if verb == "cd" and len(tokens) == 2 and not any(c in tokens[1] for c in "$`~"):
                if cwd or tokens[1].startswith("/"):
                    cwd = posixpath.normpath(posixpath.join(cwd or "", tokens[1]))
                    cwd_ref = event_id
                continue
            command_count += 1
            path = None
            script = None
            if verb == "cat" and body is not None and ">" in tokens:
                index = tokens.index(">")
                if index + 1 < len(tokens):
                    path = _script_path(tokens[index + 1], cwd)
                    if path and (event.get("tool_succeeded") or markers):
                        self._write(path, body, event_id)
                continue
            for index, token in enumerate(tokens[:-1]):
                if token in (">", ">>"):
                    changed = _script_path(tokens[index + 1], cwd)
                    if changed:
                        self.scripts.pop(changed, None)
            if verb in {"cp", "mv", "rm"}:
                for raw_path in tokens[1:] if verb in {"mv", "rm"} else tokens[-1:]:
                    changed = _script_path(raw_path, cwd)
                    if changed:
                        self.scripts.pop(changed, None)
            if verb == "sed" and any(token.startswith("-i") for token in tokens[1:]):
                changed = _script_path(tokens[-1], cwd)
                if changed:
                    self._edit(changed, event_id, [])
            if _PYTHON.fullmatch(verb) or verb in _SHELL_SCRIPTS:
                argv = iter(tokens[1:])
                raw = next(
                    (
                        arg
                        for arg in argv
                        if not re.fullmatch(r"-[A-Za-z]+", arg) or arg in ("-c", "-m")
                    ),
                    None,
                )
                inline = (
                    next(argv, None) if raw == "-c" else body if raw in (None, "-", "<<") else None
                )
                if isinstance(inline, str):
                    if _checks_in_source(inline, shell=verb in _SHELL_SCRIPTS):
                        script = _Script(inline, [event_id], True)
                    # Earlier statements can have mutated the file before the
                    # shell reported a later error. The version remains partial.
                    written = script_write_targets(inline)
                    written += [
                        target
                        for var, target in _VAR_PATH.findall(inline)
                        if re.search(rf"\b{re.escape(var)}\.write_(?:text|bytes)\s*\(", inline)
                    ]
                    for target in written:
                        changed = _script_path(target, cwd)
                        if changed:
                            self._edit(changed, event_id, [])
                elif raw and raw not in ("-m", "-c"):
                    path = _script_path(raw, cwd)
            elif tokens[0].startswith(("./", "/")):
                path = _script_path(tokens[0], cwd)
            if path:
                script = self.scripts.get(path)
            if not script or not script.checks or separator == "&":
                continue
            candidates.append(
                {
                    "event_id": event_id,
                    "source_file": event.get("source_file"),
                    "source_pointer": event.get("source_pointer"),
                    "script_path": path,
                    "script_event_refs": script.event_refs,
                    "script_source_status": "complete" if script.complete else "partial",
                    "workdir_event_ref": cwd_ref,
                    "result_status": status,
                    "result_markers": markers,
                    "observation_truncated": observation_truncated,
                    "output_excerpted": separator.startswith("|"),
                    "coverage": "not_established",
                }
            )

        if tool == "bash":
            reset = _RESET_CWD.search(observation)
            if reset:
                self.bash_workdir, self.bash_workdir_event = reset.group(1), event_id
            elif event.get("tool_succeeded"):
                self.bash_workdir, self.bash_workdir_event = cwd, cwd_ref
        if len(candidates) != 1:
            # One shell result can combine multiple checks or branches. Preserve
            # candidate source bindings only when each invocation is observable.
            return []
        if not markers:
            path = candidates[0]["script_path"]
            if (
                not event.get("tool_succeeded")
                or command_count != 1
                or not path
                or not _CHECK_LABEL.search(posixpath.basename(path))
            ):
                return []
        return candidates
