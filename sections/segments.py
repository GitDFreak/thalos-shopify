"""Customer segments sync — rule-based customer groups.

Uses GraphQL segmentCreate. Matched by name on dst. v1 = create only.
Segments reference no other resources directly (the query DSL is a string),
so no GID remap needed. If the src DSL references specific customer/product
tags that don't exist on dst, the segment will still be created but will
match 0 members until the referenced data arrives.
"""
from rich.console import Console

from ..api import ShopifyAPI, ERRORS
from ..engine import SyncResult
from ..state import SyncState, SyncHistory

console = Console()

_SEGMENTS_QUERY = '''
query($after:String){segments(first:50, after:$after){
  edges{cursor node{id name query}}
  pageInfo{hasNextPage endCursor}
}}
'''


def _fetch_all(api: ShopifyAPI) -> list[dict]:
    items, after = [], None
    while True:
        data = api.gql(_SEGMENTS_QUERY.replace("$after:String)", "$after:String)"), {"after": after}).get("data") or {}
        conn = data.get("segments") or {}
        items.extend(e["node"] for e in conn.get("edges") or [])
        pi = conn.get("pageInfo") or {}
        if not pi.get("hasNextPage"):
            break
        after = pi.get("endCursor")
    return items


def sync_segments(src_api: ShopifyAPI, dst_api: ShopifyAPI, state: SyncState,
                  history: SyncHistory, config) -> SyncResult:
    result = SyncResult()

    try:
        src_segments = _fetch_all(src_api)
    except Exception as e:
        ERRORS.add(f"segments: fetch src failed ({e}) — segments API requires read_customers scope")
        return result
    try:
        dst_segments = _fetch_all(dst_api)
    except Exception as e:
        ERRORS.add(f"segments: fetch dst failed ({e})")
        return result

    dst_by_name = {s["name"]: s for s in dst_segments}
    console.print(f"  {len(src_segments)} source │ {len(dst_segments)} dest")

    for seg in src_segments:
        if seg["name"] in dst_by_name:
            result.skipped += 1
            continue
        r = dst_api.gql(
            'mutation($name:String!,$query:String!){segmentCreate(name:$name,query:$query){segment{id name}userErrors{field message}}}',
            {"name": seg["name"], "query": seg["query"]},
        )
        ue = ((r.get("data") or {}).get("segmentCreate") or {}).get("userErrors") or []
        if ue:
            result.errors += 1
        else:
            result.created += 1
            history.record("create", "segment", seg["id"])

    return result
