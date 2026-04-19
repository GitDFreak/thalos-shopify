"""Markets sync — CREATE + UPDATE (conditions, status) + DELETE (non-primary)."""
from rich.console import Console
from ..api import ShopifyAPI
from ..engine import SyncResult
from ..state import SyncState, SyncHistory

console = Console()

MKT_Q = '{markets(first:50){edges{node{id name handle status primary conditions{regionsCondition{regions(first:50){edges{node{...on MarketRegionCountry{id code}}}}}}}}}}'


def _regions_of(m):
    edges = (m.get("conditions", {}).get("regionsCondition") or {}).get("regions", {}).get("edges", [])
    return {r["node"].get("code"): r["node"].get("id") for r in edges if r["node"].get("code")}


def sync_markets(src_api: ShopifyAPI, dst_api: ShopifyAPI, state: SyncState,
                 history: SyncHistory, config) -> SyncResult:
    result = SyncResult()

    src_mkts = [e["node"] for e in (src_api.gql(MKT_Q).get("data") or {}).get("markets", {}).get("edges", [])]
    dst_mkts = {e["node"]["handle"]: e["node"] for e in (dst_api.gql(MKT_Q).get("data") or {}).get("markets", {}).get("edges", [])}

    console.print(f"  {len(src_mkts)} source │ {len(dst_mkts)} dest")

    src_handles = {m["handle"] for m in src_mkts}

    for m in src_mkts:
        src_regions = _regions_of(m)
        status = "ACTIVE" if m.get("status") == "ACTIVE" else "DRAFT"

        if m["handle"] in dst_mkts:
            dm = dst_mkts[m["handle"]]
            dst_regions = _regions_of(dm)
            to_add = [{"countryCode": c} for c in src_regions if c not in dst_regions]
            to_del = [{"countryCode": c} for c in dst_regions if c not in src_regions]

            inp = {"name": m["name"], "status": status}
            if to_add or to_del:
                inp["conditions"] = {}
                if to_add:
                    inp["conditions"]["conditionsToAdd"] = {"regionsCondition": {"regions": to_add}}
                if to_del:
                    inp["conditions"]["conditionsToDelete"] = {"regionsCondition": {"regions": to_del}}

            needs = (dm.get("name") != m["name"] or dm.get("status") != status or to_add or to_del)
            if not needs:
                result.skipped += 1
                continue

            r = dst_api.gql(
                'mutation($id:ID!,$input:MarketUpdateInput!){marketUpdate(id:$id,input:$input){market{id}userErrors{field message}}}',
                {"id": dm["id"], "input": inp},
            )
            ue = ((r.get("data") or {}).get("marketUpdate") or {}).get("userErrors", [])
            if not ue:
                result.updated += 1
                history.record("update", "market", m["handle"])
            else:
                result.errors += 1
        else:
            regions = [{"countryCode": c} for c in src_regions]
            r = dst_api.gql(
                'mutation($i:MarketCreateInput!){marketCreate(input:$i){market{id}userErrors{field message}}}',
                {"i": {"name": m["name"], "handle": m["handle"], "status": status,
                       "conditions": {"regionsCondition": {"regions": regions}} if regions else {}}},
            )
            ue = ((r.get("data") or {}).get("marketCreate") or {}).get("userErrors", [])
            if not ue or "taken" in str(ue).lower():
                result.created += 1
                history.record("create", "market", m["handle"])
            else:
                result.errors += 1

    if config.should_delete_orphans("markets"):
        for handle, dm in dst_mkts.items():
            if handle in src_handles or dm.get("primary"):
                continue
            r = dst_api.gql(
                'mutation($id:ID!){marketDelete(id:$id){deletedId userErrors{field message}}}',
                {"id": dm["id"]},
            )
            ue = ((r.get("data") or {}).get("marketDelete") or {}).get("userErrors", [])
            if not ue:
                result.deleted += 1
                history.record("delete", "market", handle)
            else:
                result.errors += 1

    return result
