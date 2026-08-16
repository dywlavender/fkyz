from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from risk_review_web.repositories.task_repository import TaskRepository


@dataclass(frozen=True, slots=True)
class BatchWorkflowDependencies:
    """Explicit application services required by batch workflows."""

    task_repository: TaskRepository
    tasks: Mapping[str, Mapping[str, Any]]
    task_lock: Any
    create_task_record: Callable[..., Any]
    task_cancel_requested: Callable[[str], bool]
    run_agent_phase1: Callable[[dict, str], Any]
    run_agent_phase2: Callable[[dict, str], Any]
    task_log: Callable[[str, str], Any]
    finish_task: Callable[[str, str, dict | None], Any]
    list_outputs: Callable[[], list[str]]
    classify_task_error: Callable[[str], Mapping[str, Any]]
    collect_review_stats: Callable[[str], Mapping[str, Any]]
    detect_verdict_text: Callable[[str], str]


@dataclass(frozen=True, slots=True)
class RegressionWorkflowDependencies:
    """Explicit application services required by validation workflows."""

    batch: BatchWorkflowDependencies
    parse_examples: Callable[[], list[dict]]
    examples_lock: Any
    task_output_token: Callable[[str], str]
    string_list: Callable[[Any], list[str]]
    optional_nonnegative_int: Callable[[Any], int | None]
    validation_expected_code: Callable[[Any], str]
    expected_matches_verdict: Callable[[dict, str], bool]
    evaluate_issue_expectations: Callable[[dict, str], dict]
    task_cancel_requested: Callable[[str], bool]
    mark_task_cancelled: Callable[[str, str], Any]
    cancel_validation_runs_for_task: Callable[[str], Any]
    task_repository: TaskRepository
    task_log: Callable[[str, str], Any]
    upsert_validation_run: Callable[[dict], dict]
    detect_verdict_text: Callable[[str], str]
    collect_review_stats: Callable[[str], Mapping[str, Any]]
    classify_task_error: Callable[[str], Mapping[str, Any]]
    list_outputs: Callable[[], list[str]]
    finish_task: Callable[[str, str, dict | None], Any]
    iso_time: Callable[[], str]


@dataclass(frozen=True, slots=True)
class ExperimentWorkflowDependencies:
    """Explicit application services required by comparison experiments."""

    batch: BatchWorkflowDependencies
    parse_examples: Callable[[], list[dict]]
    examples_lock: Any
    string_list: Callable[[Any], list[str]]
    optional_nonnegative_int: Callable[[Any], int | None]
    validation_expected_code: Callable[[Any], str]
    expected_matches_verdict: Callable[[dict, str], bool]
    evaluate_issue_expectations: Callable[[dict, str], dict]
    iso_time: Callable[[], str]
    task_repository: TaskRepository
    upsert_review_mode_experiment: Callable[[dict], dict]
    task_log: Callable[[str, str], Any]
    task_cancel_requested: Callable[[str], bool]
    create_task_record: Callable[..., Any]
    agent_result_payload: Callable[..., dict]
    run_model_prompt: Callable[..., Any]
    build_direct_review_prompt: Callable[..., str]
    output_dir: Path
    atomic_write_text: Callable[[Path, str], Any]
    task_output_token: Callable[[str], str]
    finish_task: Callable[[str, str, dict | None], Any]
    list_outputs: Callable[[], list[str]]
