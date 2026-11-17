"""Read-only diagnoses of scoring dependencies, bound to saved verifier output."""

from pathlib import Path
from typing import Any, Dict, List, Optional


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
        if "trusted semantic review evidence hash is damaged" in text:
            code = "VERIFIER_TRUSTED_ASSET_HASH_MISMATCH"
            subtype = "trusted_asset_hash_mismatch"
            summary = "Verifier rejected its trusted semantic review evidence because the asset hash is damaged; scoring did not complete."
        elif "tests/refs.json missing or malformed" in text and task_dir:
            refs = task_dir / "tests/refs.json"
            prefix = b""
            if refs.is_file():
                with refs.open("rb") as stream:
                    prefix = stream.read(200)
            if prefix.startswith(b"version https://git-lfs.github.com/spec/v1\n"):
                code = "VERIFIER_REFERENCE_LFS_POINTER"
                subtype = "reference_lfs_pointer"
                summary = "Verifier could not parse tests/refs.json: the packaged reference is a Git LFS pointer rather than the required JSON payload."
        if code and not any(o["code"] == code for o in observations):
            observations.append(
                {
                    "obs_id": "ver:dependency:" + subtype,
                    "type": "verifier_internal_crash",
                    "code": code,
                    "subtype": subtype,
                    "summary": summary,
                    "matched_text": text.strip()[:400],
                    "source_file": rel,
                    "source_pointer": "bounded saved verifier excerpt",
                    "triggered": True,
                    "failure_binding": "direct",
                    "binding_evidence": [rel]
                    + (["tests/refs.json"] if subtype == "reference_lfs_pointer" else []),
                }
            )
    return observations
