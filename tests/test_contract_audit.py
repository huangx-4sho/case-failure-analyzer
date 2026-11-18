#!/usr/bin/env python3
"""Unit tests for Phase 4 (`scripts/audit_contract.py`)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from audit_contract import audit_contract, inspect_delivered_artifacts


class TestContractAudit(unittest.TestCase):
    def test_detects_missing_asset_and_verifier_regex_defect(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp) / "tasks" / "sample-task"
            (task_dir / "environment" / "assets").mkdir(parents=True)
            (task_dir / "tests").mkdir(parents=True)
            (task_dir / "instruction.md").write_text(
                "Copy `/workspace/assets/Si_missing.UPF` and write `results.json`:\n"
                '```json\n{"values": {"energy": -10.0}, "units": {"energy": "Ry"}}\n```',
                encoding="utf-8",
            )
            (task_dir / "tests" / "verify.py").write_text(
                "import re\n"
                "ENERGY_RE = re.compile(r'ENERGY:\\s*([-\\d.E+]+)')\n"
                "for m in re.finditer(r'&(\\w+)\\b(.*?)/', text, re.DOTALL):\n"
                "    pass\n",
                encoding="utf-8",
            )

            trial_dir = Path(tmp) / "jobs" / "trial1"
            (trial_dir / "verifier").mkdir(parents=True)
            (trial_dir / "verifier" / "verify.log").write_text(
                "FAIL: outdir mismatch: scf '' vs bands '' — value 1.23D+03",
                encoding="utf-8",
            )

            res = audit_contract(task_dir=task_dir, trial_dir=trial_dir)
            alignments = {c["alignment"] for c in res["contract_observations"]}
            self.assertIn("case_defect", alignments)
            self.assertIn("verifier_defect", alignments)
            hazard_subtypes = {v["matched_text"] for v in res["verifier_observations"]}
            self.assertIn("namelist_slash_truncation", hazard_subtypes)
            self.assertIn("scientific_notation_missing_d_exponent", hazard_subtypes)

    def test_unparseable_verify_py_records_a_diagnostic(self) -> None:
        """
        An unparseable `tests/verify.py` silently yields no AST-derived hazards.
        That recovery is correct, but it must be recorded: an empty hazard list
        otherwise reads as "the verifier looks clean", which can let a verifier
        defect fall through to an agent attribution.
        """
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp) / "tasks" / "broken-verifier"
            (task_dir / "tests").mkdir(parents=True)
            (task_dir / "tests" / "verify.py").write_text("def f(:\n", encoding="utf-8")

            res = audit_contract(task_dir=task_dir, trial_dir=None)

            codes = [d["code"] for d in res["diagnostics"]]
            self.assertIn("VERIFIER_AST_PARSE_FAILED", codes)
            record = next(d for d in res["diagnostics"] if d["code"] == "VERIFIER_AST_PARSE_FAILED")
            self.assertEqual(record["severity"], "warning")
            self.assertEqual(record["error_type"], "SyntaxError")
            self.assertTrue(record["impact"], "diagnostic must state its causal impact")
            self.assertLessEqual(len(record["error_message"]), 420)

    def test_build_restored_assets_require_the_manifest_and_all_parts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp)
            environment = task / "environment"
            assets = environment / "assets"
            runtime = assets / "_runtime"
            runtime.mkdir(parents=True)
            (task / "instruction.md").write_text(
                "Read `/app/environment/assets/series.traj` and `assets/unlisted.xyz`."
            )
            (runtime / "assets.json").write_text(
                json.dumps(
                    {
                        "assets": [
                            {
                                "path": "assets/series.traj",
                                "parts": [
                                    {"path": "assets/series.traj.gz.000", "bytes": 3},
                                    {"path": "assets/series.traj.gz.001", "bytes": 4},
                                ],
                            }
                        ]
                    }
                )
            )
            (runtime / "restore.py").write_text('MANIFEST = "assets.json"\n')
            (assets / "series.traj.gz.000").write_bytes(b"one")
            fragment = assets / "series.traj.gz.001"
            fragment.write_bytes(b"next")
            dockerfile = environment / "Dockerfile"
            copies = (
                "COPY assets/_runtime/assets.json assets/_runtime/restore.py /app/environment/\n"
            )
            build = "RUN mkdir -p /tmp && \\\n    python3 /app/environment/restore.py\n"

            def missing():
                return {
                    item["item"]
                    for item in audit_contract(task, None)["contract_observations"]
                    if item["alignment"] == "case_defect"
                }

            dockerfile.write_text(copies + build)
            self.assertEqual(missing(), {"asset:unlisted.xyz"})
            fragment.write_bytes(b"bad")
            self.assertEqual(missing(), {"asset:series.traj", "asset:unlisted.xyz"})
            fragment.write_bytes(b"next")
            dockerfile.write_text(copies)
            self.assertEqual(missing(), {"asset:series.traj", "asset:unlisted.xyz"})

    def test_clean_verify_py_records_no_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp) / "tasks" / "clean-verifier"
            (task_dir / "tests").mkdir(parents=True)
            (task_dir / "tests" / "verify.py").write_text(
                "import re\nVAL = re.compile(r'V=([\\d.]+)')\n", encoding="utf-8"
            )
            res = audit_contract(task_dir=task_dir, trial_dir=None)
            self.assertEqual(res["diagnostics"], [])

    def test_inspect_delivered_artifacts_detects_empty_nan_inf_and_missing_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            trial_dir = Path(tmp) / "trial"
            outputs = trial_dir / "artifacts" / "app" / "outputs"
            outputs.mkdir(parents=True)
            (outputs / "empty_list.json").write_text("[]", encoding="utf-8")
            (outputs / "empty_table.csv").write_text("site,charge_e\n", encoding="utf-8")
            (outputs / "energies.tsv").write_text(
                "step\tenergy\n1\tNaN\n2\t-Infinity\n",
                encoding="utf-8",
            )
            (outputs / "results.json").write_text(
                json.dumps({"values": {"energy": -10.5, "force": float("nan")}}),
                encoding="utf-8",
            )

            diag = inspect_delivered_artifacts(trial_dir, checked_keys=["energy", "band_gap"])

        self.assertEqual(len(diag["scanned_files"]), 4)
        empty_files = {item["file"]: item["kind"] for item in diag["empty_deliverables"]}
        self.assertEqual(
            empty_files,
            {
                "artifacts/app/outputs/empty_list.json": "empty_json",
                "artifacts/app/outputs/empty_table.csv": "empty_table",
            },
        )
        non_finite_files = {item["file"] for item in diag["nan_or_inf_values"]}
        self.assertIn("artifacts/app/outputs/energies.tsv", non_finite_files)
        self.assertIn("artifacts/app/outputs/results.json", non_finite_files)
        self.assertIn("energy", diag["results_json_keys"])
        self.assertEqual(diag["missing_checked_keys"], ["band_gap"])

    def test_audit_contract_emits_deliverable_observations_only_when_log_confirms(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp) / "tasks" / "sample"
            (task_dir / "tests").mkdir(parents=True)
            (task_dir / "instruction.md").write_text(
                'Write `results.json`:\n```json\n{"values": {"energy": -10.0, "band_gap": 1.1}}\n```',
                encoding="utf-8",
            )
            (task_dir / "tests" / "verify.py").write_text(
                "import json\n"
                "data = json.load(open('results.json'))\n"
                "val = data['energy']\n"
                "gap = data['band_gap']\n",
                encoding="utf-8",
            )

            # 1. Passing / unrelated verify.log must NOT emit empty/NaN contract observations
            trial_unrelated = Path(tmp) / "trial_unrelated"
            (trial_unrelated / "artifacts").mkdir(parents=True)
            (trial_unrelated / "verifier").mkdir(parents=True)
            (trial_unrelated / "artifacts" / "table.csv").write_text(
                "col1,col2\n", encoding="utf-8"
            )
            (trial_unrelated / "artifacts" / "results.json").write_text(
                json.dumps({"energy": float("nan")}), encoding="utf-8"
            )
            (trial_unrelated / "verifier" / "verify.log").write_text(
                "PASS: all checks passed", encoding="utf-8"
            )
            res_unrelated = audit_contract(task_dir=task_dir, trial_dir=trial_unrelated)
            self.assertIn("deliverable_diagnostics", res_unrelated)
            self.assertEqual(
                [
                    c
                    for c in res_unrelated["contract_observations"]
                    if c["alignment"] == "agent_mismatch"
                ],
                [],
            )

            # 2. Confirmed empty + NaN + schema mismatch in verify.log DOES emit agent_mismatch observations
            trial_confirmed = Path(tmp) / "trial_confirmed"
            (trial_confirmed / "artifacts").mkdir(parents=True)
            (trial_confirmed / "verifier").mkdir(parents=True)
            (trial_confirmed / "artifacts" / "table.csv").write_text(
                "col1,col2\n", encoding="utf-8"
            )
            (trial_confirmed / "artifacts" / "results.json").write_text(
                json.dumps({"energy": float("nan")}), encoding="utf-8"
            )
            (trial_confirmed / "verifier" / "verify.log").write_text(
                "FAIL: table.csv is empty (0 rows); NaN detected in energy; schema mismatch in results.json",
                encoding="utf-8",
            )
            res_confirmed = audit_contract(task_dir=task_dir, trial_dir=trial_confirmed)
            statuses = {c["agent_output_status"] for c in res_confirmed["contract_observations"]}
            self.assertIn("empty_deliverable", statuses)
            self.assertIn("non_finite_value", statuses)
            self.assertIn("missing_in_delivered_results_json", statuses)


if __name__ == "__main__":
    unittest.main()
