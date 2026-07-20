from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path

from .api_docs import list_api_docs
from .business_rules import business_rules_fingerprint
from .paths import (
    CORE_RULES_PROMPT_PATH,
    OUTPUTS_DIR,
    PHASE1_CORE_RULES_PROMPT_PATH,
    PHASE1_PROMPT_PATH,
    PHASE1_SKILL_PATH,
)
from .utils import atomic_write_text, parse_frontmatter, read_text

STANDARD_JSON_SCHEMA_VERSION = "risk_review_standard_json_v4"

STANDARD_DIMENSIONS = {
    "D1": {
        "heading": "因子加工输入字段",
        "key": "inputs",
        "dimension": "input_completeness",
    },
    "D2": {
        "heading": "字段溯源",
        "key": "field_lineage",
        "dimension": "logic_consistency",
    },
    "D3": {
        "heading": "过滤条件",
        "key": "filters",
        "dimension": "logic_consistency",
    },
    "D4": {
        "heading": "列表/数组字段处理",
        "key": "array_handling",
        "dimension": "logic_consistency",
    },
    "D5": {
        "heading": "计算逻辑",
        "key": "calculation",
        "dimension": "logic_consistency",
    },
    "D6": {
        "heading": "默认值兜底策略",
        "key": "default_strategy",
        "dimension": "logic_consistency",
    },
    "D8": {
        "heading": "比率/衍生因子计算",
        "key": "ratio_or_derivation",
        "dimension": "logic_consistency",
    },
    "D9": {
        "heading": "条件分支逻辑",
        "key": "conditional_branches",
        "dimension": "logic_consistency",
    },
}

STANDARD_JSON_TOP_LEVEL_FIELDS = {
    "schema_version",
    "factor_name",
    "input_parse",
    "inputs",
    "field_lineage",
    "filters",
    "array_handling",
    "calculation",
    "default_strategy",
    "ratio_or_derivation",
    "conditional_branches",
    "selected_interpretations",
    "source_metadata",
    "rules",
}

STANDARD_RULE_REQUIRED_FIELDS = {
    "rule_id",
    "dimension",
    "standard_level",
    "basis_type",
    "basis_text",
    "source_section",
    "dimension_ref",
    "evidence_ids",
    "content",
    "selection_reason",
}


STANDARD_DIGEST_HEADINGS = [
    "0. 输入解析",
    "因子加工输入字段",
    "字段溯源",
    "过滤条件",
    "列表/数组字段处理",
    "计算逻辑",
    "默认值兜底策略",
    "比率/衍生因子计算",
    "条件分支逻辑",
    "选定解释",
]


def read_phase1_standard(payload: dict, *, validate_integrity: bool = True) -> tuple[str, Path]:
    standard_file_id = (payload.get("standardFileId") or "").strip()
    if not standard_file_id:
        raise ValueError("Phase 1 标准文件 ID 不能为空。")
    std_path = OUTPUTS_DIR / standard_file_id
    if not std_path.exists():
        raise FileNotFoundError(f"Phase 1 标准文件未找到：{standard_file_id}")
    standard_text = read_text(std_path)
    if not validate_integrity:
        return standard_text, std_path
    meta = parse_frontmatter(standard_text)
    stored_checksum = meta.get("input_checksum")
    current_checksum = compute_phase1_checksum(payload)
    if not stored_checksum or stored_checksum == "<external-sha256-required>":
        raise ValueError("Phase 1 标准文件缺少 input_checksum，请重新执行 Phase 1。")
    if stored_checksum != current_checksum:
        raise ValueError(
            "输入已变更（checksum 不匹配），请重新执行 Phase 1。"
            f" stored={stored_checksum[:8]}..., current={current_checksum[:8]}..."
        )
    stored_public_rules_checksum = meta.get("public_rules_checksum")
    current_public_rules_checksum = compute_public_rules_checksum()
    if not stored_public_rules_checksum:
        raise ValueError("Phase 1 标准文件缺少 public_rules_checksum，请重新执行 Phase 1。")
    if stored_public_rules_checksum != current_public_rules_checksum:
        raise ValueError(
            "公共业务知识规则已变更（checksum 不匹配），请重新执行 Phase 1。"
            f" stored={stored_public_rules_checksum[:8]}..., current={current_public_rules_checksum[:8]}..."
        )
    return standard_text, std_path


def strip_yaml_frontmatter(text: str) -> str:
    return re.sub(r"^---\n[\s\S]*?\n---\s*", "", str(text), count=1).strip()


def normalize_markdown_document_body(text: str, required_heading: str | None = None) -> str:
    body = str(text or "").strip()
    fenced_blocks = list(re.finditer(r"```(?:markdown|md)?[ \t]*\n([\s\S]*?)\n```", body, re.I))
    for block in fenced_blocks:
        inner = block.group(1).strip()
        if not required_heading or inner.startswith(required_heading):
            body = inner
            break

    if required_heading and not body.startswith(required_heading):
        heading_match = re.search(rf"^{re.escape(required_heading)}\b.*$", body, re.M)
        if heading_match:
            body = body[heading_match.start():].strip()

    body = re.sub(
        r"\n+(?:已保存|文件已保存至)[：:]\s*outputs/[^\n]+\.md\s*$",
        "",
        body,
    ).strip()
    return body


def extract_markdown_sections(text: str) -> dict[str, str]:
    body = strip_yaml_frontmatter(text)
    sections = {}
    matches = list(re.finditer(r"^##\s+(.+?)\s*$", body, re.M))
    for index, match in enumerate(matches):
        title = match.group(1).strip()
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        sections[title] = body[start:end].strip()
    return sections


def build_standard_digest(standard_text: str) -> str:
    meta = parse_frontmatter(standard_text)
    sections = extract_markdown_sections(standard_text)
    lines = [
        "# 标准加工规范结构化摘要",
        "",
        "## 元信息",
        f"- 标准文件 checksum：{meta.get('input_checksum', '未提供')}",
        "",
        "## 评审依据",
        "以下内容由 Phase 1 标准规范抽取而来，保留 Phase 2 逐项评审所需字段、码值、过滤条件、计算步骤和兜底口径。",
    ]
    for heading in STANDARD_DIGEST_HEADINGS:
        section = sections.get(heading)
        if section:
            lines.extend(["", section])
    digest = "\n".join(lines).strip()
    max_chars = int(os.environ.get("RISK_REVIEW_STANDARD_DIGEST_MAX_CHARS", "18000"))
    if len(digest) > max_chars:
        digest = digest[:max_chars] + "\n\n[摘要截断：标准规范过长，仅保留前部关键结构。]"
    return digest


def extract_section_text(sections: dict[str, str], heading: str) -> str:
    return sections.get(heading, "").strip()

def canonical_json_dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def compute_public_rules_checksum() -> str:
    return hashlib.sha256(canonical_json_dumps(business_rules_fingerprint()).encode("utf-8")).hexdigest()


def _strip_standard_json_checksum(summary: dict) -> dict:
    copied = json.loads(json.dumps(summary, ensure_ascii=False))
    source_metadata = copied.get("source_metadata")
    if isinstance(source_metadata, dict):
        source_metadata.pop("standard_json_checksum", None)
    return copied


def compute_standard_json_checksum(summary: dict) -> str:
    return hashlib.sha256(
        canonical_json_dumps(_strip_standard_json_checksum(summary)).encode("utf-8")
    ).hexdigest()


def update_frontmatter_fields(text: str, fields: dict[str, str]) -> str:
    text = str(text)
    if not text.startswith("---\n"):
        lines = ["---", *[f"{key}: {value}" for key, value in fields.items()], "---", "", text]
        return "\n".join(lines)
    match = re.match(r"^---\n(?P<meta>[\s\S]*?)\n---\s*", text)
    if not match:
        return text
    meta_lines = match.group("meta").splitlines()
    remaining = text[match.end():]
    existing: dict[str, int] = {}
    for index, line in enumerate(meta_lines):
        key = line.split(":", 1)[0].strip() if ":" in line else ""
        if key:
            existing[key] = index
    for key, value in fields.items():
        rendered = f"{key}: {value}"
        if key in existing:
            meta_lines[existing[key]] = rendered
        else:
            meta_lines.append(rendered)
    return "---\n" + "\n".join(meta_lines).rstrip() + "\n---\n\n" + remaining.lstrip()


def split_markdown_table(row: str) -> list[str]:
    row = row.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|"):
        row = row[:-1]
    return [cell.strip() for cell in row.split("|")]


def extract_markdown_tables(section_text: str) -> list[dict[str, list[dict[str, str]]]]:
    tables = []
    lines = section_text.splitlines()
    index = 0
    while index < len(lines) - 1:
        header_line = lines[index].strip()
        separator_line = lines[index + 1].strip()
        if not (header_line.startswith("|") and separator_line.startswith("|")):
            index += 1
            continue
        separator_cells = split_markdown_table(separator_line)
        if not separator_cells or not all(re.fullmatch(r":?-{3,}:?", cell.strip()) for cell in separator_cells):
            index += 1
            continue
        headers = split_markdown_table(header_line)
        rows = []
        index += 2
        while index < len(lines) and lines[index].strip().startswith("|"):
            cells = split_markdown_table(lines[index])
            if len(cells) < len(headers):
                cells.extend([""] * (len(headers) - len(cells)))
            rows.append({header: cells[pos].strip() for pos, header in enumerate(headers)})
            index += 1
        tables.append({"headers": headers, "rows": rows})
    return tables


def infer_standard_level(text: str) -> str:
    # v4 标准是 Phase 2 的唯一可执行基线；进入标准正文的业务规则全部为 hard。
    return "hard"


def infer_basis_type(text: str, standard_level: str) -> str:
    text = text or ""
    if re.search(r"\bSEL-\d{3}\b|选定解释|选定口径|选择理由", text):
        return "selected_interpretation"
    if "公共业务知识" in text or "公共规则" in text or re.search(r"\brule-[0-9a-fA-F]+", text):
        return "public_rule"
    if "需求" in text:
        return "requirement"
    if "确定性推导" in text or "推导" in text:
        return "deterministic_inference"
    if "接口" in text or "字段" in text:
        return "api_doc"
    return "deterministic_inference"


def row_to_rule_content(row: dict[str, str]) -> str:
    parts = []
    for key, value in row.items():
        if value:
            parts.append(f"{key}: {value}")
    return "；".join(parts).strip()


def extract_basis_text(row: dict[str, str], section_text: str, content: str) -> str:
    for key in ("证据锚点", "依据", "需求依据", "来源说明", "备注"):
        if row.get(key):
            return row[key].strip()
    for line in section_text.splitlines():
        if "依据" in line or "证据" in line:
            clean = line.strip(" |")
            if clean and "---" not in clean:
                return clean[:500]
    return content[:500]


def extract_public_rule_ids(text: str) -> list[str]:
    return sorted(set(re.findall(r"\brule-[0-9a-zA-Z_-]+", text or "")))


def extract_evidence_ids(text: str) -> list[str]:
    return sorted(set(re.findall(r"\b(?:REQ|API|PUB|INF|SEL)-\d{3}\b", text or "")))


def extract_field_paths(text: str) -> list[str]:
    candidates = set(re.findall(r"`([^`]+)`", text or ""))
    candidates.update(re.findall(r"[\u4e00-\u9fffA-Za-z0-9_]+(?:接口)?(?:\.[A-Za-z_][A-Za-z0-9_]*|\[\])+(?:\.[A-Za-z_][A-Za-z0-9_]*|\[\])*", text or ""))
    candidates.update(re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\[\](?:\.[A-Za-z_][A-Za-z0-9_]*)*\b", text or ""))
    return sorted(item for item in candidates if item and len(item) <= 120)


def extract_input_field_rows(inputs_text: str) -> list[dict[str, str]]:
    rows = []
    for table in extract_markdown_tables(inputs_text or ""):
        for row in table.get("rows") or []:
            field = (row.get("字段") or row.get("字段名") or "").strip().strip("`")
            if not field or "不适用" in field:
                continue
            rows.append({
                "field": field,
                "purpose": (row.get("用途") or "").strip(),
                "source": (row.get("字段来源") or row.get("来源") or "").strip(),
            })
    return rows


def field_leaf_name(field: str) -> str:
    value = str(field or "").strip().strip("`")
    if "." in value:
        value = value.rsplit(".", 1)[-1]
    return value.replace("[]", "")


def validate_input_field_usage(summary: dict) -> None:
    """Ensure every D1 field is a processing dependency, not acquisition metadata."""
    rows = extract_input_field_rows(str(summary.get("inputs") or ""))
    if not rows:
        return

    downstream = "\n".join(str(summary.get(key) or "") for key in (
        "filters",
        "array_handling",
        "calculation",
        "ratio_or_derivation",
        "conditional_branches",
    ))
    association_only = re.compile(r"接口查询|调用接口|接口调用|请求接口|参数传递|主体关联|关联主体|上游数据获取")
    processing_role = re.compile(r"过滤|时间窗口|窗口|数组|列表|计算|求和|计数|聚合|分组|去重|条件|分支|比率|衍生|最大|最小|取值")
    legacy_direction = re.compile(r"接口请求参数|接口返回字段|请求参数|响应字段")

    invalid_direction = []
    association_fields = []
    unused_fields = []
    container_only_fields = []
    structural_use = re.compile(r"存在|不存在|为空|非空|空数组|长度|数量|条数|计数|count\s*\(|size\s*\(|length\b|exists\s*\(", re.I)
    for row in rows:
        field = row["field"]
        purpose = row["purpose"]
        source = row["source"]
        if legacy_direction.search(f"{field}\n{source}"):
            invalid_direction.append(field)
        if association_only.search(purpose) and not processing_role.search(purpose):
            association_fields.append(field)
        leaf = field_leaf_name(field)
        if field not in downstream and (not leaf or leaf not in downstream):
            unused_fields.append(field)
        normalized_field = re.sub(r"\s+", "", field)
        if normalized_field.endswith("[]") and not structural_use.search(purpose):
            container_only_fields.append(field)

    if invalid_direction:
        raise ValueError(
            "Phase 1 标准仍区分接口请求/返回方向；接口字段来源只应写接口名称和字段路径："
            + "、".join(sorted(set(invalid_direction)))
        )
    if association_fields:
        raise ValueError(
            "Phase 1 标准将仅用于接口查询、参数传递或主体关联的字段列为因子加工输入字段："
            + "、".join(sorted(set(association_fields)))
        )
    if unused_fields:
        raise ValueError(
            "Phase 1 标准存在未被过滤、数组处理、计算、聚合、衍生计算或条件分支引用的输入字段："
            + "、".join(sorted(set(unused_fields)))
        )
    if container_only_fields:
        raise ValueError(
            "Phase 1 标准将仅用于承载或遍历的数组容器节点列为独立因子加工输入字段；"
            "请保留实际使用的叶子字段完整路径，不要单列容器节点："
            + "、".join(sorted(set(container_only_fields)))
        )


def build_rules_for_section(dimension_ref: str, section_text: str) -> list[dict]:
    config = STANDARD_DIMENSIONS[dimension_ref]
    heading = config["heading"]
    rules = []
    tables = extract_markdown_tables(section_text)
    row_index = 1
    for table in tables:
        for row in table["rows"]:
            content = row_to_rule_content(row)
            if not content or re.fullmatch(r"[-—/\\\s]+", content):
                continue
            standard_level = infer_standard_level(content)
            basis_text = extract_basis_text(row, section_text, content)
            basis_type = infer_basis_type(content + "\n" + basis_text, standard_level)
            rule = {
                "rule_id": f"{dimension_ref}-{row_index:03d}",
                "dimension": config["dimension"],
                "standard_level": standard_level,
                "basis_type": basis_type,
                "basis_text": basis_text,
                "source_section": heading,
                "dimension_ref": dimension_ref,
                "evidence_ids": extract_evidence_ids(content + "\n" + basis_text),
                "public_rule_ids": extract_public_rule_ids(content + "\n" + basis_text),
                "field_paths": extract_field_paths(content),
                "content": content,
                "selection_reason": "",
            }
            rules.append(rule)
            row_index += 1
    if not rules and section_text.strip():
        content = "\n".join(
            line.strip()
            for line in section_text.splitlines()
            if line.strip() and not line.strip().startswith("##")
        ).strip()
        if content:
            standard_level = infer_standard_level(content)
            rules.append({
                "rule_id": f"{dimension_ref}-001",
                "dimension": config["dimension"],
                "standard_level": standard_level,
                "basis_type": infer_basis_type(content, standard_level),
                "basis_text": content[:500],
                "source_section": heading,
                "dimension_ref": dimension_ref,
                "evidence_ids": extract_evidence_ids(content),
                "public_rule_ids": extract_public_rule_ids(content),
                "field_paths": extract_field_paths(content),
                "content": content,
                "selection_reason": "",
            })
    return rules


def extract_selected_interpretations(sections: dict[str, str]) -> list[dict]:
    selections = []
    section_text = extract_section_text(sections, "选定解释")
    for table in extract_markdown_tables(section_text):
        for row in table["rows"]:
            selection_id = (row.get("选择编号") or "").strip()
            if not re.fullmatch(r"SEL-\d{3}", selection_id):
                continue
            selections.append({
                "selection_id": selection_id,
                "trigger": row.get("触发位置", "").strip(),
                "ambiguity": row.get("原始歧义", "").strip(),
                "selected_rule": row.get("选定口径", "").strip(),
                "selection_reason": row.get("选择理由", "").strip(),
                "evidence": row.get("证据锚点", "").strip(),
            })
    return selections


def validate_standard_json_summary(summary: dict) -> None:
    if not isinstance(summary, dict):
        raise ValueError("结构化标准 JSON 格式无效。")
    if summary.get("schema_version") != STANDARD_JSON_SCHEMA_VERSION:
        raise ValueError("结构化标准 JSON schema_version 不是 risk_review_standard_json_v4，请重新执行 Phase 1。")
    missing = sorted(field for field in STANDARD_JSON_TOP_LEVEL_FIELDS if field not in summary)
    if missing:
        raise ValueError(f"结构化标准 JSON 缺少顶层字段：{', '.join(missing)}")
    extra = sorted(field for field in summary if field not in STANDARD_JSON_TOP_LEVEL_FIELDS)
    if extra:
        raise ValueError(f"结构化标准 JSON 包含 v4 不支持的旧字段：{', '.join(extra)}")
    rules = summary.get("rules")
    if not isinstance(rules, list) or not rules:
        raise ValueError("结构化标准 JSON 缺少可评审 rules，请重新执行 Phase 1 摘要抽取。")
    valid_dimension_refs = set(STANDARD_DIMENSIONS)
    for index, rule in enumerate(rules, start=1):
        if not isinstance(rule, dict):
            raise ValueError(f"结构化标准 JSON 第 {index} 条规则格式无效。")
        missing_rule_fields = sorted(field for field in STANDARD_RULE_REQUIRED_FIELDS if field not in rule)
        if missing_rule_fields:
            raise ValueError(f"结构化标准 JSON 第 {index} 条规则缺少字段：{', '.join(missing_rule_fields)}")
        if rule.get("dimension") not in {"input_completeness", "logic_consistency"}:
            raise ValueError(f"结构化标准 JSON 第 {index} 条规则 dimension 无效。")
        if rule.get("standard_level") != "hard":
            raise ValueError(f"结构化标准 JSON 第 {index} 条规则不是 hard；v4 标准只允许确定的可执行规则。")
        if rule.get("basis_type") not in {
            "requirement",
            "api_doc",
            "public_rule",
            "deterministic_inference",
            "selected_interpretation",
        }:
            raise ValueError(f"结构化标准 JSON 第 {index} 条规则 basis_type 无效。")
        if rule.get("dimension_ref") not in valid_dimension_refs:
            raise ValueError(f"结构化标准 JSON 第 {index} 条规则 dimension_ref 无效。")
        if not isinstance(rule.get("evidence_ids"), list):
            raise ValueError(f"结构化标准 JSON 第 {index} 条规则 evidence_ids 无效。")
        if not rule.get("evidence_ids"):
            raise ValueError(f"结构化标准 JSON 第 {index} 条 hard 规则缺少证据锚点。")
        if rule.get("basis_type") == "selected_interpretation" and not str(rule.get("selection_reason") or "").strip():
            raise ValueError(f"结构化标准 JSON 第 {index} 条选定解释缺少 selection_reason。")
    selection_ids = {
        item.get("selection_id")
        for item in (summary.get("selected_interpretations") or [])
        if isinstance(item, dict)
    }
    for index, rule in enumerate(rules, start=1):
        referenced = {item for item in rule.get("evidence_ids") or [] if str(item).startswith("SEL-")}
        if referenced and not referenced.issubset(selection_ids):
            raise ValueError(f"结构化标准 JSON 第 {index} 条规则引用了未登记的 SEL 选定解释。")
    validate_input_field_usage(summary)
    validate_default_strategy(summary)
    validate_hard_logic_dependencies(summary)


def validate_default_strategy(summary: dict) -> None:
    section = str(summary.get("default_strategy") or "")
    rows = [
        row
        for table in extract_markdown_tables(section)
        for row in (table.get("rows") or [])
    ]
    if not rows:
        raise ValueError("Phase 1 v4 标准缺少默认值异常兜底规则。")
    forbidden = [
        row
        for row in rows
        if (row.get("项目") or "").strip() in {"默认值", "具体默认值", "默认返回值"}
    ]
    if forbidden:
        raise ValueError("Phase 1 v4 标准不得规定具体默认值，只保留异常返回配置默认值的语义。")
    compact = _normalize_text(section)
    if not all(token in compact for token in ("异常", "返回", "配置的默认值")):
        raise ValueError("Phase 1 v4 标准的 D6 必须明确：异常时返回配置的默认值。")


def validate_phase1_standard_output(standard_text: str) -> None:
    body = strip_yaml_frontmatter(standard_text)
    if not re.search(r"^#\s+标准加工规范\b", body, re.M):
        raise ValueError("Claude 未输出完整 Phase 1 标准加工规范正文，stdout 必须以「# 标准加工规范」开头。")
    summary = build_standard_json_summary(standard_text)
    validate_standard_json_summary(summary)
    validate_hard_logic_dependencies(summary)


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def _rule_mentions_other_rule(rule: dict, target_rule_id: str) -> bool:
    text = f"{rule.get('content') or ''}\n{rule.get('basis_text') or ''}"
    return bool(re.search(rf"\b{re.escape(target_rule_id)}\b", text))


def _is_time_window_rule(rule: dict) -> bool:
    if rule.get("dimension_ref") not in {"D3", "D4", "D5"}:
        return False
    text = f"{rule.get('content') or ''}\n{rule.get('basis_text') or ''}"
    return any(token in text for token in ("时间窗口", "窗口起点", "窗口终点", "进件时点", "kprq", "kpyf", "日期", "月份"))


def _contains_unresolved_marker(text: str) -> bool:
    compact = _normalize_text(text)
    unresolved_markers = (
        "待确认",
        "未明确",
        "原始依据未明确",
        "需确认",
        "不适用：原始依据未明确",
        "无法确定",
        "未定位",
        "未找到",
    )
    return any(marker in compact for marker in unresolved_markers)


def _extract_boundary_table_map(filters_text: str) -> dict[str, str]:
    for table in extract_markdown_tables(filters_text):
        headers = table.get("headers") or []
        if headers[:2] != ["项目", "内容"]:
            continue
        mapping = {}
        for row in table.get("rows") or []:
            key = str(row.get("项目") or "").strip()
            value = str(row.get("内容") or "").strip()
            if key:
                mapping[key] = value
        if any(key in mapping for key in ("基准时间点", "时间窗口粒度", "格式转换规则", "窗口起点计算规则", "窗口终点计算规则", "包含边界说明")):
            return mapping
    return {}


def _rule_requires_time_window_closure(rule: dict) -> bool:
    if rule.get("standard_level") != "hard":
        return False
    if not _is_time_window_rule(rule):
        return False
    text = f"{rule.get('content') or ''}\n{rule.get('basis_text') or ''}"
    if _contains_unresolved_marker(text):
        return False
    return True


def _boundary_value_has_structural_signal(label: str, value: str) -> bool:
    compact = _normalize_text(value)
    if not compact or _contains_unresolved_marker(compact):
        return False
    if label == "基准时间点":
        return bool(extract_field_paths(value))
    if label == "时间窗口粒度":
        return any(token in compact for token in ("日", "天", "月", "自然月", "年", "季度"))
    if label == "格式转换规则":
        return any(token in compact for token in ("格式", "转换", "截取", "yyyy", "YYYY", "日期", "月份"))
    if label in {"窗口起点计算规则", "窗口终点计算规则"}:
        has_formula = any(token in value for token in (">=", "<=", ">", "<", "=", "+", "-", "[", "]", "(", ")", "前", "后"))
        has_reference = bool(extract_field_paths(value)) or any(token in compact for token in ("进件时点", "申请时点", "申请月份", "基准时间点", "窗口起点", "窗口终点", "月初", "月末"))
        return has_formula or has_reference
    if label == "包含边界说明":
        return any(token in compact for token in ("包含", "不包含", "闭区间", "开区间", ">=<=", "><", "含"))
    return True


def validate_hard_logic_dependencies(summary: dict) -> None:
    rules = summary.get("rules") or []
    unresolved = [
        str(rule.get("rule_id"))
        for rule in rules
        if _contains_unresolved_marker(f"{rule.get('content') or ''}\n{rule.get('basis_text') or ''}")
    ]
    if unresolved:
        raise ValueError(
            "Phase 1 v4 标准仍包含未确定的主链口径，请先选择最可能逻辑并固化为 hard："
            + "、".join(unresolved)
        )

    filters_text = str(summary.get("filters") or "")
    time_window_hard_rules = [rule for rule in rules if _rule_requires_time_window_closure(rule)]
    if not time_window_hard_rules:
        return

    boundary_map = _extract_boundary_table_map(filters_text)
    required_labels = ["基准时间点", "时间窗口粒度", "格式转换规则", "窗口起点计算规则", "窗口终点计算规则", "包含边界说明"]
    missing = [label for label in required_labels if label not in boundary_map]
    if missing:
        raise ValueError(
            "Phase 1 标准的 hard 时间窗口规则缺少必要口径："
            + "、".join(missing)
        )

    invalid = [
        label for label in required_labels
        if not _boundary_value_has_structural_signal(label, boundary_map.get(label, ""))
    ]
    if invalid:
        raise ValueError(
            "Phase 1 标准的 hard 时间窗口规则口径不完整或不可验证："
            + "、".join(invalid)
        )


def build_standard_json_summary(standard_text: str) -> dict:
    meta = parse_frontmatter(standard_text)
    sections = extract_markdown_sections(standard_text)
    rules = []
    section_payloads = {}
    for dimension_ref, config in STANDARD_DIMENSIONS.items():
        section_text = extract_section_text(sections, config["heading"])
        section_payloads[config["key"]] = section_text
        rules.extend(build_rules_for_section(dimension_ref, section_text))
    selected_interpretations = extract_selected_interpretations(sections)
    selection_map = {
        item["selection_id"]: item
        for item in selected_interpretations
    }
    for rule in rules:
        selection_ids = [item for item in rule.get("evidence_ids") or [] if item.startswith("SEL-")]
        if selection_ids:
            rule["basis_type"] = "selected_interpretation"
            rule["selection_reason"] = "；".join(
                selection_map[item]["selection_reason"]
                for item in selection_ids
                if item in selection_map and selection_map[item].get("selection_reason")
            )

    source_metadata = {
        "phase": meta.get("phase", "1"),
        "factor_slug": meta.get("factor_slug", ""),
        "generated": meta.get("generated", ""),
        "input_checksum": meta.get("input_checksum", ""),
        "public_rules_checksum": meta.get("public_rules_checksum") or compute_public_rules_checksum(),
        "standard_json_checksum": "",
        "business_rules_fingerprint": business_rules_fingerprint(),
        "dimension_map": {
            dimension_ref: config["heading"]
            for dimension_ref, config in STANDARD_DIMENSIONS.items()
        },
    }
    summary = {
        "schema_version": STANDARD_JSON_SCHEMA_VERSION,
        "factor_name": meta.get("factor_name", ""),
        "input_parse": extract_section_text(sections, "0. 输入解析"),
        "inputs": section_payloads["inputs"],
        "field_lineage": section_payloads["field_lineage"],
        "filters": section_payloads["filters"],
        "array_handling": section_payloads["array_handling"],
        "calculation": section_payloads["calculation"],
        "default_strategy": section_payloads["default_strategy"],
        "ratio_or_derivation": section_payloads["ratio_or_derivation"],
        "conditional_branches": section_payloads["conditional_branches"],
        "selected_interpretations": selected_interpretations,
        "source_metadata": source_metadata,
        "rules": rules,
    }
    summary["source_metadata"]["standard_json_checksum"] = compute_standard_json_checksum(summary)
    return summary


def save_standard_json_summary(standard_file_id: str, standard_text: str, *, validate: bool = True) -> str:
    summary = build_standard_json_summary(standard_text)
    if validate:
        validate_standard_json_summary(summary)
    summary_name = standard_file_id.replace(".md", ".summary.json")
    OUTPUTS_DIR.mkdir(exist_ok=True)
    atomic_write_text(OUTPUTS_DIR / summary_name, json.dumps(summary, ensure_ascii=False, indent=2))
    standard_path = OUTPUTS_DIR / standard_file_id
    if standard_path.exists():
        updated_text = update_frontmatter_fields(
            read_text(standard_path),
            {
                "public_rules_checksum": summary["source_metadata"]["public_rules_checksum"],
                "standard_json_checksum": summary["source_metadata"]["standard_json_checksum"],
            },
        )
        atomic_write_text(standard_path, updated_text)
    return summary_name


def extract_saved_output_file(stdout: str, prefix: str, suffix: str = ".md") -> str | None:
    patterns = [
        rf"outputs/({re.escape(prefix)}[^`\s，。；;）)]+{re.escape(suffix)})",
        rf"已保存[：:]\s*outputs/({re.escape(prefix)}[^`\s，。；;）)]+{re.escape(suffix)})",
        rf"文件已保存至[：:]\s*outputs/({re.escape(prefix)}[^`\s，。；;）)]+{re.escape(suffix)})",
    ]
    for pattern in patterns:
        match = re.search(pattern, stdout or "")
        if match:
            name = Path(match.group(1)).name
            if (OUTPUTS_DIR / name).exists():
                return name
    candidates = sorted(OUTPUTS_DIR.glob(f"{prefix}*{suffix}"), key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0].name if candidates else None


def standard_summary_name_for(standard_file_id: str) -> str:
    return standard_file_id.replace(".md", ".summary.json")


def build_phase1_frontmatter(
    payload: dict,
    slug: str,
    factor_name: str,
    checksum: str,
    mock: bool = False,
) -> str:
    requirement = (payload.get("requirement") or "").strip()
    api_names = payload.get("apiNames") or []
    api_names = [str(x).strip() for x in api_names if str(x).strip()]
    return "\n".join([
        "---",
        "phase: 1",
        f"factor_slug: {slug}",
        f"factor_name: {factor_name}",
        f"generated: {time.strftime('%Y-%m-%d')}",
        f"input_checksum: {checksum}",
        f"public_rules_checksum: {compute_public_rules_checksum()}",
        f"mock: {'true' if mock else 'false'}",
        "input_summary:",
        f"  requirement: {requirement[:120]}",
        f"  api_docs: [{', '.join(api_names)}]",
        "---",
        "",
    ])


def normalize_phase1_output(
    output_text: str,
    payload: dict,
    slug: str,
    factor_name: str,
    checksum: str,
    mock: bool = False,
) -> str:
    body = strip_yaml_frontmatter(output_text)
    body = normalize_markdown_document_body(body, required_heading="# 标准加工规范")
    body = re.sub(
        r"^# 标准加工规范[^\n]*",
        f"# 标准加工规范 — {factor_name}",
        body,
        count=1,
    )
    return build_phase1_frontmatter(
        payload,
        slug,
        factor_name,
        checksum,
        mock=mock,
    ) + body


def build_review_frontmatter(payload: dict, slug: str, standard_file_id: str, checksum: str) -> str:
    return "\n".join([
        "---",
        "phase: 2",
        f"factor_slug: {slug}",
        f"standard_file: {standard_file_id}",
        f"generated: {time.strftime('%Y-%m-%d')}",
        f"review_checksum: {checksum}",
        "---",
        "",
    ])


def compute_review_checksum(payload: dict, standard_summary: dict | None = None) -> str:
    raw = json.dumps({
        "factorName": (payload.get("factorName") or "").strip(),
        "draft": (payload.get("draft") or "").strip(),
        "standardFileId": (payload.get("standardFileId") or "").strip(),
        "standardSummary": standard_summary or {},
        "businessRules": business_rules_fingerprint(),
    }, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def normalize_review_output(output_text: str, payload: dict, slug: str, standard_file_id: str, checksum: str) -> str:
    body = strip_yaml_frontmatter(output_text)
    body = normalize_markdown_document_body(body, required_heading="# 评审报告")
    factor_name = (payload.get("factorName") or "").strip()
    body = re.sub(
        r"^# 评审报告[^\n]*",
        f"# 评审报告 — {factor_name}",
        body,
        count=1,
    )
    return build_review_frontmatter(payload, slug, standard_file_id, checksum) + body


def compute_phase1_checksum(payload: dict) -> str:
    factor_name = (payload.get("factorName") or "").strip()
    requirement = (payload.get("requirement") or "").strip()
    api_names = sorted([str(x).strip() for x in (payload.get("apiNames") or []) if str(x).strip()])
    api_lookup = {doc["name"]: doc for doc in list_api_docs(include_content=True)}
    api_fingerprints = []
    for name in api_names:
        doc = api_lookup.get(name)
        if doc:
            api_fingerprints.append({
                "name": doc.get("name", ""),
                "path": doc.get("path", ""),
                "sha256": doc.get("content_sha256", ""),
            })
        else:
            api_fingerprints.append({"name": name, "missing": True})
    raw = json.dumps({
        "factorName": factor_name,
        "requirement": requirement,
        "apiDocs": api_fingerprints,
        "businessRules": business_rules_fingerprint(),
        "standardSchemaVersion": STANDARD_JSON_SCHEMA_VERSION,
        "promptContract": {
            "phase1Prompt": hashlib.sha256(read_text(PHASE1_PROMPT_PATH).encode("utf-8")).hexdigest(),
            "phase1Skill": hashlib.sha256(read_text(PHASE1_SKILL_PATH).encode("utf-8")).hexdigest(),
            "coreRules": hashlib.sha256(read_text(CORE_RULES_PROMPT_PATH).encode("utf-8")).hexdigest(),
            "phase1CoreRules": hashlib.sha256(read_text(PHASE1_CORE_RULES_PROMPT_PATH).encode("utf-8")).hexdigest(),
        },
    }, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def find_cached_phase1_standard(checksum: str, allow_mock: bool = False) -> dict | None:
    if not checksum or not OUTPUTS_DIR.exists():
        return None
    candidates = sorted(OUTPUTS_DIR.glob("standard-*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in candidates:
        try:
            text = read_text(path)
        except Exception:
            continue
        meta = parse_frontmatter(text)
        looks_like_mock = str(meta.get("mock", "false")).lower() == "true" or re.search(r"\bMock\b|mockDate|mockAmount", text)
        if looks_like_mock and not allow_mock:
            continue
        if "## 因子加工输入字段" not in text:
            continue
        if "## 多接口数据关联" in text:
            continue
        if meta.get("input_checksum") != checksum:
            continue
        if meta.get("public_rules_checksum") != compute_public_rules_checksum():
            continue
        if not meta.get("standard_json_checksum"):
            continue
        summary_file_id = path.name.replace(".md", ".summary.json")
        summary_path = OUTPUTS_DIR / summary_file_id
        if not summary_path.exists():
            continue
        try:
            summary = json.loads(read_text(summary_path))
            validate_standard_json_summary(summary)
            if compute_standard_json_checksum(summary) != meta.get("standard_json_checksum"):
                continue
        except Exception:
            continue
        return {
            "standardFileId": path.name,
            "standardSummaryFileId": summary_file_id,
            "stdout": text,
            "checksum": checksum,
            "cacheHit": True,
        }
    return None
