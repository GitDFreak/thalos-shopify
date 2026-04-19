"""Shopify API layer — REST + GraphQL with retry, rate limiting, dry-run."""
import requests
import json
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from rich.console import Console

console = Console()

RETRY_CODES = {429, 500, 502, 503, 504}


class _OpCounter:
    """Thread-safe op counter shared across API instances (per-process)."""
    def __init__(self):
        self._n = 0
        self._lock = threading.Lock()

    def inc(self, delta=1):
        with self._lock:
            self._n += delta
            return self._n

    def get(self):
        with self._lock:
            return self._n

    def reset(self):
        with self._lock:
            self._n = 0


OPS = _OpCounter()


_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
# International + local phone shapes — conservative; avoid matching order numbers.
_PHONE_RE = re.compile(r"(?<!\d)(?:\+\d{1,3}[\s.-]?)?(?:\(?\d{2,4}\)?[\s.-]?){2,5}\d{2,4}(?!\d)")


def _scrub_pii(msg: str) -> str:
    """Mask emails and phone numbers before the string lands in job logs or
    stdout JSON (both reach Scaleway Cockpit). Intentionally conservative:
    order numbers and dates stay visible."""
    msg = _EMAIL_RE.sub("***@***", msg)
    # Only apply phone regex when the message looks like it might contain one.
    if any(c.isdigit() for c in msg):
        msg = _PHONE_RE.sub(
            lambda m: "***" if sum(c.isdigit() for c in m.group(0)) >= 7 else m.group(0),
            msg,
        )
    return msg


class _ErrorLog:
    """Thread-safe buffer of user-visible error messages scoped to the current section."""
    MAX = 50

    def __init__(self):
        self._entries: List[str] = []
        self._lock = threading.Lock()

    def add(self, msg: str):
        if not msg:
            return
        msg = _scrub_pii(str(msg))[:240]
        with self._lock:
            if len(self._entries) < self.MAX:
                self._entries.append(msg)

    def flush(self) -> List[str]:
        with self._lock:
            out = list(self._entries)
            self._entries.clear()
            return out


ERRORS = _ErrorLog()


def _scan_user_errors(node, out: List[str]):
    """Recursively find all `userErrors` arrays in a GraphQL response.data subtree."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "userErrors" and isinstance(v, list):
                for ue in v:
                    if isinstance(ue, dict):
                        msg = ue.get("message") or ""
                        fld = ue.get("field") or ""
                        if isinstance(fld, list):
                            fld = ".".join(str(x) for x in fld)
                        if msg:
                            out.append(f"{fld}: {msg}" if fld else msg)
            else:
                _scan_user_errors(v, out)
    elif isinstance(node, list):
        for item in node:
            _scan_user_errors(item, out)


class ShopifyAPI:
    """Thread-safe Shopify API client with retry + rate limiting + connection pooling + dst cache."""

    def __init__(self, store: str, token: str, api_version: str, dry_run: bool = False):
        self.store = store
        self.token = token
        self.api_version = api_version
        self.dry_run = dry_run
        self.base_url = f"https://{store}.myshopify.com/admin/api/{api_version}"
        self.gql_url = f"{self.base_url}/graphql.json"
        self.headers = {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}

        # Connection pool: one Session reused for every call.
        # HTTPAdapter with larger pool handles parallel workers on same host.
        self.session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(pool_connections=20, pool_maxsize=20, max_retries=0)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.session.headers.update(self.headers)

        # Per-store cache for dst lookups. Populated lazily, invalidated on writes.
        self._cache: Dict[str, Any] = {}
        self._cache_lock = threading.Lock()

    # ── Cache helpers ─────────────────────────────────────

    def cached(self, key: str, loader):
        """Return cached value or load once. Thread-safe."""
        with self._cache_lock:
            if key in self._cache:
                return self._cache[key]
            val = loader()
            self._cache[key] = val
            return val

    def invalidate(self, key: str):
        with self._cache_lock:
            self._cache.pop(key, None)

    # ── REST ──────────────────────────────────────────────

    def _request(self, method: str, url: str, json_payload=None, params=None, timeout=60):
        OPS.inc()
        for attempt in range(5):
            try:
                r = self.session.request(
                    method, url,
                    json=json_payload,
                    params=params,
                    timeout=timeout,
                )
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError,
                    requests.exceptions.ChunkedEncodingError):
                if attempt < 4:
                    time.sleep(min(2 ** attempt, 16))
                    continue
                raise
            if r.status_code == 429:
                time.sleep(float(r.headers.get("Retry-After", 2)))
                continue
            if r.status_code in RETRY_CODES and attempt < 4:
                time.sleep(min(2 ** attempt, 16))
                continue
            # Proactive REST throttle
            api_limit = r.headers.get("X-Shopify-Shop-Api-Call-Limit", "")
            if "/" in api_limit:
                current, limit = map(int, api_limit.split("/"))
                if current > limit * 0.9:
                    time.sleep(1.0)
                elif current > limit * 0.7:
                    time.sleep(0.2)
            return r
        return r

    def get(self, endpoint: str, params=None):
        r = self._request("GET", f"{self.base_url}/{endpoint}", params=params)
        r.raise_for_status()
        return r

    def post(self, endpoint: str, payload: dict, silent=False):
        if self.dry_run:
            OPS.inc()
            return _FakeResponse(201, payload)
        r = self._request("POST", f"{self.base_url}/{endpoint}", json_payload=payload)
        if r.status_code >= 400:
            body = ""
            try:
                body = r.json().get("errors") or r.text[:200]
            except Exception:
                body = r.text[:200]
            ERRORS.add(f"POST {endpoint} {r.status_code}: {body}")
            if not silent:
                console.print(f"  [red]POST {endpoint}: {r.status_code}[/red]")
        return r

    def put(self, endpoint: str, payload: dict):
        if self.dry_run:
            OPS.inc()
            return _FakeResponse(200, payload)
        r = self._request("PUT", f"{self.base_url}/{endpoint}", json_payload=payload)
        if r.status_code >= 400:
            try:
                body = r.json().get("errors") or r.text[:200]
            except Exception:
                body = r.text[:200]
            ERRORS.add(f"PUT {endpoint} {r.status_code}: {body}")
        return r

    def delete(self, endpoint: str):
        if self.dry_run:
            OPS.inc()
            return _FakeResponse(200, {})
        return self._request("DELETE", f"{self.base_url}/{endpoint}")

    def paginate(self, endpoint: str, key: str, params=None) -> list:
        items = []
        params = {**(params or {}), "limit": 250}
        r = self.get(endpoint, params)
        items.extend(r.json().get(key, []))
        while True:
            link = r.headers.get("Link", "")
            if 'rel="next"' not in link:
                break
            url = [p.split("<")[1].split(">")[0] for p in link.split(",") if 'rel="next"' in p][0]
            r = self._request("GET", url)
            r.raise_for_status()
            batch = r.json().get(key, [])
            if not batch:
                break
            items.extend(batch)
        return items

    # ── GraphQL ───────────────────────────────────────────

    def gql(self, query: str, variables: dict = None) -> dict:
        payload = {"query": query}
        if variables:
            payload["variables"] = variables
        if self.dry_run and ("mutation" in query.lower() and "query" not in query.lower()[:20]):
            OPS.inc()
            return {"data": {}}
        r = self._request("POST", self.gql_url, json_payload=payload)
        try:
            data = r.json()
        except Exception:
            return {"errors": [{"message": f"HTTP {r.status_code}"}]}
        if "errors" in data:
            for e in data["errors"][:2]:
                msg = e.get("message", "")[:200]
                console.print(f"  [red]GQL: {msg[:120]}[/red]")
                ERRORS.add(f"GQL: {msg}")
        # Scan for Shopify userErrors in any mutation result
        if data.get("data"):
            _ue: List[str] = []
            _scan_user_errors(data["data"], _ue)
            for m in _ue:
                ERRORS.add(m)
        # Throttle
        ext = data.get("extensions", {}).get("cost", {}).get("throttleStatus", {})
        avail = ext.get("currentlyAvailable", 1000)
        restore = ext.get("restoreRate", 50)
        if avail < 200:
            time.sleep((200 - avail) / restore)
        return data

    def gql_paginate(self, query_template: str, resource_path: str, batch_size=50) -> list:
        """Paginate GraphQL with cursor."""
        items = []
        cursor = None
        while True:
            after = f', after: "{cursor}"' if cursor else ""
            query = query_template.replace("{AFTER}", after).replace("{FIRST}", str(batch_size))
            d = self.gql(query)
            data = d.get("data") or {}
            # Navigate nested path (e.g. "products")
            node = data
            for p in resource_path.split("."):
                node = (node or {}).get(p, {})
            edges = node.get("edges", [])
            for e in edges:
                items.append(e["node"])
                cursor = e.get("cursor")
            has_next = node.get("pageInfo", {}).get("hasNextPage", False)
            if not edges or not has_next:
                break
        return items


class _FakeResponse:
    def __init__(self, status_code, json_data):
        self.status_code = status_code
        self._json = json_data
        self.headers = {}

    def json(self):
        return self._json

    def raise_for_status(self):
        pass
