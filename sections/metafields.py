"""Metafield definitions + values sync — with GID remap + coercion."""
import json
import logging
import time
from rich.console import Console
from ..api import ShopifyAPI
from ..engine import SyncResult, run_parallel, ensure_str
from ..state import SyncState, SyncHistory

console = Console()
logger = logging.getLogger(__name__)


def sync_metafield_definitions(src_api: ShopifyAPI, dst_api: ShopifyAPI, state: SyncState,
                                history: SyncHistory, config) -> SyncResult:
    result = SyncResult()
    OWNER_TYPES = ["PRODUCT", "PRODUCTVARIANT", "COLLECTION", "PAGE", "ARTICLE", "BLOG", "SHOP"]
    rq = 'query($o:MetafieldOwnerType!,$c:String){metafieldDefinitions(ownerType:$o,first:250,after:$c){edges{cursor node{namespace key name description type{name}ownerType pinnedPosition validations{name value}}}pageInfo{hasNextPage}}}'
    cmut = 'mutation($d:MetafieldDefinitionInput!){metafieldDefinitionCreate(definition:$d){createdDefinition{id}userErrors{field message code}}}'

    # Metaobject def GID mapping (for validations) — cached per-api-instance
    src_g2t = src_api.cached("metaobject_defs_g2t", lambda: {
        e["node"]["id"]: e["node"]["type"]
        for e in (src_api.gql('{metaobjectDefinitions(first:250){edges{node{id type}}}}').get("data") or {}).get("metaobjectDefinitions", {}).get("edges", [])
    })
    dst_t2g = dst_api.cached("metaobject_defs_t2g", lambda: {
        e["node"]["type"]: e["node"]["id"]
        for e in (dst_api.gql('{metaobjectDefinitions(first:250){edges{node{id type}}}}').get("data") or {}).get("metaobjectDefinitions", {}).get("edges", [])
    })

    for ot in OWNER_TYPES:
        src_defs = []
        cursor = None
        while True:
            d = src_api.gql(rq, {"o": ot, "c": cursor})
            edges = (d.get("data") or {}).get("metafieldDefinitions", {}).get("edges", [])
            for e in edges:
                n = e["node"]
                if not n["namespace"].startswith("shopify") and not n["namespace"].startswith("app--"):
                    src_defs.append(n)
                cursor = e["cursor"]
            if not (d.get("data") or {}).get("metafieldDefinitions", {}).get("pageInfo", {}).get("hasNextPage"):
                break
        if not src_defs:
            continue
        # Dest existing
        dst_keys = set()
        cursor = None
        while True:
            d = dst_api.gql(rq, {"o": ot, "c": cursor})
            edges = (d.get("data") or {}).get("metafieldDefinitions", {}).get("edges", [])
            for e in edges:
                dst_keys.add((e["node"]["namespace"], e["node"]["key"]))
                cursor = e["cursor"]
            if not (d.get("data") or {}).get("metafieldDefinitions", {}).get("pageInfo", {}).get("hasNextPage"):
                break
        for defn in src_defs:
            if (defn["namespace"], defn["key"]) in dst_keys:
                result.skipped += 1
                continue
            inp = {"name": defn["name"], "namespace": defn["namespace"], "key": defn["key"],
                   "type": defn["type"]["name"], "ownerType": ot}
            if defn.get("description"):
                inp["description"] = defn["description"]
            if defn.get("pinnedPosition") is not None:
                inp["pin"] = True
            if defn.get("validations"):
                rv = []
                for v in defn["validations"]:
                    val = v.get("value", "")
                    if val.startswith("["):
                        try:
                            gids = json.loads(val)
                            new = [dst_t2g[src_g2t[g]] for g in gids if g in src_g2t and src_g2t[g] in dst_t2g]
                            if new:
                                rv.append({"name": v["name"], "value": json.dumps(new)})
                        except Exception:
                            logger.debug("metafield def validation remap failed: %r", val, exc_info=True)
                    elif "gid://shopify/MetaobjectDefinition/" in val:
                        rt = src_g2t.get(val)
                        if rt and rt in dst_t2g:
                            rv.append({"name": v["name"], "value": dst_t2g[rt]})
                    elif "gid://" not in val and "metaobject" not in v.get("name", "").lower():
                        rv.append({"name": v["name"], "value": val})
                if rv:
                    inp["validations"] = rv
            r = dst_api.gql(cmut, {"d": inp})
            rd = (r.get("data") or {}).get("metafieldDefinitionCreate") or {}
            if rd.get("createdDefinition"):
                result.created += 1
            elif r.get("errors") and "denied" in str(r["errors"]).lower():
                result.skipped += 1
            elif rd.get("userErrors") and "taken" in str(rd["userErrors"]).lower():
                result.skipped += 1
            else:
                result.errors += 1
            time.sleep(0.1)

    return result


def sync_metafield_values(src_api: ShopifyAPI, dst_api: ShopifyAPI, state: SyncState,
                          history: SyncHistory, config) -> SyncResult:
    result = SyncResult()

    from ..remap import build_gid_remap
    gid_remap = build_gid_remap(state)
    console.print(f"  gid_remap: {len(gid_remap)} entries")

    def _remap_val(val, mf_type):
        val = ensure_str(val)
        if not mf_type or "_reference" not in mf_type:
            return val, True
        if mf_type.startswith("list."):
            try:
                gids = json.loads(val)
                new = [gid_remap[g] for g in gids if g in gid_remap]
                return (json.dumps(new), True) if new else (val, False)
            except Exception:
                logger.debug("metafield list-ref remap failed: type=%s val=%r", mf_type, val, exc_info=True)
                return val, False
        else:
            return (gid_remap[val], True) if val in gid_remap else (val, False)

    smut = 'mutation($m:[MetafieldsSetInput!]!){metafieldsSet(metafields:$m){metafields{key}userErrors{field message}}}'
    GQL_TYPES = {"Product": "products", "ProductVariant": "productVariants", "Collection": "collections"}
    configs = [("Product", state.product), ("ProductVariant", state.variant),
               ("Collection", state.collection), ("Page", state.page), ("Article", state.article)]
    rest_paths = {"Product": "products", "ProductVariant": "variants", "Collection": "collections",
                  "Page": "pages", "Article": "articles"}

    for gid_type, id_map in configs:
        if not id_map:
            continue
        console.print(f"\n  [cyan]{gid_type}[/cyan] ({len(id_map)} owners)")
        all_mf = []
        gql_res = GQL_TYPES.get(gid_type)

        if gql_res and len(id_map) > 50:
            cursor = None
            with console.status(f"[cyan]Fetch {gid_type} metafields..."):
                while True:
                    after = f', after: "{cursor}"' if cursor else ""
                    d = src_api.gql('{%s(first:25%s){edges{cursor node{id metafields(first:100){edges{node{namespace key value type}}}}}pageInfo{hasNextPage}}}' % (gql_res, after))
                    edges = (d.get("data") or {}).get(gql_res, {}).get("edges", [])
                    for e in edges:
                        src_num = int(e["node"]["id"].split("/")[-1])
                        nid = id_map.get(src_num)
                        if nid is None:
                            continue
                        mfs = [n["node"] for n in e["node"].get("metafields", {}).get("edges", [])]
                        if mfs:
                            all_mf.append((nid, mfs))
                        cursor = e["cursor"]
                    if not edges or not (d.get("data") or {}).get(gql_res, {}).get("pageInfo", {}).get("hasNextPage"):
                        break
        else:
            rp = rest_paths.get(gid_type)
            if rp:
                def _make_fetch(rest_path):
                    def _fn(pair):
                        oid, nid = pair
                        try:
                            mfs = src_api.paginate(f"{rest_path}/{oid}/metafields.json", "metafields")
                        except Exception:
                            logger.debug("metafields fetch failed for %s/%s", rest_path, oid, exc_info=True)
                            mfs = []
                        return (nid, mfs) if mfs else None
                    return _fn
                raw = run_parallel(_make_fetch(rp), list(id_map.items()), f"Fetch {gid_type}", config.max_workers)
                all_mf = [r for r in raw if r and not isinstance(r, Exception)]

        # Build batches
        batches = []
        batch = []
        for nid, mfs in all_mf:
            owner_gid = f"gid://shopify/{gid_type}/{nid}"
            for mf in mfs:
                val, ok = _remap_val(mf["value"], mf["type"])
                if not ok:
                    result.skipped += 1
                    continue
                batch.append({"ownerId": owner_gid, "namespace": mf["namespace"],
                              "key": mf["key"], "value": ensure_str(val), "type": mf["type"]})
                if len(batch) >= 25:
                    batches.append(batch)
                    batch = []
        if batch:
            batches.append(batch)

        def _send_mf(b):
            r = dst_api.gql(smut, {"m": b})
            ue = ((r.get("data") or {}).get("metafieldsSet") or {}).get("userErrors", [])
            if ue and len(b) > 1:
                ok = 0
                for single in b:
                    r2 = dst_api.gql(smut, {"m": [single]})
                    if not ((r2.get("data") or {}).get("metafieldsSet") or {}).get("userErrors", []):
                        ok += 1
                return ok
            return len(b) if not ue else 0

        results = run_parallel(_send_mf, batches, f"Write {gid_type}", workers=4)
        ok = sum(r for r in results if isinstance(r, int))
        result.created += ok
        console.print(f"  {gid_type}: {ok} ok")

    # Shop metafields
    try:
        shop_mfs = src_api.paginate("metafields.json", "metafields")
        shop_mfs = [m for m in shop_mfs if (m.get("owner_resource") or "shop") == "shop"]
        if shop_mfs:
            shop_gid = ((dst_api.gql('{shop{id}}').get("data") or {}).get("shop") or {}).get("id")
            if shop_gid:
                for i in range(0, len(shop_mfs), 25):
                    batch = [{"ownerId": shop_gid, "namespace": m["namespace"], "key": m["key"],
                              "value": ensure_str(m["value"]), "type": m["type"]}
                             for m in shop_mfs[i:i + 25]]
                    dst_api.gql(smut, {"m": batch})
                console.print(f"  Shop: {len(shop_mfs)}")
    except Exception:
        logger.warning("shop-level metafields sync failed", exc_info=True)

    return result
