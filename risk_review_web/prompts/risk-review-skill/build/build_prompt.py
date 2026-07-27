from __future__ import annotations

import json
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
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
    rules_by_id = {rule["rule_id"]: rule for rule in spec["rules"]}
    sections = spec["sections"]
    processing = sections["processing_logic"]

    lines = [
        f"# {spec['title']}",
        "",
        f"- Schema：`{spec['schema_version']}`",
        f"- 版本：`{spec['spec_version']}`",
        "",
        "## 规则作用范围",
        "",
        "| 作用范围 | 关联规则 |",
        "|---|---|",
        f"| 整份标准规范 | {', '.join(f'`{item}`' for item in spec['global_rule_ids'])} |",
        f"| {sections['input_fields']['label']} | {', '.join(f'`{item}`' for item in sections['input_fields']['rule_ids'])} |",
        f"| {processing['label']}（通用） | {', '.join(f'`{item}`' for item in processing['common_rule_ids'])} |",
        f"| {sections['default_fallback']['label']} | {', '.join(f'`{item}`' for item in sections['default_fallback']['rule_ids'])} |",
        "",
        "## 标准加工逻辑类型",
        "",
        "| 类型 | 名称 | 类型规则 |",
        "|---|---|---|",
    ]
    for logic_type, config in processing["logic_types"].items():
        rule_ids = ", ".join(f"`{item}`" for item in config["rule_ids"])
        lines.append(f"| `{logic_type}` | {config['label']} | {rule_ids} |")

    rule_groups = [
        ("全局规则", spec["global_rule_ids"]),
        (f"{sections['input_fields']['label']}规则", sections["input_fields"]["rule_ids"]),
        (f"{processing['label']}通用规则", processing["common_rule_ids"]),
    ]
    rule_groups.extend(
        (f"{config['label']}规则", config["rule_ids"])
        for config in processing["logic_types"].values()
    )
    rule_groups.append(
        (f"{sections['default_fallback']['label']}规则", sections["default_fallback"]["rule_ids"])
    )
    for heading, rule_ids in rule_groups:
        lines.extend(["", f"## {heading}", ""])
        for rule_id in rule_ids:
            lines.extend([*render_phase1_rule(rules_by_id[rule_id]), ""])
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
    scopes = spec["review_scopes"]
    processing = scopes["processing_logic"]
    lines = [
        f"# {spec['title']}",
        "",
        f"- Schema：`{spec['schema_version']}`",
        f"- 版本：`{spec['spec_version']}`",
        "",
        "## 评审作用范围",
        "",
        "| 评审范围 | 标准章节 | 关联规则 |",
        "|---|---|---|",
        f"| {scopes['global']['label']} | 全部 | {', '.join(f'`{item}`' for item in scopes['global']['rule_ids'])} |",
        f"| 输入字段 | {scopes['input_fields']['source_section']} | {', '.join(f'`{item}`' for item in scopes['input_fields']['rule_ids'])} |",
        f"| 默认值兜底 | {scopes['default_fallback']['source_section']} | {', '.join(f'`{item}`' for item in scopes['default_fallback']['rule_ids'])} |",
        "",
        "## 标准加工逻辑类型与评审规则",
        "",
        "| 类型 | 关联规则 |",
        "|---|---|",
    ]
    for logic_type, rule_ids in processing["logic_types"].items():
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


if __name__ == "__main__":
    build()
