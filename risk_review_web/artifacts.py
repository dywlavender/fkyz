from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

from .api_docs import list_api_docs
from .business_rules import business_rules_fingerprint
from .paths import (
    OUTPUTS_DIR,
    PHASE1_PROCESSING_SPEC_PATH,
    PHASE1_PROMPT_PATH,
    PHASE1_SKILL_PATH,
    PHASE2_PROMPT_PATH,
    PHASE2_REVIEW_SPEC_PATH,
    PHASE2_SKILL_PATH,
)
from .utils import atomic_write_text, parse_frontmatter, read_text


STANDARD_SCHEMA_VERSION = "risk_review_standard_v5"
STANDARD_METADATA_SCHEMA_VERSION = "risk_review_standard_metadata_v5"
STANDARD_REQUIRED_HEADINGS = (
    "因子加工输入字段",
    "标准加工逻辑",
    "默认值兜底",
)


def canonical_json_dumps(value) -> str:
    """Serialize JSON deterministically for checksum calculation."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    """Return the SHA-256 digest of UTF-8 text."""
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def strip_yaml_frontmatter(text: str) -> str:
    """Remove one leading frontmatter block from Markdown text."""
    return re.sub(r"^---\n[\s\S]*?\n---\s*", "", str(text), count=1).strip()


def normalize_markdown_document_body(text: str, required_heading: str | None = None) -> str:
    """Extract a clean Markdown body from common Claude output wrappers."""
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
    return re.sub(
        r"\n+(?:已保存|文件已保存至)[：:]\s*outputs/[^\n]+\.md\s*$",
        "",
        body,
    ).strip()


def extract_markdown_sections(text: str) -> dict[str, str]:
    """Index level-two Markdown sections by heading text."""
    body = strip_yaml_frontmatter(text)
    sections: dict[str, str] = {}
    matches = list(re.finditer(r"^##\s+(.+?)\s*$", body, re.M))
    for index, match in enumerate(matches):
        title = match.group(1).strip()
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        sections[title] = body[start:end].strip()
    return sections


def split_markdown_table(row: str) -> list[str]:
    """Split a simple pipe-delimited Markdown table row into cells."""
    value = row.strip()
    if value.startswith("|"):
        value = value[1:]
    if value.endswith("|"):
        value = value[:-1]
    return [cell.strip() for cell in value.split("|")]


def extract_markdown_tables(section_text: str) -> list[dict[str, list[dict[str, str]]]]:
    """Parse simple Markdown tables contained in one section."""
    lines = str(section_text or "").splitlines()
    tables: list[dict[str, list[dict[str, str]]]] = []
    index = 0
    while index + 1 < len(lines):
        if not lines[index].lstrip().startswith("|") or not lines[index + 1].lstrip().startswith("|"):
            index += 1
            continue
        headers = split_markdown_table(lines[index])
        separator = split_markdown_table(lines[index + 1])
        if len(headers) != len(separator) or not all(re.fullmatch(r":?-{3,}:?", cell) for cell in separator):
            index += 1
            continue
        rows: list[dict[str, str]] = []
        index += 2
        while index < len(lines) and lines[index].lstrip().startswith("|"):
            cells = split_markdown_table(lines[index])
            if len(cells) < len(headers):
                cells.extend([""] * (len(headers) - len(cells)))
            rows.append({header: cells[pos] for pos, header in enumerate(headers)})
            index += 1
        tables.append({"headers": headers, "rows": rows})
    return tables


def _load_processing_spec(path: Path, schema_version: str, label: str) -> dict:
    spec = json.loads(read_text(path))
    if spec.get("schema_version") != schema_version:
        raise ValueError(f"{label} schema_version 无效。")
    return spec


def load_phase1_processing_spec() -> dict:
    """Load and schema-check the Phase 1 processing specification."""
    return _load_processing_spec(
        PHASE1_PROCESSING_SPEC_PATH,
        "factor_processing_phase1_spec_v1",
        "Phase 1 因子加工开发规范",
    )


def load_phase2_review_spec() -> dict:
    """Load and schema-check the Phase 2 review specification."""
    return _load_processing_spec(
        PHASE2_REVIEW_SPEC_PATH,
        "factor_processing_phase2_spec_v1",
        "Phase 2 因子加工评审规范",
    )


def _processing_spec_fingerprint(path: Path) -> dict:
    spec_text = read_text(path)
    spec = json.loads(spec_text)
    return {
        "schema_version": spec.get("schema_version", ""),
        "spec_version": spec.get("spec_version", ""),
        "checksum": sha256_text(spec_text),
    }


def phase1_processing_spec_fingerprint() -> dict:
    """Return Phase 1 specification identity and content checksum."""
    return _processing_spec_fingerprint(PHASE1_PROCESSING_SPEC_PATH)


def phase2_review_spec_fingerprint() -> dict:
    """Return Phase 2 specification identity and content checksum."""
    return _processing_spec_fingerprint(PHASE2_REVIEW_SPEC_PATH)


def extract_logic_types(standard_text: str) -> list[str]:
    """Extract distinct logic-type codes from a standard's processing table."""
    section = extract_markdown_sections(standard_text).get("标准加工逻辑", "")
    logic_types: list[str] = []
    for table in extract_markdown_tables(section):
        if "逻辑类型" not in table["headers"]:
            continue
        for row in table["rows"]:
            value = str(row.get("逻辑类型") or "").strip().strip("`")
            if value and value not in logic_types:
                logic_types.append(value)
    return logic_types


def validate_phase1_standard_basic_output(standard_text: str) -> None:
    """Enforce the non-skippable structure required for a Phase 1 artifact."""
    body = strip_yaml_frontmatter(standard_text)
    if not body.strip():
        raise ValueError("Claude 未输出 Phase 1 标准加工规范正文。")
    if not re.match(r"#\s+标准加工规范\b", body.lstrip()):
        raise ValueError("Claude 未输出完整 Phase 1 标准加工规范正文，stdout 必须以「# 标准加工规范」开头。")
    sections = extract_markdown_sections(body)
    missing = [heading for heading in STANDARD_REQUIRED_HEADINGS if heading not in sections]
    if missing:
        raise ValueError("Phase 1 标准加工规范缺少必要章节：" + "、".join(missing))


def validate_phase2_review_basic_output(review_text: str) -> None:
    """Enforce the non-skippable structure required for a Phase 2 artifact."""
    body = strip_yaml_frontmatter(review_text)
    if not body.strip():
        raise ValueError("Claude 未输出 Phase 2 评审报告正文。")
    if not re.match(r"#\s+评审报告\b", body.lstrip()):
        raise ValueError("Claude 未输出完整 Phase 2 评审报告正文，stdout 必须以「# 评审报告」开头。")
    sections = extract_markdown_sections(body)
    missing = [heading for heading in ("总体结论", "汇总问题清单") if heading not in sections]
    if missing:
        raise ValueError("Phase 2 评审报告缺少必要章节：" + "、".join(missing))


def build_standard_metadata(standard_file_id: str, standard_text: str, payload: dict | None = None) -> dict:
    """Build v5 metadata describing a frozen Phase 1 standard and its inputs."""
    validate_phase1_standard_basic_output(standard_text)
    meta = parse_frontmatter(standard_text)
    spec = phase1_processing_spec_fingerprint()
    return {
        "schema_version": STANDARD_METADATA_SCHEMA_VERSION,
        "standard_schema_version": STANDARD_SCHEMA_VERSION,
        "standard_file": standard_file_id,
        "standard_body_checksum": sha256_text(strip_yaml_frontmatter(standard_text)),
        "input_checksum": meta.get("input_checksum", ""),
        "generated": meta.get("generated", ""),
        "mock": str(meta.get("mock", "false")).lower() == "true",
        "display_metadata": {
            "factor_name": meta.get("factor_name", ""),
            "factor_slug": meta.get("factor_slug", ""),
        },
        "source_inputs": {
            "requirement": str((payload or {}).get("requirement") or ""),
            "api_docs": [
                str(item).strip()
                for item in (payload or {}).get("apiNames") or []
                if str(item).strip()
            ],
        },
        "phase1_spec": spec,
        "logic_types": extract_logic_types(standard_text),
        "generation_contract": {
            "phase1_prompt_checksum": sha256_text(read_text(PHASE1_PROMPT_PATH)),
            "phase1_skill_checksum": sha256_text(read_text(PHASE1_SKILL_PATH)),
        },
    }


def standard_metadata_name_for(standard_file_id: str) -> str:
    """Derive the metadata filename paired with a standard Markdown file."""
    return standard_file_id.replace(".md", ".metadata.json")


def save_standard_metadata(standard_file_id: str, standard_text: str, payload: dict | None = None) -> str:
    """Validate and persist metadata for a Phase 1 standard."""
    metadata = build_standard_metadata(standard_file_id, standard_text, payload)
    metadata_name = standard_metadata_name_for(standard_file_id)
    OUTPUTS_DIR.mkdir(exist_ok=True)
    atomic_write_text(OUTPUTS_DIR / metadata_name, json.dumps(metadata, ensure_ascii=False, indent=2))
    return metadata_name


def read_phase1_standard(payload: dict) -> tuple[str, Path]:
    """Resolve and validate the Phase 1 standard selected for Phase 2."""
    standard_file_id = (payload.get("standardFileId") or "").strip()
    if not standard_file_id:
        raise ValueError("Phase 1 标准文件 ID 不能为空。")
    std_path = OUTPUTS_DIR / Path(standard_file_id).name
    if not std_path.exists():
        raise FileNotFoundError(f"Phase 1 标准文件未找到：{standard_file_id}")
    standard_text = read_text(std_path)
    validate_phase1_standard_basic_output(standard_text)
    return standard_text, std_path


def build_phase1_frontmatter(
    payload: dict,
    slug: str,
    factor_name: str,
    checksum: str,
    mock: bool = False,
) -> str:
    """Build reproducibility frontmatter for a Phase 1 standard."""
    requirement = (payload.get("requirement") or "").strip()
    api_names = [str(item).strip() for item in payload.get("apiNames") or [] if str(item).strip()]
    spec = phase1_processing_spec_fingerprint()
    return "\n".join([
        "---",
        "phase: 1",
        f"schema_version: {STANDARD_SCHEMA_VERSION}",
        f"factor_slug: {slug}",
        f"factor_name: {factor_name}",
        f"generated: {time.strftime('%Y-%m-%d')}",
        f"input_checksum: {checksum}",
        f"phase1_spec_version: {spec['spec_version']}",
        f"phase1_spec_checksum: {spec['checksum']}",
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
    """Normalize Claude Phase 1 output and prepend canonical frontmatter."""
    body = normalize_markdown_document_body(strip_yaml_frontmatter(output_text), required_heading="# 标准加工规范")
    body = re.sub(r"^# 标准加工规范[^\n]*", f"# 标准加工规范 — {factor_name}", body, count=1)
    return build_phase1_frontmatter(payload, slug, factor_name, checksum, mock=mock) + body


def build_review_frontmatter(payload: dict, slug: str, standard_file_id: str, checksum: str) -> str:
    """Build reproducibility frontmatter for a Phase 2 review report."""
    return "\n".join([
        "---",
        "phase: 2",
        f"schema_version: {STANDARD_SCHEMA_VERSION}",
        f"factor_slug: {slug}",
        f"standard_file: {standard_file_id}",
        f"generated: {time.strftime('%Y-%m-%d')}",
        f"review_checksum: {checksum}",
        "---",
        "",
    ])


def compute_review_checksum(payload: dict, standard_text: str = "") -> str:
    """Hash every business input that can affect a Phase 2 review."""
    raw = {
        "draft": (payload.get("draft") or "").strip(),
        "requirement": (payload.get("requirement") or "").strip(),
        "standardBody": strip_yaml_frontmatter(standard_text),
        "phase2Spec": phase2_review_spec_fingerprint(),
        "phase2Prompt": sha256_text(read_text(PHASE2_PROMPT_PATH)),
        "phase2Skill": sha256_text(read_text(PHASE2_SKILL_PATH)),
    }
    return sha256_text(canonical_json_dumps(raw))


def normalize_review_output(
    output_text: str,
    payload: dict,
    slug: str,
    standard_file_id: str,
    checksum: str,
) -> str:
    """Normalize Claude Phase 2 output and prepend canonical frontmatter."""
    body = normalize_markdown_document_body(strip_yaml_frontmatter(output_text), required_heading="# 评审报告")
    factor_name = (payload.get("factorName") or "").strip()
    body = re.sub(r"^# 评审报告[^\n]*", f"# 评审报告 — {factor_name}", body, count=1)
    return build_review_frontmatter(payload, slug, standard_file_id, checksum) + body


def compute_phase1_checksum(payload: dict) -> str:
    """Hash every business input that can affect a Phase 1 standard."""
    requirement = (payload.get("requirement") or "").strip()
    api_names = sorted(str(item).strip() for item in payload.get("apiNames") or [] if str(item).strip())
    api_lookup = {doc["name"]: doc for doc in list_api_docs(include_content=True)}
    api_fingerprints = []
    for name in api_names:
        doc = api_lookup.get(name)
        api_fingerprints.append(
            {
                "name": name,
                "path": (doc or {}).get("path", ""),
                "sha256": (doc or {}).get("content_sha256", ""),
                "missing": doc is None,
            }
        )
    raw = {
        "requirement": requirement,
        "apiDocs": api_fingerprints,
        "businessRules": business_rules_fingerprint(),
        "standardSchemaVersion": STANDARD_SCHEMA_VERSION,
        "phase1Spec": phase1_processing_spec_fingerprint(),
        "phase1Prompt": sha256_text(read_text(PHASE1_PROMPT_PATH)),
        "phase1Skill": sha256_text(read_text(PHASE1_SKILL_PATH)),
    }
    return sha256_text(canonical_json_dumps(raw))


def find_cached_phase1_standard(checksum: str, allow_mock: bool = False) -> dict | None:
    """Return the newest valid standard matching an input checksum."""
    if not checksum or not OUTPUTS_DIR.exists():
        return None
    candidates = sorted(OUTPUTS_DIR.glob("standard-*.md"), key=lambda path: path.stat().st_mtime, reverse=True)
    for path in candidates:
        try:
            text = read_text(path)
            meta = parse_frontmatter(text)
            if meta.get("schema_version") != STANDARD_SCHEMA_VERSION:
                continue
            if meta.get("input_checksum") != checksum:
                continue
            if str(meta.get("mock", "false")).lower() == "true" and not allow_mock:
                continue
            validate_phase1_standard_basic_output(text)
            metadata_name = standard_metadata_name_for(path.name)
            metadata_path = OUTPUTS_DIR / metadata_name
            if not metadata_path.exists():
                metadata_name = save_standard_metadata(path.name, text)
            return {
                "standardFileId": path.name,
                "standardMetadataFileId": metadata_name,
                "stdout": text,
                "checksum": checksum,
                "cacheHit": True,
            }
        except Exception:
            continue
    return None
