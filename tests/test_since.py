from thalos.config import SyncConfig


def test_since_none_returns_none_params():
    assert SyncConfig(since=None).since_params() is None
    assert SyncConfig().since_params() is None


def test_since_set_returns_updated_at_min():
    params = SyncConfig(since="2026-04-01").since_params()
    assert params == {"updated_at_min": "2026-04-01"}


def test_since_forces_delete_orphans_off_global_bool():
    c = SyncConfig(delete_orphans=True, since="2026-04-01")
    assert c.should_delete_orphans("products") is False
    assert c.should_delete_orphans("redirects") is False


def test_since_forces_delete_orphans_off_scoped_list():
    # Even when a user explicitly scoped delete_orphans to a list, incremental
    # mode MUST win — otherwise we'd purge items that simply weren't fetched.
    c = SyncConfig(
        delete_orphans=False,
        delete_orphans_sections=["products", "redirects"],
        since="2026-04-01",
    )
    assert c.should_delete_orphans("products") is False
    assert c.should_delete_orphans("redirects") is False


def test_no_since_respects_delete_orphans_as_before():
    c = SyncConfig(delete_orphans=True)
    assert c.should_delete_orphans("products") is True
