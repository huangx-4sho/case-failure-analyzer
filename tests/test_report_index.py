"""Index links follow actual report locations across jobs and legacy layouts."""

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from render_index import update_failure_report_index


class ReportIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "failure-report"
        self.root.mkdir()

    def put(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value) if isinstance(value, dict) else value)

    def task(self, folder, modern=False, analysis=True):
        directory = self.root / folder
        data = directory / "data" if modern else directory
        self.put(
            data / "evidence.json",
            {
                "job_dir": "/saved/jobs/real-job",
                "runtime": {"verification_status": "failed", "score_produced": True, "reward": 0.0},
            },
        )
        if analysis:
            self.put(
                data / "analysis.json",
                {
                    "case_id": "scientific/name-not-a-directory",
                    "verdict": "failed",
                    "primary_root_cause": {
                        "category": "agent",
                        "summary": "saved finding",
                        "confidence": 0.8,
                    },
                },
            )
            self.put(directory / (directory.name + ".md" if modern else "report.md"), "# report")
        self.put(directory / "deep-review.md", "# review")
        return directory

    def assert_links_exist(self, index):
        text = index.read_text()
        for link in re.findall(r"\]\(([^)]+)\)", text):
            self.assertTrue((index.parent / unquote(link)).exists(), link)
        return text

    def test_global_and_per_job_indexes_keep_identical_task_ids_separate(self):
        self.task("job-a/task078", modern=True)
        self.task("job-b/task078")
        index = update_failure_report_index(self.root)
        text = self.assert_links_exist(index)
        self.assertIn("| Job | Task ID |", text)
        self.assertIn("[`job-a`](job-a/INDEX.md)", text)
        self.assertIn("[报告](job-a/task078/task078.md)", text)
        self.assertIn("[报告](job-b/task078/report.md)", text)
        self.assertIn("[证据](job-b/task078/evidence.json)", text)
        self.assertIn("[归因数据](job-b/task078/analysis.json)", text)
        self.assertIn("[复核](job-b/task078/deep-review.md)", text)
        self.assertIn("Total**: `2`", text)
        for job in ("job-a", "job-b"):
            job_text = self.assert_links_exist(self.root / job / "INDEX.md")
            self.assertNotIn(job + "/task078/", job_text)
            self.assertIn("Total**: `1`", job_text)

    def test_legacy_flat_reports_and_collect_only_deep_reviews_are_discoverable(self):
        self.task("task078")
        collected = self.task("job-c/task009", analysis=False)
        text = self.assert_links_exist(update_failure_report_index(self.root))
        self.assertIn("[报告](task078/report.md)", text)
        self.assertNotIn("scientific/name-not-a-directory/", text)
        self.assertIn("[复核](job-c/task009/deep-review.md)", text)
        self.assertIn("[证据](job-c/task009/evidence.json)", text)
        self.assertIn("Collected evidence; attribution not reviewed", text)
        self.assertNotIn("job-c/task009/report.md", text)
        # Task archives must not become extra top-level cases.
        self.put(collected / "trials/old/analysis.json", {"verdict": "passed"})
        self.assertIn("Total**: `2`", update_failure_report_index(self.root).read_text())

    def test_directory_names_are_url_encoded_and_scoring_comes_from_evidence(self):
        self.task("job space/task space")
        evidence = self.root / "job space/task space/evidence.json"
        self.put(evidence, {"runtime": {"verification_status": "errored", "score_produced": False}})
        text = self.assert_links_exist(update_failure_report_index(self.root))
        self.assertIn("job%20space/task%20space/report.md", text)
        self.assertIn("Failed**: `0`", text)
        self.assertIn("Errored & Unscored**: `1`", text)

    def test_empty_root_and_symlinked_directories_are_not_report_cases(self):
        external = Path(self.temp.name) / "external"
        self.put(external / "analysis.json", {"verdict": "failed"})
        (self.root / "external-link").symlink_to(external, target_is_directory=True)
        self.assertIn("Total**: `0`", update_failure_report_index(self.root).read_text())


if __name__ == "__main__":
    unittest.main()
