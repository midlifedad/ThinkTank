"""Unit tests for Phase 6 discovery handler registration and signatures.

Tests that all 2 discovery handlers are registered in the handler registry
and conform to the JobHandler protocol (async callable taking session + job).
"""

import inspect

from thinktank.handlers.registry import get_handler


class TestDiscoveryHandlerRegistration:
    """All 2 Phase 6 handlers must be registered in the handler registry."""

    def test_scan_for_candidates_registered(self):
        handler = get_handler("scan_for_candidates")
        assert handler is not None, "scan_for_candidates not registered"

    def test_discover_guests_podcastindex_registered(self):
        handler = get_handler("discover_guests_podcastindex")
        assert handler is not None, "discover_guests_podcastindex not registered"


class TestDiscoveryHandlerProtocol:
    """Each handler must be an async callable with (session, job) signature."""

    def test_scan_for_candidates_is_async(self):
        handler = get_handler("scan_for_candidates")
        assert handler is not None
        assert inspect.iscoroutinefunction(handler)

    def test_discover_guests_podcastindex_is_async(self):
        handler = get_handler("discover_guests_podcastindex")
        assert handler is not None
        assert inspect.iscoroutinefunction(handler)

    def test_scan_for_candidates_signature(self):
        handler = get_handler("scan_for_candidates")
        assert handler is not None
        sig = inspect.signature(handler)
        params = list(sig.parameters.keys())
        assert "session" in params
        assert "job" in params

    def test_discover_guests_podcastindex_signature(self):
        handler = get_handler("discover_guests_podcastindex")
        assert handler is not None
        sig = inspect.signature(handler)
        params = list(sig.parameters.keys())
        assert "session" in params
        assert "job" in params
