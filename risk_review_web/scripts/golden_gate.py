from __future__ import annotations

import argparse
import json
import re
import time
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES_PATH = PROJECT_ROOT / "risk_review_web" / "data" / "examples.json"
GOLDEN_PATH = PROJECT_ROOT / "risk_review_web" / "data" / "golden_cases.json"
LOCAL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
TERMINAL_STATUSES = {"done", "error", "cancelled"}


def load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def load_golden() -> tuple[dict, dict[str, dict]]:
    manifest = load_json(GOLDEN_PATH)
    examples = load_json(EXAMPLES_PATH)
    if not isinstance(manifest, dict) or manifest.get("schema_version") != "risk_review_golden_cases_v1":
        raise ValueError("黄金案例清单 schema_version 无效。")
    if not isinstance(examples, list):
        raise ValueError("examples.json 必须是数组。")
    by_id = {str(item.get("id") or ""): item for item in examples if isinstance(item, dict)}
    frozen = manifest.get("case_versions") or {}
    if not isinstance(frozen, dict) or not frozen:
        raise ValueError("黄金案例清单不能为空。")
    errors = []
    for case_id, version in frozen.items():
        example = by_id.get(str(case_id))
        if not example:
            errors.append(f"{case_id}: 案例不存在")
        elif str(example.get("version") or "") != str(version):
            errors.append(
                f"{case_id}: 版本已变化（冻结={version}，当前={example.get('version') or '空'}）"
            )
    if errors:
        raise ValueError("黄金案例冻结检查失败：\n- " + "\n- ".join(errors))
    return manifest, {case_id: by_id[case_id] for case_id in frozen}


def request_json(base_url: str, path: str, payload: dict | None = None) -> dict:
    data = None
    headers = {}
    method = "GET"
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
        method = "POST"
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=data,
        headers=headers,
        method=method,
    )
    with LOCAL_OPENER.open(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def wait_task(base_url: str, task_id: str, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = request_json(base_url, f"/api/tasks/{task_id}").get("task") or {}
        if task.get("status") in TERMINAL_STATUSES:
            return task
        time.sleep(0.5)
    raise TimeoutError(f"黄金验证任务超时：{task_id}")


def execute_round(base_url: str, case_ids: list[str], timeout: float) -> list[dict]:
    response = request_json(
        base_url,
        "/api/validation/regression",
        {"exampleIds": case_ids, "taskName": "黄金验证集门禁"},
    )
    task = wait_task(base_url, str(response["taskId"]), timeout)
    if task.get("status") != "done":
        raise RuntimeError(f"黄金验证任务失败：{(task.get('result') or {}).get('stderr') or task}")
    items = (task.get("result") or {}).get("items") or []
    if len(items) != len(case_ids):
        raise AssertionError(f"黄金验证结果数量不完整：期望 {len(case_ids)}，实际 {len(items)}")
    return items


def result_signature(item: dict) -> tuple[str, int, tuple[str, ...]]:
    raw = str(item.get("raw") or "")
    rule_ids = tuple(sorted(set(re.findall(r"\b(?:SOURCE|SEMANTIC|FIELD|TIME|FILTER|ITERATE|AGGREGATION|DERIVATION|BRANCH|DEFAULT)-\d+\b", raw))))
    issue_count = int((item.get("summary") or {}).get("fail") or 0)
    return str(item.get("verdict") or ""), issue_count, rule_ids


def evaluate(manifest: dict, rounds: list[list[dict]]) -> dict:
    gate = manifest.get("gate") or {}
    by_case: dict[str, list[dict]] = defaultdict(list)
    for items in rounds:
        for item in items:
            by_case[str(item.get("caseId") or item.get("id") or "")].append(item)

    mismatched = 0
    missing_patterns = 0
    forbidden_hits = 0
    unstable_conclusion = []
    unstable_issue_count = []
    unstable_rule_ids = []
    cases = []
    for case_id, items in sorted(by_case.items()):
        signatures = [result_signature(item) for item in items]
        mismatched += sum(1 for item in items if not item.get("expectedOk"))
        missing_patterns += sum(
            len((item.get("issueEvaluation") or {}).get("missingIssuePatterns") or [])
            for item in items
        )
        forbidden_hits += sum(
            len((item.get("issueEvaluation") or {}).get("forbiddenIssueHits") or [])
            for item in items
        )
        conclusions = {value[0] for value in signatures}
        issue_counts = {value[1] for value in signatures}
        rule_sets = {value[2] for value in signatures}
        if len(conclusions) > 1:
            unstable_conclusion.append(case_id)
        if len(issue_counts) > 1:
            unstable_issue_count.append(case_id)
        if len(rule_sets) > 1:
            unstable_rule_ids.append(case_id)
        cases.append({
            "caseId": case_id,
            "runs": len(items),
            "matched": sum(1 for item in items if item.get("expectedOk")),
            "signatures": [
                {"conclusion": value[0], "issueCount": value[1], "ruleIds": list(value[2])}
                for value in signatures
            ],
        })

    failures = []
    if mismatched > int(gate.get("max_mismatched", 0)):
        failures.append(f"预期不匹配 {mismatched} 次")
    if missing_patterns > int(gate.get("max_missing_expected_patterns", 0)):
        failures.append(f"漏报期望问题 {missing_patterns} 项")
    if forbidden_hits > int(gate.get("max_forbidden_hits", 0)):
        failures.append(f"命中禁止问题 {forbidden_hits} 项")
    if gate.get("require_stable_conclusion") and unstable_conclusion:
        failures.append("结论不稳定：" + "、".join(unstable_conclusion))
    if gate.get("require_stable_issue_count") and unstable_issue_count:
        failures.append("问题数量不稳定：" + "、".join(unstable_issue_count))
    if gate.get("require_stable_rule_ids") and unstable_rule_ids:
        failures.append("问题类型不稳定：" + "、".join(unstable_rule_ids))
    return {
        "passed": not failures,
        "summary": {
            "cases": len(by_case),
            "runs": sum(len(items) for items in by_case.values()),
            "mismatched": mismatched,
            "missingExpectedPatterns": missing_patterns,
            "forbiddenHits": forbidden_hits,
        },
        "stability": {
            "conclusion": unstable_conclusion,
            "issueCount": unstable_issue_count,
            "ruleIds": unstable_rule_ids,
        },
        "failures": failures,
        "cases": cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="检查或执行人工冻结的黄金验证集门禁。")
    parser.add_argument("--base-url", help="已启动的评审服务地址；省略时只检查黄金案例冻结状态。")
    parser.add_argument("--repetitions", type=int, help="覆盖清单中的重复次数。")
    parser.add_argument("--timeout", type=float, default=1800.0)
    parser.add_argument("--output", type=Path, help="可选 JSON 报告路径。")
    args = parser.parse_args()

    manifest, examples = load_golden()
    print(f"[PASS] 黄金案例冻结检查：{len(examples)} 条")
    if not args.base_url:
        return 0

    repetitions = args.repetitions or int((manifest.get("gate") or {}).get("repetitions", 3))
    if repetitions < 1:
        raise ValueError("重复次数必须大于 0。")
    case_ids = list(examples)
    rounds = []
    for index in range(repetitions):
        print(f"[RUN ] 黄金验证第 {index + 1}/{repetitions} 轮", flush=True)
        rounds.append(execute_round(args.base_url, case_ids, args.timeout))
    report = evaluate(manifest, rounds)
    report.update({"manifestVersion": manifest.get("version"), "repetitions": repetitions})
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (urllib.error.URLError, TimeoutError, RuntimeError, AssertionError, ValueError) as exc:
        print(f"[FAIL] {exc}")
        raise SystemExit(1)
