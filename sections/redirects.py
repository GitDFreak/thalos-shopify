"""URL redirects sync — matched by path, destination can differ."""
from rich.console import Console

from ..api import ShopifyAPI
from ..engine import sync_resources, SyncResult
from ..state import SyncState, SyncHistory

console = Console()


def sync_redirects(src_api: ShopifyAPI, dst_api: ShopifyAPI, state: SyncState,
                   history: SyncHistory, config) -> SyncResult:
    src_items = src_api.paginate("redirects.json", "redirects")
    dst_items = dst_api.paginate("redirects.json", "redirects")
    console.print(f"  {len(src_items)} source │ {len(dst_items)} dest")

    def _compare(s, d):
        return (s.get("target", "") or "") != (d.get("target", "") or "")

    def _create(item):
        r = dst_api.post("redirects.json", {"redirect": {
            "path": item["path"], "target": item.get("target", ""),
        }}, silent=True)
        if r.status_code == 201:
            created_id = r.json()["redirect"]["id"]
            history.record("create", "redirect", item["id"], new_state={"id": created_id})
            return created_id
        return None

    def _update(dst_id, src_item):
        r = dst_api.put(f"redirects/{dst_id}.json", {"redirect": {
            "id": dst_id, "path": src_item["path"], "target": src_item.get("target", ""),
        }})
        if r.status_code == 200:
            history.record("update", "redirect", dst_id)
            return True
        return False

    def _delete(dst_id):
        r = dst_api.delete(f"redirects/{dst_id}.json")
        if r.status_code == 200:
            history.record("delete", "redirect", dst_id)
            return True
        return False

    result, _ = sync_resources(
        src_items=src_items, dst_items=dst_items, key_field="path",
        create_fn=_create, update_fn=_update, delete_fn=_delete,
        compare_fn=_compare, delete_orphans=config.should_delete_orphans("redirects"),
        workers=config.max_workers, desc="redirects",
    )
    return result
