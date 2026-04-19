"""Configuration & validation with auto-detection."""
import os
from dataclasses import dataclass, field
from typing import List, Optional, Union
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Shopify plan → max concurrent API workers
PLAN_WORKERS = {
    "trial": 2,
    "basic": 2,
    "shopify": 4,
    "advanced": 6,
    "plus": 10,
    "shopify_plus": 10,
    "enterprise": 15,
    "unlimited": 10,       # Shopify Plus (unlimited plan name)
    "partner_test": 6,
    "staff_business": 4,
    "developer_preview": 6,
}


@dataclass
class SyncConfig:
    source_store: str = ""
    source_token: str = ""
    dest_store: str = ""
    dest_token: str = ""
    api_version: str = "2026-01"
    max_workers: int = 0  # 0 = auto-detect from plan
    dry_run: bool = False
    delete_orphans: bool = False
    # If set, delete_orphans applies ONLY to these sections (overrides bool).
    # Empty list / None = the `delete_orphans` boolean applies globally.
    delete_orphans_sections: Optional[List[str]] = None
    orders_since: Optional[str] = None
    # Incremental sync: fetch only source items updated since this ISO date.
    # Applies to products, collections, pages, blogs/articles. Orders use
    # `orders_since` which is a distinct lever.
    since: Optional[str] = None
    snapshot_dir: str = "snapshot_store"
    sections: Union[str, List[str]] = "full"
    # Auto-detected
    source_plan: str = ""
    dest_plan: str = ""

    def should_delete_orphans(self, section: str) -> bool:
        """Per-section gate. If `delete_orphans_sections` is set, that list is
        authoritative (bool is ignored). Otherwise fall back to the global bool.

        Incremental runs force this to False: a source-side fetch filtered by
        `updated_at_min` cannot see deletions, so any dst item missing from
        the partial src view would be wrongly classified as orphan and purged.
        """
        if self.since:
            return False
        if self.delete_orphans_sections:
            return section in self.delete_orphans_sections
        return bool(self.delete_orphans)

    def since_params(self) -> Optional[dict]:
        """Params dict to merge into paginate() calls for sections that
        support incremental fetching (products, collections, pages, blogs)."""
        return {"updated_at_min": self.since} if self.since else None

    @classmethod
    def from_env(cls) -> "SyncConfig":
        return cls(
            source_store=os.getenv("SHOPIFY_SOURCE_STORE", ""),
            source_token=os.getenv("SHOPIFY_SOURCE_TOKEN", ""),
            dest_store=os.getenv("SHOPIFY_DEST_STORE", ""),
            dest_token=os.getenv("SHOPIFY_DEST_TOKEN", ""),
            api_version=os.getenv("SHOPIFY_API_VERSION", "2026-01"),
            max_workers=int(os.getenv("MAX_WORKERS", "0")),
            dry_run=os.getenv("DRY_RUN", "false").lower() == "true",
            delete_orphans=os.getenv("DELETE_ORPHANS", "false").lower() == "true",
            orders_since=os.getenv("ORDERS_SINCE") or None,
            snapshot_dir=os.getenv("SNAPSHOT_DIR", "snapshot_store"),
        )

    def validate(self):
        missing = []
        if not self.source_store:
            missing.append("source_store")
        if not self.source_token:
            missing.append("source_token")
        if not self.dest_store:
            missing.append("dest_store")
        if not self.dest_token:
            missing.append("dest_token")
        if missing:
            raise ValueError(f"Champs manquants: {', '.join(missing)}")

    def auto_detect_workers(self, dest_plan: str):
        """Set max_workers based on dest store plan if not explicitly set."""
        self.dest_plan = dest_plan
        if self.max_workers == 0:
            plan_key = dest_plan.lower().replace(" ", "_")
            self.max_workers = PLAN_WORKERS.get(plan_key, 4)

    def should_run(self, section: str) -> bool:
        return self.sections == "full" or section in self.sections

    @property
    def source_url(self) -> str:
        return f"https://{self.source_store}.myshopify.com/admin/api/{self.api_version}"

    @property
    def dest_url(self) -> str:
        return f"https://{self.dest_store}.myshopify.com/admin/api/{self.api_version}"
