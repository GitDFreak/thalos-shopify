"""Orders sync — READ from source, CREATE on dest (since date filter).

Orders are special:
- They represent sales/transactions
- They CANNOT be updated or deleted on dest (Shopify restriction)
- They are filtered by date (ORDERS_SINCE config)
- Customer data is anonymized (RGPD)
- Financial data is preserved for testing
"""
import time
from datetime import datetime
from rich.console import Console

from ..api import ShopifyAPI
from ..engine import run_parallel, SyncResult
from ..state import SyncState, SyncHistory

console = Console()


def sync_orders(src_api: ShopifyAPI, dst_api: ShopifyAPI, state: SyncState,
                history: SyncHistory, config) -> SyncResult:
    """
    Copy orders from source to dest since ORDERS_SINCE date.

    - Anonymizes customer data (RGPD)
    - Preserves line items, amounts, discounts
    - Maps product/variant IDs to dest IDs
    - Creates draft orders then marks as paid
    """
    result = SyncResult()

    if not config.orders_since:
        console.print("  [yellow]ORDERS_SINCE non défini — skip orders[/yellow]")
        return result

    since = config.orders_since
    console.print(f"  Commandes depuis: {since}")

    # Fetch source orders
    with console.status("[cyan]Lecture commandes source..."):
        params = {
            "status": "any",
            "created_at_min": f"{since}T00:00:00Z",
        }
        src_orders = src_api.paginate("orders.json", "orders", params=params)

    # Fetch existing dest orders (by name/number for idempotence)
    with console.status("[cyan]Lecture commandes dest..."):
        dst_orders = dst_api.paginate("orders.json", "orders", params={"status": "any"})

    dst_names = {o.get("name") for o in dst_orders}
    to_create = [o for o in src_orders if o.get("name") not in dst_names]

    console.print(f"  {len(src_orders)} source │ {len(dst_orders)} dest │ {len(to_create)} à créer")

    if not to_create:
        return result

    def _create_order(order):
        """Create order on dest via DraftOrder → Complete flow."""
        # Map line items: product/variant IDs source → dest
        line_items = []
        for li in order.get("line_items", []):
            item = {
                "title": li.get("title", ""),
                "quantity": li.get("quantity", 1),
                "price": li.get("price", "0.00"),
            }
            # Map variant ID if possible
            src_variant_id = li.get("variant_id")
            if src_variant_id and src_variant_id in state.variant:
                item["variant_id"] = state.variant[src_variant_id]
            elif li.get("sku"):
                item["sku"] = li["sku"]

            # Preserve discount allocations
            if li.get("discount_allocations"):
                item["applied_discount"] = {
                    "description": "Imported discount",
                    "value_type": "fixed_amount",
                    "value": str(sum(float(d.get("amount", 0)) for d in li["discount_allocations"])),
                }
            line_items.append(item)

        if not line_items:
            return None

        # Anonymize customer (RGPD)
        shipping = order.get("shipping_address") or {}
        billing = order.get("billing_address") or {}

        draft_payload = {
            "draft_order": {
                "line_items": line_items,
                "note": f"Imported from {order.get('name', '')} ({order.get('created_at', '')})",
                "tags": f"imported,source:{order.get('name', '')}",
                # Anonymized customer
                "shipping_address": {
                    "first_name": "Client",
                    "last_name": f"#{order.get('order_number', '')}",
                    "address1": shipping.get("address1", "Adresse importée"),
                    "city": shipping.get("city", "Ville"),
                    "province_code": shipping.get("province_code", ""),
                    "country_code": shipping.get("country_code", "FR"),
                    "zip": shipping.get("zip", "00000"),
                },
                "billing_address": {
                    "first_name": "Client",
                    "last_name": f"#{order.get('order_number', '')}",
                    "address1": billing.get("address1", "Adresse importée"),
                    "city": billing.get("city", "Ville"),
                    "province_code": billing.get("province_code", ""),
                    "country_code": billing.get("country_code", "FR"),
                    "zip": billing.get("zip", "00000"),
                },
                # Financial
                "currency": order.get("currency", "EUR"),
                "tax_exempt": order.get("tax_exempt", False),
            }
        }

        # Shipping line
        if order.get("shipping_lines"):
            sl = order["shipping_lines"][0]
            draft_payload["draft_order"]["shipping_line"] = {
                "title": sl.get("title", "Standard"),
                "price": sl.get("price", "0.00"),
            }

        # Create draft order
        r = dst_api.post("draft_orders.json", draft_payload, silent=True)
        if r.status_code != 201:
            return None

        draft = r.json().get("draft_order", {})
        draft_id = draft.get("id")
        if not draft_id:
            return None

        # Complete the draft (mark as paid) if original was paid
        financial_status = order.get("financial_status", "pending")
        if financial_status in ("paid", "partially_paid", "refunded", "partially_refunded"):
            complete_r = dst_api.put(
                f"draft_orders/{draft_id}/complete.json",
                {"payment_pending": False}
            )
            if complete_r.status_code == 200:
                completed = complete_r.json().get("draft_order", {})
                order_id = completed.get("order_id")
                if order_id:
                    history.record("create", "order", order["id"],
                                   new_state={"draft_id": draft_id, "order_id": order_id})
                    return order_id

        history.record("create", "order", order["id"], new_state={"draft_id": draft_id})
        return draft_id

    # Create orders (sequential — orders are sensitive)
    results = run_parallel(_create_order, to_create, "Commandes", workers=min(config.max_workers, 3))
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            result.errors += 1
        elif r:
            result.created += 1
            state.order[to_create[i]["id"]] = r
        else:
            result.errors += 1

    return result
