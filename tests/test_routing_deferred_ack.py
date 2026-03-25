"""Invariant: deferred-ack view callback IDs stay registered in VIEW_MAPPER."""

from routing import VIEW_MAPPER
from slack.deferred_ack_views import DEFERRED_ACK_VIEW_CALLBACK_IDS


def test_deferred_ack_views_are_routed():
    for callback_id in DEFERRED_ACK_VIEW_CALLBACK_IDS:
        assert callback_id in VIEW_MAPPER, f"missing VIEW_MAPPER entry for deferred view {callback_id!r}"


def test_deferred_ack_set_is_nonempty():
    assert len(DEFERRED_ACK_VIEW_CALLBACK_IDS) >= 1
