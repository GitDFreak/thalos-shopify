from thalos.config import SyncConfig


def test_global_false_blocks_all():
    c = SyncConfig(delete_orphans=False)
    assert c.should_delete_orphans("products") is False
    assert c.should_delete_orphans("redirects") is False


def test_global_true_enables_all():
    c = SyncConfig(delete_orphans=True)
    assert c.should_delete_orphans("products") is True
    assert c.should_delete_orphans("redirects") is True


def test_specific_list_overrides_bool():
    c = SyncConfig(delete_orphans=True, delete_orphans_sections=["redirects"])
    # Even with bool=True, only listed sections delete orphans.
    assert c.should_delete_orphans("redirects") is True
    assert c.should_delete_orphans("products") is False
    assert c.should_delete_orphans("collections") is False


def test_empty_list_falls_back_to_bool():
    c = SyncConfig(delete_orphans=True, delete_orphans_sections=[])
    # Empty list == falsy → fall back to global bool.
    assert c.should_delete_orphans("anything") is True


def test_list_with_bool_false_still_works():
    # User explicitly wants ONLY these sections to delete.
    c = SyncConfig(delete_orphans=False, delete_orphans_sections=["products"])
    assert c.should_delete_orphans("products") is True
    assert c.should_delete_orphans("collections") is False
