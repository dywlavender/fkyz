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
    """Render a project-relative path for prompt-facing file references."""
    return path.relative_to(ROOT).as_posix()


def render_template(template: str, values: dict[str, str]) -> str:
    """Replace the declared placeholders in a prompt template."""
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace("{{" + key + "}}", str(value))
    return rendered


def skill_path_values() -> dict[str, str]:
    """Build common Phase 1 and Phase 2 Skill-path replacements."""
    return {
        "PHASE1_SKILL_PATH": rel(PHASE1_SKILL_PATH),
        "PHASE2_SKILL_PATH": rel(PHASE2_SKILL_PATH),
    }


def build_phase1_prompt(
    *,
    requirement: str,
    api_names: list[str],
) -> str:
    """Build the Phase 1 prompt from the requirement and approved API names."""
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
    """Build the Phase 2 prompt from its frozen standard and review input."""
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
    """Build the single-stage prompt used by mode-comparison experiments."""
    return render_template(
        read_text(DIRECT_REVIEW_PROMPT_PATH),
        {
            "REQUIREMENT": requirement,
            "DRAFT": draft,
        },
    )
