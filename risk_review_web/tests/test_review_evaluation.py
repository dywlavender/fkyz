from __future__ import annotations

import unittest

from risk_review_web.services.review_evaluation import (
    expected_matches_verdict,
    validation_expected_code,
)
from risk_review_web.services.validation_store import validate_example


class ReviewEvaluationTest(unittest.TestCase):
    def test_normalizes_descriptive_expected_conclusions(self):
        cases = {
            "pass": "pass",
            "pass（正向）": "pass",
            "passed - positive": "pass",
            "✅ 通过（完整逻辑）": "pass",
            "预期 fail": "fail",
            "failure：字段错配": "fail",
            "❌ 不通过，需修改": "fail",
            "unreviewable：缺少输入": "unreviewable",
            "⛔ 无法评审": "unreviewable",
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                self.assertEqual(validation_expected_code(source), expected)

    def test_does_not_match_english_fragments_inside_words(self):
        for source in ("compassion", "bypass", "passenger"):
            with self.subTest(source=source):
                self.assertEqual(validation_expected_code(source), source)

    def test_unknown_expected_conclusion_fails_closed(self):
        self.assertFalse(expected_matches_verdict({"expected": "随机文本"}, "pass"))
        self.assertFalse(expected_matches_verdict({"expected": "compassion"}, "pass"))
        self.assertTrue(expected_matches_verdict({"expected": ""}, "fail"))

    def test_validation_rejects_unknown_expected_conclusion(self):
        example = {
            "title": "测试案例",
            "factorName": "测试因子",
            "requirement": "测试需求",
            "apiNames": ["授信申请接口"],
            "draft": "【加工逻辑】\n读取字段。",
            "expected": "随机文本",
        }
        self.assertIn(
            "期望结论无法识别，请填写通过、不通过或无法评审",
            validate_example(example),
        )


if __name__ == "__main__":
    unittest.main()
