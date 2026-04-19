"""Cross-store reference remapping: HTML body URLs + GraphQL GIDs."""
from typing import Dict, Optional


def remap_body_html(html: Optional[str], source_store: str, dest_store: str) -> Optional[str]:
    """Rewrite absolute myshopify.com URLs pointing at the source store
    so they point at the destination store instead.

    Without this, cloned products/pages/articles/collections whose body_html
    embeds absolute links (navigation, iframes, images) keep pointing at the
    source store and return 404 when visited from the destination.
    """
    if not html or not isinstance(html, str):
        return html
    if not source_store or not dest_store or source_store == dest_store:
        return html
    return html.replace(
        f"https://{source_store}.myshopify.com",
        f"https://{dest_store}.myshopify.com",
    )


# Numeric-id state attrs → Shopify GID type labels they can appear under.
# Multiple labels per attr = aliases (Shopify uses both interchangeably in refs).
_INT_ID_TYPES: Dict[str, tuple[str, ...]] = {
    "product":        ("Product",),
    "variant":        ("ProductVariant",),
    "collection":     ("Collection",),
    "page":           ("Page", "OnlineStorePage"),
    "article":        ("Article", "OnlineStoreArticle"),
    "blog":           ("Blog", "OnlineStoreBlog"),
    "order":          ("Order",),
    "location":       ("Location",),
    "inventory_item": ("InventoryItem",),
}

# Attrs already stored as src_gid → dst_gid (not numeric ids).
_GID_KEYED_ATTRS = ("metaobject", "file")


def build_gid_remap(state) -> Dict[str, str]:
    """Return a dict mapping source GIDs to destination GIDs across every
    resource type a metafield / metaobject field / menu item / theme asset
    can reference.

    Covers int-id attrs of SyncState (wrapped into gid://shopify/<Type>/<id>)
    and already-GID-keyed attrs (metaobject, file). When a resource has
    multiple GID aliases (Page vs OnlineStorePage), all are mapped so a value
    written under either alias resolves.
    """
    remap: Dict[str, str] = {}
    for attr, types in _INT_ID_TYPES.items():
        mapping = getattr(state, attr, None) or {}
        for old, new in mapping.items():
            for t in types:
                remap[f"gid://shopify/{t}/{old}"] = f"gid://shopify/{t}/{new}"
    for attr in _GID_KEYED_ATTRS:
        mapping = getattr(state, attr, None) or {}
        remap.update(mapping)
    return remap
