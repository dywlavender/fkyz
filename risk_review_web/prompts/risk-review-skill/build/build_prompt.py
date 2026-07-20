from __future__ import annotations

from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
PROMPTS_DIR = SKILL_ROOT.parent
OUTPUT_DIR = SKILL_ROOT / "output"

SHARED_CORE_FILES = [
    "rule_engine.md",
    "evidence_engine.md",
    "freeze_rule.md",
    "deterministic_output.md",
]

PHASE1_CORE_FILES = [
    "hallucination_guard.md",
    "phase1_contract.md",
    "selected_interpretation.md",
]

PHASE2_CORE_FILES = [
    "phase2_contract.md",
    "phase2_issue_guard.md",
]


def read_parts(paths: list[Path]) -> str:
    parts = []
    for path in paths:
        parts.append(path.read_text(encoding="utf-8").strip())
    return "\n\n---\n\n".join(parts).strip() + "\n"


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def build() -> None:
    shared_core_paths = [SKILL_ROOT / "core" / name for name in SHARED_CORE_FILES]
    phase1_core_paths = [SKILL_ROOT / "core" / name for name in PHASE1_CORE_FILES]
    phase2_core_paths = [SKILL_ROOT / "core" / name for name in PHASE2_CORE_FILES]
    shared_core_rules = read_parts(shared_core_paths)
    phase1_core_rules = read_parts([*shared_core_paths, *phase1_core_paths])
    phase2_core_rules = read_parts([*shared_core_paths, *phase2_core_paths])
    write_text(PROMPTS_DIR / "core_rules.md", shared_core_rules)
    write_text(PROMPTS_DIR / "core_rules_phase1.md", phase1_core_rules)
    write_text(PROMPTS_DIR / "core_rules_phase2.md", phase2_core_rules)

    phase1_prompt = read_parts([
        *shared_core_paths,
        *phase1_core_paths,
        SKILL_ROOT / "phase" / "phase1_standard.md",
        SKILL_ROOT / "template" / "phase1_output.md",
    ])
    phase2_prompt = read_parts([
        *shared_core_paths,
        *phase2_core_paths,
        SKILL_ROOT / "phase" / "phase2_review.md",
        SKILL_ROOT / "template" / "phase2_output.md",
    ])
    write_text(OUTPUT_DIR / "phase1.prompt", phase1_prompt)
    write_text(OUTPUT_DIR / "phase2.prompt", phase2_prompt)


if __name__ == "__main__":
    build()
