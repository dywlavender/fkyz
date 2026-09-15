from __future__ import annotations

"""Composition root for the risk-review application.

HTTP handlers import this module for assembled services and compatibility
callables.  Domain behavior lives in the repositories, services, workflows,
and scheduling package instead of being implemented here.
"""

import os

from risk_review_web.environment import load_project_env

load_project_env()

from risk_review_web.api_docs import get_api_doc, list_api_docs
from risk_review_web.artifacts import normalize_requirement_review_output, validate_requirement_review_basic_output
from risk_review_web.error_classification import attach_error_metadata, classify_task_error
from risk_review_web.paths import (
    BUSINESS_RULES_PATH,
    EXAMPLES_PATH,
    OUTPUTS_DIR,
    PHASE1_PROCESSING_SPEC_PATH,
    PHASE1_SKILL_PATH,
    PHASE2_REVIEW_SPEC_PATH,
    PHASE2_SKILL_PATH,
    REQUIREMENT_REVIEW_SKILL_PATH,
    REQUIREMENT_REVIEW_SPEC_PATH,
    ROOT,
    RULE_ENGINE_BUILD_PATH,
    STATIC_DIR,
)
from risk_review_web.prompt_builder import (
    build_direct_review_prompt,
    build_phase1_prompt,
    build_phase2_prompt,
    build_requirement_review_prompt,
)
from risk_review_web.repositories.review_record_repository import ReviewRecordRepository
from risk_review_web.repositories.task_repository import TaskRegistryView, TaskRepository
from risk_review_web.runtime_settings import get_runtime_settings, update_runtime_settings
from risk_review_web.services.health import build_system_health
from risk_review_web.services.model_runtime import agent_health, run_agent_call
from risk_review_web.services.requirement_review import (
    create_requirement_review_workspace,
    mock_requirement_review_markdown,
    selected_api_documents,
)
from risk_review_web.services.review_evaluation import (
    collect_review_stats,
    detect_verdict_text,
    evaluate_issue_expectations,
    expected_matches_verdict,
    optional_nonnegative_int as _optional_nonnegative_int,
    string_list as _string_list,
    validation_expected_code,
)
from risk_review_web.services.review_history import list_outputs
from risk_review_web.services.review_history_service import ReviewHistoryDependencies, ReviewHistoryService
from risk_review_web.services.runtime_logging import new_trace_id, runtime_log
from risk_review_web.services.stage_runtime import StageRuntimeDependencies, StageRuntimeService
from risk_review_web.services.task_lifecycle import TaskLifecycleDependencies, TaskLifecycleService, task_output_token
from risk_review_web.services.task_submission import TaskSubmissionDependencies, TaskSubmissionService
from risk_review_web.services.time_utils import iso_time as _iso_time
from risk_review_web.services.validation_store import (
    EXAMPLES_LOCK,
    cancel_validation_runs_for_task,
    parse_examples,
    recover_stale_review_mode_experiments,
    recover_stale_validation_runs,
    upsert_review_mode_experiment,
    upsert_validation_run,
)
from risk_review_web.task_pool import GLOBAL_TASK_POOL
from risk_review_web.utils import atomic_write_text
from risk_review_web.workflows import StageExecutor
from risk_review_web.workflows.batch_review import run_review_pair, start_batch_review
from risk_review_web.workflows.dependencies import (
    BatchWorkflowDependencies,
    ExperimentWorkflowDependencies,
    RegressionWorkflowDependencies,
)
from risk_review_web.workflows.experiment import run_review_mode_experiment, summarize_review_mode_experiment
from risk_review_web.workflows.regression import (
    prepare_validation_runs,
    run_regression_validation,
    validation_run_for_example as _validation_run_for_example,
)
from risk_review_web.scheduling.service import build_service as build_scheduled_task_service


TASK_REPOSITORY = TaskRepository()
TASKS = TaskRegistryView(TASK_REPOSITORY)
TASK_LOCK = TASK_REPOSITORY.lock
REVIEW_RECORD_REPOSITORY = ReviewRecordRepository()
TASK_TTL_SECONDS = int(os.environ.get("RISK_REVIEW_TASK_TTL", "1800"))
TASK_MAX_COUNT = int(os.environ.get("RISK_REVIEW_TASK_MAX", "400"))


TASK_LIFECYCLE_SERVICE = TaskLifecycleService(TaskLifecycleDependencies(
    task_repository=TASK_REPOSITORY,
    task_registry=TASKS,
    task_lock=TASK_LOCK,
    task_ttl_seconds=TASK_TTL_SECONDS,
    task_max_count=TASK_MAX_COUNT,
    task_pool=GLOBAL_TASK_POOL,
    review_record_sink=lambda record: REVIEW_RECORD_REPOSITORY.upsert_task(record),
    list_outputs=list_outputs,
    runtime_log=lambda *args, **kwargs: runtime_log(*args, **kwargs),
    cancel_validation_runs_for_task=cancel_validation_runs_for_task,
    upsert_review_mode_experiment=upsert_review_mode_experiment,
    iso_time=_iso_time,
))
task_log = TASK_LIFECYCLE_SERVICE.log
task_debug_log = TASK_LIFECYCLE_SERVICE.debug_log
task_cancel_requested = TASK_LIFECYCLE_SERVICE.cancel_requested
agent_result_payload = TASK_LIFECYCLE_SERVICE.agent_result_payload
finish_task = TASK_LIFECYCLE_SERVICE.finish
retry_task_persistence = TASK_LIFECYCLE_SERVICE.retry_persistence
mark_task_cancelled = TASK_LIFECYCLE_SERVICE.mark_cancelled
request_task_cancel = TASK_LIFECYCLE_SERVICE.request_cancel
gc_tasks = TASK_LIFECYCLE_SERVICE.gc


REVIEW_HISTORY_SERVICE = ReviewHistoryService(ReviewHistoryDependencies(
    repository=REVIEW_RECORD_REPOSITORY,
    task_repository=TASK_REPOSITORY,
    task_registry=TASKS,
    task_lock=TASK_LOCK,
    task_pool=GLOBAL_TASK_POOL,
    gc_tasks=gc_tasks,
))
review_history_records = REVIEW_HISTORY_SERVICE.records
normalize_task_record_view = REVIEW_HISTORY_SERVICE.normalize_task_record_view
review_record_to_task = REVIEW_HISTORY_SERVICE.record_to_task
find_persisted_task = REVIEW_HISTORY_SERVICE.find_persisted_task
query_review_records = REVIEW_HISTORY_SERVICE.query
review_record_detail = REVIEW_HISTORY_SERVICE.record_detail
archive_review_record = REVIEW_HISTORY_SERVICE.archive
delete_review_history_record = REVIEW_HISTORY_SERVICE.delete_record
decorate_task = REVIEW_HISTORY_SERVICE.decorate_task
task_history = REVIEW_HISTORY_SERVICE.history
delete_task_record = REVIEW_HISTORY_SERVICE.delete_task
task_view = REVIEW_HISTORY_SERVICE.task_view


STAGE_EXECUTOR = StageExecutor(
    cancelled=lambda task_id: task_cancel_requested(task_id),
    log=lambda task_id, message: task_log(task_id, message),
)
STAGE_RUNTIME_SERVICE = StageRuntimeService(StageRuntimeDependencies(
    root=ROOT,
    output_dir=OUTPUTS_DIR,
    phase1_skill_path=PHASE1_SKILL_PATH,
    phase1_processing_spec_path=PHASE1_PROCESSING_SPEC_PATH,
    phase2_skill_path=PHASE2_SKILL_PATH,
    phase2_review_spec_path=PHASE2_REVIEW_SPEC_PATH,
    business_rules_path=BUSINESS_RULES_PATH,
    task_repository=TASK_REPOSITORY,
    task_lock=TASK_LOCK,
    task_registry=TASKS,
    stage_executor=STAGE_EXECUTOR,
    task_log=task_log,
    task_debug_log=task_debug_log,
    task_cancel_requested=task_cancel_requested,
    finish_task=finish_task,
    agent_result_payload=agent_result_payload,
    task_output_token=task_output_token,
    run_agent_call=run_agent_call,
    get_api_doc=get_api_doc,
    build_phase1_prompt=build_phase1_prompt,
    build_phase2_prompt=build_phase2_prompt,
    build_requirement_review_prompt=build_requirement_review_prompt,
    build_requirement_review_workspace=create_requirement_review_workspace,
    mock_requirement_review_markdown=mock_requirement_review_markdown,
    selected_api_documents=selected_api_documents,
    normalize_requirement_review_output=normalize_requirement_review_output,
    validate_requirement_review_basic_output=validate_requirement_review_basic_output,
))

test_model_runtime = STAGE_RUNTIME_SERVICE.test_model_runtime
build_prompt_phase1 = STAGE_RUNTIME_SERVICE.build_prompt_phase1
build_prompt_phase2 = STAGE_RUNTIME_SERVICE.build_prompt_phase2
build_prompt_requirement_review = STAGE_RUNTIME_SERVICE.build_prompt_requirement_review
create_phase1_isolated_workspace = STAGE_RUNTIME_SERVICE.create_phase1_workspace
create_phase2_isolated_workspace = STAGE_RUNTIME_SERVICE.create_phase2_workspace


def run_agent_prompt(*args, **kwargs):
    """Keep the historical patchable Agent entry point at the app boundary."""
    return STAGE_RUNTIME_SERVICE.run_agent_prompt(*args, **kwargs)


def run_model_prompt(*args, **kwargs):
    """Dispatch model calls through the patchable Agent entry point."""
    return run_agent_prompt(*args, **kwargs)


prepare_phase2_context = STAGE_RUNTIME_SERVICE.prepare_phase2_context
run_agent_phase1 = STAGE_RUNTIME_SERVICE.run_agent_phase1
run_agent_phase2 = STAGE_RUNTIME_SERVICE.run_agent_phase2
run_agent_requirement_review = STAGE_RUNTIME_SERVICE.run_agent_requirement_review


def batch_workflow_dependencies() -> BatchWorkflowDependencies:
    return BatchWorkflowDependencies(
        task_repository=TASK_REPOSITORY,
        tasks=TASKS,
        task_lock=TASK_LOCK,
        create_task_record=create_task_record,
        task_cancel_requested=task_cancel_requested,
        run_agent_phase1=run_agent_phase1,
        run_agent_phase2=run_agent_phase2,
        task_log=task_log,
        finish_task=finish_task,
        list_outputs=list_outputs,
        classify_task_error=classify_task_error,
        collect_review_stats=collect_review_stats,
        detect_verdict_text=detect_verdict_text,
    )


def regression_workflow_dependencies() -> RegressionWorkflowDependencies:
    return RegressionWorkflowDependencies(
        batch=batch_workflow_dependencies(),
        parse_examples=parse_examples,
        examples_lock=EXAMPLES_LOCK,
        task_output_token=task_output_token,
        string_list=_string_list,
        optional_nonnegative_int=_optional_nonnegative_int,
        validation_expected_code=validation_expected_code,
        expected_matches_verdict=expected_matches_verdict,
        evaluate_issue_expectations=evaluate_issue_expectations,
        task_cancel_requested=task_cancel_requested,
        mark_task_cancelled=mark_task_cancelled,
        cancel_validation_runs_for_task=cancel_validation_runs_for_task,
        task_repository=TASK_REPOSITORY,
        task_log=task_log,
        upsert_validation_run=upsert_validation_run,
        detect_verdict_text=detect_verdict_text,
        collect_review_stats=collect_review_stats,
        classify_task_error=classify_task_error,
        list_outputs=list_outputs,
        finish_task=finish_task,
        iso_time=_iso_time,
    )


def experiment_workflow_dependencies() -> ExperimentWorkflowDependencies:
    return ExperimentWorkflowDependencies(
        batch=batch_workflow_dependencies(),
        parse_examples=parse_examples,
        examples_lock=EXAMPLES_LOCK,
        string_list=_string_list,
        optional_nonnegative_int=_optional_nonnegative_int,
        validation_expected_code=validation_expected_code,
        expected_matches_verdict=expected_matches_verdict,
        evaluate_issue_expectations=evaluate_issue_expectations,
        iso_time=_iso_time,
        task_repository=TASK_REPOSITORY,
        upsert_review_mode_experiment=upsert_review_mode_experiment,
        task_log=task_log,
        task_cancel_requested=task_cancel_requested,
        create_task_record=create_task_record,
        agent_result_payload=agent_result_payload,
        run_model_prompt=run_model_prompt,
        build_direct_review_prompt=build_direct_review_prompt,
        output_dir=OUTPUTS_DIR,
        atomic_write_text=atomic_write_text,
        task_output_token=task_output_token,
        finish_task=finish_task,
        list_outputs=list_outputs,
    )


TASK_SUBMISSION_SERVICE = TaskSubmissionService(TaskSubmissionDependencies(
    task_repository=TASK_REPOSITORY,
    tasks=TASKS,
    task_lock=TASK_LOCK,
    task_pool=GLOBAL_TASK_POOL,
    review_record_sink=lambda task: REVIEW_RECORD_REPOSITORY.upsert_task(task),
    runtime_log=lambda *args, **kwargs: runtime_log(*args, **kwargs),
    attach_error_metadata=attach_error_metadata,
    new_trace_id=new_trace_id,
    agent_result_payload=agent_result_payload,
    task_log=task_log,
    finish_task=finish_task,
    task_view=task_view,
    decorate_task=decorate_task,
    gc_tasks=gc_tasks,
    task_cancel_requested=task_cancel_requested,
    run_agent_phase1=run_agent_phase1,
    run_agent_phase2=run_agent_phase2,
    run_agent_requirement_review=run_agent_requirement_review,
    start_batch_review=start_batch_review,
    batch_workflow_dependencies=lambda: batch_workflow_dependencies(),
    prepare_validation_runs=prepare_validation_runs,
    regression_workflow_dependencies=lambda: regression_workflow_dependencies(),
    run_regression_validation=run_regression_validation,
    experiment_workflow_dependencies=lambda: experiment_workflow_dependencies(),
    run_review_mode_experiment=run_review_mode_experiment,
    summarize_review_mode_experiment=summarize_review_mode_experiment,
    run_review_pair=run_review_pair,
    task_output_token=task_output_token,
    get_runtime_settings=get_runtime_settings,
    iso_time=_iso_time,
))
create_task_record = TASK_SUBMISSION_SERVICE.create_task_record
track_task_future = TASK_SUBMISSION_SERVICE.track_task_future
submit_requirement_review = TASK_SUBMISSION_SERVICE.submit_requirement_review
submit_phase1 = TASK_SUBMISSION_SERVICE.submit_phase1
submit_phase2 = TASK_SUBMISSION_SERVICE.submit_phase2
submit_batch_review = TASK_SUBMISSION_SERVICE.submit_batch_review
submit_regression_task = TASK_SUBMISSION_SERVICE.submit_regression_task
submit_review_mode_experiment = TASK_SUBMISSION_SERVICE.submit_review_mode_experiment
run_scheduled_phase1 = TASK_SUBMISSION_SERVICE.run_scheduled_phase1
run_scheduled_phase2 = TASK_SUBMISSION_SERVICE.run_scheduled_phase2
run_scheduled_review_pair = TASK_SUBMISSION_SERVICE.run_scheduled_review_pair


def validation_run_for_example(task_id: str, example: dict, **kwargs) -> dict:
    return _validation_run_for_example(task_id, example, dependencies=regression_workflow_dependencies(), **kwargs)


def model_runtime_health() -> dict:
    agent = agent_health()
    available = bool(agent.get("langchain_version") and agent.get("model") and agent.get("api_key_configured"))
    return {"available": available, "agent": {**agent, "available": available}}


def system_health() -> dict:
    required = [
        PHASE1_SKILL_PATH,
        PHASE2_SKILL_PATH,
        PHASE1_PROCESSING_SPEC_PATH,
        PHASE2_REVIEW_SPEC_PATH,
        REQUIREMENT_REVIEW_SKILL_PATH,
        REQUIREMENT_REVIEW_SPEC_PATH,
        RULE_ENGINE_BUILD_PATH,
        STATIC_DIR / "dist" / "index.html",
        STATIC_DIR / "vendor" / "marked.min.js",
        STATIC_DIR / "vendor" / "highlight.min.js",
        STATIC_DIR / "vendor" / "github.min.css",
        EXAMPLES_PATH,
        BUSINESS_RULES_PATH,
    ]
    return build_system_health(
        required_paths=required,
        root=ROOT,
        outputs_dir=OUTPUTS_DIR,
        list_api_docs=list_api_docs,
        model_runtime=model_runtime_health(),
        runtime_settings=get_runtime_settings(),
        task_pool=GLOBAL_TASK_POOL.snapshot(),
        mock_mode=os.environ.get("RISK_REVIEW_MOCK") == "1",
        review_history=REVIEW_RECORD_REPOSITORY.health(),
    )


SCHEDULED_TASK_SERVICE = None


def get_scheduled_task_service():
    global SCHEDULED_TASK_SERVICE
    if SCHEDULED_TASK_SERVICE is None:
        SCHEDULED_TASK_SERVICE = build_scheduled_task_service(
            run_review_pair=run_scheduled_review_pair,
            run_phase1=run_scheduled_phase1,
            run_phase2=run_scheduled_phase2,
            review_record_sink=lambda record: REVIEW_RECORD_REPOSITORY.upsert(record),
            cancel_execution=request_task_cancel,
            log=lambda message: runtime_log(
                "scheduled_tasks",
                message,
                level="ERROR" if "failed" in str(message).lower() else "INFO",
                event="scheduler",
            ),
        )
    return SCHEDULED_TASK_SERVICE
