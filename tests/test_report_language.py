"""Language editions preserve authored text without presenting it as translation."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from render_report import render_report_markdown
from render_verdict import build_verdict_card, render_verdict_card_markdown
from report_style import format_confidence_breakdown, localized_field, report_text


class ReportLanguageTests(unittest.TestCase):
    def setUp(self):
        self.evidence = {
            "case_id": "case-language",
            "trial_name": "trial-language",
            "runtime": {"agent_started": True, "verifier_started": True, "reward": 0.0},
            "verifier_report": {
                "failed_checks": [{"name": "energy_check", "detail": "能量不匹配"}],
                "primary_report": "verification.json",
            },
        }
        self.analysis = {
            "verdict": "failed",
            "primary_root_cause": {
                "category": "unknown",
                "subtype": "insufficient_evidence",
                "confidence": 0.0,
                "summary": "原始分析说明",
                "summary_zh": "明确的中文摘要",
                "summary_en": "Explicit English summary",
                "confidence_breakdown": {
                    "points": 0,
                    "denominator": 6,
                    "factors": [
                        {
                            "key": "missing_evidence",
                            "delta": -2,
                            "label_zh": "缺失关键证据",
                            "label_en": "Missing key evidence",
                        }
                    ],
                    "missing_to_raise": ["验证器未提供定量数值偏差与容差比值"],
                },
            },
            "failure_manifestation": {"summary": "验证失败"},
            "first_unrecovered_deviation": {"summary": "尚未定位偏差"},
            "competing_hypotheses": [
                {
                    "hypothesis_id": "H1",
                    "category": "unknown",
                    "subtype": "insufficient_evidence",
                    "confidence": 0.0,
                    "claim": "候选说明",
                    "claim_en": "An explicitly authored candidate",
                }
            ],
            "recommended_actions": [
                {
                    "owner": "Infra",
                    "action": "Inspect detailed verifier diagnostic logs or review task specification to disambiguate competing hypotheses.",
                }
            ],
            "skill_prescription": {
                "recommended_skill": {"name": "example", "capability_gap": ["待核查的能力缺口"]}
            },
        }

    def test_explicit_summaries_and_source_quotations_in_all_editions(self):
        for lang in ("zh", "en", "bilingual"):
            report = render_report_markdown(self.evidence, self.analysis, lang=lang)
            card = render_verdict_card_markdown(
                build_verdict_card(self.evidence, self.analysis), lang=lang
            )
            expected = "Explicit English summary" if lang == "en" else "明确的中文摘要"
            self.assertIn(expected, report)
            self.assertIn(expected, card)
            self.assertNotIn("原始分析说明", report)
            self.assertIn("能量不匹配", report)
            self.assertIn(
                "Source quotation (original language)"
                if lang == "en"
                else "原文引用（保留原语言）",
                report,
            )
            self.assertEqual(sum(line.startswith("## ") for line in report.splitlines()), 4)

    def test_english_report_from_chinese_analysis_labels_untranslated_fields(self):
        report = render_report_markdown(self.evidence, self.analysis, lang="en")
        self.assertIn("Source quotation (original language): “尚未定位偏差”", report)
        self.assertIn("Source quotation (original language): “待核查的能力缺口”", report)
        self.assertIn("An explicitly authored candidate", report)
        self.assertIn(
            "The verifier did not provide a quantitative deviation-to-tolerance ratio", report
        )
        self.assertNotIn("验证器未提供定量数值偏差与容差比值", report)
        card = render_verdict_card_markdown(
            build_verdict_card(self.evidence, self.analysis), lang="en"
        )
        self.assertNotIn("验证器未提供定量数值偏差与容差比值", card)

    def test_chinese_stock_actions_translate_and_authored_actions_are_labeled(self):
        report = render_report_markdown(self.evidence, self.analysis, lang="zh")
        self.assertIn("检查详细的验证器诊断日志或核对题目规格，以区分竞争假设。", report)
        self.assertNotIn("Inspect detailed verifier diagnostic logs", report)
        self.analysis["recommended_actions"][0].update(
            action="Inspect this particular saved artifact", action_zh="核查这一份保存的产物"
        )
        for renderer in (
            lambda: render_report_markdown(self.evidence, self.analysis, lang="zh"),
            lambda: render_verdict_card_markdown(
                build_verdict_card(self.evidence, self.analysis), lang="zh"
            ),
        ):
            self.assertIn("核查这一份保存的产物", renderer())
        self.analysis["recommended_actions"][0].pop("action_zh")
        self.assertIn(
            "原文引用（保留原语言）: “Inspect this particular saved artifact”",
            render_report_markdown(self.evidence, self.analysis, lang="zh"),
        )

    def test_supported_nonagent_report_distinguishes_review_from_original_rule_score(self):
        self.analysis["primary_root_cause"].update(
            category="verifier", subtype="regex_or_parser_defect"
        )
        self.analysis["competing_hypotheses"][0].update(
            hypothesis_id="H_REVIEW", category="verifier", subtype="regex_or_parser_defect"
        )
        contract = {
            "review_items": [
                {
                    "category": "semantic_rejection",
                    "field": "value",
                    "summary": "Retained original observation",
                }
            ]
        }
        self.evidence["contract_review"] = contract
        self.evidence["causal_anchors"] = {"parameter": {"event_ref": "trajectory:step:1"}}
        self.analysis["causal_assessment"] = {
            "level": "evidence_supported_cause",
            "contract_review": contract,
        }
        with patch(
            "trace_causal_anchors.render_causal_anchors_markdown", return_value=["AGENT ANCHOR"]
        ):
            for lang in ("zh", "en", "bilingual"):
                report = render_report_markdown(self.evidence, self.analysis, lang=lang)
                self.assertIn("Reviewed attribution" if lang == "en" else "审查归因", report)
                self.assertIn(
                    "Original contract observation" if lang == "en" else "原评分契约观测", report
                )
                self.assertNotIn("**Rule candidate**", report)
                self.assertNotIn("**Contract review required**", report)
                self.assertNotIn("AGENT ANCHOR", report)
                hypothesis = next(line for line in report.splitlines() if "H_REVIEW" in line)
                self.assertNotIn("0.00", hypothesis)
            self.analysis["causal_assessment"]["level"] = "candidate_cause"
            candidate = render_report_markdown(self.evidence, self.analysis, lang="en")
            self.assertIn("**Rule candidate**", candidate)
            self.assertIn("**Contract review required**", candidate)
            self.assertIn("AGENT ANCHOR", candidate)
            self.assertIn(
                "confidence `0.00`",
                next(line for line in candidate.splitlines() if "H_REVIEW" in line),
            )

    def test_pending_reports_do_not_assign_failure_or_passed_status(self):
        self.analysis["verdict"] = "pending"
        for lang, status in (
            ("zh", "复核状态"),
            ("en", "Review status"),
            ("bilingual", "复核状态 (Review status)"),
        ):
            report = render_report_markdown(self.evidence, self.analysis, lang=lang)
            self.assertIn("## 1. " + status, report)
            self.assertEqual(sum(line.startswith("## ") for line in report.splitlines()), 2)
            self.assertNotIn("**Attribution**", report)
            self.assertNotIn("**归因判断**", report)
            self.assertNotIn("Root Cause Verdict", report)

    def test_verifier_startup_does_not_imply_scoring_or_completion(self):
        self.evidence["runtime"].update(score_produced=False, reward=None)
        self.assertFalse(build_verdict_card(self.evidence, self.analysis)["scored"])
        self.evidence["runtime"]["score_produced"] = True
        self.assertTrue(build_verdict_card(self.evidence, self.analysis)["scored"])
        self.evidence["runtime"].pop("score_produced")
        self.evidence["runtime"]["reward"] = 0.0
        self.assertTrue(build_verdict_card(self.evidence, self.analysis)["scored"])
        self.analysis["excluded_hypotheses"] = [{"category": "infra", "reason": "Startup observed"}]
        rendered = render_verdict_card_markdown(
            build_verdict_card(self.evidence, self.analysis), lang="en"
        )
        self.assertNotIn("ran to completion", rendered)
        self.assertIn("verifier startup was recorded", rendered)

    def test_shared_helpers_preserve_explicit_fields_and_unknown_source(self):
        self.assertEqual(
            localized_field(
                {"summary": "原始摘要", "summary_en": "Authored English"}, "summary", "en"
            ),
            "Authored English",
        )
        self.assertEqual(report_text("unrecognized_schema_code", "zh"), "unrecognized_schema_code")
        self.assertEqual(
            report_text("原始摘要", "en"), "Source quotation (original language): “原始摘要”"
        )
        row = format_confidence_breakdown(
            {
                "points": 0,
                "factors": [{"delta": 0, "key": "untranslated", "label_zh": "仅有中文"}],
                "missing_to_raise_en": ["Review the evidence"],
                "missing_to_raise": ["不应输出"],
            },
            0.0,
            "en",
        )
        self.assertNotIn("None", row)
        self.assertNotIn("不应输出", row)
        self.assertIn("Review the evidence", row)


if __name__ == "__main__":
    unittest.main()
