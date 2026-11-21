"""Read-only diagnoses of scoring dependencies, bound to saved verifier output."""

import re
from pathlib import Path
from typing import Any, Dict, List, Optional


_LFS_HEADER = b"version https://git-lfs.github.com/spec/v1\n"
_PACKAGED_INPUT_FAILURE = re.compile(
    r"(?:evaluator input SHA-256 mismatch|package asset hash mismatch|"
    r"public input asset digest mismatch|protected asset missing or damaged)"
    r":\s*([A-Za-z0-9_./-]+\.[A-Za-z0-9_]+)"
)
_DEPENDENCY_PREFLIGHT_FAILURE = re.compile(
    r"protected source missing/corrupt:\s*[\w./-]+|"
    r"protected reference integrity mismatch\b|"
    r"integrity failure:\s*assets/[\w./-]+|"
    r"tests/refs\.json missing or malformed\b",
    re.IGNORECASE,
)
_REPLAY_RUNTIME_REQUIREMENT = re.compile(
    r"^candidate replay requires executable /dev/shm tmpfs\b", re.IGNORECASE
)


def dependency_preflight_matches(message: str) -> bool:
    """Recognize saved preflight rejections of verifier-owned inputs."""
    return bool(
        _PACKAGED_INPUT_FAILURE.search(message) or _DEPENDENCY_PREFLIGHT_FAILURE.search(message)
    )


def replay_runtime_error(report: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """A recorded environment guard, separate from verifier code or reference defects."""
    if str(report.get("status") or "").upper() != "ENV_INVALID":
        return None
    message = next(iter(report.get("messages") or []), "")
    if not _REPLAY_RUNTIME_REQUIREMENT.match(message):
        return None
    location = report.get("first_message_location") or {}
    source = location.get("source")
    if not source or source != report.get("primary_report"):
        return None
    return {
        "error_id": "err:verifier_runtime_requirement",
        "code": "INFRA_VERIFIER_RUNTIME_REQUIREMENT",
        "subtype": "verifier_runtime_requirement",
        "stage": "verifier_execution",
        "matched_text": message[:300],
        "causal_candidate": True,
        "transient": False,
        "source_file": "verifier/" + source,
        "source_pointer": location.get("pointer"),
        "scoring_reached": False,
    }


def _pointer_metadata(path: Path) -> Optional[Dict[str, Any]]:
    """Inspect a small regular pointer, never fetch or read a payload in full."""
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024:
            return None
        prefix = path.read_bytes()
    except OSError:
        return None
    if not prefix.startswith(_LFS_HEADER):
        return None
    text = prefix.decode("ascii", errors="replace")
    oid = re.search(r"^oid sha256:([0-9a-f]{64})$", text, re.M)
    size = re.search(r"^size ([0-9]+)$", text, re.M)
    return {
        "format": "git-lfs-pointer/v1",
        "pointer_size_bytes": len(prefix),
        "payload_oid": "sha256:" + oid.group(1) if oid else None,
        "declared_payload_size_bytes": int(size.group(1)) if size else None,
    }


def _named_packaged_input(task: Path, name: str) -> Optional[Path]:
    """Resolve the rejected name only within packaged input locations.

    Basename-only errors require a unique regular-file match. A different LFS
    asset elsewhere in the package cannot explain the named failure.
    """
    relative = Path(name)
    if relative.is_absolute() or ".." in relative.parts:
        return None
    roots = (task / "environment/assets", task / "tests/protected")
    candidates = []
    if name.startswith("assets/"):
        candidates = [roots[0] / Path(*relative.parts[1:])]
    elif name.startswith("environment/assets/") or name.startswith("tests/protected/"):
        candidates = [task / relative]
    elif len(relative.parts) == 1:
        for root in roots:
            if root.is_dir():
                candidates.extend(root.rglob(name))
    else:
        # A relative path can be rooted at either documented input location;
        # ambiguity causes abstention instead of choosing a pointer match.
        candidates = [root / relative for root in roots]
    existing = [path for path in candidates if path.is_file() and not path.is_symlink()]
    return existing[0] if len(existing) == 1 else None


def collect_scoring_dependency_observations(
    task_dir: Optional[Path], trial_dir: Optional[Path], runtime: Optional[Dict[str, Any]] = None
) -> List[Dict[str, Any]]:
    if not trial_dir or (runtime is not None and runtime.get("score_produced") is True):
        return []
    observations = []
    for rel in ("verifier/test-stdout.txt", "verifier/scorer.stdout", "verifier/verification.json"):
        path = trial_dir / rel
        if not path.is_file():
            continue
        with path.open("rb") as stream:
            text_bytes = stream.read(500_000)
            if path.stat().st_size > 500_000:
                stream.seek(max(0, path.stat().st_size - 500_000))
                text_bytes += b"\n[bounded tail]\n" + stream.read(500_000)
        text = text_bytes.decode("utf-8", errors="replace")
        code = subtype = summary = None
        dependency = None
        metadata = None
        matched_text = text.strip()[:400]
        if "trusted semantic review evidence hash is damaged" in text:
            code = "VERIFIER_TRUSTED_ASSET_HASH_MISMATCH"
            subtype = "trusted_asset_hash_mismatch"
            summary = "Verifier rejected its trusted semantic review evidence because the asset hash is damaged; scoring did not complete."
        elif "tests/refs.json missing or malformed" in text and task_dir:
            refs = task_dir / "tests/refs.json"
            metadata = _pointer_metadata(refs)
            if metadata is not None:
                dependency = refs
                code = "VERIFIER_REFERENCE_LFS_POINTER"
                subtype = "reference_lfs_pointer"
                summary = "Verifier could not parse tests/refs.json: the packaged reference is a Git LFS pointer rather than the required JSON payload."
        if code:
            records = [(code, subtype, summary, dependency, metadata, matched_text)]
        else:
            records = []
            if task_dir:
                for match in _PACKAGED_INPUT_FAILURE.finditer(text):
                    dependency = _named_packaged_input(task_dir, match.group(1))
                    metadata = _pointer_metadata(dependency) if dependency else None
                    if metadata is not None:
                        local = dependency.relative_to(task_dir).as_posix()
                        records.append(
                            (
                                "VERIFIER_REFERENCE_LFS_POINTER",
                                "reference_lfs_pointer",
                                f"Verifier rejected the named packaged input {local}; the matching local file is a Git LFS pointer rather than the required payload.",
                                dependency,
                                metadata,
                                match.group(0),
                            )
                        )
        for code, subtype, summary, dependency, metadata, matched_text in records:
            local = dependency.relative_to(task_dir).as_posix() if dependency and task_dir else None
            if any(o["code"] == code and o.get("dependency_path") == local for o in observations):
                continue
            observation = {
                "obs_id": "ver:dependency:"
                + subtype
                + (":" + local if local and local != "tests/refs.json" else ""),
                "type": "verifier_internal_crash",
                "code": code,
                "subtype": subtype,
                "summary": summary,
                "matched_text": matched_text[:400],
                "source_file": rel,
                "source_pointer": "bounded saved verifier excerpt",
                "triggered": True,
                "failure_binding": "direct",
                "binding_evidence": [rel] + ([local] if local else []),
            }
            if local:
                observation.update(
                    dependency_path=local,
                    dependency_role="packaged_verifier_input",
                    pointer_metadata=metadata,
                    uncertainty=(
                        "The saved verifier rejection names this packaged input and its current local file is a pointer. "
                        "Historical byte identity is not independently proven; unrelated pointers and successful-score stale logs are excluded."
                    ),
                )
            observations.append(observation)
    return observations
