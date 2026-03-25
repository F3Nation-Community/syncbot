"""Guardrails: Slack Bolt listener wiring for deferred view submissions (sync app.view)."""

from pathlib import Path


def test_app_py_registers_view_listener_synchronously():
    """app.view must call main_response directly so view_submission ack reaches Slack (not lazy)."""
    root = Path(__file__).resolve().parents[1]
    app_py = root / "syncbot" / "app.py"
    text = app_py.read_text(encoding="utf-8")
    assert "app.view(MATCH_ALL_PATTERN)(main_response)" in text
    assert "app.event(MATCH_ALL_PATTERN)(*ARGS, **LAZY_KWARGS)" in text
    assert "app.action(MATCH_ALL_PATTERN)(*ARGS, **LAZY_KWARGS)" in text


def test_bolt_view_listener_has_no_lazy_functions():
    """Bolt CustomListener for app.view should use main_response as ack only (no lazy split)."""
    import app as app_module

    bolt_app = app_module.app
    sync_main = [
        li
        for li in bolt_app._listeners
        if getattr(li.ack_function, "__name__", None) == "main_response" and len(li.lazy_functions) == 0
    ]
    assert sync_main, "expected at least one listener with ack_function=main_response and empty lazy_functions"


def test_bolt_event_or_action_uses_lazy_main_response_in_prod_mode():
    """When not LOCAL_DEVELOPMENT, event/action listeners should defer work to lazy main_response."""
    import app as app_module

    if app_module.LOCAL_DEVELOPMENT:
        return
    bolt_app = app_module.app
    lazy = [
        li
        for li in bolt_app._listeners
        if li.lazy_functions and any(getattr(f, "__name__", None) == "main_response" for f in li.lazy_functions)
    ]
    assert lazy, "expected lazy listeners with main_response when LOCAL_DEVELOPMENT is false"
