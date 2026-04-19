"""Sanity: new sections are wired in orchestrator + have proper deps declared."""
from thalos.orchestrator import SECTION_ORDER, SECTION_DEPS, preflight_check
from thalos.state import SyncState


def test_new_sections_in_section_order():
    names = [n for n, _ in SECTION_ORDER]
    assert "delivery" in names
    assert "segments" in names
    assert "webhooks" in names
    # Theme must stay the last section (references everything).
    assert names[-1] == "theme"


def test_delivery_requires_location_map():
    warns = preflight_check(["delivery"], SyncState())
    assert len(warns) == 1
    assert "location" in warns[0]


def test_segments_and_webhooks_are_independent():
    # No cross-section deps — should produce zero warnings when run alone.
    assert preflight_check(["segments"], SyncState()) == []
    assert preflight_check(["webhooks"], SyncState()) == []


def test_delivery_with_location_present_has_no_warnings():
    s = SyncState()
    s.location[1] = 100
    assert preflight_check(["delivery"], s) == []
