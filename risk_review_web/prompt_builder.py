from __future__ import annotations

from .paths import (
    CORE_RULES_PROMPT_PATH,
    PHASE1_PROMPT_PATH,
    PHASE1_SKILL_PATH,
    PHASE2_PROMPT_PATH,
    REVIEW_FEEDBACK_PROMPT_PATH,
    PHASE2_SKILL_PATH,
    REVIEWER_PROMPT_PATH,
    ROOT,
)
from .utils import read_text


def rel(path) -> str:
    return path.relative_to(ROOT).as_posix()


def render_template(template: str, values: dict[str, str]) -> str:
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace("{{" + key + "}}", str(value))
    return rendered


def skill_path_values() -> dict[str, str]:
    return {
        "PHASE1_SKILL_PATH": rel(PHASE1_SKILL_PATH),
        "PHASE2_SKILL_PATH": rel(PHASE2_SKILL_PATH),
        "CORE_RULES": read_text(CORE_RULES_PROMPT_PATH),
    }


def build_phase1_prompt(
    *,
    requirement: str,
    api_names: list[str],
    review_feedback: str = "",
) -> str:
    api_block = "\n".join(f"- {name}" for name in api_names)
    return render_template(
        read_text(PHASE1_PROMPT_PATH),
        {
            **skill_path_values(),
            "REQUIREMENT": requirement,
            "API_NAMES": api_block,
            "REVIEW_FEEDBACK": review_feedback,
        },
    )


def build_phase2_prompt(
    *,
    standard_summary_json: str,
    draft: str,
    standard_validation_status: str,
    review_feedback: str = "",
) -> str:
    return render_template(
        read_text(PHASE2_PROMPT_PATH),
        {
            **skill_path_values(),
            "STANDARD_SUMMARY_JSON": standard_summary_json,
            "STANDARD_VALIDATION_STATUS": standard_validation_status,
            "DRAFT": draft,
            "REVIEW_FEEDBACK": review_feedback,
        },
    )


def build_reviewer_prompt(
    *,
    phase_label: str,
    original_input: str,
    candidate_output: str,
) -> str:
    current_skill_path = PHASE2_SKILL_PATH if "2" in str(phase_label) else PHASE1_SKILL_PATH
    return render_template(
        read_text(REVIEWER_PROMPT_PATH),
        {
            **skill_path_values(),
            "CURRENT_SKILL_PATH": rel(current_skill_path),
            "PHASE_LABEL": phase_label,
            "ORIGINAL_INPUT": original_input,
            "CANDIDATE_OUTPUT": candidate_output,
        },
    )


def build_review_feedback_prompt(*, blocking_issues: str, warnings: str) -> str:
    return render_template(
        read_text(REVIEW_FEEDBACK_PROMPT_PATH),
        {
            "BLOCKING_ISSUES": blocking_issues or "无",
            "WARNINGS": warnings or "无",
        },
    )
