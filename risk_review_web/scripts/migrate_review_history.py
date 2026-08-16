#!/usr/bin/env python3
"""One-time review-history migration and orphan-artifact report."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from risk_review_web.environment import load_project_env

load_project_env()

from risk_review_web.paths import OUTPUTS_DIR
from risk_review_web.repositories.review_record_repository import ReviewRecordRepository
from risk_review_web.services.review_evaluation import collect_review_stats, detect_verdict_text
from risk_review_web.services.review_history import list_outputs
from risk_review_web.utils import parse_frontmatter, read_text


def review_history_record_id(base: str) -> str:
    """Match the browser's unsigned 32-bit JavaScript hash for old records."""
    hash_value = 0
    encoded = str(base).encode("utf-16-be", errors="surrogatepass")
    for index in range(0, len(encoded), 2):
        code_unit = int.from_bytes(encoded[index:index + 2], "big")
        hash_value = ((hash_value * 31) + code_unit) & 0xFFFFFFFF
    return f"session-{hash_value:08x}"


def factor_name_from_output_name(name: str) -> str:
    """Derive a display-only factor token from an old artifact name."""
    current = re.match(
        r"^(?:standard|review)-(.+?)-\d{4}-\d{2}-\d{2}-[a-z0-9]{8}-[a-z0-9]{8}(?:\.|$)",
        name,
        re.I,
    )
    if current:
        return current.group(1).replace("-", " ").strip()
    legacy = re.match(
        r"^(?:standard|review)-(.+)-\d{4}-\d{2}-\d{2}-[a-z0-9]{8}\.",
        name,
        re.I,
    )
    return legacy.group(1).replace("-", " ").strip() if legacy else name


def parse_review_history_artifact(name: str) -> dict | None:
    """Recognize legacy output names for the explicit migration command."""
    legacy = re.match(
        r"^(standard|review)-(.+)\.(standard|review)\.(md|json|raw\.txt|validation\.json)$",
        name,
        re.I,
    )
    if legacy and legacy.group(1).lower() == legacy.group(3).lower():
        extension = legacy.group(4).lower()
        return {
            "phase": legacy.group(1).lower(),
            "base": legacy.group(2),
            "kind": "raw" if extension == "raw.txt" else "validation" if extension == "validation.json" else extension,
        }
    current = re.match(
        r"^(standard|review)-(.+)\.(md|metadata\.json|raw\.txt|validation\.json)$",
        name,
        re.I,
    )
    if not current:
        return None
    extension = current.group(3).lower()
    return {
        "phase": current.group(1).lower(),
        "base": f"{current.group(1).lower()}-{current.group(2)}",
        "kind": "json" if extension == "metadata.json" else "raw" if extension == "raw.txt" else "validation" if extension == "validation.json" else "md",
    }


def artifact_review_history_records() -> list[dict]:
    """Build migration candidates from artifacts absent from the index."""
    grouped: dict[str, dict] = {}
    created_at_numbers: dict[str, int] = {}
    for output in list_outputs():
        parsed = parse_review_history_artifact(output["name"])
        if not parsed:
            continue
        review_text = ""
        base = parsed["base"]
        if parsed["phase"] == "review" and parsed["kind"] == "md":
            try:
                review_text = read_text(OUTPUTS_DIR / output["name"])
                linked_standard = str(parse_frontmatter(review_text).get("standard_file") or "").strip()
                if linked_standard and Path(linked_standard).name == linked_standard:
                    base = Path(linked_standard).stem
            except OSError:
                review_text = ""
        record = grouped.get(base)
        if record is None:
            record = {
                "recordId": review_history_record_id(base),
                "factorName": factor_name_from_output_name(output["name"]),
                "createdAt": datetime.fromtimestamp(output.get("mtime") or 0, tz=timezone.utc).isoformat().replace("+00:00", "Z"),
                "source": "validation" if "vrun-" in base else "single",
                "mode": "phase1_only",
                "conclusion": "unreviewable",
                "riskCount": 0,
                "standardArtifacts": {},
                "reviewArtifacts": {},
            }
            grouped[base] = record
            created_at_numbers[base] = int(output.get("mtime") or 0)
        if int(output.get("mtime") or 0) > created_at_numbers[base]:
            created_at_numbers[base] = int(output.get("mtime") or 0)
            record["createdAt"] = datetime.fromtimestamp(output.get("mtime") or 0, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        artifact_group = record["standardArtifacts"] if parsed["phase"] == "standard" else record["reviewArtifacts"]
        artifact_group[parsed["kind"]] = output["name"]
        if parsed["phase"] == "review" and parsed["kind"] == "md":
            try:
                review_text = review_text or read_text(OUTPUTS_DIR / output["name"])
                record["conclusion"] = detect_verdict_text(review_text)
                record["riskCount"] = int(collect_review_stats(review_text).get("fail") or 0)
            except OSError:
                record["conclusion"] = "unreviewable"
                record["riskCount"] = 0
        record["mode"] = "one_click" if record["standardArtifacts"].get("md") and record["reviewArtifacts"].get("md") else "phase2_only" if record["reviewArtifacts"].get("md") else "phase1_only"
    return sorted(grouped.values(), key=lambda item: str(item.get("createdAt") or ""), reverse=True)


def migrate(*, apply: bool) -> dict:
    repository = ReviewRecordRepository()
    existing = repository.list(include_deleted=True)
    by_id = {str(item.get("recordId") or ""): item for item in existing}
    discovered = artifact_review_history_records()
    additions = [
        item for item in discovered
        if str(item.get("recordId") or "") and str(item.get("recordId") or "") not in by_id
    ]
    report = repository.migrate_from_legacy_outputs(dry_run=not apply)
    report.update({
        "existingRecords": len(existing),
        "discoveredRecords": len(discovered),
        "candidateRecords": len(additions),
        "applied": False,
    })
    if apply:
        for record in additions:
            repository.upsert(record)
        report["applied"] = True
        report["migrated"] = len(additions)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="迁移 review_records.json 并报告 outputs/孤立产物")
    parser.add_argument("--apply", action="store_true", help="写入候选历史记录；默认只做 dry-run")
    args = parser.parse_args()
    print(json.dumps(migrate(apply=args.apply), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
