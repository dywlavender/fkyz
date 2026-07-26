from __future__ import annotations

import os
import threading
import time
from collections import Counter, deque
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class _WorkItem:
    job_id: str
    group_id: str
    fn: Callable[..., Any]
    args: tuple
    kwargs: dict
    future: Future
    group_limit: int
    created: float
    sequence: int


class GlobalTaskPool:
    """A process-wide fair worker pool grouped by top-level task.

    A group is a single request visible to the user (single factor, batch, or
    regression). Workers rotate between groups and respect each group's own
    concurrency cap, while max_workers remains the hard platform-wide limit.
    """

    def __init__(self, max_workers: int | None = None):
        configured = max_workers or int(os.environ.get("RISK_REVIEW_GLOBAL_CONCURRENCY", "3"))
        self.max_workers = max(1, min(32, int(configured)))
        self._condition = threading.Condition()
        self._queues: dict[str, deque[_WorkItem]] = {}
        self._group_order: deque[str] = deque()
        self._active_by_group: Counter[str] = Counter()
        self._queued_by_id: dict[str, _WorkItem] = {}
        self._active_by_id: dict[str, _WorkItem] = {}
        self._sequence = 0
        self._workers = []
        for index in range(self.max_workers):
            self._start_worker(index)

    def _start_worker(self, index: int) -> None:
        worker = threading.Thread(
            target=self._worker_loop,
            name=f"risk-review-global-{index + 1}",
            daemon=True,
        )
        worker.start()
        self._workers.append(worker)

    def resize(self, max_workers: int) -> int:
        """Apply a new live concurrency cap without interrupting active work."""
        configured = max(1, min(32, int(max_workers)))
        with self._condition:
            self.max_workers = configured
            while len(self._workers) < configured:
                self._start_worker(len(self._workers))
            self._condition.notify_all()
        return self.max_workers

    def submit(
        self,
        group_id: str,
        job_id: str,
        fn: Callable[..., Any],
        *args: Any,
        group_limit: int | None = None,
        **kwargs: Any,
    ) -> Future:
        future = Future()
        limit = max(1, min(self.max_workers, int(group_limit or self.max_workers)))
        with self._condition:
            if job_id in self._queued_by_id or job_id in self._active_by_id:
                raise ValueError(f"duplicate global-pool job id: {job_id}")
            self._sequence += 1
            item = _WorkItem(
                job_id=job_id,
                group_id=group_id,
                fn=fn,
                args=args,
                kwargs=kwargs,
                future=future,
                group_limit=limit,
                created=time.time(),
                sequence=self._sequence,
            )
            queue = self._queues.get(group_id)
            if queue is None:
                queue = self._queues[group_id] = deque()
            if not queue:
                self._group_order.append(group_id)
            queue.append(item)
            self._queued_by_id[job_id] = item
            self._condition.notify_all()
        return future

    def cancel(self, job_id: str) -> bool:
        """Cancel a queued job. Running jobs must be stopped by the caller."""
        with self._condition:
            item = self._queued_by_id.pop(job_id, None)
            if item is None:
                return False
            self._condition.notify_all()
        item.future.cancel()
        return True

    def cancel_group(self, group_id: str) -> list[str]:
        with self._condition:
            cancelled = []
            cancelled_items = []
            for job_id, item in list(self._queued_by_id.items()):
                if item.group_id != group_id:
                    continue
                self._queued_by_id.pop(job_id, None)
                cancelled.append(job_id)
                cancelled_items.append(item)
            self._condition.notify_all()
        for item in cancelled_items:
            item.future.cancel()
        return cancelled

    def job_info(self, job_id: str) -> dict:
        with self._condition:
            if job_id in self._active_by_id:
                return {"poolStatus": "running", "queuePosition": 0}
            item = self._queued_by_id.get(job_id)
            if item is None:
                return {"poolStatus": None, "queuePosition": None}
            ordered = sorted(self._queued_by_id.values(), key=lambda value: value.sequence)
            position = next((i + 1 for i, value in enumerate(ordered) if value.job_id == job_id), None)
            return {"poolStatus": "queued", "queuePosition": position}

    def group_info(self, group_id: str) -> dict:
        with self._condition:
            queued = sum(1 for item in self._queued_by_id.values() if item.group_id == group_id)
            active = self._active_by_group.get(group_id, 0)
            return {"poolQueued": queued, "poolActive": active}

    def snapshot(self) -> dict:
        with self._condition:
            queued = sorted(self._queued_by_id.values(), key=lambda value: value.sequence)
            return {
                "maxWorkers": self.max_workers,
                "activeWorkers": len(self._active_by_id),
                "idleWorkers": max(0, self.max_workers - len(self._active_by_id)),
                "queuedJobs": len(queued),
                "activeGroups": len([value for value in self._active_by_group.values() if value > 0]),
                "queue": [
                    {
                        "jobId": item.job_id,
                        "groupId": item.group_id,
                        "position": index + 1,
                        "queuedAt": item.created,
                    }
                    for index, item in enumerate(queued)
                ],
            }

    def _take_next_locked(self) -> _WorkItem | None:
        if len(self._active_by_id) >= self.max_workers:
            return None
        group_count = len(self._group_order)
        for _ in range(group_count):
            group_id = self._group_order.popleft()
            queue = self._queues.get(group_id)
            if not queue:
                self._queues.pop(group_id, None)
                continue
            while queue and queue[0].job_id not in self._queued_by_id:
                queue.popleft()
            if not queue:
                self._queues.pop(group_id, None)
                continue
            item = queue[0]
            if self._active_by_group[group_id] >= item.group_limit:
                self._group_order.append(group_id)
                continue
            queue.popleft()
            if queue:
                self._group_order.append(group_id)
            else:
                self._queues.pop(group_id, None)
            self._queued_by_id.pop(item.job_id, None)
            if item.future.set_running_or_notify_cancel():
                self._active_by_id[item.job_id] = item
                self._active_by_group[group_id] += 1
                return item
        return None

    def _worker_loop(self):
        while True:
            with self._condition:
                item = self._take_next_locked()
                while item is None:
                    self._condition.wait()
                    item = self._take_next_locked()
            try:
                result = item.fn(*item.args, **item.kwargs)
            except BaseException as exc:
                item.future.set_exception(exc)
            else:
                item.future.set_result(result)
            finally:
                with self._condition:
                    self._active_by_id.pop(item.job_id, None)
                    self._active_by_group[item.group_id] -= 1
                    if self._active_by_group[item.group_id] <= 0:
                        self._active_by_group.pop(item.group_id, None)
                    self._condition.notify_all()


GLOBAL_TASK_POOL = GlobalTaskPool()
