"""Locations sync — match by name, auto-create missing, update address."""
from rich.console import Console
from ..api import ShopifyAPI
from ..engine import SyncResult
from ..state import SyncState, SyncHistory

console = Console()


def _build_address(sl):
    addr = {"countryCode": sl.get("country_code") or "FR"}
    for f in ["address1", "address2", "city", "zip", "phone"]:
        if sl.get(f):
            addr[f] = sl[f]
    if sl.get("province_code"):
        addr["provinceCode"] = sl["province_code"]
    return addr


def _needs_update(sl, dl):
    for f, df in [("address1", "address1"), ("address2", "address2"), ("city", "city"),
                  ("zip", "zip"), ("phone", "phone"), ("province_code", "province_code"),
                  ("country_code", "country_code")]:
        if (sl.get(f) or "") != (dl.get(df) or ""):
            return True
    return False


def sync_locations(src_api: ShopifyAPI, dst_api: ShopifyAPI, state: SyncState,
                   history: SyncHistory, config) -> SyncResult:
    result = SyncResult()
    src_locs = src_api.paginate("locations.json", "locations")
    dst_locs = dst_api.paginate("locations.json", "locations")
    dst_by_name = {l["name"]: l for l in dst_locs}

    for sl in src_locs:
        if sl["name"] in dst_by_name:
            dl = dst_by_name[sl["name"]]
            state.location[sl["id"]] = dl["id"]
            if _needs_update(sl, dl):
                gid = f"gid://shopify/Location/{dl['id']}"
                r = dst_api.gql(
                    'mutation($id:ID!,$input:LocationEditInput!){locationEdit(id:$id,input:$input){location{id}userErrors{field message}}}',
                    {"id": gid, "input": {"name": sl["name"], "address": _build_address(sl)}},
                )
                ue = ((r.get("data") or {}).get("locationEdit") or {}).get("userErrors", [])
                if not ue:
                    result.updated += 1
                    history.record("update", "location", sl["id"])
                else:
                    result.errors += 1
            else:
                result.skipped += 1
        else:
            r = dst_api.gql(
                'mutation($i:LocationAddInput!){locationAdd(input:$i){location{id name}userErrors{field message}}}',
                {"i": {"name": sl["name"], "address": _build_address(sl)}},
            )
            loc = ((r.get("data") or {}).get("locationAdd") or {}).get("location")
            if loc:
                state.location[sl["id"]] = int(loc["id"].split("/")[-1])
                result.created += 1
                history.record("create", "location", sl["id"])
            else:
                result.errors += 1

    state.save_map("location", config.snapshot_dir)
    return result
