from __future__ import annotations

import base64
import csv
import io
import tempfile
import unittest
from pathlib import Path

from risk_review_web.domain.agent_result import AgentResult, AgentStatus
from risk_review_web.domain.task import InvalidTaskTransition, StageArtifacts, Task, TaskStatus
from risk_review_web.repositories.review_record_repository import ReviewRecordCorruptError, ReviewRecordRepository
from risk_review_web.repositories.task_repository import TaskRepository
from risk_review_web.services.review_history import _task_to_review_record
from risk_review_web.services.tabular_import import TabularImportError, decode_import_rows
from risk_review_web.workflows.stage_executor import PreparedStage, StageExecutor


def encoded_csv(text: str, encoding: str = "utf-8") -> str:
    return base64.b64encode(text.encode(encoding)).decode("ascii")


class DomainContractTest(unittest.TestCase):
    def test_agent_result_has_no_process_fields(self):
        result = AgentResult.succeeded("正文")
        self.assertIs(result.status, AgentStatus.SUCCEEDED)
        self.assertEqual(result.content, "正文")
        self.assertNotIn("stdout", result.as_dict())
        self.assertNotIn("returncode", result.as_dict())

    def test_task_state_machine_rejects_terminal_overwrite(self):
        repository = TaskRepository()
        repository.create(Task("task-1", "phase1", TaskStatus.QUEUED))
        repository.start("task-1")
        repository.complete("task-1", {"content": "ok"})
        with self.assertRaises(InvalidTaskTransition):
            repository.fail("task-1", {"error": "late"})

    def test_task_reads_are_snapshots_and_result_items_are_defensive(self):
        repository = TaskRepository()
        repository.create(Task("snapshot-task", "batch", TaskStatus.QUEUED))
        repository.set_result("snapshot-task", {"items": [{"status": "queued"}]})
        snapshot = repository.get("snapshot-task")
        snapshot.result["items"][0]["status"] = "done"
        snapshot.context["changed"] = True
        self.assertEqual(repository.get("snapshot-task").result["items"][0]["status"], "queued")
        self.assertNotIn("changed", repository.get("snapshot-task").context)

    def test_task_item_updates_are_repository_operations(self):
        repository = TaskRepository()
        repository.create(Task("item-task", "batch", TaskStatus.QUEUED))
        repository.set_result("item-task", {"items": [{"status": "queued"}]})
        repository.update_item("item-task", 0, {"status": "done"})
        self.assertEqual(repository.get("item-task").result["items"][0]["status"], "done")

    def test_queued_cancel_is_terminal_and_parent_cancelled_child_is_not_started(self):
        repository = TaskRepository()
        repository.create(Task("parent", "batch", TaskStatus.QUEUED))
        repository.request_cancel("parent")
        self.assertEqual(repository.require("parent").status, TaskStatus.CANCELLED)
        with self.assertRaises(InvalidTaskTransition):
            repository.start("parent")

    def test_cancelled_task_can_be_marked_unpersisted_and_reconciled(self):
        repository = TaskRepository()
        repository.create(Task("cancel-persistence", "phase2", TaskStatus.QUEUED))
        repository.request_cancel("cancel-persistence")
        repository.fail_unpersisted(
            "cancel-persistence",
            {"error": "history unavailable"},
            target_status="cancelled",
            persistence_error="history unavailable",
        )
        failed = repository.require("cancel-persistence")
        self.assertEqual(failed.status, TaskStatus.ERROR)
        self.assertTrue(failed.unpersisted)
        reconciled = repository.mark_persisted("cancel-persistence")
        self.assertEqual(reconciled.status, TaskStatus.CANCELLED)
        self.assertFalse(reconciled.unpersisted)

    def test_shared_tabular_decoder_supports_utf8_bom_and_gb18030(self):
        bom = decode_import_rows("items.csv", encoded_csv("因子名,需求\n测试,内容\n", "utf-8-sig"))
        gb = decode_import_rows("items.csv", encoded_csv("因子名,需求\n测试,内容\n", "gb18030"))
        self.assertEqual(bom, gb)
        self.assertEqual(bom[1], ["测试", "内容"])

    def test_malformed_xlsx_is_explicit_error(self):
        with self.assertRaises(TabularImportError):
            decode_import_rows("items.xlsx", base64.b64encode(b"not-an-xlsx").decode("ascii"))

    def test_stage_executor_cleans_workspace_and_returns_stage_outcome(self):
        created: list[Path] = []

        class Definition:
            name = "Test Stage"

            def prepare(self, _request, _task):
                return PreparedStage()

            def build_prompt(self, _prepared):
                return "prompt"

            def mock(self, _prepared):
                return "mock"

            def normalize(self, content, _prepared):
                return content.upper()

            def validate(self, content, _prepared):
                self.seen = content

            def persist(self, content, _prepared):
                return type("Artifacts", (), {"content": content})()

            def create_workspace(self, _prepared):
                path = Path(tempfile.mkdtemp(prefix="stage-executor-test-"))
                created.append(path)
                return path

        executor = StageExecutor(cancelled=lambda _task_id: False)
        outcome = executor.execute(
            task=Task("stage-task", "test", TaskStatus.QUEUED),
            request={},
            definition=Definition(),
            agent_call=lambda _prompt, _workspace, _phase: AgentResult.succeeded("agent"),
        )
        self.assertEqual(outcome.status, TaskStatus.DONE)
        self.assertEqual(outcome.artifacts.content, "AGENT")
        self.assertTrue(created)
        self.assertFalse(created[0].exists())

    def test_stage_executor_run_task_owns_start_and_terminal_outcome(self):
        events: list[tuple[str, object]] = []

        class Definition:
            name = "Lifecycle Stage"

            def prepare(self, _request, _task):
                return PreparedStage()

            def build_prompt(self, _prepared):
                return "prompt"

            def mock(self, _prepared):
                return "mock body"

            def normalize(self, content, _prepared):
                return content

            def validate(self, _content, _prepared):
                return None

            def persist(self, content, _prepared):
                return StageArtifacts(content, {"reviewFileId": "review.md"})

            def create_workspace(self, _prepared):
                raise AssertionError("Mock lifecycle must not create an Agent workspace")

        repository = TaskRepository()
        repository.create(Task("lifecycle-task", "phase2", TaskStatus.QUEUED))
        executor = StageExecutor(cancelled=lambda _task_id: False)
        outcome = executor.run_task(
            task=repository.require("lifecycle-task"),
            request={},
            definition=Definition(),
            agent_call=lambda *_args: AgentResult.failed("should not call Agent"),
            use_mock=True,
            start_task=lambda task_id: (events.append(("start", task_id)), repository.start(task_id))[1],
            finish_task=lambda task_id, value: (events.append(("finish", value.status)), repository.complete(task_id, value))[1],
        )
        self.assertEqual(outcome.status, TaskStatus.DONE)
        self.assertEqual(repository.require("lifecycle-task").status, TaskStatus.DONE)
        self.assertEqual(events, [("start", "lifecycle-task"), ("finish", TaskStatus.DONE)])


class ReviewRecordRepositoryTest(unittest.TestCase):
    def test_internal_experiment_child_is_not_projected_to_history(self):
        record = _task_to_review_record({
            "id": "experiment-1-direct",
            "taskType": "comparison-direct",
            "parentTaskId": "experiment-1",
            "status": "done",
            "result": {"content": "report"},
            "created": 1,
            "updated": 2,
        })
        self.assertIsNone(record)

    def test_query_is_read_only_and_upsert_is_idempotent(self):
        with tempfile.TemporaryDirectory(prefix="review-record-repository-") as temp_dir:
            root = Path(temp_dir)
            repository = ReviewRecordRepository(path=root / "review_records.json", outputs_dir=root / "outputs")
            record = {"recordId": "record-1", "taskId": "task-1", "status": "done"}
            repository.upsert(record)
            before = (root / "review_records.json").read_bytes()
            self.assertEqual(repository.list(), [record])
            self.assertEqual((root / "review_records.json").read_bytes(), before)
            repository.upsert(record)
            self.assertEqual(len(repository.list()), 1)

    def test_corrupt_index_is_explicit_and_never_replaced_by_upsert(self):
        with tempfile.TemporaryDirectory(prefix="review-record-corrupt-") as temp_dir:
            root = Path(temp_dir)
            path = root / "review_records.json"
            path.write_text('{"broken":', encoding="utf-8")
            repository = ReviewRecordRepository(path=path, outputs_dir=root / "outputs")
            before = path.read_bytes()
            with self.assertRaises(ReviewRecordCorruptError):
                repository.upsert({"recordId": "new-record"})
            with self.assertRaises(ReviewRecordCorruptError):
                repository.list()
            self.assertEqual(path.read_bytes(), before)
            self.assertFalse(repository.health()["ok"])

    def test_delete_persists_tombstone_before_removing_unshared_artifact(self):
        with tempfile.TemporaryDirectory(prefix="review-record-delete-") as temp_dir:
            root = Path(temp_dir)
            outputs = root / "outputs"
            outputs.mkdir()
            artifact = outputs / "review.md"
            artifact.write_text("report", encoding="utf-8")
            repository = ReviewRecordRepository(path=root / "review_records.json", outputs_dir=outputs)
            repository.upsert({
                "recordId": "record-delete",
                "taskId": "task-delete",
                "status": "done",
                "reviewArtifacts": {"md": "review.md"},
            })
            result = repository.delete("record-delete")
            self.assertFalse(artifact.exists())
            self.assertEqual(result["cleanupPending"], [])
            tombstone = repository.get("record-delete", include_deleted=True)
            self.assertTrue(tombstone["deleted"])


if __name__ == "__main__":
    unittest.main()
