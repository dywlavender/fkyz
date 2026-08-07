from __future__ import annotations

import json
import os
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = Path(__file__).resolve().parent
STATIC_DIR = Path(__file__).resolve().parent / "static"
RAW_API_DIR = ROOT / "raw" / "api"
OUTPUTS_DIR = Path(os.environ.get("RISK_REVIEW_OUTPUTS_DIR") or ROOT / "outputs").expanduser()
REQUIREMENT_REVIEW_OUTPUTS_DIR = OUTPUTS_DIR / "requirement_reviews"
# 验证集可能位于 wiki/outputs/ 或 validation/outputs/；运行时按 glob 取最新一份，见 resolve_validation_set_path()。
VALIDATION_SET_DIR_CANDIDATES = [
    ROOT / "wiki" / "outputs",
    ROOT / "validation" / "outputs",
]
VALIDATION_SET_GLOB = "*validation-set*.md"
DATA_DIR = PACKAGE_DIR / "data"
STATE_DIR = Path(os.environ.get("RISK_REVIEW_STATE_DIR") or PACKAGE_DIR / ".local" / "state").expanduser()
LOG_DIR = Path(os.environ.get("RISK_REVIEW_LOG_DIR") or PACKAGE_DIR / "local" / "logs").expanduser()
EXAMPLES_PATH = DATA_DIR / "examples.json"
BUSINESS_RULES_PATH = DATA_DIR / "business_rules.json"
VALIDATION_RUNS_PATH = STATE_DIR / "validation_runs.json"
REVIEW_MODE_EXPERIMENTS_PATH = STATE_DIR / "review_mode_experiments.json"
REVIEW_RECORDS_PATH = STATE_DIR / "review_records.json"
SCHEDULED_TASKS_PATH = STATE_DIR / "scheduled_tasks.json"
SCHEDULED_RUNS_PATH = STATE_DIR / "scheduled_runs.json"
RUNTIME_SETTINGS_PATH = STATE_DIR / "runtime_settings.json"
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
RISK_REVIEW_SKILL_DIR = PROMPTS_DIR / "risk-review-skill"
PHASE1_SKILL_PATH = RISK_REVIEW_SKILL_DIR / "phase" / "phase1_standard.md"
PHASE2_SKILL_PATH = RISK_REVIEW_SKILL_DIR / "phase" / "phase2_review.md"
PHASE1_PROCESSING_SPEC_PATH = RISK_REVIEW_SKILL_DIR / "spec" / "phase1_processing_spec.json"
PHASE1_PROCESSING_SPEC_DOC_PATH = RISK_REVIEW_SKILL_DIR / "spec" / "phase1_processing_spec.md"
PHASE2_REVIEW_SPEC_PATH = RISK_REVIEW_SKILL_DIR / "spec" / "phase2_review_spec.json"
PHASE2_REVIEW_SPEC_DOC_PATH = RISK_REVIEW_SKILL_DIR / "spec" / "phase2_review_spec.md"
RULE_ENGINE_BUILD_PATH = RISK_REVIEW_SKILL_DIR / "build" / "build_prompt.py"
PHASE1_PROMPT_PATH = PROMPTS_DIR / "phase1.md"
PHASE1_USER_PROMPT_PATH = PROMPTS_DIR / "phase1_user.md"
PHASE2_PROMPT_PATH = PROMPTS_DIR / "phase2.md"
PHASE2_USER_PROMPT_PATH = PROMPTS_DIR / "phase2_user.md"
DIRECT_REVIEW_PROMPT_PATH = PROMPTS_DIR / "direct_review.md"
REQUIREMENT_REVIEW_PROMPT_PATH = PROMPTS_DIR / "requirement_review.md"
REQUIREMENT_REVIEW_SKILL_DIR = PROMPTS_DIR / "requirement-review-skill"
REQUIREMENT_REVIEW_SKILL_PATH = REQUIREMENT_REVIEW_SKILL_DIR / "requirement_review.md"
REQUIREMENT_REVIEW_SPEC_PATH = REQUIREMENT_REVIEW_SKILL_DIR / "spec" / "requirement_review_spec.json"


def bootstrap_runtime_state() -> None:
    """Initialize local runtime state without mutating versioned seed data."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    seed_enabled = os.environ.get("RISK_REVIEW_STATE_SEED", "1").strip().lower() not in {
        "0", "false", "no", "off",
    }
    defaults = {
        "validation_runs.json": [],
        "review_mode_experiments.json": [],
        "review_records.json": [],
        "scheduled_tasks.json": [],
        "scheduled_runs.json": [],
        "runtime_settings.json": {},
    }
    for file_name, default_value in defaults.items():
        target = STATE_DIR / file_name
        if target.exists():
            continue
        seed = DATA_DIR / file_name
        if seed_enabled and seed.is_file():
            shutil.copy2(seed, target)
            continue
        target.write_text(
            json.dumps(default_value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


bootstrap_runtime_state()


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
