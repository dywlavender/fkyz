from __future__ import annotations

import re


def string_list(value) -> list[str]:
    """Normalize a list-like input while accepting common Chinese separators."""
    if value is None:
        return []
    if isinstance(value, str):
        value = re.split(r"[\n,，、]", value)
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def optional_nonnegative_int(value) -> int | None:
    """Parse an optional non-negative integer; invalid values remain unset."""
    if value in (None, ""):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def validation_expected_code(value: str | None) -> str:
    """Normalize descriptive expected conclusions to pass, fail, or unreviewable."""
    raw = str(value or "").strip()
    source = raw.casefold()
    if re.search(
        r"不通过|不符合|失败|(?<![a-z])(?:fail|failed|failure)(?![a-z])",
        source,
    ):
        return "fail"
    if re.search(
        r"⛔|无法评审|(?<![a-z])(?:unreviewable|unknown)(?![a-z])",
        source,
    ):
        return "unreviewable"
    if re.search(
        r"✅|通过|符合|(?<![a-z])(?:pass|passed)(?![a-z])",
        source,
    ):
        return "pass"
    return raw or "unreviewable"


def detect_verdict_text(text: str) -> str:
    """Read the overall verdict from a Phase 2 Markdown report."""
    source = str(text or "")
    overall = re.search(r"\|\s*总体结论\s*\|\s*([^|]+?)\s*\|", source)
    target = overall.group(1) if overall else source
    if re.search(r"\s*不通过", target):
        return "fail"
    if re.search(r"⛔\s*无法评审|无法评审", target):
        return "unreviewable"
    if re.search(r"✅\s*(通过|符合)", target):
        return "pass"
    return "unreviewable"


def collect_review_stats(text: str) -> dict:
    """Extract the hard-issue count, falling back to counting issue table rows."""
    source = str(text or "")
    stats_match = re.search(r"\|\s*问题统计\s*\|\s*([^|]+?)\s*\|", source)
    if stats_match and (fail_match := re.search(r"不符合\s*(\d+)\s*项", stats_match.group(1))):
        return {"fail": int(fail_match.group(1))}
    return {"fail": len(re.findall(r"^\|\s*I-\d+\s*\|", source, re.M))}


def normalize_issue_match_text(value: str | None) -> str:
    """Remove presentation-only whitespace before matching issue expectations."""
    return re.sub(r"\s+", "", str(value or "")).casefold()


def evaluate_issue_expectations(example: dict, report: str) -> dict:
    """Compare configured required/forbidden issue patterns with a review report."""
    expected_patterns = string_list(example.get("expectedIssuePatterns"))
    forbidden_patterns = string_list(example.get("forbiddenIssuePatterns"))
    max_issue_count = optional_nonnegative_int(example.get("maxIssueCount"))
    source = normalize_issue_match_text(report)
    missing_patterns = [pattern for pattern in expected_patterns if normalize_issue_match_text(pattern) not in source]
    forbidden_hits = [pattern for pattern in forbidden_patterns if normalize_issue_match_text(pattern) in source]
    issue_count = int(collect_review_stats(report).get("fail") or 0)
    count_matched = max_issue_count is None or issue_count <= max_issue_count
    return {
        "configured": bool(expected_patterns or forbidden_patterns or max_issue_count is not None),
        "matched": not missing_patterns and not forbidden_hits and count_matched,
        "expectedIssuePatterns": expected_patterns,
        "forbiddenIssuePatterns": forbidden_patterns,
        "missingIssuePatterns": missing_patterns,
        "forbiddenIssueHits": forbidden_hits,
        "maxIssueCount": max_issue_count,
        "actualIssueCount": issue_count,
    }


def expected_matches_verdict(example: dict, verdict: str) -> bool:
    """Compare actual and expected verdicts; unknown expectations fail closed."""
    expected = str(example.get("expected") or "")
    if not expected.strip():
        return True
    expected_code = validation_expected_code(expected)
    if expected_code in {"pass", "fail", "unreviewable"}:
        return expected_code == verdict
    return False
