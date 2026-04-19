from thalos.engine import sync_resources, SyncResult


def _item(i, key, **extra):
    return {"id": i, key: f"item-{i}", **extra}


def test_create_all_when_dst_empty():
    src = [_item(1, "handle"), _item(2, "handle")]
    created = []
    result, id_map = sync_resources(
        src_items=src, dst_items=[], key_field="handle",
        create_fn=lambda it: (created.append(it["id"]) or 100 + it["id"]),
        update_fn=None, compare_fn=None, workers=1, desc="t",
    )
    assert result.created == 2 and result.updated == 0 and result.skipped == 0
    assert id_map == {1: 101, 2: 102}
    assert sorted(created) == [1, 2]


def test_skip_when_match_and_no_diff():
    src = [_item(1, "handle", title="A"), _item(2, "handle", title="B")]
    dst = [{"id": 11, "handle": "item-1", "title": "A"},
           {"id": 12, "handle": "item-2", "title": "B"}]
    result, id_map = sync_resources(
        src_items=src, dst_items=dst, key_field="handle",
        create_fn=lambda it: None,
        update_fn=lambda dst_id, src_item: True,
        compare_fn=lambda s, d: s["title"] != d["title"],
        workers=1, desc="t",
    )
    assert result.skipped == 2 and result.created == 0 and result.updated == 0
    assert id_map == {1: 11, 2: 12}


def test_update_when_compare_says_different():
    src = [_item(1, "handle", title="NEW")]
    dst = [{"id": 11, "handle": "item-1", "title": "OLD"}]
    updates = []
    result, _ = sync_resources(
        src_items=src, dst_items=dst, key_field="handle",
        create_fn=lambda it: None,
        update_fn=lambda dst_id, src_item: (updates.append((dst_id, src_item["title"])) or True),
        compare_fn=lambda s, d: s["title"] != d["title"],
        workers=1, desc="t",
    )
    assert result.updated == 1 and result.skipped == 0
    assert updates == [(11, "NEW")]


def test_delete_orphans_when_requested():
    src = [_item(1, "handle")]
    dst = [{"id": 11, "handle": "item-1"}, {"id": 99, "handle": "orphan"}]
    deleted = []
    result, _ = sync_resources(
        src_items=src, dst_items=dst, key_field="handle",
        create_fn=lambda it: None,
        update_fn=lambda dst_id, src_item: True,
        delete_fn=lambda dst_id: (deleted.append(dst_id) or True),
        compare_fn=lambda s, d: False,
        delete_orphans=True, workers=1, desc="t",
    )
    assert result.deleted == 1 and deleted == [99]


def test_create_failure_counts_as_error():
    src = [_item(1, "handle"), _item(2, "handle")]
    result, _ = sync_resources(
        src_items=src, dst_items=[], key_field="handle",
        create_fn=lambda it: None if it["id"] == 2 else 100,
        workers=1, desc="t",
    )
    assert result.created == 1 and result.errors == 1


def test_syncresult_repr_and_dict():
    r = SyncResult(); r.created = 3; r.updated = 1; r.errors = 2
    d = r.to_dict()
    assert d == {"created": 3, "updated": 1, "deleted": 0, "skipped": 0, "errors": 2}
    assert "3 created" in repr(r) and "2 errors" in repr(r)
