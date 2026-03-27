"""Tests for Cloud Run / container HTTP server helpers in ``app``."""

import json
import os
import socket
import threading
import time
import urllib.error
import urllib.request
from unittest.mock import patch

import pytest


def test_http_listen_port_from_env() -> None:
    from app import _http_listen_port

    with patch.dict(os.environ, {"PORT": "8080"}):
        assert _http_listen_port() == 8080


def test_http_listen_port_invalid_falls_back() -> None:
    from app import _http_listen_port

    with patch.dict(os.environ, {"PORT": "nope"}):
        assert _http_listen_port() == 3000


def test_health_endpoint_on_container_server() -> None:
    """GET ``/health`` returns 200 and JSON (same server path as Cloud Run)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    def serve() -> None:
        from app import run_syncbot_http_server

        run_syncbot_http_server(port=port, http_server_logger_enabled=False)

    threading.Thread(target=serve, daemon=True).start()

    url = f"http://127.0.0.1:{port}/health"
    last_err: BaseException | None = None
    for _ in range(100):
        try:
            with urllib.request.urlopen(url, timeout=0.3) as r:
                assert r.status == 200
                assert json.loads(r.read().decode()) == {"status": "ok"}
                return
        except (urllib.error.URLError, OSError) as e:
            last_err = e
            time.sleep(0.05)
    pytest.fail(f"/health never became ready: {last_err!r}")
