"""Stock & Inventory sync — batch read + activate + set quantities."""
import logging
from collections import defaultdict
from rich.console import Console
from ..api import ShopifyAPI
from ..engine import SyncResult, run_parallel
from ..state import SyncState, SyncHistory

console = Console()
logger = logging.getLogger(__name__)


def sync_stock(src_api: ShopifyAPI, dst_api: ShopifyAPI, state: SyncState,
               history: SyncHistory, config) -> SyncResult:
    result = SyncResult()
    BATCH = 50

    items_list = list(state.inventory_item.items())
    batches = [items_list[i:i + BATCH] for i in range(0, len(items_list), BATCH)]
    console.print(f"  {len(items_list)} items │ {len(state.location)} locations │ {len(batches)} batches")

    if not items_list or not state.location:
        console.print("  [yellow]Pas d'inventory items ou locations — skip[/yellow]")
        return result

    # Read levels (batch 50/request)
    item_levels = defaultdict(list)
    skipped = 0

    def _fetch_batch(batch):
        old_ids = [str(oid) for oid, _ in batch]
        id2new = {oid: nid for oid, nid in batch}
        try:
            r = src_api.get("inventory_levels.json", {"inventory_item_ids": ",".join(old_ids)})
            return [(id2new[lv["inventory_item_id"]], lv)
                    for lv in r.json().get("inventory_levels", [])
                    if lv["inventory_item_id"] in id2new]
        except Exception:
            logger.warning("inventory_levels batch fetch failed (%d items)", len(old_ids), exc_info=True)
            return []

    results = run_parallel(_fetch_batch, batches, "Lecture stock", config.max_workers)
    for res in results:
        if isinstance(res, Exception) or not res:
            continue
        for nid, lv in res:
            dloc = state.location.get(lv["location_id"])
            qty = lv.get("available")
            if not dloc or qty is None:
                skipped += 1
                continue
            item_levels[nid].append((dloc, qty))

    total = sum(len(v) for v in item_levels.values())
    console.print(f"  {total} niveaux à écrire")

    # Activate
    act_mut = 'mutation($id:ID!,$u:[InventoryBulkToggleActivationInput!]!){inventoryBulkToggleActivation(inventoryItemId:$id,inventoryItemUpdates:$u){inventoryItem{id}userErrors{field message}}}'

    def _activate(pair):
        nid, levels = pair
        locs = list({lid for lid, _ in levels})
        dst_api.gql(act_mut, {"id": f"gid://shopify/InventoryItem/{nid}",
                               "u": [{"locationId": f"gid://shopify/Location/{l}", "activate": True} for l in locs]})

    run_parallel(_activate, list(item_levels.items()), "Activation", workers=min(config.max_workers, 8))

    # Set quantities
    set_mut = 'mutation($i:InventorySetQuantitiesInput!){inventorySetQuantities(input:$i){inventoryAdjustmentGroup{reason}userErrors{message}}}'
    by_loc = defaultdict(list)
    for nid, levels in item_levels.items():
        for lid, qty in levels:
            by_loc[lid].append((nid, qty))

    all_batches = [(lid, items[i:i + 100]) for lid, items in by_loc.items() for i in range(0, len(items), 100)]

    def _set_batch(args):
        lid, batch = args
        quantities = [{"inventoryItemId": f"gid://shopify/InventoryItem/{iid}",
                        "locationId": f"gid://shopify/Location/{lid}", "quantity": q}
                       for iid, q in batch]
        dst_api.gql(set_mut, {"i": {"ignoreCompareQuantity": True, "name": "available",
                                     "reason": "correction", "referenceDocumentUri": "shopify-sync://v4",
                                     "quantities": quantities}})
        return len(batch)

    results = run_parallel(_set_batch, all_batches, "Écriture stock", workers=min(config.max_workers, 4))
    synced = sum(r for r in results if isinstance(r, int))
    result.updated = synced
    result.skipped = skipped
    return result
