"""Review summaries use scoped trial identity and portable output links."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from review_summary import build_summary, main


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


class ReviewSummaryTests(unittest.TestCase):
    def test_validated_reviews_dedupe_across_bundles_and_use_relative_links(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            audit = root / "audit" / "job" / "task7"
            evidence = {
                "job_dir": str(root / "jobs" / "job"),
                "trial_dir": str(root / "jobs" / "job" / "trial"),
                "trial_name": "trial",
                "case_id": "task7",
                "runtime": {"score_produced": True},
            }
            analysis = {
                "verdict": "failed",
                "primary_root_cause": {"category": "unknown", "summary": "Wrong value"},
            }
            write(audit / "data/evidence.json", evidence)
            write(audit / "data/analysis.json", analysis)
            (audit / "task7.md").write_text("Audit.")
            dossier = root / "dossiers" / "job" / "task7"
            write(dossier / "dossier.json", {"trial_name": "trial"})
            (dossier / "dossier.md").write_text("Dossier.")
            for name in ("one", "two", "invalid"):
                bundle = root / "reviews" / name
                review = {
                    "verdict": "failed",
                    "primary_root_cause": {"category": "agent", "summary": "Wrong column"},
                    "causal_assessment": {
                        "level": "evidence_supported_cause",
                        "decision": {"event_ref": "trajectory:step:1"},
                        "capability": {
                            "diagnosis": {
                                "dimension": "postprocessing_provenance",
                                "mechanism_tag": "wrong_derived_column_mapping",
                            }
                        },
                    },
                }
                for basename, value in (
                    ("review", {}),
                    ("evidence", evidence),
                    ("analysis", review),
                    ("original-analysis", analysis),
                ):
                    write(bundle / f"{basename}.json", value)
                (bundle / "report.zh.md").write_text("Review.")

            def validate(evidence, review, review_root):
                return (False, ["invalid binding"]) if review_root.name == "invalid" else (True, [])

            out = root / "output"
            with patch("review_summary.validate_all", validate):
                main(
                    [
                        "--audit-root",
                        str(root / "audit"),
                        "--review-root",
                        str(root / "reviews"),
                        "--dossier-root",
                        str(root / "dossiers"),
                        "--output",
                        str(out),
                    ]
                )
            report = (out / "review-summary.json").read_text()
            self.assertNotIn(str(root), report)
            data = json.loads(report)
            self.assertEqual(data["supported_categories"], {"agent": 1})
            self.assertEqual(data["mechanisms"][0]["independent_cases"], 1)
            self.assertEqual(len(data["rejected_reviews"]), 1)
            self.assertEqual(data["rows"][0]["audit"], "../audit/job/task7/task7.md")

    def test_unreviewed_runtime_trial_is_scheduled_without_assigning_supported_cause(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            audit = root / "audit" / "task7"
            write(
                audit / "evidence.json",
                {
                    "trial_dir": str(root / "trial"),
                    "trial_name": "trial",
                    "runtime": {"score_produced": False},
                    "error_observations": [
                        {"code": "INFRA_AGENT_TIMEOUT", "causal_candidate": True}
                    ],
                },
            )
            write(
                audit / "analysis.json",
                {
                    "verdict": "errored",
                    "primary_root_cause": {"category": "infra"},
                },
            )
            write(root / "dossiers" / "task7" / "dossier.json", {"trial_name": "trial"})
            data = build_summary(root / "audit", dossier_root=root / "dossiers")
            self.assertEqual(data["queues"], {"runtime": 1})
            self.assertEqual(data["supported_categories"], {})
