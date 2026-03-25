"""Invariant: deferred-ack view callback IDs stay registered in VIEW_ACK_MAPPER / VIEW_MAPPER."""

from routing import VIEW_ACK_MAPPER, VIEW_MAPPER
from slack import actions
from slack.deferred_ack_views import DEFERRED_ACK_VIEW_CALLBACK_IDS


def test_deferred_ack_matches_view_ack_mapper():
    assert frozenset(VIEW_ACK_MAPPER.keys()) == DEFERRED_ACK_VIEW_CALLBACK_IDS


def test_publish_mode_is_ack_only_not_in_work_mapper():
    assert actions.CONFIG_PUBLISH_MODE_SUBMIT in VIEW_ACK_MAPPER
    assert actions.CONFIG_PUBLISH_MODE_SUBMIT not in VIEW_MAPPER


def test_deferred_work_views_have_work_handlers():
    for callback_id in DEFERRED_ACK_VIEW_CALLBACK_IDS:
        if callback_id == actions.CONFIG_PUBLISH_MODE_SUBMIT:
            continue
        assert callback_id in VIEW_MAPPER, f"missing VIEW_MAPPER work entry for {callback_id!r}"


def test_deferred_ack_set_is_nonempty():
    assert len(DEFERRED_ACK_VIEW_CALLBACK_IDS) >= 1
