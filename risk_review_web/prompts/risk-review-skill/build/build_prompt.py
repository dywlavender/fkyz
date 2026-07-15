from __future__ import annotations

from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
PROMPTS_DIR = SKILL_ROOT.parent
OUTPUT_DIR = SKILL_ROOT / "output"

CORE_FILES = [
    "rule_engine.md",
    "evidence_engine.md",
    "freeze_rule.md",
    "hallucination_guard.md",
    "deterministic_output.md",
    "selected_interpretation.md",
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
    core_paths = [SKILL_ROOT / "core" / name for name in CORE_FILES]
    core_rules = read_parts(core_paths)
    write_text(PROMPTS_DIR / "core_rules.md", core_rules)

    phase1_prompt = read_parts([
        *core_paths,
        SKILL_ROOT / "phase" / "phase1_standard.md",
        SKILL_ROOT / "template" / "phase1_output.md",
    ])
    phase2_prompt = read_parts([
        *core_paths,
        SKILL_ROOT / "phase" / "phase2_review.md",
        SKILL_ROOT / "template" / "phase2_output.md",
    ])
    write_text(OUTPUT_DIR / "phase1.prompt", phase1_prompt)
    write_text(OUTPUT_DIR / "phase2.prompt", phase2_prompt)


if __name__ == "__main__":
    build()
