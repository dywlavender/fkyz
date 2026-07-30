from __future__ import annotations

from collections.abc import Callable
from urllib.parse import parse_qs

from risk_review_web.api_docs import get_api_doc, list_api_docs
from risk_review_web.artifacts import load_phase1_processing_spec, load_phase2_review_spec
from risk_review_web.paths import PHASE1_PROCESSING_SPEC_DOC_PATH, PHASE2_REVIEW_SPEC_DOC_PATH
from risk_review_web.runtime_settings import get_runtime_settings
from risk_review_web.services.http_responses import json_response, success_response
from risk_review_web.utils import read_text


def handle_get(
    handler,
    path: str,
    query_string: str,
    *,
    list_outputs: Callable,
    pool_snapshot: Callable,
    system_health: Callable,
    claude_test: Callable,
) -> bool:
    """Handle read-only API documentation, health, settings, and artifact endpoints."""
    if path == "/api/apis":
        json_response(handler, 200, {"apis": list_api_docs(include_content=False)})
        return True
    if path == "/api/api-doc":
        query = parse_qs(query_string)
        doc = get_api_doc((query.get("name") or query.get("path") or [""])[0])
        if not doc:
            json_response(handler, 404, {"error": "api doc not found"})
        else:
            json_response(handler, 200, {"api": doc})
        return True
    if path == "/api/outputs":
        json_response(handler, 200, {"outputs": list_outputs()})
        return True
    if path == "/api/factor-processing-spec":
        success_response(handler, {
            "phase1": {
                "spec": load_phase1_processing_spec(),
                "markdown": read_text(PHASE1_PROCESSING_SPEC_DOC_PATH)
                if PHASE1_PROCESSING_SPEC_DOC_PATH.exists()
                else "",
            },
            "phase2": {
                "spec": load_phase2_review_spec(),
                "markdown": read_text(PHASE2_REVIEW_SPEC_DOC_PATH)
                if PHASE2_REVIEW_SPEC_DOC_PATH.exists()
                else "",
            },
        })
        return True
    if path == "/api/pool/status":
        json_response(handler, 200, {"pool": pool_snapshot()})
        return True
    if path == "/api/runtime-settings":
        success_response(handler, get_runtime_settings())
        return True
    if path in ("/api/health", "/api/self-check"):
        json_response(handler, 200, system_health())
        return True
    if path in {"/api/model-test", "/api/claude-test"}:
        json_response(handler, 200, claude_test())
        return True
    return False
