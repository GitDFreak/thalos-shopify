"""Persistent state management — ID maps, checkpoints, history."""
import json
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Set, Optional


@dataclass
class SyncState:
    """Centralized state with persistence."""
    # ID maps: source_id → dest_id
    product: Dict[int, int] = field(default_factory=dict)
    variant: Dict[int, int] = field(default_factory=dict)
    inventory_item: Dict[int, int] = field(default_factory=dict)
    location: Dict[int, int] = field(default_factory=dict)
    collection: Dict[int, int] = field(default_factory=dict)
    blog: Dict[int, int] = field(default_factory=dict)
    article: Dict[int, int] = field(default_factory=dict)
    page: Dict[int, int] = field(default_factory=dict)
    metaobject: Dict[str, str] = field(default_factory=dict)
    file: Dict[str, str] = field(default_factory=dict)
    order: Dict[int, int] = field(default_factory=dict)

    # Timings
    timings: Dict[str, float] = field(default_factory=dict)
    # Stats per section: {"Products": {"created": 10, "updated": 5, "deleted": 0, "errors": 1}}
    stats: Dict[str, Dict[str, int]] = field(default_factory=dict)
    # Completed sections (for resume)
    completed: Set[str] = field(default_factory=set)

    def save_map(self, name: str, snapshot_dir: str):
        data = getattr(self, name, {})
        path = Path(snapshot_dir) / f"{name}_id_map.json"
        with open(path, "w") as f:
            json.dump(data, f, ensure_ascii=False, default=str)

    def save_all(self, snapshot_dir: str):
        for name in ["product", "variant", "inventory_item", "location",
                      "collection", "blog", "article", "page", "metaobject", "file", "order"]:
            data = getattr(self, name, {})
            if data:
                self.save_map(name, snapshot_dir)
        # Save state
        state_path = Path(snapshot_dir) / "sync_state.json"
        with open(state_path, "w") as f:
            json.dump({
                "completed": list(self.completed),
                "timings": self.timings,
                "stats": self.stats,
            }, f, indent=2)

    def load_all(self, snapshot_dir: str) -> int:
        loaded = 0
        for name in ["product", "variant", "inventory_item", "location",
                      "collection", "blog", "article", "page", "metaobject", "file", "order"]:
            path = Path(snapshot_dir) / f"{name}_id_map.json"
            if path.exists():
                with open(path) as f:
                    data = json.load(f)
                if name not in ("metaobject", "file"):
                    data = {int(k): (int(v) if str(v).isdigit() else v)
                            for k, v in data.items()}
                setattr(self, name, data)
                loaded += 1
        # Load state
        state_path = Path(snapshot_dir) / "sync_state.json"
        if state_path.exists():
            with open(state_path) as f:
                state = json.load(f)
            self.completed = set(state.get("completed", []))
            self.timings = state.get("timings", {})
            self.stats = state.get("stats", {})
        return loaded

    def is_done(self, section: str) -> bool:
        return section in self.completed

    def mark_done(self, section: str, snapshot_dir: str):
        self.completed.add(section)
        self.save_all(snapshot_dir)


class SyncHistory:
    """Append-only history for rollback."""

    def __init__(self, snapshot_dir: str):
        self.path = Path(snapshot_dir) / "sync_history.jsonl"

    def record(self, action: str, resource_type: str, resource_id, old_state=None, new_state=None):
        entry = {
            "ts": datetime.now().isoformat(),
            "action": action,
            "type": resource_type,
            "id": str(resource_id),
        }
        if old_state:
            entry["old"] = old_state
        if new_state:
            entry["new"] = new_state
        with open(self.path, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")

    def last_n(self, n=20) -> list:
        if not self.path.exists():
            return []
        with open(self.path) as f:
            lines = f.readlines()
        return [json.loads(l) for l in lines[-n:]]
