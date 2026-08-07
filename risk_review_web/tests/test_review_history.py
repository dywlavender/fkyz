from __future__ import annotations

import unittest

from risk_review_web.services.review_history import _task_to_review_record, normalize_review_item


FAIL_REPORT = """# 评审报告

## 总体结论

| 项目 | 内容 |
|---|---|
| 总体结论 | ❌ 不通过，需修改 |
| 问题统计 | 不符合 2 项 |

## 汇总问题清单
"""


class ReviewHistoryBatchDetailTest(unittest.TestCase):
    def test_normalizes_batch_item_conclusion_and_risk_count_from_raw_report(self):
        item = normalize_review_item({
            "index": 0,
            "factorName": "测试因子",
            "status": "done",
            "raw": FAIL_REPORT,
        })

        self.assertEqual(item["conclusion"], "fail")
        self.assertEqual(item["riskCount"], 2)

    def test_task_record_exposes_normalized_batch_item_result(self):
        record = _task_to_review_record({
            "id": "batch-detail-test",
            "taskType": "batch",
            "status": "done",
            "created": 1,
            "updated": 2,
            "result": {
                "items": [{
                    "index": 0,
                    "factorName": "测试因子",
                    "status": "done",
                    "raw": FAIL_REPORT,
                }],
            },
        })

        self.assertIsNotNone(record)
        self.assertEqual(record["conclusion"], "fail")
        self.assertEqual(record["riskCount"], 2)
        self.assertEqual(record["items"][0]["conclusion"], "fail")
        self.assertEqual(record["items"][0]["riskCount"], 2)

    def test_terminal_error_item_is_explicitly_unreviewable(self):
        item = normalize_review_item({"status": "error", "raw": "模型调用失败"})

        self.assertEqual(item["conclusion"], "unreviewable")
        self.assertEqual(item["riskCount"], 0)


if __name__ == "__main__":
    unittest.main()
