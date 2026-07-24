from __future__ import annotations

import json
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = SKILL_ROOT / "output"
SPEC_JSON_PATH = SKILL_ROOT / "spec" / "factor_processing_spec.json"
SPEC_MD_PATH = SKILL_ROOT / "spec" / "factor_processing_spec.md"


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.strip() + "\n", encoding="utf-8")


def render_rule(rule: dict) -> list[str]:
    lines = [
        f"### {rule['rule_id']} {rule['name']}",
        "",
        f"- 分类：`{rule['category']}`",
        f"- 适用条件：{rule['trigger']}",
        f"- Phase 1：{rule['phase1_obligation']}",
        f"- Phase 2：{rule['phase2_comparison']}",
        f"- 证据要求：{rule['evidence_policy']}",
        "- 违反情形：",
    ]
    lines.extend(f"  - {item}" for item in rule.get("violation_conditions") or [])
    return lines


def render_spec_markdown(spec: dict) -> str:
    lines = [
        f"# {spec['title']}",
        "",
        f"- Schema：`{spec['schema_version']}`",
        f"- 版本：`{spec['spec_version']}`",
        "",
        "## 逻辑类型",
        "",
        "| 类型 | 名称 | 关联规则 |",
        "|---|---|---|",
    ]
    for logic_type, config in spec["logic_types"].items():
        lines.append(
            f"| `{logic_type}` | {config['label']} | {', '.join(f'`{item}`' for item in config['rule_ids'])} |"
        )
    lines.extend(["", "## 全局原则", ""])
    for rule in spec["principles"]:
        lines.extend([*render_rule(rule), ""])
    lines.extend(["## 原子规则", ""])
    for rule in spec["rules"]:
        lines.extend([*render_rule(rule), ""])
    return "\n".join(lines)


def build() -> None:
    spec = json.loads(SPEC_JSON_PATH.read_text(encoding="utf-8"))
    spec_markdown = render_spec_markdown(spec)
    write_text(SPEC_MD_PATH, spec_markdown)

    phase1_prompt = "\n\n---\n\n".join([
        spec_markdown,
        read_text(SKILL_ROOT / "phase" / "phase1_standard.md"),
        read_text(SKILL_ROOT / "template" / "phase1_output.md"),
    ])
    phase2_prompt = "\n\n---\n\n".join([
        spec_markdown,
        read_text(SKILL_ROOT / "phase" / "phase2_review.md"),
        read_text(SKILL_ROOT / "template" / "phase2_output.md"),
    ])
    write_text(OUTPUT_DIR / "phase1.prompt", phase1_prompt)
    write_text(OUTPUT_DIR / "phase2.prompt", phase2_prompt)


if __name__ == "__main__":
    build()
