"""Collect bounded verifier Python sources as data, without importing or extracting them."""

from __future__ import annotations

import ast
import base64
import gzip
import hashlib
import io
import json
import re
import tarfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Optional

from runtime_state import resolve_verifier_script_path

MAX_ENCODED_BYTES = 40 * 1024 * 1024
MAX_UNPACKED_BYTES = 128 * 1024 * 1024
MAX_SOURCE_BYTES = 1024 * 1024
MAX_TOTAL_SOURCE_BYTES = 8 * 1024 * 1024
MAX_SOURCE_FILES = 96
MAX_ARCHIVE_MEMBERS = 4096


@dataclass(frozen=True)
class VerifierSource:
    path: str
    text: str
    sha256: str
    container: Optional[str] = None
    member: Optional[str] = None


@dataclass
class VerifierSources:
    sources: list[VerifierSource] = field(default_factory=list)
    diagnostics: list[dict] = field(default_factory=list)
    encoded_source_detected: bool = False
    encoded_source_decoded: bool = False


class _BoundedArchive:
    def __init__(self, stream):
        self.stream = stream
        self.remaining = MAX_UNPACKED_BYTES

    def read(self, size):
        data = self.stream.read(min(size, self.remaining + 1))
        self.remaining -= len(data)
        if self.remaining < 0:
            raise ValueError("uncompressed archive exceeds budget")
        return data


def _source(path, data, *, container=None, member=None):
    return VerifierSource(
        path, data.decode("utf-8"), "sha256:" + hashlib.sha256(data).hexdigest(), container, member
    )


def _read_bounded(task: Path, path: Path, limit: int) -> bytes:
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(task.resolve()):
        raise ValueError("not a contained regular file")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("file exceeds budget")
    return data


def _read_bundle(raw: bytes, path: str) -> list[VerifierSource]:
    metadata = json.loads(raw)
    if not isinstance(metadata, dict) or metadata.get("encoding") != "tar+gzip;base64":
        raise ValueError("unsupported bundle encoding")
    chunks, expected = metadata.get("chunks"), metadata.get("sha256")
    if (
        not isinstance(chunks, list)
        or not isinstance(expected, str)
        or not re.fullmatch(r"[a-fA-F0-9]{64}", expected)
    ):
        raise ValueError("bundle metadata is invalid")
    compressed = io.BytesIO()
    digest = hashlib.sha256()
    for chunk in chunks:
        if not isinstance(chunk, str):
            raise ValueError("bundle contains a non-string block")
        data = base64.b64decode(chunk, validate=True)
        compressed.write(data)
        digest.update(data)
    if digest.hexdigest() != expected.lower():
        raise ValueError("bundle checksum mismatch")
    compressed.seek(0)

    found = []
    seen = set()
    total = 0
    with gzip.GzipFile(fileobj=compressed) as gzip_stream:
        with tarfile.open(fileobj=_BoundedArchive(gzip_stream), mode="r|") as archive:
            for index, member in enumerate(archive):
                if index >= MAX_ARCHIVE_MEMBERS:
                    raise ValueError("archive member limit reached")
                relative = PurePosixPath(member.name)
                if relative.is_absolute() or ".." in relative.parts or "\\" in member.name:
                    raise ValueError("unsafe archive member path")
                if not member.isfile() and not member.isdir():
                    raise ValueError("unsupported archive member type")
                name = relative.as_posix()
                if name in seen and member.isfile():
                    raise ValueError("duplicate archive file")
                seen.add(name)
                if not (
                    member.isfile()
                    and name.startswith("tests/")
                    and not name.startswith("tests/protected/source/")
                    and relative.suffix == ".py"
                ):
                    continue
                total += member.size
                if (
                    member.size > MAX_SOURCE_BYTES
                    or len(found) >= MAX_SOURCE_FILES
                    or total > MAX_TOTAL_SOURCE_BYTES
                ):
                    raise ValueError("archive source budget exceeded")
                stream = archive.extractfile(member)
                if stream is None:
                    raise ValueError("archive source is unavailable")
                with stream:
                    data = stream.read(MAX_SOURCE_BYTES + 1)
                found.append(_source(f"{path}!{name}", data, container=path, member=name))
    return sorted(found, key=lambda source: (source.member != "tests/verify.py", source.path))


def _read_literal_modules(raw: bytes, path: str) -> list[VerifierSource]:
    """Read statically embedded launcher source without evaluating expressions."""
    tree = ast.parse(raw)
    assignments = [
        node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "MODULE_SOURCE"
    ]
    if len(assignments) != 1 or not isinstance(assignments[0], ast.Dict):
        raise ValueError("expected one literal module dictionary")
    found = []
    seen = set()
    total = 0
    for key, value in zip(assignments[0].keys, assignments[0].values):
        if (
            not isinstance(key, ast.Constant)
            or not isinstance(key.value, str)
            or not isinstance(value, ast.Constant)
            or not isinstance(value.value, str)
        ):
            raise ValueError("module dictionary contains a nonliteral entry")
        relative = PurePosixPath(key.value)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or "\\" in key.value
            or relative.suffix != ".py"
            or relative.as_posix() in seen
        ):
            raise ValueError("unsafe or duplicate literal module path")
        seen.add(relative.as_posix())
        member = "tests/" + relative.as_posix()
        data = value.value.encode("utf-8")
        total += len(data)
        if (
            len(data) > MAX_SOURCE_BYTES
            or total > MAX_TOTAL_SOURCE_BYTES
            or len(found) >= MAX_SOURCE_FILES
        ):
            raise ValueError("literal module budget exceeded")
        found.append(_source(f"{path}!{member}", data, container=path, member=member))
    return sorted(found, key=lambda source: (source.member != "tests/verify.py", source.path))


def collect_verifier_sources(task: Optional[Path]) -> VerifierSources:
    snapshot = VerifierSources()
    if task is None:
        return snapshot
    entry = resolve_verifier_script_path(task)
    entry_data = b""
    if entry and entry.is_file():
        try:
            entry_data = _read_bounded(task, entry, MAX_SOURCE_BYTES)
        except (OSError, ValueError) as exc:
            snapshot.diagnostics.append(
                {"code": "VERIFIER_SOURCE_UNAVAILABLE", "path": str(entry), "reason": str(exc)}
            )
    bundle = task / "tests/refs.json"
    # The launcher's control flow identifies the embedded code source. A refs.json
    # file that merely contains reference values stays on the ordinary-source path.
    if b"tar+gzip;base64" in entry_data and b"refs.json" in entry_data:
        snapshot.encoded_source_detected = True
        try:
            raw = _read_bounded(task, bundle, MAX_ENCODED_BYTES)
            snapshot.sources = _read_bundle(raw, "tests/refs.json")
            snapshot.encoded_source_decoded = True
        except (OSError, ValueError, AttributeError, tarfile.TarError, EOFError, zlib.error) as exc:
            snapshot.diagnostics.append(
                {
                    "code": "VERIFIER_BUNDLE_UNAVAILABLE",
                    "path": "tests/refs.json",
                    "reason": str(exc),
                }
            )
        return snapshot
    if b"MODULE_SOURCE" in entry_data and b"ccw-tests-refs/v1" in entry_data:
        snapshot.encoded_source_detected = True
        path = entry.relative_to(task).as_posix()
        try:
            snapshot.sources = _read_literal_modules(entry_data, path)
            snapshot.encoded_source_decoded = bool(snapshot.sources)
        except (ValueError, UnicodeError, SyntaxError, RecursionError) as exc:
            snapshot.diagnostics.append(
                {"code": "VERIFIER_BUNDLE_UNAVAILABLE", "path": path, "reason": str(exc)}
            )
        return snapshot

    total = 0
    paths = [entry] if entry_data else []
    tests = task / "tests"
    if not tests.is_symlink() and tests.is_dir():
        for path in tests.rglob("*.py"):
            if path != entry:
                paths.append(path)
            if len(paths) > MAX_SOURCE_FILES:
                snapshot.diagnostics.append({"code": "VERIFIER_SOURCE_COUNT_LIMIT"})
                break
    for path in sorted(paths[:MAX_SOURCE_FILES], key=lambda path: (path != entry, path)):
        try:
            data = entry_data if path == entry else _read_bounded(task, path, MAX_SOURCE_BYTES)
            if total + len(data) > MAX_TOTAL_SOURCE_BYTES:
                raise ValueError("aggregate source budget exceeded")
            snapshot.sources.append(_source(path.relative_to(task).as_posix(), data))
            total += len(data)
        except (OSError, ValueError) as exc:
            snapshot.diagnostics.append(
                {"code": "VERIFIER_SOURCE_UNAVAILABLE", "path": str(path), "reason": str(exc)}
            )
    return snapshot
