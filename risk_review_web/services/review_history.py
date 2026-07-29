from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from risk_review_web.paths import (
    OUTPUTS_DIR,
    REVIEW_RECORDS_PATH,
    ROOT,
    SCHEDULED_RUNS_PATH,
    VALIDATION_RUNS_PATH,
)
from risk_review_web.services.artifact_catalog import list_outputs as list_output_artifacts
from risk_review_web.services.review_evaluation import collect_review_stats, detect_verdict_text
from risk_review_web.services.time_utils import iso_time as _iso_time
from risk_review_web.utils import atomic_write_text, parse_frontmatter, read_text


REVIEW_RECORDS_LOCK = threading.Lock()


def _is_terminal_status(status: str | None) -> bool:
    """Return whether an in-memory task can be persisted as historical data."""
    return status in ("done", "error", "cancelled")


def list_outputs():
    """List review artifacts from the configured output directory."""
    return list_output_artifacts(OUTPUTS_DIR, ROOT)


def review_history_record_id(base: str) -> str:
    """Match the browser's unsigned 32-bit JavaScript hash for existing session IDs."""
    hash_value = 0
    encoded = str(base).encode("utf-16-be", errors="surrogatepass")
    for index in range(0, len(encoded), 2):
        code_unit = int.from_bytes(encoded[index:index + 2], "big")
        hash_value = ((hash_value * 31) + code_unit) & 0xFFFFFFFF
    return f"session-{hash_value:08x}"


def factor_name_from_output_name(name: str) -> str:
    """Derive a display-only factor token from a generated artifact name."""
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
    """Convert a review artifact into a history record when its content is readable."""
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
        "kind": (
            "json"
            if extension == "metadata.json"
            else "raw"
            if extension == "raw.txt"
            else "validation"
            if extension == "validation.json"
            else "md"
        ),
    }


def artifact_review_history_records() -> list[dict]:
    """Build history records for output files not represented by persisted tasks."""
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
            record["createdAt"] = datetime.fromtimestamp(
                output.get("mtime") or 0,
                tz=timezone.utc,
            ).isoformat().replace("+00:00", "Z")
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
        record["mode"] = (
            "one_click"
            if record["standardArtifacts"].get("md") and record["reviewArtifacts"].get("md")
            else "phase2_only"
            if record["reviewArtifacts"].get("md")
            else "phase1_only"
        )
    return sorted(grouped.values(), key=lambda item: str(item.get("createdAt") or ""), reverse=True)


def parse_review_records() -> list[dict]:
    """Read persisted review records using a tolerant empty-on-corruption policy."""
    if not REVIEW_RECORDS_PATH.exists():
        return []
    try:
        data = json.loads(read_text(REVIEW_RECORDS_PATH))
    except Exception:
        return []
    return data if isinstance(data, list) else []


def save_review_records(records: list[dict]) -> None:
    """Persist a bounded, newest-first review history snapshot atomically."""
    REVIEW_RECORDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        REVIEW_RECORDS_PATH,
        json.dumps(records[-5000:], ensure_ascii=False, indent=2),
    )


def _review_record_artifact_names(record: dict) -> set[str]:
    """Collect safe artifact basenames referenced by a history record."""
    names = set()
    for group_name in ("standardArtifacts", "reviewArtifacts"):
        for file_name in (record.get(group_name) or {}).values():
            if file_name:
                names.add(str(file_name))
    for item in record.get("items") or []:
        for key in ("standardFileId", "standardMetadataFileId", "reviewFileId"):
            if item.get(key):
                names.add(str(item[key]))
    return names


def _task_record_title(task: dict) -> str:
    """Choose the most useful human-readable title available on a task."""
    context = task.get("context") or {}
    task_type = str(task.get("taskType") or "")
    if context.get("taskName"):
        return str(context["taskName"])
    if context.get("factorName"):
        return str(context["factorName"])
    labels = {
        "batch": "批量评审",
        "regression": "质量验证",
        "review-mode-experiment": "模式对比实验",
        "phase1": "标准生成",
        "phase2": "单因子评审",
    }
    return f"{labels.get(task_type, '评审任务')} · {str(task.get('id') or '')[:8]}"


def _task_to_review_record(task: dict, *, include_active: bool = False) -> dict | None:
    """Project an in-memory task into the stable review-history schema."""
    status = str(task.get("status") or "")
    if not include_active and not _is_terminal_status(status):
        return None
    task_type = str(task.get("taskType") or "")
    if task_type in {"child", "scheduled-phase1", "scheduled-phase2"}:
        return None
    result = task.get("result") or {}
    context = task.get("context") or {}
    items = result.get("items") if isinstance(result, dict) else None
    if not isinstance(items, list):
        items = []
    standard_file = result.get("standardFileId") or context.get("standardFileId")
    standard_metadata = result.get("standardMetadataFileId")
    if standard_file and not standard_metadata:
        standard_stem = Path(str(standard_file)).stem
        for metadata_candidate in (
            f"{standard_stem}.metadata.json",
            f"{standard_stem}.summary.json",
        ):
            if (OUTPUTS_DIR / metadata_candidate).is_file():
                standard_metadata = metadata_candidate
                break
    review_file = result.get("reviewFileId")
    conclusion = "unreviewable"
    risk_count = 0
    if review_file:
        try:
            review_text = read_text(OUTPUTS_DIR / Path(str(review_file)).name)
            conclusion = detect_verdict_text(review_text)
            risk_count = int(collect_review_stats(review_text).get("fail") or 0)
        except OSError:
            pass
    done_items = [item for item in items if item.get("status") in {"done", "pass", "fail"}]
    failed_items = [item for item in items if item.get("status") in {"error", "skip"}]
    if items:
        conclusions = [
            detect_verdict_text(str(item.get("raw") or ""))
            for item in done_items
            if item.get("raw")
        ]
        if conclusions and all(value == "pass" for value in conclusions):
            conclusion = "pass"
        elif any(value == "fail" for value in conclusions):
            conclusion = "fail"
        elif failed_items:
            conclusion = "unreviewable"
        risk_count = sum(
            int((item.get("summary") or {}).get("fail") or 0)
            for item in items
        )
    source = {
        "batch": "batch",
        "regression": "validation",
        "review-mode-experiment": "experiment",
        "phase1": "single",
        "phase2": "single",
    }.get(task_type, task_type or "single")
    created = float(task.get("created") or time.time())
    updated = float(task.get("updated") or created)
    progress = {
        "total": len(items) if items else 1,
        "completed": len(done_items) if items else int(task.get("status") == "done"),
        "failed": len(failed_items) if items else int(task.get("status") == "error"),
    }
    return {
        "recordId": f"task-{task.get('id')}",
        "id": task.get("id"),
        "taskId": task.get("id"),
        "taskType": task_type,
        "parentTaskId": task.get("parentTaskId"),
        "clientId": task.get("clientId") or "",
        "status": status,
        "context": context,
        "progress": progress,
        "title": _task_record_title(task),
        "factorName": context.get("factorName") or _task_record_title(task),
        "source": source,
        "taskStatus": status,
        "conclusion": conclusion,
        "riskCount": risk_count,
        "createdAt": datetime.fromtimestamp(created, tz=timezone.utc).isoformat().replace("+00:00", "Z"),
        "updatedAt": datetime.fromtimestamp(updated, tz=timezone.utc).isoformat().replace("+00:00", "Z"),
        "archived": False,
        "summary": progress,
        "standardArtifacts": {
            **({"md": standard_file} if standard_file else {}),
            **({"json": standard_metadata} if standard_metadata else {}),
        },
        "reviewArtifacts": {"md": review_file} if review_file else {},
        "items": items,
    }


def _artifact_conclusion(file_name: str | None) -> tuple[str, int]:
    """Read a report artifact and return its verdict and hard-issue count."""
    if not file_name:
        return "unreviewable", 0
    try:
        text = read_text(OUTPUTS_DIR / Path(str(file_name)).name)
    except OSError:
        return "unreviewable", 0
    return detect_verdict_text(text), int(collect_review_stats(text).get("fail") or 0)


def _load_json_list(path: Path) -> list[dict]:
    """Best-effort read a JSON list; missing or malformed state produces no records."""
    if not path.is_file():
        return []
    try:
        data = json.loads(read_text(path))
    except Exception:
        return []
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def _external_review_records() -> list[dict]:
    """Project validation and scheduled-run state into review-history records."""
    records: list[dict] = []
    for run in _load_json_list(SCHEDULED_RUNS_PATH):
        status = str(run.get("status") or "")
        if status in {"queued", "running", "cancelling"}:
            continue
        items = []
        conclusions = []
        risk_count = 0
        for item in run.get("items") or []:
            if not isinstance(item, dict):
                continue
            review_file = item.get("reviewFile")
            conclusion, item_risk = _artifact_conclusion(review_file)
            if review_file:
                conclusions.append(conclusion)
            risk_count += item_risk
            items.append({
                **item,
                "standardFileId": item.get("standardFile"),
                "reviewFileId": review_file,
                "conclusion": conclusion,
                "riskCount": item_risk,
            })
        conclusion = (
            "fail" if "fail" in conclusions
            else "pass" if conclusions and all(value == "pass" for value in conclusions)
            else "unreviewable"
        )
        task_status = (
            "cancelled" if status == "cancelled"
            else "error" if status == "failed"
            else "done"
        )
        records.append({
            "recordId": f"scheduled-{run.get('runId')}",
            "taskId": run.get("runId"),
            "title": run.get("taskName") or "定时评审",
            "factorName": run.get("taskName") or "定时评审",
            "source": "scheduled",
            "taskStatus": task_status,
            "conclusion": conclusion,
            "riskCount": risk_count,
            "createdAt": run.get("createdAt") or run.get("startedAt") or _iso_time(),
            "updatedAt": run.get("finishedAt") or run.get("startedAt") or _iso_time(),
            "archived": False,
            "summary": {
                "total": int(run.get("total") or len(items)),
                "completed": int(run.get("success") or 0),
                "failed": int(run.get("failed") or 0),
            },
            "standardArtifacts": {},
            "reviewArtifacts": {},
            "items": items,
        })

    validation_groups: dict[str, list[dict]] = {}
    for run in _load_json_list(VALIDATION_RUNS_PATH):
        status = str(run.get("status") or "")
        if status in {"pending", "queued", "running", "cancelling"}:
            continue
        validation_groups.setdefault(str(run.get("groupId") or run.get("runId")), []).append(run)
    for group_id, runs in validation_groups.items():
        items = []
        conclusions = []
        risk_count = 0
        for run in runs:
            review_file = run.get("reviewFile")
            conclusion = str(run.get("actualConclusion") or "")
            item_risk = 0
            if review_file:
                detected, item_risk = _artifact_conclusion(review_file)
                conclusion = conclusion or detected
            if conclusion:
                conclusions.append(conclusion)
            risk_count += item_risk
            items.append({
                **run,
                "factorName": run.get("caseName"),
                "standardFileId": run.get("standardFile"),
                "standardMetadataFileId": run.get("standardMetadataFile"),
                "reviewFileId": review_file,
                "conclusion": conclusion or "unreviewable",
                "riskCount": item_risk,
            })
        failed = sum(1 for item in runs if item.get("status") in {"failed", "cancelled"})
        conclusion = (
            "fail" if "fail" in conclusions
            else "pass" if conclusions and all(value == "pass" for value in conclusions)
            else "unreviewable"
        )
        created_at = min((str(item.get("startedAt") or item.get("createdAt") or "") for item in runs), default="")
        updated_at = max((str(item.get("finishedAt") or item.get("startedAt") or "") for item in runs), default="")
        records.append({
            "recordId": f"validation-{group_id}",
            "taskId": group_id,
            "title": f"质量验证 · {group_id[:12]}",
            "factorName": "质量验证",
            "source": "validation",
            "taskStatus": "error" if failed == len(runs) else "done",
            "conclusion": conclusion,
            "riskCount": risk_count,
            "createdAt": created_at or _iso_time(),
            "updatedAt": updated_at or created_at or _iso_time(),
            "archived": False,
            "summary": {
                "total": len(runs),
                "completed": len(runs) - failed,
                "failed": failed,
            },
            "standardArtifacts": {},
            "reviewArtifacts": {},
            "items": items,
        })
    return records

