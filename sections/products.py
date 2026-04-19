"""Products sync — CREATE + UPDATE + DELETE (archive)."""
import logging
import time
from rich.console import Console

from ..api import ShopifyAPI, ERRORS
from ..engine import sync_resources, run_parallel, SyncResult
from ..state import SyncState, SyncHistory
from ..remap import remap_body_html

console = Console()
logger = logging.getLogger(__name__)


def _compare_products(src, dst) -> bool:
    """Return True if products differ (= needs update)."""
    for field in ["title", "body_html", "vendor", "product_type", "tags", "status",
                   "template_suffix", "published_at"]:
        if str(src.get(field, "")) != str(dst.get(field, "")):
            return True
    return False


def sync_products(src_api: ShopifyAPI, dst_api: ShopifyAPI, state: SyncState,
                  history: SyncHistory, config) -> SyncResult:
    """Full product sync: create new, update changed, optionally archive removed."""

    src_params = config.since_params()
    with console.status("[cyan]Lecture produits source..."):
        src_products = src_api.paginate("products.json", "products", params=src_params)
    with console.status("[cyan]Lecture produits dest..."):
        dst_products = dst_api.paginate("products.json", "products")
    if src_params:
        console.print(f"  [dim]incremental: src filtered by updated_at_min={config.since}[/dim]")

    # Fulfillment services: any variant whose fs isn't installed on dst is
    # downgraded to "manual". Detect + log the downgraded set once so the
    # user knows BEFORE discovering a 3PL is silently detached.
    try:
        src_fs_names = {f["name"] for f in src_api.get("fulfillment_services.json").json().get("fulfillment_services", [])}
    except Exception:
        logger.warning("src fulfillment_services fetch failed — variants may downgrade to 'manual' unnecessarily", exc_info=True)
        src_fs_names = set()
    try:
        dst_fs_names = {f["name"] for f in dst_api.get("fulfillment_services.json").json().get("fulfillment_services", [])}
    except Exception:
        logger.warning("dst fulfillment_services fetch failed — all variants will downgrade to 'manual'", exc_info=True)
        dst_fs_names = set()
    fs = dst_fs_names | {"manual"}

    missing_fs = src_fs_names - dst_fs_names
    if missing_fs:
        # Count how many variants across the source catalog use a missing fs —
        # gives the operator a visible blast-radius.
        affected = sum(
            1 for p in src_products for v in p.get("variants", [])
            if v.get("fulfillment_service") in missing_fs
        )
        ERRORS.add(
            f"fulfillment_services manquants côté dst: {sorted(missing_fs)} "
            f"→ {affected} variant(s) rebrandés en 'manual'. "
            f"Install les apps correspondantes côté dst pour préserver l'iso-prod."
        )
        console.print(f"  [yellow]⚠ {len(missing_fs)} fs manquants · {affected} variants → manual[/yellow]")

    console.print(f"  {len(src_products)} source │ {len(dst_products)} dest")

    def _create(product):
        variants = [{
            "title": v.get("title", ""), "price": v.get("price", "0.00"),
            "compare_at_price": v.get("compare_at_price"),
            "sku": v.get("sku", ""), "barcode": v.get("barcode"),
            "weight": v.get("weight", 0), "weight_unit": v.get("weight_unit", "kg"),
            "inventory_management": v.get("inventory_management"),
            "inventory_policy": v.get("inventory_policy", "deny"),
            "fulfillment_service": v.get("fulfillment_service") if v.get("fulfillment_service") in fs else "manual",
            "option1": v.get("option1"), "option2": v.get("option2"), "option3": v.get("option3"),
            "taxable": v.get("taxable", True), "requires_shipping": v.get("requires_shipping", True),
        } for v in product.get("variants", [])]
        images = [{"src": i["src"], "alt": i.get("alt", ""), "position": i.get("position", 1)}
                  for i in product.get("images", [])]
        options = [{"name": o["name"], "values": o.get("values", [])} for o in product.get("options", [])]
        payload = {
            "title": product["title"],
            "body_html": remap_body_html(product.get("body_html", ""), config.source_store, config.dest_store),
            "vendor": product.get("vendor", ""), "product_type": product.get("product_type", ""),
            "tags": product.get("tags", ""), "status": product.get("status", "draft"),
            "handle": product.get("handle"), "published_at": product.get("published_at"),
            "template_suffix": product.get("template_suffix"),
        }
        if variants:
            payload["variants"] = variants
        if options:
            payload["options"] = options
        if images:
            payload["images"] = images
        r = dst_api.post("products.json", {"product": payload}, silent=True)
        if r.status_code != 201:
            return None
        created = r.json()["product"]
        # Map variants + inventory items
        for ov, nv in zip(product.get("variants", []), created.get("variants", [])):
            state.inventory_item[ov["inventory_item_id"]] = nv["inventory_item_id"]
            state.variant[ov["id"]] = nv["id"]
        history.record("create", "product", product["id"], new_state={"id": created["id"]})
        return created["id"]

    def _update(dst_id, src_product):
        payload = {
            "product": {
                "id": dst_id,
                "title": src_product["title"],
                "body_html": remap_body_html(src_product.get("body_html", ""), config.source_store, config.dest_store),
                "vendor": src_product.get("vendor", ""),
                "product_type": src_product.get("product_type", ""),
                "tags": src_product.get("tags", ""),
                "status": src_product.get("status", "draft"),
                "template_suffix": src_product.get("template_suffix"),
            }
        }
        r = dst_api.put(f"products/{dst_id}.json", payload)
        if r.status_code == 200:
            history.record("update", "product", dst_id)
            return True
        return False

    def _delete(dst_id):
        # Soft delete: archive instead of hard delete
        r = dst_api.put(f"products/{dst_id}.json", {"product": {"id": dst_id, "status": "archived"}})
        if r.status_code == 200:
            history.record("delete", "product", dst_id)
            return True
        return False

    result, id_map = sync_resources(
        src_items=src_products,
        dst_items=dst_products,
        key_field="handle",
        create_fn=_create,
        update_fn=_update,
        delete_fn=_delete,
        compare_fn=_compare_products,
        delete_orphans=config.should_delete_orphans("products"),
        workers=config.max_workers,
        desc="products",
    )

    # Merge into state
    state.product.update(id_map)
    return result
