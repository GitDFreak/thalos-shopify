"""Custom + smart collections sync with product associations (collects).

Ported from notebook v1: handles both custom_collections and smart_collections,
plus collects (product↔collection associations) for custom collections.
"""
from rich.console import Console

from ..api import ShopifyAPI
from ..engine import sync_resources, run_parallel, SyncResult
from ..state import SyncState, SyncHistory
from ..remap import remap_body_html

console = Console()

_COMMON_FIELDS = ["title", "body_html", "handle", "sort_order", "template_suffix",
                  "published_at", "published_scope"]


def _build_payload(col, cfg, extras=None) -> dict:
    payload = {
        "title": col["title"],
        "body_html": remap_body_html(col.get("body_html", ""), cfg.source_store, cfg.dest_store),
        "handle": col.get("handle"),
        "sort_order": col.get("sort_order", "alpha-asc"),
        "template_suffix": col.get("template_suffix"),
        "published_at": col.get("published_at"),
        "published_scope": col.get("published_scope", "web"),
    }
    if col.get("image") and col["image"].get("src"):
        payload["image"] = {"src": col["image"]["src"]}
    if extras:
        payload.update(extras)
    return payload


def _compare(src, dst) -> bool:
    for f in _COMMON_FIELDS:
        if str(src.get(f, "") or "") != str(dst.get(f, "") or ""):
            return True
    return False


def _sync_custom(src_api, dst_api, state, history, cfg) -> SyncResult:
    src_items = src_api.paginate("custom_collections.json", "custom_collections", params=cfg.since_params())
    dst_items = dst_api.paginate("custom_collections.json", "custom_collections")
    console.print(f"  custom: {len(src_items)} source │ {len(dst_items)} dest")

    def _create(col):
        payload = _build_payload(col, cfg)
        r = dst_api.post("custom_collections.json", {"custom_collection": payload}, silent=True)
        if r.status_code != 201:
            return None
        created = r.json()["custom_collection"]
        history.record("create", "custom_collection", col["id"], new_state={"id": created["id"]})
        return created["id"]

    def _update(dst_id, src_col):
        payload = _build_payload(src_col, cfg, {"id": dst_id})
        r = dst_api.put(f"custom_collections/{dst_id}.json", {"custom_collection": payload})
        if r.status_code == 200:
            history.record("update", "custom_collection", dst_id)
            return True
        return False

    def _delete(dst_id):
        r = dst_api.delete(f"custom_collections/{dst_id}.json")
        if r.status_code == 200:
            history.record("delete", "custom_collection", dst_id)
            return True
        return False

    result, id_map = sync_resources(
        src_items=src_items, dst_items=dst_items, key_field="handle",
        create_fn=_create, update_fn=_update, delete_fn=_delete,
        compare_fn=_compare, delete_orphans=cfg.should_delete_orphans("collections"),
        workers=cfg.max_workers, desc="custom collections",
    )
    state.collection.update(id_map)
    return result, id_map


def _sync_smart(src_api, dst_api, state, history, cfg) -> SyncResult:
    src_items = src_api.paginate("smart_collections.json", "smart_collections", params=cfg.since_params())
    dst_items = dst_api.paginate("smart_collections.json", "smart_collections")
    console.print(f"  smart:  {len(src_items)} source │ {len(dst_items)} dest")

    def _create(col):
        extras = {"disjunctive": col.get("disjunctive", False)}
        if col.get("rules"):
            extras["rules"] = [{"column": r["column"], "relation": r["relation"],
                                "condition": r["condition"]} for r in col["rules"]]
        payload = _build_payload(col, cfg, extras)
        r = dst_api.post("smart_collections.json", {"smart_collection": payload}, silent=True)
        if r.status_code != 201:
            return None
        created = r.json()["smart_collection"]
        history.record("create", "smart_collection", col["id"], new_state={"id": created["id"]})
        return created["id"]

    def _update(dst_id, src_col):
        extras = {"id": dst_id}
        if src_col.get("rules"):
            extras["rules"] = [{"column": r["column"], "relation": r["relation"],
                                "condition": r["condition"]} for r in src_col["rules"]]
        payload = _build_payload(src_col, cfg, extras)
        r = dst_api.put(f"smart_collections/{dst_id}.json", {"smart_collection": payload})
        if r.status_code == 200:
            history.record("update", "smart_collection", dst_id)
            return True
        return False

    def _delete(dst_id):
        r = dst_api.delete(f"smart_collections/{dst_id}.json")
        if r.status_code == 200:
            history.record("delete", "smart_collection", dst_id)
            return True
        return False

    result, id_map = sync_resources(
        src_items=src_items, dst_items=dst_items, key_field="handle",
        create_fn=_create, update_fn=_update, delete_fn=_delete,
        compare_fn=_compare, delete_orphans=cfg.should_delete_orphans("collections"),
        workers=cfg.max_workers, desc="smart collections",
    )
    state.collection.update(id_map)
    return result, id_map


def _sync_collects(src_api, dst_api, state, history, cfg, custom_id_map) -> int:
    """For each custom collection, mirror product↔collection associations.
    Smart collections are rule-based so they don't need collects.
    """
    if not state.product:
        console.print("  [yellow]skip collects: product id_map empty (run products section first)[/yellow]")
        return 0

    def _mirror_one(pair):
        src_cid, dst_cid = pair
        src_collects = src_api.paginate("collects.json", "collects",
                                         params={"collection_id": src_cid})
        dst_collects = dst_api.paginate("collects.json", "collects",
                                         params={"collection_id": dst_cid})
        existing_dst_pids = {c["product_id"] for c in dst_collects}
        created = 0
        for c in src_collects:
            dst_pid = state.product.get(c["product_id"])
            if not dst_pid or dst_pid in existing_dst_pids:
                continue
            r = dst_api.post("collects.json", {"collect": {
                "product_id": dst_pid, "collection_id": dst_cid,
                "position": c.get("position"),
            }}, silent=True)
            if r.status_code == 201:
                created += 1
                history.record("create", "collect", c["id"],
                               new_state={"product_id": dst_pid, "collection_id": dst_cid})
        return created

    pairs = list(custom_id_map.items())
    if not pairs:
        return 0
    results = run_parallel(_mirror_one, pairs, "collects", cfg.max_workers)
    return sum(r for r in results if isinstance(r, int))


def sync_collections(src_api: ShopifyAPI, dst_api: ShopifyAPI, state: SyncState,
                     history: SyncHistory, config) -> SyncResult:
    """Full collections sync: custom + smart + collects."""
    total = SyncResult()

    custom_res, custom_map = _sync_custom(src_api, dst_api, state, history, config)
    smart_res, _ = _sync_smart(src_api, dst_api, state, history, config)

    for r in (custom_res, smart_res):
        total.created += r.created
        total.updated += r.updated
        total.deleted += r.deleted
        total.skipped += r.skipped
        total.errors += r.errors

    collects_created = _sync_collects(src_api, dst_api, state, history, config, custom_map)
    if collects_created:
        console.print(f"  {collects_created} product↔collection associations")
        total.created += collects_created

    state.save_map("collection", config.snapshot_dir)
    return total
