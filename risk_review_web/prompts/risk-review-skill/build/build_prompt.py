from __future__ import annotations

import json
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = SKILL_ROOT / "output"
PHASE1_SPEC_JSON_PATH = SKILL_ROOT / "spec" / "phase1_processing_spec.json"
PHASE1_SPEC_MD_PATH = SKILL_ROOT / "spec" / "phase1_processing_spec.md"
PHASE2_SPEC_JSON_PATH = SKILL_ROOT / "spec" / "phase2_review_spec.json"
PHASE2_SPEC_MD_PATH = SKILL_ROOT / "spec" / "phase2_review_spec.md"


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.strip() + "\n", encoding="utf-8")


def render_phase1_rule(rule: dict) -> list[str]:
    lines = [
        f"### {rule['rule_id']} {rule['name']}",
        "",
        f"- 适用条件：{rule['applies_when']}",
        f"- 编写要求：{rule['instruction']}",
    ]
    return lines


def render_phase1_spec_markdown(spec: dict) -> str:
    lines = [
        f"# {spec['title']}",
        "",
        f"- Schema：`{spec['schema_version']}`",
        f"- 版本：`{spec['spec_version']}`",
        "",
        "## 逻辑类型",
        "",
        "| 类型 | 名称 |",
        "|---|---|",
    ]
    for logic_type, label in spec["logic_types"].items():
        lines.append(f"| `{logic_type}` | {label} |")
    lines.extend(["", "## 编写规则", ""])
    for rule in spec["rules"]:
        lines.extend([*render_phase1_rule(rule), ""])
    return "\n".join(lines)


def render_phase2_rule(rule: dict) -> list[str]:
    lines = [
        f"### {rule['rule_id']} {rule['name']}",
        "",
        f"- 分类：`{rule['category']}`",
        f"- 适用条件：{rule['applies_when']}",
        f"- 比较要求：{rule['comparison']}",
        f"- 证据要求：{rule['evidence']}",
        "- 违反情形：",
    ]
    lines.extend(f"  - {item}" for item in rule.get("violations") or [])
    return lines


def render_phase2_spec_markdown(spec: dict) -> str:
    lines = [
        f"# {spec['title']}",
        "",
        f"- Schema：`{spec['schema_version']}`",
        f"- 版本：`{spec['spec_version']}`",
        "",
        "## 逻辑类型与评审规则",
        "",
        "| 类型 | 关联规则 |",
        "|---|---|",
    ]
    for logic_type, rule_ids in spec["logic_types"].items():
        lines.append(f"| `{logic_type}` | {', '.join(f'`{item}`' for item in rule_ids)} |")
    lines.extend(["", "## 评审规则", ""])
    for rule in spec["rules"]:
        lines.extend([*render_phase2_rule(rule), ""])
    return "\n".join(lines)


def build() -> None:
    phase1_spec = json.loads(PHASE1_SPEC_JSON_PATH.read_text(encoding="utf-8"))
    phase2_spec = json.loads(PHASE2_SPEC_JSON_PATH.read_text(encoding="utf-8"))
    phase1_spec_markdown = render_phase1_spec_markdown(phase1_spec)
    phase2_spec_markdown = render_phase2_spec_markdown(phase2_spec)
    write_text(PHASE1_SPEC_MD_PATH, phase1_spec_markdown)
    write_text(PHASE2_SPEC_MD_PATH, phase2_spec_markdown)

    phase1_prompt = "\n\n---\n\n".join([
        phase1_spec_markdown,
        read_text(SKILL_ROOT / "phase" / "phase1_standard.md"),
        read_text(SKILL_ROOT / "template" / "phase1_output.md"),
    ])
    phase2_prompt = "\n\n---\n\n".join([
        phase2_spec_markdown,
        read_text(SKILL_ROOT / "phase" / "phase2_review.md"),
        read_text(SKILL_ROOT / "template" / "phase2_output.md"),
    ])
    write_text(OUTPUT_DIR / "phase1.prompt", phase1_prompt)
    write_text(OUTPUT_DIR / "phase2.prompt", phase2_prompt)


if __name__ == "__main__":
    build()
