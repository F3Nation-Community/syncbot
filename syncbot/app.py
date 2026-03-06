"""SyncBot — Slack app that syncs messages across workspaces.

This module is the entry point for both AWS Lambda (via :func:`handler`) and
local development (``python app.py`` starts a Bolt dev server on port 3000).

All incoming Slack events, actions, view submissions, and slash commands are
funnelled through :func:`main_response`, which looks up the appropriate
handler in :data:`~utils.routing.MAIN_MAPPER` and dispatches the request.

Federation API endpoints (``/api/federation/*``) handle cross-instance
communication and are dispatched separately from Slack events.
"""

import json
import logging
import os
import re

from dotenv import load_dotenv

# Load .env before any other app imports so env vars are available everywhere.
# In production (Lambda) there is no .env file and this is a harmless no-op.
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from slack_bolt import App
from slack_bolt.adapter.aws_lambda import SlackRequestHandler

from constants import (
    DANGER_DROP_AND_INIT_DB,
    FEDERATION_ENABLED,
    HAS_REAL_BOT_TOKEN,
    LOCAL_DEVELOPMENT,
    validate_config,
)
from federation.api import dispatch_federation_request
from helpers import get_oauth_flow, get_request_type, safe_get
from logger import (
    configure_logging,
    emit_metric,
    get_request_duration_ms,
    set_correlation_id,
)
from routing import MAIN_MAPPER
from slack.actions import CONFIG_PUBLISH_CHANNEL_SUBMIT, CONFIG_PUBLISH_MODE_SUBMIT

_DEFERRED_ACK_VIEWS = frozenset({CONFIG_PUBLISH_MODE_SUBMIT, CONFIG_PUBLISH_CHANNEL_SUBMIT})
"""view_submission callback_ids whose handlers control their own ack response."""

_SENSITIVE_KEYS = frozenset({
    "token", "bot_token", "access_token", "shared_secret",
    "public_key", "private_key", "private_key_encrypted",
})


def _redact_sensitive(obj, _depth=0):
    """Return a copy of *obj* with sensitive keys replaced by ``"[REDACTED]"``."""
    if _depth > 10:
        return obj
    if isinstance(obj, dict):
        return {
            k: "[REDACTED]" if k in _SENSITIVE_KEYS else _redact_sensitive(v, _depth + 1)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_redact_sensitive(v, _depth + 1) for v in obj]
    return obj


SlackRequestHandler.clear_all_log_handlers()
configure_logging()

if os.environ.get(DANGER_DROP_AND_INIT_DB, "").strip().lower() == "true":
    from db import drop_and_init_db
    drop_and_init_db()

validate_config()

app = App(
    process_before_response=not LOCAL_DEVELOPMENT,
    token_verification_enabled=not LOCAL_DEVELOPMENT or HAS_REAL_BOT_TOKEN,
    oauth_flow=get_oauth_flow(),
)


def handler(event: dict, context: dict) -> dict:
    """AWS Lambda entry point.

    Receives an API Gateway proxy event.  Federation API paths
    (``/api/federation/*``) are handled directly; everything else
    is delegated to the Slack Bolt request handler.
    """
    path = event.get("path", "") or event.get("rawPath", "")
    if path.startswith("/api/federation"):
        return _lambda_federation_handler(event)

    slack_request_handler = SlackRequestHandler(app=app)
    return slack_request_handler.handle(event, context)


def _lambda_federation_handler(event: dict) -> dict:
    """Handle a federation API request inside Lambda."""
    method = event.get("httpMethod", "GET")
    path = event.get("path", "")
    body_str = event.get("body", "") or ""
    raw_headers = event.get("headers", {}) or {}
    headers = {k: v for k, v in raw_headers.items()}

    status, resp = dispatch_federation_request(method, path, body_str, headers)
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(resp),
    }


_logger = logging.getLogger(__name__)


def main_response(body: dict, logger, client, ack, context: dict) -> None:
    """Central dispatcher for every Slack request.

    Acknowledges the request immediately (required by Slack's 3-second
    timeout), then resolves the ``(request_type, request_id)`` pair to
    a handler function via :data:`MAIN_MAPPER` and invokes it.

    A unique correlation ID is assigned to every incoming request and
    attached to all log entries emitted while processing it.
    """
    set_correlation_id()
    request_type, request_id = get_request_type(body)

    # Most requests are acked immediately.  Certain view_submission
    # handlers need to control the ack themselves (e.g. to respond with
    # response_action="update" for multi-step modals).  For those, we
    # defer the ack and expose it via context["ack"].
    defer_ack = request_type == "view_submission" and request_id in _DEFERRED_ACK_VIEWS
    ack_called = False

    if defer_ack:
        def _tracked_ack(*args, **kwargs):
            nonlocal ack_called
            ack_called = True
            return ack(*args, **kwargs)
        context["ack"] = _tracked_ack
    else:
        ack()

    _logger.info(
        "request_received",
        extra={
            "request_type": request_type,
            "request_id": request_id,
            "team_id": safe_get(body, "team_id"),
        },
    )
    _logger.debug("request_body", extra={"body": json.dumps(_redact_sensitive(body))})

    run_function = MAIN_MAPPER.get(request_type, {}).get(request_id)
    if run_function:
        try:
            run_function(body, client, logger, context)
            if defer_ack and not ack_called:
                ack()
            emit_metric(
                "request_handled",
                duration_ms=round(get_request_duration_ms(), 1),
                request_type=request_type,
                request_id=request_id,
            )
        except Exception:
            if defer_ack and not ack_called:
                ack()
            emit_metric(
                "request_error",
                request_type=request_type,
                request_id=request_id,
            )
            raise
    else:
        if defer_ack and not ack_called:
            ack()
        _logger.error(
            "no_handler",
            extra={
                "request_type": request_type,
                "request_id": request_id,
            },
        )


if LOCAL_DEVELOPMENT:
    ARGS = [main_response]
    LAZY_KWARGS = {}
else:
    ARGS = []
    LAZY_KWARGS = {
        "ack": lambda ack: ack(),
        "lazy": [main_response],
    }

MATCH_ALL_PATTERN = re.compile(".*")
app.event(MATCH_ALL_PATTERN)(*ARGS, **LAZY_KWARGS)
app.action(MATCH_ALL_PATTERN)(*ARGS, **LAZY_KWARGS)
app.view(MATCH_ALL_PATTERN)(*ARGS, **LAZY_KWARGS)


if __name__ == "__main__":
    if LOCAL_DEVELOPMENT:
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        class FederationHTTPHandler(BaseHTTPRequestHandler):
            """Lightweight HTTP handler for federation API endpoints."""

            def do_GET(self):
                if self.path.startswith("/api/federation"):
                    self._handle_federation("GET")
                else:
                    self.send_error(404)

            def do_POST(self):
                if self.path.startswith("/api/federation"):
                    self._handle_federation("POST")
                else:
                    self.send_error(404)

            _MAX_BODY = 1_048_576  # 1 MB

            def _handle_federation(self, method: str):
                try:
                    content_len = min(int(self.headers.get("Content-Length", 0)), self._MAX_BODY)
                except (TypeError, ValueError):
                    content_len = 0
                body_str = self.rfile.read(content_len).decode() if content_len else ""
                headers = {k: v for k, v in self.headers.items()}

                status, resp = dispatch_federation_request(method, self.path, body_str, headers)

                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(resp).encode())

            def log_message(self, format, *args):
                pass

        if FEDERATION_ENABLED:
            fed_server = HTTPServer(("0.0.0.0", 3001), FederationHTTPHandler)
            fed_thread = threading.Thread(target=fed_server.serve_forever, daemon=True)
            fed_thread.start()
            _logger.info("Federation API server started on port 3001")

    app.start(3000)
