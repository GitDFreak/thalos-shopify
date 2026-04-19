"""Navigation menus sync — with resourceId remap + UPDATE/DELETE."""
from rich.console import Console
from ..api import ShopifyAPI, ERRORS
from ..engine import SyncResult
from ..state import SyncState, SyncHistory

console = Console()

MENU_Q = '{menus(first:50){edges{node{id title handle items{id title type url resourceId tags items{id title type url resourceId tags items{id title type url resourceId tags}}}}}}}'


def sync_menus(src_api: ShopifyAPI, dst_api: ShopifyAPI, state: SyncState,
               history: SyncHistory, config) -> SyncResult:
    result = SyncResult()

    src_menus = [e["node"] for e in (src_api.gql(MENU_Q).get("data") or {}).get("menus", {}).get("edges", [])]
    dst_menus = {e["node"]["handle"]: e["node"] for e in (dst_api.gql(MENU_Q).get("data") or {}).get("menus", {}).get("edges", [])}

    console.print(f"  {len(src_menus)} source │ {len(dst_menus)} dest")

    def _remap_rid(rid, menu_handle, item_title):
        """Return (new_gid, ok). ok=False => menu item will be dropped to avoid
        creating a dead link on dst."""
        if not rid:
            return rid, True
        parts = rid.split("/")
        if len(parts) < 4:
            return rid, True
        t, oid = parts[-2], int(parts[-1])
        m = {"Collection": state.collection, "Product": state.product,
             "Page": state.page, "Blog": state.blog, "Article": state.article}.get(t)
        nid = m.get(oid) if m else None
        if nid:
            return f"gid://shopify/{t}/{nid}", True
        ERRORS.add(f"menu '{menu_handle}' → item '{item_title}': {t}#{oid} not in id_map (dropped)")
        return None, False

    def _remap_items(items, menu_handle):
        out = []
        for i in items:
            entry = {
                "title": i["title"],
                "type": i.get("type", "HTTP"),
            }
            if i.get("url"):
                entry["url"] = i["url"]
            if i.get("resourceId"):
                new_rid, ok = _remap_rid(i["resourceId"], menu_handle, i["title"])
                if not ok:
                    continue  # drop this item — broken link would stay
                entry["resourceId"] = new_rid
            if i.get("tags"):
                entry["tags"] = i["tags"]
            entry["items"] = _remap_items(i.get("items") or [], menu_handle)
            out.append(entry)
        return out

    src_handles = {m["handle"] for m in src_menus}

    for menu in src_menus:
        items = _remap_items(menu.get("items") or [], menu["handle"])
        if menu["handle"] in dst_menus:
            dm = dst_menus[menu["handle"]]
            r = dst_api.gql(
                'mutation($id:ID!,$t:String!,$h:String!,$i:[MenuItemUpdateInput!]!){menuUpdate(id:$id,title:$t,handle:$h,items:$i){menu{id}userErrors{field message}}}',
                {"id": dm["id"], "t": menu["title"], "h": menu["handle"], "i": items},
            )
            ue = ((r.get("data") or {}).get("menuUpdate") or {}).get("userErrors", [])
            if not ue:
                result.updated += 1
                history.record("update", "menu", menu["handle"])
            else:
                result.errors += 1
        else:
            r = dst_api.gql(
                'mutation($t:String!,$h:String!,$i:[MenuItemCreateInput!]!){menuCreate(title:$t,handle:$h,items:$i){menu{id}userErrors{field message}}}',
                {"t": menu["title"], "h": menu["handle"], "i": items},
            )
            ue = ((r.get("data") or {}).get("menuCreate") or {}).get("userErrors", [])
            if not ue:
                result.created += 1
                history.record("create", "menu", menu["handle"])
            else:
                result.errors += 1

    if config.should_delete_orphans("menus"):
        for handle, dm in dst_menus.items():
            if handle in src_handles or handle == "main-menu":
                continue
            r = dst_api.gql(
                'mutation($id:ID!){menuDelete(id:$id){deletedMenuId userErrors{field message}}}',
                {"id": dm["id"]},
            )
            ue = ((r.get("data") or {}).get("menuDelete") or {}).get("userErrors", [])
            if not ue:
                result.deleted += 1
                history.record("delete", "menu", handle)
            else:
                result.errors += 1

    return result
