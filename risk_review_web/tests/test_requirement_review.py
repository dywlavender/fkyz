from __future__ import annotations

import json
import shutil
import unittest

from risk_review_web.artifacts import (
    normalize_requirement_review_output,
    requirement_review_conclusion,
    validate_requirement_review_basic_output,
)
from risk_review_web.paths import REQUIREMENT_REVIEW_SPEC_PATH, ROOT
from risk_review_web.services.requirement_review import create_requirement_review_workspace


VALID_REPORT = """# 需求评审报告

## 总体结论

| 项目 | 内容 |
|---|---|
| 总体结论 | ❌ 不通过，需修改 |
| 问题统计 | 不符合 1 项 |

## 问题清单

| 序号 | 核心问题 | 需求原文 | 修改建议 |
|---|---|---|---|
| 1 | 字段枚举与接口文档不一致 | “状态为 3” | 按接口文档修正枚举 |
"""


class RequirementReviewArtifactTest(unittest.TestCase):
    def test_spec_treats_api_document_as_input_contract_by_default(self):
        spec = json.loads(REQUIREMENT_REVIEW_SPEC_PATH.read_text(encoding="utf-8"))
        policy = spec["interface_document_policy"]
        self.assertEqual(policy["default_role"], "input_data_contract")
        self.assertTrue(policy["forbid_inferred_invocation"])
        capability_rule = next(
            item for item in spec["issue_types"] if item["id"] == "interface_capability_missing"
        )
        self.assertIn("明确要求系统主动调用", capability_rule["applicable_when"])
        self.assertIn("不得因缺少请求参数", capability_rule["check"])

    def test_accepts_complete_report_and_extracts_binary_conclusion(self):
        validate_requirement_review_basic_output(VALID_REPORT)
        self.assertEqual(requirement_review_conclusion(VALID_REPORT), "不通过")

    def test_normalizes_markdown_fence(self):
        wrapped = f"检查完成。\n```markdown\n{VALID_REPORT}\n```\n"
        self.assertEqual(normalize_requirement_review_output(wrapped), VALID_REPORT.strip())

    def test_rejects_missing_section(self):
        with self.assertRaisesRegex(ValueError, "缺少必要章节"):
            validate_requirement_review_basic_output(VALID_REPORT.replace("## 问题清单", "## 其他"))

    def test_rejects_non_binary_conclusion(self):
        with self.assertRaisesRegex(ValueError, "通过.*不通过"):
            validate_requirement_review_basic_output(VALID_REPORT.replace("❌ 不通过，需修改", "无法评审"))

    def test_accepts_pass_conclusion_and_returns_canonical_value(self):
        report = VALID_REPORT.replace("❌ 不通过，需修改", "✅ 通过")
        validate_requirement_review_basic_output(report)
        self.assertEqual(requirement_review_conclusion(report), "通过")

    def test_rejects_missing_issue_table(self):
        report = VALID_REPORT.replace("| 序号 | 核心问题 | 需求原文 | 修改建议 |", "字段枚举与文档不一致。")
        with self.assertRaisesRegex(ValueError, "问题清单.*Markdown 表格"):
            validate_requirement_review_basic_output(report)

    def test_isolated_workspace_contains_only_skill_spec_and_selected_api_docs(self):
        workspace, documents = create_requirement_review_workspace(
            "requirement-review-test",
            ["授信申请接口"],
        )
        try:
            staged = {
                path.relative_to(workspace).as_posix()
                for path in workspace.rglob("*")
                if path.is_file()
            }
            expected_api_paths = {str(item.get("path") or "") for item in documents}
            self.assertTrue(expected_api_paths)
            self.assertTrue(expected_api_paths.issubset(staged))
            self.assertIn(
                "risk_review_web/prompts/requirement-review-skill/requirement_review.md",
                staged,
            )
            self.assertIn(
                "risk_review_web/prompts/requirement-review-skill/spec/requirement_review_spec.json",
                staged,
            )
            self.assertFalse(any("phase1" in path or "phase2" in path for path in staged))
            self.assertTrue(all((ROOT / path).is_file() for path in staged))
        finally:
            shutil.rmtree(workspace, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
