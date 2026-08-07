from __future__ import annotations

from dataclasses import dataclass

from .paths import (
    PHASE1_PROMPT_PATH,
    PHASE1_USER_PROMPT_PATH,
    PHASE1_SKILL_PATH,
    PHASE2_PROMPT_PATH,
    PHASE2_USER_PROMPT_PATH,
    DIRECT_REVIEW_PROMPT_PATH,
    REQUIREMENT_REVIEW_PROMPT_PATH,
    REQUIREMENT_REVIEW_SKILL_PATH,
    REQUIREMENT_REVIEW_SPEC_PATH,
    PHASE2_SKILL_PATH,
    ROOT,
)
from .utils import read_text


@dataclass(frozen=True)
class ModelPrompt:
    """Role-separated model input with a single-prompt adapter for CLI backends."""

    system: str
    user: str

    def combined(self) -> str:
        return "\n\n".join(part.strip() for part in (self.system, self.user) if part.strip())


def model_prompt_text(prompt: str | ModelPrompt) -> str:
    """Render a prompt for a backend that only accepts one text input."""
    return prompt.combined() if isinstance(prompt, ModelPrompt) else str(prompt)


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
) -> ModelPrompt:
    """Build role-separated Phase 1 instructions and task-specific evidence."""
    api_block = "\n".join(f"- {name}" for name in api_names)
    values = {
        **skill_path_values(),
        "REQUIREMENT": requirement,
        "API_NAMES": api_block,
    }
    return ModelPrompt(
        system=render_template(read_text(PHASE1_PROMPT_PATH), values),
        user=render_template(read_text(PHASE1_USER_PROMPT_PATH), values),
    )


def build_phase2_prompt(
    *,
    standard_text: str,
    requirement: str,
    draft: str,
) -> ModelPrompt:
    """Build role-separated Phase 2 instructions and task-specific evidence."""
    values = {
        **skill_path_values(),
        "STANDARD_TEXT": standard_text,
        "REQUIREMENT": requirement,
        "DRAFT": draft,
    }
    return ModelPrompt(
        system=render_template(read_text(PHASE2_PROMPT_PATH), values),
        user=render_template(read_text(PHASE2_USER_PROMPT_PATH), values),
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


def build_requirement_review_prompt(*, requirement: str, api_names: list[str]) -> str:
    """Build the standalone requirement-review prompt from approved inputs."""
    return render_template(
        read_text(REQUIREMENT_REVIEW_PROMPT_PATH),
        {
            "REQUIREMENT_REVIEW_SKILL_PATH": rel(REQUIREMENT_REVIEW_SKILL_PATH),
            "REQUIREMENT_REVIEW_SPEC_PATH": rel(REQUIREMENT_REVIEW_SPEC_PATH),
            "REQUIREMENT": str(requirement or "").strip(),
            "API_NAMES": "\n".join(f"- {name}" for name in api_names),
        },
    )
