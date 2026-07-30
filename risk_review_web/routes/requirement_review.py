from __future__ import annotations

import json
from collections.abc import Callable

from risk_review_web.services.http_responses import error_response, json_response
from risk_review_web.services.requirement_review import normalize_requirement_review_payload


def handle_post(handler, path: str, *, submit_review: Callable[[dict], tuple[str, dict]]) -> bool:
    """Validate and submit the standalone requirement-review endpoint."""
    if path != "/api/requirement-review":
        return False
    try:
        payload = normalize_requirement_review_payload(handler.read_json_body())
        task_id, task = submit_review(payload)
        json_response(handler, 202, {"taskId": task_id, "task": task})
    except json.JSONDecodeError:
        error_response(handler, 400, "invalid json")
    except (FileNotFoundError, TypeError, ValueError) as exc:
        error_response(handler, 400, str(exc))
    return True
