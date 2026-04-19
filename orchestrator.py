"""Job lifecycle orchestration for programmatic use.

The orchestrator is a persistence-agnostic job runner. It accepts a
`JobCallbacks` adapter that handles logging/status updates, a cancel
Event for cooperative cancellation, and a simple-sync factory (for
pages/blogs fallbacks). Gives library users a sync pipeline they can
embed (e.g. in a scheduler, a worker, or custom tooling) without going
through the CLI entry point.
"""
from __future__ import annotations
import os
import threading
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional, Protocol


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)

from .api import ShopifyAPI, OPS, ERRORS
from .config import SyncConfig
from .engine import SyncResult
from .state import SyncState, SyncHistory


SECTION_ORDER = [
    ("files", 1), ("locations", 2), ("pages", 3), ("blogs", 4),
    ("products", 5), ("collections", 6), ("metaobjects", 7),
    ("publication", 8), ("menus", 9), ("stock", 10), ("discounts", 11),
    ("metafield_definitions", 12), ("metafields", 13), ("translations", 14),
    ("redirects", 15), ("markets", 16), ("delivery", 17), ("orders", 18),
    ("segments", 19), ("webhooks", 20), ("theme", 21),
]


# For each section, the state.* id_maps it needs as input to produce
# meaningful results. Sections missing here have no cross-section deps.
# These names refer to attributes of SyncState.
SECTION_DEPS: dict[str, frozenset[str]] = {
    "files":                  frozenset(),
    "locations":              frozenset(),
    "pages":                  frozenset(),
    "blogs":                  frozenset(),
    "products":               frozenset({"file"}),
    "collections":            frozenset({"product"}),
    "metaobjects":            frozenset({"file", "product", "collection", "page", "article", "blog"}),
    "publication":            frozenset({"product", "collection"}),
    "menus":                  frozenset({"product", "collection", "page", "blog", "article"}),
    "stock":                  frozenset({"location", "inventory_item", "variant"}),
    "discounts":              frozenset({"product", "collection"}),
    "metafield_definitions":  frozenset(),
    "metafields":             frozenset({"product", "variant", "collection", "page", "article",
                                          "blog", "metaobject", "file"}),
    "translations":           frozenset({"product", "collection", "page", "article"}),
    "redirects":              frozenset(),
    "markets":                frozenset({"location"}),
    "delivery":               frozenset({"location"}),
    "orders":                 frozenset({"product", "variant"}),
    "segments":               frozenset(),
    "webhooks":               frozenset(),
    "theme":                  frozenset({"file"}),
}


def preflight_check(selected: list[str], state: SyncState) -> list[str]:
    """Return a list of human-readable warnings about dependencies that are
    neither already present in `state` nor scheduled to run before the
    consumer section in `selected`. An empty list means the plan is coherent.

    `selected` is the ordered list of sections the user asked to run
    (as it will be executed, matching SECTION_ORDER order).
    """
    warnings: list[str] = []
    # Build set of attrs that will be populated by the time each section runs:
    # either already loaded in state OR produced by an earlier selected section.
    # What each section *produces* (writes to state.*):
    produces: dict[str, frozenset[str]] = {
        "files":        frozenset({"file"}),
        "locations":    frozenset({"location"}),
        "pages":        frozenset({"page"}),
        "blogs":        frozenset({"blog", "article"}),
        "products":     frozenset({"product", "variant", "inventory_item"}),
        "collections":  frozenset({"collection"}),
        "metaobjects":  frozenset({"metaobject"}),
        "orders":       frozenset({"order"}),
    }
    # Attrs already non-empty in state (resume scenario):
    already = {a for a in (
        "product", "variant", "inventory_item", "location", "collection",
        "blog", "article", "page", "metaobject", "file", "order",
    ) if getattr(state, a, None)}

    # Walk selected sections in the SECTION_ORDER sequence (important: deps
    # are only satisfied by earlier entries, not later ones).
    selected_order = [s for s, _ in SECTION_ORDER if s in selected]
    available = set(already)
    for section in selected_order:
        deps = SECTION_DEPS.get(section, frozenset())
        missing = deps - available
        if missing:
            warnings.append(
                f"section '{section}' → id_maps manquants: {sorted(missing)} "
                f"(section va tourner mais produira ~0 résultat; lance les sections "
                f"productrices avant, ou resume depuis un job précédent)"
            )
        available |= produces.get(section, frozenset())
    return warnings


class JobCallbacks(Protocol):
    def log(self, msg: str) -> None: ...
    def update_job(self, **fields) -> None: ...
    def set_stat(self, section: str, stat: dict) -> None: ...


@dataclass
class JobContext:
    job_id: str
    config: SyncConfig
    callbacks: JobCallbacks
    cancel_event: threading.Event = field(default_factory=threading.Event)
    resume_from: Optional[str] = None


def _build_section_fns(src_api, dst_api, state, history, cfg, simple_sync_fn):
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
    from .sections.delivery import sync_delivery
    from .sections.segments import sync_segments
    from .sections.webhooks import sync_webhooks

    return {
        "files": lambda: sync_files(src_api, dst_api, state, history, cfg),
        "locations": lambda: sync_locations(src_api, dst_api, state, history, cfg),
        "pages": lambda: simple_sync_fn(src_api, dst_api, state, history, cfg, "pages", "pages", "handle"),
        "blogs": lambda: sync_blogs_and_articles(src_api, dst_api, state, history, cfg),
        "products": lambda: sync_products(src_api, dst_api, state, history, cfg),
        "metaobjects": lambda: sync_metaobjects(src_api, dst_api, state, history, cfg),
        "collections": lambda: sync_collections(src_api, dst_api, state, history, cfg),
        "publication": lambda: sync_publication(src_api, dst_api, state, cfg),
        "menus": lambda: sync_menus(src_api, dst_api, state, history, cfg),
        "stock": lambda: sync_stock(src_api, dst_api, state, history, cfg),
        "discounts": lambda: sync_discounts(src_api, dst_api, state, history, cfg),
        "metafield_definitions": lambda: sync_metafield_definitions(src_api, dst_api, state, history, cfg),
        "metafields": lambda: sync_metafield_values(src_api, dst_api, state, history, cfg),
        "translations": lambda: sync_translations(src_api, dst_api, state, cfg),
        "redirects": lambda: sync_redirects(src_api, dst_api, state, history, cfg),
        "markets": lambda: sync_markets(src_api, dst_api, state, history, cfg),
        "delivery": lambda: sync_delivery(src_api, dst_api, state, history, cfg),
        "orders": lambda: sync_orders(src_api, dst_api, state, history, cfg),
        "segments": lambda: sync_segments(src_api, dst_api, state, history, cfg),
        "webhooks": lambda: sync_webhooks(src_api, dst_api, state, history, cfg),
        "theme": lambda: sync_theme(src_api, dst_api, state, cfg),
    }


class JobOrchestrator:
    """Executes a full sync with heartbeat + cancellation + section lifecycle."""

    def __init__(self, ctx: JobContext, simple_sync_fn: Callable):
        self.ctx = ctx
        self.simple_sync_fn = simple_sync_fn
        self._t0 = time.time()
        self._section_state = {"name": "Démarrage…", "num": 0, "ops_start": 0}
        self._stop_heartbeat = threading.Event()

    def _heartbeat(self) -> None:
        while not self._stop_heartbeat.is_set():
            elapsed = int(time.time() - self._t0)
            ops = OPS.get()
            ops_in_section = ops - self._section_state["ops_start"]
            cs = f'{self._section_state["num"]}. {self._section_state["name"]}'
            if ops_in_section > 0:
                cs = f'{cs} · {ops_in_section} ops'
            try:
                self.ctx.callbacks.update_job(elapsed=elapsed, current_section=cs)
            except Exception:  # nosec B110 — heartbeat update is best-effort; skipping one tick on transient DB hiccup is fine
                pass
            self._stop_heartbeat.wait(2.0)

    def run(self) -> None:
        cb = self.ctx.callbacks
        cfg = self.ctx.config
        cb.update_job(status="running")
        OPS.reset()

        hb = threading.Thread(target=self._heartbeat, daemon=True)
        hb.start()

        try:
            os.makedirs(cfg.snapshot_dir, exist_ok=True)
            src_api = ShopifyAPI(cfg.source_store, cfg.source_token, cfg.api_version)
            dst_api = ShopifyAPI(cfg.dest_store, cfg.dest_token, cfg.api_version, dry_run=cfg.dry_run)
            state = SyncState()
            if self.ctx.resume_from:
                loaded = state.load_all(cfg.snapshot_dir)
                cb.log(f"↺ State rechargé: {loaded} id_maps, {len(state.completed)} sections déjà OK")
            history = SyncHistory(cfg.snapshot_dir)

            dst_shop = dst_api.get("shop.json").json()["shop"]
            src_shop = src_api.get("shop.json").json()["shop"]
            cfg.source_plan = src_shop.get("plan_name", "")
            cfg.auto_detect_workers(dst_shop.get("plan_name", ""))
            cb.log(f"Connexion: {cfg.source_store} ({cfg.source_plan}) → {cfg.dest_store} ({cfg.dest_plan})")
            cb.log(f"Workers: {cfg.max_workers} (auto-détecté du plan {cfg.dest_plan})")
            if cfg.dry_run:
                cb.log("⚠ Mode DRY RUN — aucune modification")
            if cfg.since:
                cb.log(f"⚡ Mode INCRÉMENTAL — source filtrée depuis {cfg.since} "
                       f"(products, collections, pages, blogs) · delete_orphans FORCÉ OFF")

            # Preflight: warn if a selected section has deps neither already in
            # state (resume) nor scheduled to run earlier in this job.
            selected = [s for s, _ in SECTION_ORDER if cfg.should_run(s)]
            for w in preflight_check(selected, state):
                cb.log(f"⚠ preflight: {w}")

            section_fns = _build_section_fns(src_api, dst_api, state, history, cfg, self.simple_sync_fn)
            total = len(selected)
            done = 0

            for name, num in SECTION_ORDER:
                if self.ctx.cancel_event.is_set():
                    cb.log("🛑 Annulé par l'utilisateur")
                    cb.update_job(status="failed", elapsed=int(time.time() - self._t0),
                                  completed_at=_utcnow())
                    return
                if not cfg.should_run(name):
                    continue
                if state.is_done(name):
                    cb.log(f"⊙ {num}. {name} — déjà done (skippé)")
                    done += 1
                    cb.update_job(progress=int(100 * done / total))
                    continue

                self._section_state["name"] = name
                self._section_state["num"] = num
                self._section_state["ops_start"] = OPS.get()
                cb.update_job(current_section=f"{num}. {name}")
                cb.log(f"▶ {num}. {name}")
                ERRORS.flush()

                try:
                    result = section_fns[name]()
                    err_samples = ERRORS.flush()
                    if isinstance(result, SyncResult):
                        stat_dict = result.to_dict()
                        if err_samples:
                            stat_dict["error_samples"] = err_samples
                        cb.set_stat(name, stat_dict)
                        cb.log(f"  ✓ {result}")
                        if result.errors == 0:
                            state.mark_done(name, cfg.snapshot_dir)
                        else:
                            cb.log(f"  ⚠ {result.errors} erreurs — section rejouable au resume")
                            state.save_all(cfg.snapshot_dir)
                    else:
                        state.mark_done(name, cfg.snapshot_dir)
                except Exception as e:
                    err_samples = ERRORS.flush()
                    cb.log(f"  ✗ {name}: {str(e)[:150]}")
                    cb.set_stat(name, {"errors": 1,
                                       "error_samples": err_samples + [f"exception: {str(e)[:200]}"]})
                    state.save_all(cfg.snapshot_dir)

                done += 1
                cb.update_job(progress=int(100 * done / total), elapsed=int(time.time() - self._t0))

            state.save_all(cfg.snapshot_dir)
            cb.update_job(status="completed", progress=100,
                          completed_at=_utcnow(), elapsed=int(time.time() - self._t0))
            cb.log("✅ Sync terminé!")

        except Exception as e:
            cb.update_job(status="failed", elapsed=int(time.time() - self._t0),
                          completed_at=_utcnow())
            cb.log(f"❌ {str(e)[:200]}")
            cb.log(f"Trace: {traceback.format_exc()[:500]}")
        finally:
            self._stop_heartbeat.set()
