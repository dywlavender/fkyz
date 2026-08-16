from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from risk_review_web.artifacts import (
    compute_phase1_checksum,
    compute_review_checksum,
    find_cached_phase1_standard,
    normalize_phase1_output,
    normalize_review_output,
    read_phase1_standard,
    save_requirement_review_artifacts,
    save_standard_metadata,
    strip_yaml_frontmatter,
    validate_phase1_standard_basic_output,
    validate_phase2_review_basic_output,
)
from risk_review_web.domain.agent_result import AgentResult, AgentStatus
from risk_review_web.domain.task import StageArtifacts, StageOutcome, Task, TaskStatus
from risk_review_web.prompt_builder import ModelPrompt
from risk_review_web.utils import atomic_write_text, parse_frontmatter, read_text
from risk_review_web.workflows import (
    PreparedStage,
    Phase1Definition,
    Phase2Definition,
    RequirementReviewDefinition,
    mock_phase1_markdown,
    mock_phase2_markdown,
)


@dataclass(frozen=True, slots=True)
class StageRuntimeDependencies:
    root: Path
    output_dir: Path
    phase1_skill_path: Path
    phase1_processing_spec_path: Path
    phase2_skill_path: Path
    phase2_review_spec_path: Path
    business_rules_path: Path
    task_repository: Any
    task_lock: Any
    task_registry: Any
    stage_executor: Any
    task_log: Callable[[str, str], Any]
    task_debug_log: Callable[..., Any]
    task_cancel_requested: Callable[[str], bool]
    finish_task: Callable[[str, str, dict | None], Any]
    agent_result_payload: Callable[..., dict]
    task_output_token: Callable[[str], str]
    run_agent_call: Callable[..., AgentResult]
    get_api_doc: Callable[[str], dict | None]
    build_phase1_prompt: Callable[..., ModelPrompt]
    build_phase2_prompt: Callable[..., ModelPrompt]
    build_requirement_review_prompt: Callable[..., str]
    build_requirement_review_workspace: Callable[..., tuple[Path, list[dict]]]
    mock_requirement_review_markdown: Callable[[list[str]], str]
    selected_api_documents: Callable[[list[str]], list[dict]]
    normalize_requirement_review_output: Callable[[str], str]
    validate_requirement_review_basic_output: Callable[[str], None]


class StageRuntimeService:
    """Own prompt assembly, stage isolation, Agent execution, and persistence."""

    def __init__(self, dependencies: StageRuntimeDependencies):
        self.dependencies = dependencies

    def test_model_runtime(self) -> dict:
        started = time.time()
        with tempfile.TemporaryDirectory(prefix="risk-review-agent-test-") as workspace:
            agent_result = self.dependencies.run_agent_call(
                prompt="只回复 OK",
                phase_label="Model Test",
                workspace=Path(workspace),
                cancelled=lambda: False,
            )
        return {
            "ok": agent_result.ok and agent_result.content.strip() == "OK",
            "seconds": round(time.time() - started, 2),
            **agent_result.as_dict(),
        }

    def build_prompt_phase1(self, payload: dict) -> ModelPrompt:
        requirement = (payload.get("requirement") or "").strip()
        api_names = [str(value).strip() for value in (payload.get("apiNames") or []) if str(value).strip()]
        return self.dependencies.build_phase1_prompt(requirement=requirement, api_names=api_names)

    def prepare_phase2_context(self, payload: dict) -> dict:
        standard_text, std_path = read_phase1_standard(payload)
        requirement = (payload.get("requirement") or "").strip()
        if not requirement:
            raise ValueError("Phase 2 缺少原始需求，无法提供原文依据。")
        model_standard_text = re.sub(
            r"^# 标准加工规范[^\n]*",
            "# 标准加工规范",
            strip_yaml_frontmatter(standard_text),
            count=1,
        )
        return {"standardText": model_standard_text, "standardPath": std_path, "standardFileId": std_path.name}

    def build_prompt_phase2(self, payload: dict) -> ModelPrompt:
        draft = (payload.get("draft") or "").strip()
        context = payload.get("_phase2_context") or self.prepare_phase2_context(payload)
        return self.dependencies.build_phase2_prompt(
            standard_text=context["standardText"],
            requirement=(payload.get("requirement") or "").strip(),
            draft=draft,
        )

    def build_prompt_requirement_review(self, payload: dict) -> str:
        return self.dependencies.build_requirement_review_prompt(
            requirement=str(payload.get("requirement") or "").strip(),
            api_names=[str(item) for item in payload.get("apiNames") or []],
        )

    def create_phase1_workspace(self, task_id: str, payload: dict) -> Path:
        workspace = Path(tempfile.mkdtemp(prefix=f"risk-review-phase1-{task_id[:8]}-"))
        try:
            allowed_sources = [
                self.dependencies.phase1_skill_path,
                self.dependencies.phase1_processing_spec_path,
                self.dependencies.business_rules_path,
            ]
            for identifier in payload.get("apiNames") or []:
                doc = self.dependencies.get_api_doc(str(identifier))
                if doc is None:
                    continue
                source_path = (self.dependencies.root / str(doc.get("path") or "")).resolve()
                try:
                    source_path.relative_to(self.dependencies.root.resolve())
                except ValueError as exc:
                    raise ValueError(f"接口文档路径越界：{identifier}") from exc
                allowed_sources.append(source_path)
            staged_files = []
            for source_path in allowed_sources:
                source_path = source_path.resolve()
                if not source_path.is_file():
                    continue
                relative_path = source_path.relative_to(self.dependencies.root.resolve())
                target_path = workspace / relative_path
                target_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_path, target_path)
                staged_files.append(relative_path.as_posix())
            self.dependencies.task_log(task_id, "Phase 1 隔离工作目录已创建，仅包含：" + "、".join(staged_files))
            return workspace
        except Exception:
            shutil.rmtree(workspace, ignore_errors=True)
            raise

    def create_phase2_workspace(self, task_id: str) -> Path:
        workspace = Path(tempfile.mkdtemp(prefix=f"risk-review-phase2-{task_id[:8]}-"))
        try:
            staged_files = []
            for source_path in (self.dependencies.phase2_skill_path, self.dependencies.phase2_review_spec_path):
                source_path = source_path.resolve()
                if not source_path.is_file():
                    continue
                relative_path = source_path.relative_to(self.dependencies.root.resolve())
                target_path = workspace / relative_path
                target_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_path, target_path)
                staged_files.append(relative_path.as_posix())
            self.dependencies.task_log(task_id, "Phase 2 隔离工作目录已创建，仅包含：" + "、".join(staged_files))
            return workspace
        except Exception:
            shutil.rmtree(workspace, ignore_errors=True)
            raise

    def run_agent_prompt(
        self,
        task_id: str,
        phase_label: str,
        prompt: str | ModelPrompt,
        *,
        isolated_workspace: Path | None = None,
    ) -> AgentResult:
        if isolated_workspace is None:
            return AgentResult.failed("Agent 调用必须提供隔离工作目录。")
        workspace = isolated_workspace.resolve()
        if isinstance(prompt, ModelPrompt):
            system_prompt, user_prompt = prompt.system, prompt.user
        else:
            system_prompt, user_prompt = "", str(prompt)
        with self.dependencies.task_lock:
            debug_enabled = bool((self.dependencies.task_registry.get(task_id) or {}).get("debug_agent"))
        self.dependencies.task_log(task_id, f"调用 LangChain Agent（{phase_label}）")
        self.dependencies.task_log(task_id, f"Prompt 字符数：system={len(system_prompt)}，user={len(user_prompt)}")
        if system_prompt:
            self.dependencies.task_debug_log(task_id, "system_prompt", system_prompt, memory=debug_enabled)
        self.dependencies.task_debug_log(task_id, "user_prompt", user_prompt, memory=debug_enabled)

        def agent_event(event: str, payload: object) -> None:
            rendered = json.dumps(payload, ensure_ascii=False, default=str)
            if event in {"model_input", "model_output"}:
                rendered = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
                self.dependencies.task_debug_log(task_id, f"agent_{event}", rendered, memory=debug_enabled, raw_message=True)
                return
            self.dependencies.task_debug_log(task_id, f"agent_{event}", rendered, memory=debug_enabled)

        result = self.dependencies.run_agent_call(
            prompt=user_prompt,
            system_prompt=system_prompt,
            phase_label=phase_label,
            workspace=workspace,
            cancelled=lambda: self.dependencies.task_cancel_requested(task_id),
            event_callback=agent_event,
        )
        self.dependencies.task_debug_log(task_id, "response", result.content, memory=debug_enabled)
        self.dependencies.task_debug_log(task_id, "error", result.error or "", memory=debug_enabled)
        self.dependencies.task_log(task_id, f"LangChain Agent 返回，状态：{result.status.value}")
        return result

    def run_model_prompt(self, task_id: str, phase_label: str, prompt: str | ModelPrompt, *, isolated_workspace: Path | None = None) -> AgentResult:
        return self.run_agent_prompt(task_id, phase_label, prompt, isolated_workspace=isolated_workspace)

    def finish_stage_outcome(self, task_id: str, outcome: StageOutcome) -> None:
        artifacts = outcome.artifacts
        if outcome.status is TaskStatus.DONE:
            payload = {
                "agentStatus": AgentStatus.SUCCEEDED.value,
                "content": artifacts.content if artifacts else "",
                "error": None,
            }
            if artifacts:
                payload.update(artifacts.file_ids)
                payload.update(artifacts.metadata)
            self.dependencies.finish_task(task_id, "done", payload)
            return
        message = outcome.error.message if outcome.error else "阶段执行失败"
        status = "cancelled" if outcome.status is TaskStatus.CANCELLED else "error"
        result = self.dependencies.agent_result_payload(
            AgentResult.cancelled(message) if status == "cancelled" else AgentResult.failed(message),
            error=message,
        )
        self.dependencies.finish_task(task_id, status, result)

    def phase1_definition(self, task_id: str, payload: dict) -> tuple[Phase1Definition, bool]:
        use_mock = os.environ.get("RISK_REVIEW_MOCK") == "1" or payload.get("mock") is True

        def prepare(request: dict, _task: Task) -> PreparedStage:
            request = dict(request)
            checksum = compute_phase1_checksum(request)
            factor_name = (request.get("factorName") or "factor").strip()
            slug = re.sub(r"[^a-zA-Z0-9]", "-", factor_name.lower()).strip("-") or "factor"
            cached = None if request.get("forceRefresh") else find_cached_phase1_standard(checksum, allow_mock=use_mock)
            cached_text = str(cached.get("content") or "") if cached else ""
            cached_meta = parse_frontmatter(cached_text) if cached_text else {}
            same_factor = bool(cached_text and str(cached_meta.get("factor_name") or "") == factor_name)
            cached_artifacts = None
            if same_factor:
                cached_artifacts = StageArtifacts(
                    content=cached_text,
                    file_ids={
                        "standardFileId": str(cached.get("standardFileId") or ""),
                        **({"standardMetadataFileId": str(cached.get("standardMetadataFileId"))} if cached.get("standardMetadataFileId") else {}),
                    },
                    metadata={"checksum": checksum, "cacheHit": True},
                )
            return PreparedStage(
                value={"payload": request, "checksum": checksum, "factorName": factor_name, "slug": slug, "cacheHit": bool(cached_text)},
                cached_content=cached_text if cached_text and not same_factor else None,
                cached_artifacts=cached_artifacts,
            )

        def persist(content: str, prepared: PreparedStage) -> StageArtifacts:
            value = prepared.value
            output_name = f"standard-{value['slug']}-{time.strftime('%Y-%m-%d')}-{self.dependencies.task_output_token(task_id)}.md"
            self.dependencies.output_dir.mkdir(exist_ok=True)
            atomic_write_text(self.dependencies.output_dir / output_name, content)
            metadata_name = save_standard_metadata(output_name, content, value["payload"])
            return StageArtifacts(
                content=read_text(self.dependencies.output_dir / output_name),
                file_ids={"standardFileId": output_name, "standardMetadataFileId": metadata_name},
                metadata={"checksum": value["checksum"], "cacheHit": value["cacheHit"]},
            )

        definition = Phase1Definition(
            prepare_fn=prepare,
            prompt_fn=lambda prepared: self.build_prompt_phase1(prepared.value["payload"]),
            mock_fn=lambda _prepared: mock_phase1_markdown(),
            normalize_fn=lambda content, prepared: normalize_phase1_output(
                content,
                prepared.value["payload"],
                prepared.value["slug"],
                prepared.value["factorName"],
                prepared.value["checksum"],
                mock=use_mock,
            ),
            validate_fn=lambda content, _prepared: validate_phase1_standard_basic_output(content),
            persist_fn=persist,
            workspace_fn=lambda prepared: self.create_phase1_workspace(task_id, prepared.value["payload"]),
        )
        return definition, use_mock

    def run_agent_phase1(self, payload: dict, task_id: str):
        definition, use_mock = self.phase1_definition(task_id, payload)
        return self.dependencies.stage_executor.run_task(
            task=self.dependencies.task_repository.require(task_id),
            request=payload,
            definition=definition,
            agent_call=lambda prompt, workspace, phase: self.run_model_prompt(task_id, phase, prompt, isolated_workspace=workspace),
            use_mock=use_mock,
            start_task=self.dependencies.task_repository.start,
            finish_task=self.finish_stage_outcome,
        )

    def phase2_definition(self, task_id: str, payload: dict) -> tuple[Phase2Definition, bool]:
        use_mock = os.environ.get("RISK_REVIEW_MOCK") == "1" or payload.get("mock") is True

        def prepare(request: dict, _task: Task) -> PreparedStage:
            request = dict(request)
            context = self.prepare_phase2_context(request)
            return PreparedStage(value={
                "payload": {**request, "_phase2_context": context},
                "context": context,
                "factorName": (request.get("factorName") or "factor").strip(),
                "slug": re.sub(r"[^a-zA-Z0-9]", "-", (request.get("factorName") or "factor").strip().lower()).strip("-") or "factor",
                "checksum": compute_review_checksum(request, context["standardText"]),
            })

        def persist(content: str, prepared: PreparedStage) -> StageArtifacts:
            value = prepared.value
            output_name = f"review-{value['slug']}-{time.strftime('%Y-%m-%d')}-{self.dependencies.task_output_token(task_id)}.md"
            self.dependencies.output_dir.mkdir(exist_ok=True)
            atomic_write_text(self.dependencies.output_dir / output_name, content)
            return StageArtifacts(
                content=read_text(self.dependencies.output_dir / output_name),
                file_ids={"reviewFileId": output_name},
                metadata={"reviewChecksum": value["checksum"]},
            )

        definition = Phase2Definition(
            prepare_fn=prepare,
            prompt_fn=lambda prepared: self.build_prompt_phase2(prepared.value["payload"]),
            mock_fn=lambda prepared: mock_phase2_markdown(prepared.value["factorName"]),
            normalize_fn=lambda content, prepared: content if use_mock else normalize_review_output(
                content,
                prepared.value["payload"],
                prepared.value["slug"],
                prepared.value["context"]["standardFileId"],
                prepared.value["checksum"],
            ),
            validate_fn=lambda content, _prepared: validate_phase2_review_basic_output(content),
            persist_fn=persist,
            workspace_fn=lambda _prepared: self.create_phase2_workspace(task_id),
        )
        return definition, use_mock

    def run_agent_phase2(self, payload: dict, task_id: str):
        definition, use_mock = self.phase2_definition(task_id, payload)
        return self.dependencies.stage_executor.run_task(
            task=self.dependencies.task_repository.require(task_id),
            request=payload,
            definition=definition,
            agent_call=lambda prompt, workspace, phase: self.run_model_prompt(task_id, phase, prompt, isolated_workspace=workspace),
            use_mock=use_mock,
            start_task=self.dependencies.task_repository.start,
            finish_task=self.finish_stage_outcome,
        )

    def requirement_review_definition(self, task_id: str, payload: dict) -> tuple[RequirementReviewDefinition, bool]:
        use_mock = os.environ.get("RISK_REVIEW_MOCK") == "1" or payload.get("mock") is True

        def prepare(request: dict, _task: Task) -> PreparedStage:
            request = dict(request)
            return PreparedStage(value={"payload": request, "documents": self.dependencies.selected_api_documents(request.get("apiNames") or [])})

        def persist(content: str, prepared: PreparedStage) -> StageArtifacts:
            value = prepared.value
            saved = save_requirement_review_artifacts(
                task_id=task_id,
                review_text=content,
                requirement=value["payload"].get("requirement") or "",
                api_documents=value["documents"],
                mock=use_mock,
            )
            return StageArtifacts(
                content=content,
                file_ids={key: value for key, value in saved.items() if key.endswith("FileId")},
                metadata={key: value for key, value in saved.items() if not key.endswith("FileId")},
            )

        definition = RequirementReviewDefinition(
            prepare_fn=prepare,
            prompt_fn=lambda prepared: self.build_prompt_requirement_review(prepared.value["payload"]),
            mock_fn=lambda prepared: self.dependencies.mock_requirement_review_markdown(prepared.value["payload"].get("apiNames") or []),
            normalize_fn=lambda content, _prepared: self.dependencies.normalize_requirement_review_output(content),
            validate_fn=lambda content, _prepared: self.dependencies.validate_requirement_review_basic_output(content),
            persist_fn=persist,
            workspace_fn=lambda prepared: self._create_requirement_workspace(task_id, prepared.value["payload"]),
        )
        return definition, use_mock

    def _create_requirement_workspace(self, task_id: str, payload: dict) -> Path:
        workspace, _documents = self.dependencies.build_requirement_review_workspace(
            task_id,
            payload.get("apiNames") or [],
            log=lambda message: self.dependencies.task_log(task_id, message),
        )
        return workspace

    def run_agent_requirement_review(self, payload: dict, task_id: str):
        definition, use_mock = self.requirement_review_definition(task_id, payload)
        return self.dependencies.stage_executor.run_task(
            task=self.dependencies.task_repository.require(task_id),
            request=payload,
            definition=definition,
            agent_call=lambda prompt, workspace, phase: self.run_model_prompt(task_id, phase, prompt, isolated_workspace=workspace),
            use_mock=use_mock,
            start_task=self.dependencies.task_repository.start,
            finish_task=self.finish_stage_outcome,
        )
