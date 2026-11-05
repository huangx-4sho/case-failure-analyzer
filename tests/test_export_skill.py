#!/usr/bin/env python3
"""
Unit tests for safe skill packaging and export (`scripts/export_skill.py`).
"""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_DIR = REPO_ROOT / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from export_skill import (
    EXPORT_MANIFEST_SCHEMA_VERSION,
    ExportSafetyError,
    MANIFEST_FILENAME,
    export_skill,
    validate_destination,
)


class TestExportSkill(unittest.TestCase):
    def test_validate_destination_safety_rules(self) -> None:
        """Verifies refusal of empty, system root, user home root, and repo root."""
        # Empty
        with self.assertRaises(ExportSafetyError):
            validate_destination("")
        with self.assertRaises(ExportSafetyError):
            validate_destination("   ")

        # System root and system directories
        with self.assertRaises(ExportSafetyError):
            validate_destination("/")
        with self.assertRaises(ExportSafetyError):
            validate_destination("/usr")
        with self.assertRaises(ExportSafetyError):
            validate_destination("/etc")

        # User home root
        with self.assertRaises(ExportSafetyError):
            validate_destination(str(Path.home()))

        # Repo root and parent
        with self.assertRaises(ExportSafetyError):
            validate_destination(str(REPO_ROOT))
        with self.assertRaises(ExportSafetyError):
            validate_destination(str(REPO_ROOT.parent))

        # Any path inside the source repository
        with self.assertRaises(ExportSafetyError):
            validate_destination(str(REPO_ROOT / "scripts" / "nested_export"))
        with self.assertRaises(ExportSafetyError):
            validate_destination(str(REPO_ROOT / "references" / "nested_export"))

    def test_export_skill_files_and_manifest(self) -> None:
        """Verifies clean export of all managed components and manifest creation."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            dest = Path(tmp_dir) / "exported_skill"
            copied, cleaned = export_skill(dest)

            self.assertGreater(copied, 20)
            self.assertEqual(cleaned, 0)

            # Core files check
            self.assertTrue((dest / "SKILL.md").is_file())
            self.assertTrue((dest / "agents" / "openai.yaml").is_file())
            self.assertTrue((dest / "scripts" / "analyze_case.py").is_file())
            self.assertTrue((dest / "scripts" / "staging.py").is_file())
            self.assertTrue((dest / "scripts" / "render_report.py").is_file())
            self.assertTrue((dest / "scripts" / "render_batch.py").is_file())
            self.assertTrue((dest / "scripts" / "export_skill.py").is_file())
            self.assertTrue((dest / "references" / "attribution-protocol.md").is_file())
            self.assertTrue((dest / "references" / "software" / "quantum-espresso.md").is_file())

            # Manifest check
            manifest_file = dest / MANIFEST_FILENAME
            self.assertTrue(manifest_file.is_file())
            data = json.loads(manifest_file.read_text(encoding="utf-8"))
            self.assertEqual(data.get("schema_version"), EXPORT_MANIFEST_SCHEMA_VERSION)
            self.assertEqual(data.get("name"), "case-failure-analyzer")
            self.assertIn("SKILL.md", data.get("files", []))
            self.assertIn("scripts/analyze_case.py", data.get("files", []))

            # Excluded patterns check
            for p in dest.rglob("*"):
                self.assertNotIn("__pycache__", p.parts)
                self.assertFalse(p.name.endswith(".pyc"))
                self.assertNotEqual(p.name, ".DS_Store")

    def test_export_preserves_user_custom_files(self) -> None:
        """Verifies that user custom files in dest/scripts or dest/ are NEVER deleted."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            dest = Path(tmp_dir) / "my_skill"
            dest.mkdir(parents=True, exist_ok=True)
            (dest / "scripts").mkdir(parents=True, exist_ok=True)

            user_script = dest / "scripts" / "user_custom_tool.py"
            user_script.write_text("print('user custom tool')\n", encoding="utf-8")
            user_note = dest / "user_notes.txt"
            user_note.write_text("Important notes\n", encoding="utf-8")

            # Perform export
            export_skill(dest)

            # Assert user files remain intact
            self.assertTrue(user_script.is_file())
            self.assertEqual(user_script.read_text(encoding="utf-8"), "print('user custom tool')\n")
            self.assertTrue(user_note.is_file())
            self.assertEqual(user_note.read_text(encoding="utf-8"), "Important notes\n")

            # Assert managed CFA files also present
            self.assertTrue((dest / "SKILL.md").is_file())
            self.assertTrue((dest / "scripts" / "analyze_case.py").is_file())

    def test_clean_flag_only_prunes_stale_managed_files(self) -> None:
        """Verifies --clean prunes obsolete tracked files but preserves unmanaged user files."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            dest = Path(tmp_dir) / "managed_skill"
            dest.mkdir(parents=True, exist_ok=True)

            # Simulate a stale managed file from a previous export
            manifest_file = dest / MANIFEST_FILENAME
            manifest_data = {
                "name": "case-failure-analyzer",
                "files": ["scripts/old_removed_tool.py", "SKILL.md"],
            }
            manifest_file.write_text(json.dumps(manifest_data), encoding="utf-8")

            stale_file = dest / "scripts" / "old_removed_tool.py"
            stale_file.parent.mkdir(parents=True, exist_ok=True)
            stale_file.write_text("old code", encoding="utf-8")

            # And an unmanaged user file
            user_file = dest / "scripts" / "user_file.py"
            user_file.write_text("user code", encoding="utf-8")

            # Export with clean=True
            copied, cleaned = export_skill(dest, clean=True)

            self.assertEqual(cleaned, 1)
            self.assertFalse(stale_file.exists())
            self.assertTrue(user_file.is_file())
            self.assertTrue((dest / "SKILL.md").is_file())

    def test_clean_rejects_manifest_paths_escaping_destination(self) -> None:
        """Traversal, absolute, and symlink escapes must never delete outside dest."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            dest = base / "managed_skill"
            dest.mkdir()
            victim = base / "victim.txt"
            victim.write_text("precious data", encoding="utf-8")
            outside_target = base / "outside_target.txt"
            outside_target.write_text("also precious", encoding="utf-8")
            symlink = dest / "escape.txt"
            symlink.symlink_to(outside_target)

            manifest = dest / MANIFEST_FILENAME
            manifest.write_text(
                json.dumps(
                    {
                        "name": "case-failure-analyzer",
                        "files": [
                            "../victim.txt",
                            str(victim.resolve()),
                            "escape.txt",
                            "safe-stale.txt",
                        ],
                    }
                ),
                encoding="utf-8",
            )
            safe_stale = dest / "safe-stale.txt"
            safe_stale.write_text("old managed file", encoding="utf-8")

            _copied, cleaned = export_skill(dest, clean=True)

            self.assertEqual(cleaned, 1)
            self.assertEqual(victim.read_text(encoding="utf-8"), "precious data")
            self.assertEqual(outside_target.read_text(encoding="utf-8"), "also precious")
            self.assertTrue(symlink.is_symlink())
            self.assertFalse(safe_stale.exists())

    def test_clean_ignores_malformed_or_foreign_manifests(self) -> None:
        """Untrusted manifest metadata must not become a deletion allow-list."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            victim = base / "victim.txt"
            victim.write_text("precious data", encoding="utf-8")

            for index, payload in enumerate(
                (
                    {"name": "another-skill", "files": ["../victim.txt"]},
                    {"name": "case-failure-analyzer", "files": "../victim.txt"},
                    {
                        "schema_version": "future-version",
                        "name": "case-failure-analyzer",
                        "files": ["../victim.txt"],
                    },
                    [{"name": "case-failure-analyzer", "files": ["../victim.txt"]}],
                )
            ):
                with self.subTest(payload=payload):
                    dest = base / f"managed_skill_{index}"
                    dest.mkdir()
                    (dest / MANIFEST_FILENAME).write_text(json.dumps(payload), encoding="utf-8")
                    _copied, cleaned = export_skill(dest, clean=True)
                    self.assertEqual(cleaned, 0)
                    self.assertEqual(victim.read_text(encoding="utf-8"), "precious data")

    def test_clean_tolerates_non_utf8_manifest(self) -> None:
        """An unreadable manifest must warn and yield no deletion allow-list, not crash."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            dest = Path(tmp_dir) / "managed_skill"
            dest.mkdir()
            stale_file = dest / "scripts" / "old_managed_tool.py"
            stale_file.parent.mkdir(parents=True)
            stale_file.write_text("old code", encoding="utf-8")
            (dest / MANIFEST_FILENAME).write_bytes(b"\xff\xfe\x00broken")

            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                _copied, cleaned = export_skill(dest, clean=True)

            self.assertEqual(cleaned, 0)
            self.assertTrue(stale_file.is_file(), "stale file must survive an unreadable manifest")
            self.assertIn("unreadable export manifest", stderr.getvalue())

    def test_cli_refuses_export_inside_source_repository(self) -> None:
        nested_dest = SCRIPT_DIR / "nested_export_must_not_exist"
        cmd = [
            sys.executable,
            str(SCRIPT_DIR / "export_skill.py"),
            "--dest",
            str(nested_dest),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("EXPORT SAFETY ERROR", proc.stderr)
        self.assertFalse(nested_dest.exists())

    def test_cli_exit_codes(self) -> None:
        """Verifies CLI exit codes for valid and invalid destinations."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            dest = Path(tmp_dir) / "cli_export"
            cmd = [sys.executable, str(SCRIPT_DIR / "export_skill.py"), "--dest", str(dest)]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("successfully exported", proc.stdout)

        # Unsafe destination: system root
        cmd_unsafe = [sys.executable, str(SCRIPT_DIR / "export_skill.py"), "--dest", "/"]
        proc_unsafe = subprocess.run(cmd_unsafe, capture_output=True, text=True)
        self.assertEqual(proc_unsafe.returncode, 2)
        self.assertIn("EXPORT SAFETY ERROR", proc_unsafe.stderr)


if __name__ == "__main__":
    unittest.main()
