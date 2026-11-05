#!/usr/bin/env python3
"""
Staging Directory, Advisory Process Locking & Transactional Manifest Publishing (`scripts/staging.py`)

Handles:
  - Input-output boundary isolation validation (`validate_input_output_isolation`, `paths_overlap`)
  - Output writability verification (`validate_output_writable`)
  - Cross-process advisory locking (`acquire_advisory_process_lock`, `release_advisory_process_lock`, `acquire_publish_lock`)
  - Staging directory transactional publishing with atomic file replacement and rollback (`publish_staging_to_output`)
  - Orphaned temporary file cleanup and path safety validation (`cleanup_orphaned_cfa_tmp_files`, `is_safe_manifest_relpath`)
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

MANIFEST_FILENAME = ".cfa_manifest.json"


def manifest_filename(manifest_key: Optional[str] = None) -> str:
    """
    Manifest file for one publish target.

    A shared output folder (e.g. `<bench>/failure-report/` holding every task's
    reports) needs one manifest per writer; otherwise publishing task208 would
    treat task207's files as stale and delete them. Without a key the classic
    single-writer name is used.
    """
    if not manifest_key:
        return MANIFEST_FILENAME
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(manifest_key)).strip("._") or "task"
    return f".cfa_manifest.{safe}.json"


def paths_overlap(p1: Path, p2: Path) -> bool:
    """Check if p1 and p2 are identical, or if either is a parent/child of the other (resolving symlinks)."""
    try:
        r1 = p1.resolve()
        r2 = p2.resolve()
    except Exception:
        r1 = Path(p1.absolute())
        r2 = Path(p2.absolute())
    if r1 == r2:
        return True
    try:
        r1.relative_to(r2)
        return True
    except ValueError:
        pass
    try:
        r2.relative_to(r1)
        return True
    except ValueError:
        pass
    return False


def validate_input_output_isolation(
    out_dir: Path,
    input_roots: List[Tuple[Optional[Path], str]],
    planned_trial_names: List[str],
    allowed_report_dir: Optional[Path] = None,
) -> None:
    """Enforce strict read-only boundary between output targets and all inputs.

    `allowed_report_dir` is a narrow, sanctioned exception for task-ID mode:
    reports may be written into a dedicated ``failure-report`` subfolder of a
    task definition directory (which is itself an input root). The exception is
    granted only when the folder is literally named ``failure-report`` and its
    parent is one of the declared input roots. Even then:

    - the output may never be *identical* to an input root,
    - the output may never be a *parent* of an input root,
    - symlinks inside the output tree that resolve to input paths are still
      rejected (an output symlink escaping into the read-only inputs is unsafe),
    - output elsewhere inside inputs remains forbidden.
    """
    resolved_inputs: List[Tuple[Path, str]] = []
    for inp, label in input_roots:
        if inp is not None:
            try:
                resolved_inputs.append((inp.resolve(), label))
            except Exception:
                resolved_inputs.append((Path(inp.absolute()), label))

    allowed: Optional[Path] = None
    if allowed_report_dir is not None:
        try:
            candidate = allowed_report_dir.resolve()
        except Exception:
            candidate = Path(allowed_report_dir.absolute())
        if candidate.name != "failure-report" or not any(
            candidate.parent == r_inp for r_inp, _ in resolved_inputs
        ):
            print(
                "ERROR: sanctioned report directory must be a 'failure-report' "
                f"subfolder of a declared input root, got: {allowed_report_dir}",
                file=sys.stderr,
            )
            sys.exit(2)
        allowed = candidate

    def _assert_no_overlap(target: Path, target_label: str) -> None:
        try:
            r_target = target.resolve()
        except Exception:
            r_target = Path(target.absolute())
        for r_inp, inp_label in resolved_inputs:
            is_overlap = False
            output_inside_input = False
            if r_target == r_inp:
                is_overlap = True
            else:
                try:
                    r_target.relative_to(r_inp)
                    is_overlap = True
                    output_inside_input = True
                except ValueError:
                    pass
                if not is_overlap:
                    try:
                        r_inp.relative_to(r_target)
                        is_overlap = True
                    except ValueError:
                        pass
            if is_overlap:
                # Sanctioned exception: this exact report folder (or a plain
                # descendant of it) inside its own parent input root.
                if allowed is not None and output_inside_input and r_inp == allowed.parent:
                    try:
                        r_target.relative_to(allowed)
                        continue
                    except ValueError:
                        pass
                print(
                    f"ERROR: {target_label} ({target}) cannot be identical to, inside, or a parent of {inp_label} ({r_inp}).",
                    file=sys.stderr,
                )
                sys.exit(2)

    # 1. Check output root
    _assert_no_overlap(out_dir, "--output path")

    # 2. Check each planned trial output directory
    for t_name in planned_trial_names:
        t_dest = out_dir / t_name
        _assert_no_overlap(t_dest, f"--output trial directory '{t_name}'")

    # 3. If out_dir exists or is a symlink, inspect its entire tree without following symlinks to reject links into inputs
    if out_dir.exists() or out_dir.is_symlink():
        if out_dir.is_symlink():
            _assert_no_overlap(out_dir, "--output symlink target")
        for root, dirs, files in os.walk(out_dir, followlinks=False):
            root_path = Path(root)
            for d in dirs:
                d_path = root_path / d
                _assert_no_overlap(d_path, f"output subdirectory '{d_path.relative_to(out_dir)}'")
            for f in files:
                f_path = root_path / f
                if f_path.is_symlink():
                    _assert_no_overlap(f_path, f"output symlink '{f_path.relative_to(out_dir)}'")


def validate_output_writable(out_dir: Path) -> None:
    """Ensure out_dir can be created and written to; fail fast with exit code 2 if non-writable."""
    target = out_dir.resolve()
    if target.exists() and not target.is_dir():
        print(f"ERROR: --output path exists and is not a directory: {out_dir}", file=sys.stderr)
        sys.exit(2)

    probe_file = target / f".cfa_probe_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    try:
        target.mkdir(parents=True, exist_ok=True)
        probe_file.write_text("probe", encoding="utf-8")
        probe_file.unlink()
    except Exception as e:
        print(
            f"ERROR: --output directory is not writable or cannot be created: {out_dir} ({e})",
            file=sys.stderr,
        )
        sys.exit(2)


def is_safe_manifest_relpath(rel_str: Any, out_dir: Path) -> bool:
    """Validate that rel_str is a safe relative path strictly contained inside out_dir."""
    if not isinstance(rel_str, str) or not rel_str.strip():
        return False
    cleaned = rel_str.strip()
    p = Path(cleaned)
    if p.is_absolute() or ".." in p.parts:
        return False
    try:
        resolved_out = out_dir.resolve()
        resolved_target = (out_dir / p).resolve()
        if resolved_target == resolved_out:
            return False
        resolved_target.relative_to(resolved_out)
        return True
    except (ValueError, RuntimeError):
        return False


def is_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def cleanup_orphaned_cfa_tmp_files(out_dir: Path) -> None:
    """Clean up abandoned temporary or restore files left by crashed processes in out_dir."""
    current_pid = os.getpid()
    for p in out_dir.rglob(".*"):
        if not p.is_file():
            continue
        m = re.search(r"\.(?:cfa_tmp|restore)_(\d+)_[0-9a-fA-F]+$", p.name)
        if m:
            try:
                file_pid = int(m.group(1))
                if file_pid != current_pid and not is_pid_alive(file_pid):
                    p.unlink()
            except (ValueError, OSError):
                pass


def get_publish_lock_path(out_dir: Path) -> Path:
    h = hashlib.sha256(str(out_dir.resolve()).encode("utf-8")).hexdigest()[:16]
    return Path(tempfile.gettempdir()) / f"cfa_publish_{h}.lock"


def clean_tier2_lock_dir(lock_dir: Path) -> None:
    """Clean up tier-2 directory lock if stale or upon release."""
    try:
        pid_file = lock_dir / "pid"
        if pid_file.is_file():
            pid_file.unlink()
    except OSError:
        pass
    try:
        if lock_dir.is_dir():
            os.rmdir(str(lock_dir))
    except OSError:
        pass


class ProcessLockHandle:
    """Encapsulates acquired state for an advisory process lock."""

    def __init__(
        self,
        lock_path: Path,
        lock_dir: Path,
        lock_fd: Optional[int] = None,
        flock_acquired: bool = False,
        lock_dir_acquired: bool = False,
    ):
        self.lock_path = lock_path
        self.lock_dir = lock_dir
        self.lock_fd = lock_fd
        self.flock_acquired = flock_acquired
        self.lock_dir_acquired = lock_dir_acquired


def acquire_advisory_process_lock(out_dir: Path, timeout_sec: float = 10.0) -> ProcessLockHandle:
    """Acquire an exclusive advisory process lock for out_dir to prevent concurrent publish races.

    Uses fcntl.flock as primary mechanism; automatically falls back to atomic directory locking
    (os.mkdir) on network filesystems or platforms where advisory locking is unsupported or fails.
    """
    lock_path = get_publish_lock_path(out_dir)
    lock_dir = lock_path.with_suffix(".lockdir")
    handle = ProcessLockHandle(lock_path=lock_path, lock_dir=lock_dir)

    try:
        handle.lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
        try:
            import fcntl

            fcntl.flock(handle.lock_fd, fcntl.LOCK_EX)
            handle.flock_acquired = True
        except (ImportError, OSError, AttributeError):
            handle.flock_acquired = False
    except OSError:
        handle.flock_acquired = False

    if not handle.flock_acquired:
        # Tier 2 fallback: atomic directory locking via os.mkdir
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            try:
                os.mkdir(str(lock_dir))
                (lock_dir / "pid").write_text(str(os.getpid()), encoding="utf-8")
                handle.lock_dir_acquired = True
                break
            except FileExistsError:
                pid_file = lock_dir / "pid"
                stale = False
                if pid_file.is_file():
                    try:
                        holder_pid = int(pid_file.read_text(encoding="utf-8").strip())
                        if not is_pid_alive(holder_pid):
                            stale = True
                    except Exception:
                        stale = True
                else:
                    stale = True

                if stale:
                    clean_tier2_lock_dir(lock_dir)
                    continue
                time.sleep(0.05)
            except OSError as dir_err:
                print(
                    f"WARNING: Process locking fallback unavailable ({dir_err}); "
                    "proceeding without concurrency serialization.",
                    file=sys.stderr,
                )
                break

    return handle


def release_advisory_process_lock(handle: ProcessLockHandle) -> None:
    """Release previously acquired advisory process lock."""
    if handle.flock_acquired and handle.lock_fd is not None:
        try:
            import fcntl

            fcntl.flock(handle.lock_fd, fcntl.LOCK_UN)
        except Exception:
            pass
    if handle.lock_fd is not None:
        try:
            os.close(handle.lock_fd)
        except Exception:
            pass
        handle.lock_fd = None
        handle.flock_acquired = False
    if handle.lock_dir_acquired and handle.lock_dir.is_dir():
        clean_tier2_lock_dir(handle.lock_dir)
        handle.lock_dir_acquired = False


@contextlib.contextmanager
def acquire_publish_lock(out_dir: Path):
    """Context manager wrapper for acquire_advisory_process_lock and release_advisory_process_lock."""
    handle = acquire_advisory_process_lock(out_dir)
    try:
        yield handle
    finally:
        release_advisory_process_lock(handle)


def publish_staging_to_output(
    staging_dir: Path,
    out_dir: Path,
    is_multi_trial: bool,
    manifest_key: Optional[str] = None,
) -> None:
    """Publish staging directory contents into out_dir with atomic file replacement and best-effort rollback."""
    out_dir.resolve().mkdir(parents=True, exist_ok=True)
    with acquire_publish_lock(out_dir):
        cleanup_orphaned_cfa_tmp_files(out_dir)
        publish_staging_to_output_locked(
            staging_dir, out_dir, is_multi_trial, manifest_key=manifest_key
        )


def publish_staging_to_output_locked(
    staging_dir: Path,
    out_dir: Path,
    is_multi_trial: bool,
    manifest_key: Optional[str] = None,
) -> None:
    """Internal publish logic executed under process lock."""
    out_dir.resolve().mkdir(parents=True, exist_ok=True)
    manifest_name = manifest_filename(manifest_key)

    # 1. Collect staged files and write manifest into staging_dir
    staged_rel_paths = sorted(
        str(p.relative_to(staging_dir)) for p in staging_dir.rglob("*") if p.is_file()
    )
    staged_dirs = sorted(p.name for p in staging_dir.iterdir() if p.is_dir())
    manifest_data = {
        "schema_version": "cfa-manifest-v1",
        "layout": "multi" if is_multi_trial else "single",
        "scope": manifest_key or None,
        "managed_files": staged_rel_paths + [manifest_name],
        "managed_dirs": staged_dirs,
    }
    manifest_file = staging_dir / manifest_name
    manifest_file.write_text(
        json.dumps(manifest_data, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    staged_rel_paths.append(manifest_name)

    # 2. Determine previous managed artifacts in out_dir from a verified manifest only
    old_manifest_path = out_dir / manifest_name
    old_managed_files: Set[str] = set()
    old_managed_dirs: Set[str] = set()
    has_valid_old_manifest = False

    if old_manifest_path.is_file():
        try:
            old_manifest = json.loads(old_manifest_path.read_text(encoding="utf-8"))
            if (
                isinstance(old_manifest, dict)
                and old_manifest.get("schema_version") == "cfa-manifest-v1"
            ):
                raw_files = old_manifest.get("managed_files") or []
                raw_dirs = old_manifest.get("managed_dirs") or []
                if isinstance(raw_files, list) and isinstance(raw_dirs, list):
                    for f in raw_files:
                        if is_safe_manifest_relpath(f, out_dir):
                            old_managed_files.add(str(Path(f)))
                    for d in raw_dirs:
                        if is_safe_manifest_relpath(d, out_dir):
                            old_managed_dirs.add(str(Path(d)))
                    has_valid_old_manifest = True
        except Exception:
            pass

    # If no valid previous manifest exists, CFA assumes ownership of ZERO pre-existing files!
    # Unmanaged files and directories (user notes, reports, data) are NEVER deleted.
    if not has_valid_old_manifest:
        old_managed_files = set()
        old_managed_dirs = set()

    # Managed files to remove (existed previously in manifest, not in new staged files)
    files_to_remove = old_managed_files - set(staged_rel_paths)

    # 3. Create transactional backup of all files in out_dir that will be deleted or overwritten
    backup_dir = Path(tempfile.mkdtemp(prefix="cfa_tx_backup_"))
    backed_up_items: Dict[str, Path] = {}
    pre_publish_files = {str(p.relative_to(out_dir)) for p in out_dir.rglob("*") if p.is_file()}

    try:
        # Back up existing files that will be touched or deleted
        files_to_backup = files_to_remove | {
            rel_str for rel_str in staged_rel_paths if (out_dir / rel_str).is_file()
        }
        for rel_str in files_to_backup:
            if not is_safe_manifest_relpath(rel_str, out_dir):
                continue
            target = out_dir / rel_str
            if target.is_file():
                b_path = backup_dir / rel_str
                b_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, b_path)
                backed_up_items[rel_str] = b_path

        # Step A: Atomically write and replace all staged files into out_dir
        # By putting new files in place first, readers never see missing files before replacement
        for rel_str in staged_rel_paths:
            if not is_safe_manifest_relpath(rel_str, out_dir):
                continue
            src = staging_dir / rel_str
            dest = out_dir / rel_str
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp_dest = dest.parent / f".{dest.name}.cfa_tmp_{os.getpid()}_{uuid.uuid4().hex[:8]}"
            try:
                shutil.copy2(src, tmp_dest)
                os.replace(tmp_dest, dest)
            except Exception:
                if tmp_dest.is_file() or tmp_dest.is_symlink():
                    try:
                        tmp_dest.unlink()
                    except OSError:
                        pass
                raise

        # Step B: Delete stale managed files (now that new versions are fully in place)
        for rel_str in files_to_remove:
            if not is_safe_manifest_relpath(rel_str, out_dir):
                continue
            target = out_dir / rel_str
            if target.is_file() or target.is_symlink():
                target.unlink()

        # Step C: Clean old managed trial directories if empty
        for d_name in old_managed_dirs - set(staged_dirs):
            if not is_safe_manifest_relpath(d_name, out_dir):
                continue
            d_path = out_dir / d_name
            if d_path.is_dir():
                try:
                    d_path.rmdir()
                except OSError:
                    pass  # Keep if user files exist

    except Exception as primary_exc:
        # ROLLBACK: Best-effort restore of out_dir to exact pre-publish state
        rollback_errors: List[Exception] = []
        try:
            current_files = {str(p.relative_to(out_dir)) for p in out_dir.rglob("*") if p.is_file()}
            # 1. Remove newly created files
            for newly_created in current_files - pre_publish_files:
                try:
                    p = out_dir / newly_created
                    if p.is_file() or p.is_symlink():
                        p.unlink()
                except Exception as e:
                    rollback_errors.append(e)

            # 2. Restore all backed up files using atomic replace
            for rel_str, b_path in backed_up_items.items():
                dest = out_dir / rel_str
                tmp_restore = (
                    dest.parent / f".{dest.name}.restore_{os.getpid()}_{uuid.uuid4().hex[:8]}"
                )
                try:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(b_path, tmp_restore)
                    os.replace(tmp_restore, dest)
                except Exception as e:
                    rollback_errors.append(e)
                finally:
                    if tmp_restore.is_file() or tmp_restore.is_symlink():
                        try:
                            tmp_restore.unlink()
                        except OSError:
                            pass

            # 3. Clean any empty dirs created during failed attempt
            for rel_str in staged_rel_paths:
                parent = (out_dir / rel_str).parent
                while parent != out_dir and parent.is_dir():
                    try:
                        parent.rmdir()
                        parent = parent.parent
                    except OSError:
                        break
        except Exception as e:
            rollback_errors.append(e)

        if rollback_errors:
            print(
                f"WARNING: Rollback encountered secondary I/O errors: {rollback_errors[0]}",
                file=sys.stderr,
            )
        raise primary_exc
    finally:
        shutil.rmtree(backup_dir, ignore_errors=True)


# Backwards compatibility aliases
_paths_overlap = paths_overlap
_validate_input_output_isolation = validate_input_output_isolation
_validate_output_writable = validate_output_writable
_is_safe_manifest_relpath = is_safe_manifest_relpath
_is_pid_alive = is_pid_alive
_cleanup_orphaned_cfa_tmp_files = cleanup_orphaned_cfa_tmp_files
_get_publish_lock_path = get_publish_lock_path
_acquire_publish_lock = acquire_publish_lock
_publish_staging_to_output = publish_staging_to_output
_publish_staging_to_output_locked = publish_staging_to_output_locked
