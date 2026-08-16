from __future__ import annotations

import os
from http.server import ThreadingHTTPServer

from risk_review_web.app_context import (
    get_scheduled_task_service,
    new_trace_id,
    recover_stale_review_mode_experiments,
    recover_stale_validation_runs,
    runtime_log,
)
from risk_review_web.routes.http import Handler


def main():
    """Recover persisted state, start scheduling, and serve the local application."""
    startup_trace_id = new_trace_id()
    runtime_log(
        "server",
        "风控 AI 评审平台正在启动。",
        event="server_starting",
        trace_id=startup_trace_id,
    )
    recover_stale_validation_runs()
    recover_stale_review_mode_experiments()
    scheduled_service = get_scheduled_task_service()
    scheduled_service.start_scheduler()
    port = int(os.environ.get("PORT", "8765"))
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Risk review web running at http://127.0.0.1:{port}")
    runtime_log(
        "server",
        f"服务已启动：http://127.0.0.1:{port}",
        event="server_started",
        trace_id=startup_trace_id,
    )
    try:
        server.serve_forever()
    finally:
        runtime_log(
            "server",
            "风控 AI 评审平台正在停止。",
            event="server_stopping",
            trace_id=startup_trace_id,
        )
        scheduled_service.stop_scheduler()
        server.server_close()


if __name__ == "__main__":
    main()
