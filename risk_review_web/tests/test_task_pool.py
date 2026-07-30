from __future__ import annotations

import threading
import time
import unittest

from risk_review_web.task_pool import GlobalTaskPool


class GlobalTaskPoolTest(unittest.TestCase):
    def test_global_concurrency_limit_is_enforced(self):
        pool = GlobalTaskPool(max_workers=2)
        lock = threading.Lock()
        active = 0
        peak = 0

        def work():
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.03)
            with lock:
                active -= 1

        futures = [pool.submit("group", f"job-{index}", work) for index in range(6)]
        for future in futures:
            future.result(timeout=3)
        self.assertLessEqual(peak, 2)
        self.assertEqual(peak, 2)

    def test_groups_are_scheduled_round_robin(self):
        pool = GlobalTaskPool(max_workers=1)
        first_started = threading.Event()
        release_first = threading.Event()
        order: list[str] = []

        def first():
            order.append("a1")
            first_started.set()
            release_first.wait(timeout=3)

        def record(name: str):
            order.append(name)

        first_future = pool.submit("a", "a1", first)
        self.assertTrue(first_started.wait(timeout=3))
        remaining = [
            pool.submit("a", "a2", record, "a2"),
            pool.submit("a", "a3", record, "a3"),
            pool.submit("b", "b1", record, "b1"),
        ]
        release_first.set()
        first_future.result(timeout=3)
        for future in remaining:
            future.result(timeout=3)
        self.assertEqual(order, ["a1", "a2", "b1", "a3"])

    def test_queued_job_can_be_cancelled(self):
        pool = GlobalTaskPool(max_workers=1)
        started = threading.Event()
        release = threading.Event()

        def blocking():
            started.set()
            release.wait(timeout=3)

        running = pool.submit("a", "running", blocking)
        self.assertTrue(started.wait(timeout=3))
        queued = pool.submit("b", "queued", lambda: None)
        self.assertTrue(pool.cancel("queued"))
        self.assertTrue(queued.cancelled())
        release.set()
        running.result(timeout=3)


if __name__ == "__main__":
    unittest.main()
