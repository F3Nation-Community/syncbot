"""SyncBot — Slack app that syncs messages across workspaces.

This module is the entry point for both AWS Lambda (via :func:`handler`) and
local development (``python app.py`` starts a Bolt dev server on port 3000).

All incoming Slack events, actions, view submissions, and slash commands are
dispatched through :func:`main_response`.  In production (non-local), view
submissions first run :func:`view_ack` for the HTTP response, then :func:`main_response`
for the work phase (lazy).  Handlers are looked up in :data:`routing.MAIN_MAPPER`
and :data:`routing.VIEW_ACK_MAPPER`.

Federation API endpoints (``/api/federation/*``) handle cross-instance
communication and are dispatched separately from Slack events.
"""

import json
import logging
import os
import re
from importlib.metadata import PackageNotFoundError, version

from dotenv import load_dotenv

try:
    __version__ = version("syncbot")
except PackageNotFoundError:
    __version__ = "dev"

# Load .env before any other app imports so env vars are available everywhere.
# In production (Lambda) there is no .env file and this is a harmless no-op.
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from slack_bolt import App
from slack_bolt.adapter.aws_lambda import SlackRequestHandler

from constants import (
    FEDERATION_ENABLED,
    HAS_REAL_BOT_TOKEN,
    LOCAL_DEVELOPMENT,
    validate_config,
)
from db import initialize_database
from federation.api import dispatch_federation_request
from helpers import get_oauth_flow, get_request_type, safe_get
from logger import (
    configure_logging,
    emit_metric,
    get_request_duration_ms,
    set_correlation_id,
)
from routing import MAIN_MAPPER, VIEW_ACK_MAPPER, VIEW_MAPPER

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

validate_config()
initialize_database()

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


def view_ack(body: dict, logger, client, ack, context: dict) -> None:
    """Production ack handler for ``view_submission``: fast response to Slack (3s budget).

    Deferred-ack views use :data:`~routing.VIEW_ACK_MAPPER`; all others get an empty ``ack()``.
    """
    set_correlation_id()
    request_type, request_id = get_request_type(body)
    _logger.info(
        "request_received",
        extra={
            "request_type": request_type,
            "request_id": request_id,
            "team_id": safe_get(body, "team_id"),
            "phase": "view_ack",
        },
    )
    _logger.debug("request_body", extra={"body": json.dumps(_redact_sensitive(body))})

    ack_handler = VIEW_ACK_MAPPER.get(request_id)
    if ack_handler:
        result = ack_handler(body, client, context)
        if isinstance(result, dict):
            ack(**result)
        else:
            ack()
    else:
        ack()


def main_response(body: dict, logger, client, ack, context: dict) -> None:
    """Central dispatcher for every Slack request (lazy work phase in production).

    In production, ``view_submission`` HTTP ack is sent by :func:`view_ack` first;
    this function runs afterward and must not call ``ack()`` again for views.

    In local development, view ack + work run in one invocation: deferred views
    call the ack handler from :data:`~routing.VIEW_ACK_MAPPER`, then the work handler.

    A unique correlation ID is assigned to every incoming request and
    attached to all log entries emitted while processing it.
    """
    set_correlation_id()
    request_type, request_id = get_request_type(body)

    if request_type == "view_submission":
        if LOCAL_DEVELOPMENT:
            ack_handler = VIEW_ACK_MAPPER.get(request_id)
            if ack_handler:
                result = ack_handler(body, client, context)
                if isinstance(result, dict):
                    ack(**result)
                else:
                    ack()
            else:
                ack()
        # Production: ack already sent by view_ack
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
            emit_metric(
                "request_handled",
                duration_ms=round(get_request_duration_ms(), 1),
                request_type=request_type,
                request_id=request_id,
            )
        except Exception:
            emit_metric(
                "request_error",
                request_type=request_type,
                request_id=request_id,
            )
            raise
    else:
        if not (
            request_type == "view_submission"
            and request_id in VIEW_ACK_MAPPER
            and request_id not in VIEW_MAPPER
        ):
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
if LOCAL_DEVELOPMENT:
    app.view(MATCH_ALL_PATTERN)(main_response)
else:
    app.view(MATCH_ALL_PATTERN)(ack=view_ack, lazy=[main_response])


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
