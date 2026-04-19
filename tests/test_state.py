from thalos.state import SyncState, SyncHistory


def test_roundtrip_save_and_load(tmp_snapshot_dir):
    s = SyncState()
    s.product[1] = 100
    s.product[2] = 200
    s.collection[10] = 500
    s.completed.add("products")
    s.timings["products"] = 12.3
    s.save_all(tmp_snapshot_dir)

    s2 = SyncState()
    loaded = s2.load_all(tmp_snapshot_dir)
    assert loaded >= 2
    assert s2.product == {1: 100, 2: 200}
    assert s2.collection == {10: 500}
    assert "products" in s2.completed
    assert s2.timings["products"] == 12.3


def test_mark_done_persists_across_reloads(tmp_snapshot_dir):
    s = SyncState()
    s.mark_done("blogs", tmp_snapshot_dir)
    s2 = SyncState()
    s2.load_all(tmp_snapshot_dir)
    assert s2.is_done("blogs")


def test_history_append_and_readback(tmp_snapshot_dir):
    h = SyncHistory(tmp_snapshot_dir)
    h.record("create", "product", 1, new_state={"id": 100})
    h.record("update", "product", 100)
    entries = h.last_n(10)
    assert len(entries) == 2
    assert entries[0]["action"] == "create"
    assert entries[1]["action"] == "update"
