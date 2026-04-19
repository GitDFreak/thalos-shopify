"""Sync engine — generic CREATE/UPDATE/DELETE pattern."""
import time
from typing import Callable, Dict, List, Optional, Tuple, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
from rich.console import Console
from rich.progress import Progress, BarColumn, TextColumn, TimeRemainingColumn, SpinnerColumn

console = Console()


def ensure_str(v):
    """Convert bool/int/float to string for GraphQL metafieldsSet."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if v is None:
        return ""
    return str(v)


def run_parallel(fn, items, desc="", workers=6):
    """Parallel execution with Rich progress bar."""
    results = [None] * len(items)
    if not items:
        return results
    with Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(bar_width=30),
        "[progress.percentage]{task.percentage:>3.0f}%",
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task = progress.add_task(desc, total=len(items))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = {pool.submit(fn, item): i for i, item in enumerate(items)}
            for f in as_completed(futs):
                idx = futs[f]
                try:
                    results[idx] = f.result()
                except Exception as e:
                    results[idx] = e
                progress.advance(task)
    return results


class SyncResult:
    """Counts for one section sync."""
    def __init__(self):
        self.created = 0
        self.updated = 0
        self.deleted = 0
        self.skipped = 0
        self.errors = 0

    def to_dict(self):
        return {
            "created": self.created,
            "updated": self.updated,
            "deleted": self.deleted,
            "skipped": self.skipped,
            "errors": self.errors,
        }

    def __repr__(self):
        parts = []
        if self.created: parts.append(f"{self.created} created")
        if self.updated: parts.append(f"{self.updated} updated")
        if self.deleted: parts.append(f"{self.deleted} deleted")
        if self.skipped: parts.append(f"{self.skipped} skipped")
        if self.errors: parts.append(f"{self.errors} errors")
        return " │ ".join(parts) if parts else "nothing to do"


def sync_resources(
    src_items: list,
    dst_items: list,
    key_field: str,
    create_fn: Callable,
    update_fn: Optional[Callable] = None,
    delete_fn: Optional[Callable] = None,
    compare_fn: Optional[Callable] = None,
    id_field: str = "id",
    delete_orphans: bool = False,
    workers: int = 6,
    desc: str = "",
) -> Tuple[SyncResult, Dict]:
    """
    Generic sync: CREATE + UPDATE + DELETE.

    Args:
        src_items: items from source store
        dst_items: items already on dest store
        key_field: unique field to match (e.g. "handle")
        create_fn: fn(src_item) → dest_id or None
        update_fn: fn(dst_id, src_item) → bool (success)
        delete_fn: fn(dst_id) → bool
        compare_fn: fn(src, dst) → bool (True if different = needs update)
        id_field: field name for dest item ID
        delete_orphans: if True, delete dest items not on source
        workers: parallel threads

    Returns:
        (SyncResult, id_map {src_id: dst_id})
    """
    result = SyncResult()
    id_map = {}

    src_by_key = {item.get(key_field): item for item in src_items if item.get(key_field)}
    dst_by_key = {item.get(key_field): item for item in dst_items if item.get(key_field)}

    to_create = []
    to_update = []

    for key, src_item in src_by_key.items():
        if key in dst_by_key:
            dst_item = dst_by_key[key]
            id_map[src_item[id_field]] = dst_item[id_field]
            # Check if update needed
            if update_fn and compare_fn and compare_fn(src_item, dst_item):
                to_update.append((dst_item[id_field], src_item))
            else:
                result.skipped += 1
        else:
            to_create.append(src_item)

    # CREATE
    if to_create:
        def _create(item):
            try:
                dst_id = create_fn(item)
                return ("ok", item[id_field], dst_id)
            except Exception as e:
                return ("err", item.get(id_field), str(e))

        results = run_parallel(_create, to_create, f"Create {desc}", workers)
        for r in results:
            if isinstance(r, Exception):
                result.errors += 1
                continue
            status, src_id, dst_id = r
            if status == "ok" and dst_id:
                result.created += 1
                id_map[src_id] = dst_id
            else:
                result.errors += 1

    # UPDATE
    if to_update and update_fn:
        def _update(pair):
            dst_id, src_item = pair
            try:
                ok = update_fn(dst_id, src_item)
                return "ok" if ok else "err"
            except Exception:
                return "err"

        results = run_parallel(_update, to_update, f"Update {desc}", workers)
        for r in results:
            if r == "ok":
                result.updated += 1
            else:
                result.errors += 1

    # DELETE orphans
    if delete_orphans and delete_fn:
        orphan_keys = set(dst_by_key.keys()) - set(src_by_key.keys())
        orphan_ids = [dst_by_key[k][id_field] for k in orphan_keys]
        if orphan_ids:
            console.print(f"  [yellow]{len(orphan_ids)} orphans to archive[/yellow]")

            def _delete(dst_id):
                try:
                    return "ok" if delete_fn(dst_id) else "err"
                except Exception:
                    return "err"

            results = run_parallel(_delete, orphan_ids, f"Delete {desc}", workers)
            for r in results:
                if r == "ok":
                    result.deleted += 1
                else:
                    result.errors += 1

    return result, id_map
