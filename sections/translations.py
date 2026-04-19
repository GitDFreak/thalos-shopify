"""Translations sync — parallel per resource type + locale."""
from rich.console import Console
from ..api import ShopifyAPI
from ..engine import SyncResult, run_parallel
from ..state import SyncState

console = Console()


def sync_translations(src_api: ShopifyAPI, dst_api: ShopifyAPI, state: SyncState, config) -> SyncResult:
    result = SyncResult()

    d = src_api.gql('{shopLocales{locale primary published}}')
    locales = (d.get("data") or {}).get("shopLocales", [])
    non_primary = [l for l in locales if not l["primary"] and l["published"]]

    if not non_primary:
        console.print("  Store monolingue — skip")
        return result

    console.print(f"  Locales: {[l['locale'] for l in non_primary]}")

    # Enable locales on dest
    for l in non_primary:
        dst_api.gql('mutation($l:String!){shopLocaleEnable(locale:$l){shopLocale{locale}userErrors{message}}}', {"l": l["locale"]})

    rq = 'query($r:ID!,$l:String!){translatableResource(resourceId:$r){translations(locale:$l){key value}translatableContent{key digest}}}'
    wm = 'mutation($r:ID!,$t:[TranslationInput!]!){translationsRegister(resourceId:$r,translations:$t){translations{key}userErrors{message}}}'

    types = [("Product", state.product), ("Collection", state.collection),
             ("Page", state.page), ("Article", state.article)]

    for gtype, idmap in types:
        if not idmap:
            continue

        def _make_tr(gid_type):
            def _tr_one(pair):
                oid, nid = pair
                copied = 0
                for loc in non_primary:
                    sd = src_api.gql(rq, {"r": f"gid://shopify/{gid_type}/{oid}", "l": loc["locale"]})
                    sr = (sd.get("data") or {}).get("translatableResource")
                    if not sr:
                        continue
                    trans = sr.get("translations", [])
                    if not trans:
                        continue
                    dd = dst_api.gql(rq, {"r": f"gid://shopify/{gid_type}/{nid}", "l": loc["locale"]})
                    dr = (dd.get("data") or {}).get("translatableResource")
                    if not dr:
                        continue
                    digests = {c["key"]: c["digest"] for c in dr.get("translatableContent", [])}
                    inp = [{"key": t["key"], "value": t["value"], "locale": loc["locale"],
                            "translatableContentDigest": digests[t["key"]]}
                           for t in trans if t["value"] and t["key"] in digests]
                    if inp:
                        dst_api.gql(wm, {"r": f"gid://shopify/{gid_type}/{nid}", "t": inp})
                        copied += len(inp)
                return copied
            return _tr_one

        results = run_parallel(_make_tr(gtype), list(idmap.items()), f"Trad {gtype}", workers=min(config.max_workers, 6))
        ct = sum(r for r in results if isinstance(r, int))
        result.created += ct
        console.print(f"  {gtype}: {ct}")

    return result
