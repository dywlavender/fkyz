from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = Path(__file__).resolve().parent / "static"
RAW_API_DIR = ROOT / "raw" / "api"
OUTPUTS_DIR = ROOT / "outputs"
# 验证集可能位于 wiki/outputs/ 或 validation/outputs/；运行时按 glob 取最新一份，见 resolve_validation_set_path()。
VALIDATION_SET_DIR_CANDIDATES = [
    ROOT / "wiki" / "outputs",
    ROOT / "validation" / "outputs",
]
VALIDATION_SET_GLOB = "*validation-set*.md"
EXAMPLES_DIR = Path(__file__).resolve().parent / "data"
EXAMPLES_PATH = EXAMPLES_DIR / "examples.json"
BUSINESS_RULES_PATH = EXAMPLES_DIR / "business_rules.json"
SCHEDULED_TASKS_PATH = EXAMPLES_DIR / "scheduled_tasks.json"
SCHEDULED_RUNS_PATH = EXAMPLES_DIR / "scheduled_runs.json"
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
RISK_REVIEW_SKILL_DIR = PROMPTS_DIR / "risk-review-skill"
PHASE1_SKILL_PATH = RISK_REVIEW_SKILL_DIR / "phase" / "phase1_standard.md"
PHASE2_SKILL_PATH = RISK_REVIEW_SKILL_DIR / "phase" / "phase2_review.md"
CORE_RULES_PROMPT_PATH = PROMPTS_DIR / "core_rules.md"
RULE_ENGINE_BUILD_PATH = RISK_REVIEW_SKILL_DIR / "build" / "build_prompt.py"
RULE_ENGINE_PHASE1_OUTPUT_PATH = RISK_REVIEW_SKILL_DIR / "output" / "phase1.prompt"
RULE_ENGINE_PHASE2_OUTPUT_PATH = RISK_REVIEW_SKILL_DIR / "output" / "phase2.prompt"
PHASE1_PROMPT_PATH = PROMPTS_DIR / "phase1.md"
PHASE2_PROMPT_PATH = PROMPTS_DIR / "phase2.md"
REVIEWER_PROMPT_PATH = PROMPTS_DIR / "reviewer.md"
REVIEW_FEEDBACK_PROMPT_PATH = PROMPTS_DIR / "review_feedback.md"


def resolve_validation_set_path() -> Path | None:
    """按候选目录 glob 取最新的 validation-set 文件，文件名带日期时自然按 mtime 取最新。"""
    candidates: list[tuple[float, Path]] = []
    for base in VALIDATION_SET_DIR_CANDIDATES:
        if not base.exists():
            continue
        for path in base.glob(VALIDATION_SET_GLOB):
            try:
                candidates.append((path.stat().st_mtime, path))
            except OSError:
                continue
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]
