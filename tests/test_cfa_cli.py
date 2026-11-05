#!/usr/bin/env python3
"""
Tests for unified CFA CLI (`scripts/cfa.py`).
"""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
SCRIPT_DIR = ROOT_DIR / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import cfa
from cfa import redact_sensitive


class TestCfaCli(unittest.TestCase):
    def test_redact_sensitive(self) -> None:
        raw = "User /Users/alice/repo has token sk-12345678901234567890 and secret='supersecret'"
        redacted = redact_sensitive(raw)
        self.assertNotIn("sk-12345678901234567890", redacted)
        self.assertIn("[REDACTED_KEY]", redacted)
        self.assertIn('secret: "[REDACTED]"', redacted)

    def test_cfa_help(self) -> None:
        f = io.StringIO()
        with redirect_stdout(f):
            rc = cfa.main(["--help"])
        self.assertEqual(rc, 0)
        self.assertIn("Case Failure Analyzer", f.getvalue())

    def test_cfa_index_missing_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rc = cfa.main(["index", "--report-root", str(Path(tmp) / "reports")])
            self.assertEqual(rc, 0)
            self.assertTrue((Path(tmp) / "reports" / "INDEX.md").is_file())


if __name__ == "__main__":
    unittest.main()
