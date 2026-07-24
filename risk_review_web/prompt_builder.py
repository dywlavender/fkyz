from __future__ import annotations

from .paths import (
    PHASE1_PROMPT_PATH,
    PHASE1_SKILL_PATH,
    PHASE2_PROMPT_PATH,
    DIRECT_REVIEW_PROMPT_PATH,
    PHASE2_SKILL_PATH,
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
    }


def build_phase1_prompt(
    *,
    requirement: str,
    api_names: list[str],
) -> str:
    api_block = "\n".join(f"- {name}" for name in api_names)
    return render_template(
        read_text(PHASE1_PROMPT_PATH),
        {
            **skill_path_values(),
            "REQUIREMENT": requirement,
            "API_NAMES": api_block,
        },
    )


def build_phase2_prompt(
    *,
    standard_text: str,
    requirement: str,
    draft: str,
) -> str:
    return render_template(
        read_text(PHASE2_PROMPT_PATH),
        {
            **skill_path_values(),
            "STANDARD_TEXT": standard_text,
            "REQUIREMENT": requirement,
            "DRAFT": draft,
        },
    )


def build_direct_review_prompt(*, requirement: str, draft: str) -> str:
    return render_template(
        read_text(DIRECT_REVIEW_PROMPT_PATH),
        {
            "REQUIREMENT": requirement,
            "DRAFT": draft,
        },
    )
