#!/usr/bin/env python3
"""
thalos-shopify — Shopify store-to-store sync CLI.

Usage:
    python -m thalos                        # Full sync
    python -m thalos --sections products    # Partial
    python -m thalos --dry-run              # Preview only
    python -m thalos --resume               # Resume from checkpoint
"""
import argparse
import os
import sys
import time
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

from .config import SyncConfig
from .api import ShopifyAPI
from .state import SyncState, SyncHistory
from .report import section_header, section_done, print_summary, export_html, console
from .engine import SyncResult

# ── Parse CLI args ────────────────────────────────────────
parser = argparse.ArgumentParser(
    prog="thalos",
    description="thalos-shopify — Shopify store-to-store sync.",
)
parser.add_argument("--sections", default="full", help="Sections: 'full' ou 'products,theme,...'")
parser.add_argument("--dry-run", action="store_true", help="Preview sans modifier")
parser.add_argument("--delete-orphans", action="store_true", help="Supprimer les orphelins sur dest")
parser.add_argument("--orders-since", help="Date ISO depuis laquelle copier les commandes (ex: 2025-01-01)")
parser.add_argument("--resume", action="store_true", help="Reprendre depuis le dernier checkpoint")

args, _ = parser.parse_known_args()

# ── Config ────────────────────────────────────────────────
cfg = SyncConfig.from_env()
if args.sections != "full":
    cfg.sections = args.sections.split(",")
if args.dry_run:
    cfg.dry_run = True
if args.delete_orphans:
    cfg.delete_orphans = True
if args.orders_since:
    cfg.orders_since = args.orders_since

cfg.validate()
os.makedirs(cfg.snapshot_dir, exist_ok=True)

# ── API clients ───────────────────────────────────────────
src = ShopifyAPI(cfg.source_store, cfg.source_token, cfg.api_version, dry_run=False)  # NEVER write to source
dst = ShopifyAPI(cfg.dest_store, cfg.dest_token, cfg.api_version, dry_run=cfg.dry_run)

# ── State ─────────────────────────────────────────────────
S = SyncState()
H = SyncHistory(cfg.snapshot_dir)

if args.resume:
    loaded = S.load_all(cfg.snapshot_dir)
    if loaded:
        console.print(f"[dim]📂 {loaded} maps chargés — resume activé[/dim]")

# ── Connection test ───────────────────────────────────────
with console.status("[bold green]Connexion..."):
    src_shop = src.get("shop.json").json()["shop"]
    dst_shop = dst.get("shop.json").json()["shop"]

console.print(Panel(
    f"[cyan]Source[/cyan]: {src_shop['name']} ({src_shop.get('plan_name', '?')})\n"
    f"[yellow]Dest[/yellow]:   {dst_shop['name']} ({dst_shop.get('plan_name', '?')})"
    + ("\n[bold yellow]MODE: DRY RUN[/bold yellow]" if cfg.dry_run else "")
    + (f"\n[bold red]DELETE ORPHANS: ON[/bold red]" if cfg.delete_orphans else ""),
    title="✅ Connexion", style="green", width=60,
))
console.print()


# ── Section runner ────────────────────────────────────────
def run_section(name: str, num: int, emoji: str, fn):
    """Run a section with timing, checkpoint, and error handling."""
    if not cfg.should_run(name):
        return
    if S.is_done(name) and args.resume:
        console.print(f"  [dim]⏭ {name} (déjà fait)[/dim]")
        return
    section_header(num, name, emoji)
    t0 = time.time()
    try:
        result = fn()
        S.timings[f"{num}. {name}"] = time.time() - t0
        if isinstance(result, SyncResult):
            S.stats[name] = result.to_dict()
            section_done(name, result.to_dict())
        elif isinstance(result, dict):
            S.stats[name] = result
            section_done(name, result)
        S.mark_done(name, cfg.snapshot_dir)
    except Exception as e:
        console.print(f"  [red bold]ERREUR: {e}[/red bold]")
        S.timings[f"{num}. {name}"] = time.time() - t0
        S.stats[name] = {"errors": 1}
        S.save_all(cfg.snapshot_dir)
        raise


# ══════════════════════════════════════════════════════════
#  SECTIONS
# ══════════════════════════════════════════════════════════

# Import section modules (lazy — only what's needed)
def _simple_rest_sync(name, endpoint, key, key_field="handle", fields=None):
    """Generic REST sync for simple resources (pages, blogs, collections, redirects)."""
    from .engine import sync_resources
    from .remap import remap_body_html
    src_params = cfg.since_params() if key in ("pages", "blogs", "custom_collections") else None
    src_items = src.paginate(f"{endpoint}.json", key, params=src_params)
    dst_items = dst.paginate(f"{endpoint}.json", key)
    console.print(f"  {len(src_items)} source │ {len(dst_items)} dest")

    fields = fields or ["title", "body_html", "handle", "template_suffix", "published_at"]

    def _val(item, f, default=None):
        v = item.get(f, default)
        if f == "body_html":
            return remap_body_html(v, cfg.source_store, cfg.dest_store)
        return v

    def _compare(s, d):
        for f in fields:
            if str(_val(s, f, "")) != str(d.get(f, "")):
                return True
        return False

    def _create(item):
        payload = {key[:-1] if key.endswith("s") else key: {f: _val(item, f) for f in fields + [key_field, "id"] if _val(item, f) is not None}}
        # Remove 'id' from payload (Shopify assigns new one)
        payload_inner = payload[list(payload.keys())[0]]
        payload_inner.pop("id", None)
        r = dst.post(f"{endpoint}.json", payload, silent=True)
        if r.status_code == 201:
            created = r.json()[list(payload.keys())[0]]
            H.record("create", key, item["id"], new_state={"id": created["id"]})
            return created["id"]
        return None

    def _update(dst_id, src_item):
        resource_key = key[:-1] if key.endswith("s") else key
        payload = {resource_key: {"id": dst_id}}
        for f in fields:
            v = _val(src_item, f)
            if v is not None:
                payload[resource_key][f] = v
        r = dst.put(f"{endpoint}/{dst_id}.json", payload)
        if r.status_code == 200:
            H.record("update", key, dst_id)
            return True
        return False

    def _delete(dst_id):
        r = dst.delete(f"{endpoint}/{dst_id}.json")
        if r.status_code == 200:
            H.record("delete", key, dst_id)
            return True
        return False

    result, id_map = sync_resources(
        src_items=src_items, dst_items=dst_items,
        key_field=key_field,
        create_fn=_create, update_fn=_update, delete_fn=_delete,
        compare_fn=_compare,
        delete_orphans=cfg.should_delete_orphans(name),
        workers=cfg.max_workers,
        desc=name,
    )
    # Update state (map name → SyncState attribute)
    NAME_TO_STATE = {"page": S.page, "blog": S.blog, "collection": S.collection, "redirect": None}
    state_attr = NAME_TO_STATE.get(name)
    if state_attr is not None:
        state_attr.update(id_map)
        S.save_map(name, cfg.snapshot_dir)
    return result


# ── Import sections ───────────────────────────────────────
from .sections.files import sync_files
from .sections.locations import sync_locations
from .sections.blogs import sync_blogs_and_articles
from .sections.products import sync_products
from .sections.metaobjects import sync_metaobjects
from .sections.publication import sync_publication
from .sections.menus import sync_menus
from .sections.stock import sync_stock
from .sections.discounts import sync_discounts
from .sections.metafields import sync_metafield_definitions, sync_metafield_values
from .sections.translations import sync_translations
from .sections.markets import sync_markets
from .sections.orders import sync_orders
from .sections.theme import sync_theme
from .sections.collections import sync_collections
from .sections.redirects import sync_redirects

# ── Run all sections in dependency order ──────────────────
# Couche 0: Independant
run_section("files", 1, "📁", lambda: sync_files(src, dst, S, H, cfg))
run_section("locations", 2, "📍", lambda: sync_locations(src, dst, S, H, cfg))
run_section("pages", 3, "📄", lambda: _simple_rest_sync("page", "pages", "pages", "handle",
    ["title", "body_html", "handle", "template_suffix", "published_at", "author"]))
run_section("blogs", 4, "📝", lambda: sync_blogs_and_articles(src, dst, S, H, cfg))

# Couche 1: Ref couche 0
run_section("products", 5, "🛍️", lambda: sync_products(src, dst, S, H, cfg))

# Couche 2: Ref couche 0+1 — collections avant metaobjects car entries peuvent ref collections/products/pages/articles
run_section("collections", 6, "📦", lambda: sync_collections(src, dst, S, H, cfg))
run_section("metaobjects", 7, "🧩", lambda: sync_metaobjects(src, dst, S, H, cfg))
run_section("publication", 8, "🌐", lambda: sync_publication(src, dst, S, cfg))
run_section("menus", 9, "🧭", lambda: sync_menus(src, dst, S, H, cfg))
run_section("stock", 10, "📊", lambda: sync_stock(src, dst, S, H, cfg))
run_section("discounts", 11, "🏷️", lambda: sync_discounts(src, dst, S, H, cfg))

# Couche 3: Ref tout
run_section("metafield_definitions", 12, "🔧", lambda: sync_metafield_definitions(src, dst, S, H, cfg))
run_section("metafields", 13, "📋", lambda: sync_metafield_values(src, dst, S, H, cfg))
run_section("translations", 14, "🌍", lambda: sync_translations(src, dst, S, cfg))
run_section("redirects", 15, "🔗", lambda: sync_redirects(src, dst, S, H, cfg))
run_section("markets", 16, "🗺️", lambda: sync_markets(src, dst, S, H, cfg))
run_section("orders", 17, "💰", lambda: sync_orders(src, dst, S, H, cfg))

# Couche 4: Theme (dernier)
run_section("theme", 18, "🎨", lambda: sync_theme(src, dst, S, cfg))

# ── Summary ───────────────────────────────────────────────
S.save_all(cfg.snapshot_dir)
print_summary(S.stats, S.timings, cfg)
export_html(str(Path(cfg.snapshot_dir) / "sync_report.html"))
