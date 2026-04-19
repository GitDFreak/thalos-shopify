"""Webhook subscriptions sync — dev/prod parity for HTTP webhooks.

GraphQL webhookSubscriptionCreate. Matched by (topic, uri) pair on dst.
Only HTTP endpoints (WebhookHttpEndpoint) are synced; EventBridge and
PubSub subscriptions require AWS/GCP accounts per-store and are skipped.

v1: create missing, skip existing.
"""
from rich.console import Console

from ..api import ShopifyAPI, ERRORS
from ..engine import SyncResult
from ..state import SyncState, SyncHistory

console = Console()

_WEBHOOKS_QUERY = '''
query($after:String){webhookSubscriptions(first:100, after:$after){
  edges{cursor node{
    id topic format includeFields metafieldNamespaces
    endpoint{
      __typename
      ... on WebhookHttpEndpoint { callbackUrl }
    }
  }}
  pageInfo{hasNextPage endCursor}
}}
'''


def _fetch_all(api: ShopifyAPI) -> list[dict]:
    items, after = [], None
    while True:
        data = api.gql(_WEBHOOKS_QUERY, {"after": after}).get("data") or {}
        conn = data.get("webhookSubscriptions") or {}
        items.extend(e["node"] for e in conn.get("edges") or [])
        pi = conn.get("pageInfo") or {}
        if not pi.get("hasNextPage"):
            break
        after = pi.get("endCursor")
    return items


def _key(wh: dict) -> tuple[str, str] | None:
    """Identity = (topic, http callback URL). Skips non-HTTP endpoints."""
    ep = wh.get("endpoint") or {}
    if ep.get("__typename") != "WebhookHttpEndpoint":
        return None
    return (wh["topic"], ep.get("callbackUrl") or "")


def sync_webhooks(src_api: ShopifyAPI, dst_api: ShopifyAPI, state: SyncState,
                  history: SyncHistory, config) -> SyncResult:
    result = SyncResult()

    try:
        src_webhooks = _fetch_all(src_api)
        dst_webhooks = _fetch_all(dst_api)
    except Exception as e:
        ERRORS.add(f"webhooks: fetch failed ({e})")
        return result

    dst_keys = {k for k in (_key(w) for w in dst_webhooks) if k is not None}
    console.print(f"  {len(src_webhooks)} source │ {len(dst_webhooks)} dest")

    for wh in src_webhooks:
        k = _key(wh)
        if k is None:
            result.skipped += 1  # EventBridge / PubSub — need platform-specific setup
            continue
        if k in dst_keys:
            result.skipped += 1
            continue
        payload = {"uri": k[1], "format": wh.get("format", "JSON")}
        if wh.get("includeFields"):
            payload["includeFields"] = wh["includeFields"]
        if wh.get("metafieldNamespaces"):
            payload["metafieldNamespaces"] = wh["metafieldNamespaces"]
        r = dst_api.gql(
            'mutation($topic:WebhookSubscriptionTopic!,$sub:WebhookSubscriptionInput!)'
            '{webhookSubscriptionCreate(topic:$topic,webhookSubscription:$sub){'
            'webhookSubscription{id}userErrors{field message}}}',
            {"topic": wh["topic"], "sub": payload},
        )
        ue = ((r.get("data") or {}).get("webhookSubscriptionCreate") or {}).get("userErrors") or []
        if ue:
            result.errors += 1
        else:
            result.created += 1
            history.record("create", "webhook", wh["id"])

    return result
