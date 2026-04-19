"""Discounts sync — code/automatic basic + free shipping (CREATE + UPDATE)."""
import time
from rich.console import Console
from ..api import ShopifyAPI
from ..engine import SyncResult
from ..state import SyncState, SyncHistory

console = Console()

FETCH_Q = 'query($c:String){discountNodes(first:100,after:$c){edges{node{id discount{...on DiscountCodeBasic{__typename title startsAt endsAt status usageLimit appliesOncePerCustomer codes(first:5){edges{node{code}}}customerGets{value{...on DiscountPercentage{percentage}...on DiscountAmount{amount{amount currencyCode}}}items{...on AllDiscountItems{allItems}}}}...on DiscountAutomaticBasic{__typename title startsAt endsAt status customerGets{value{...on DiscountPercentage{percentage}...on DiscountAmount{amount{amount currencyCode}}}items{...on AllDiscountItems{allItems}}}}...on DiscountCodeFreeShipping{__typename title startsAt endsAt status codes(first:5){edges{node{code}}}}...on DiscountAutomaticFreeShipping{__typename title startsAt endsAt status}}}}pageInfo{hasNextPage endCursor}}}'

CREATE_MUTS = {
    "DiscountCodeBasic": 'mutation($d:DiscountCodeBasicInput!){discountCodeBasicCreate(basicCodeDiscount:$d){codeDiscountNode{id}userErrors{field message}}}',
    "DiscountAutomaticBasic": 'mutation($d:DiscountAutomaticBasicInput!){discountAutomaticBasicCreate(automaticBasicDiscount:$d){automaticDiscountNode{id}userErrors{field message}}}',
    "DiscountCodeFreeShipping": 'mutation($d:DiscountCodeFreeShippingInput!){discountCodeFreeShippingCreate(freeShippingCodeDiscount:$d){codeDiscountNode{id}userErrors{field message}}}',
    "DiscountAutomaticFreeShipping": 'mutation($d:DiscountAutomaticFreeShippingInput!){discountAutomaticFreeShippingCreate(freeShippingAutomaticDiscount:$d){automaticDiscountNode{id}userErrors{field message}}}',
}

UPDATE_MUTS = {
    "DiscountCodeBasic": 'mutation($id:ID!,$d:DiscountCodeBasicInput!){discountCodeBasicUpdate(id:$id,basicCodeDiscount:$d){codeDiscountNode{id}userErrors{field message}}}',
    "DiscountAutomaticBasic": 'mutation($id:ID!,$d:DiscountAutomaticBasicInput!){discountAutomaticBasicUpdate(id:$id,automaticBasicDiscount:$d){automaticDiscountNode{id}userErrors{field message}}}',
    "DiscountCodeFreeShipping": 'mutation($id:ID!,$d:DiscountCodeFreeShippingInput!){discountCodeFreeShippingUpdate(id:$id,freeShippingCodeDiscount:$d){codeDiscountNode{id}userErrors{field message}}}',
    "DiscountAutomaticFreeShipping": 'mutation($id:ID!,$d:DiscountAutomaticFreeShippingInput!){discountAutomaticFreeShippingUpdate(id:$id,freeShippingAutomaticDiscount:$d){automaticDiscountNode{id}userErrors{field message}}}',
}


def _fetch_discounts(api):
    all_pairs = []
    cursor = None
    while True:
        d = api.gql(FETCH_Q, {"c": cursor})
        edges = (d.get("data") or {}).get("discountNodes", {}).get("edges", [])
        for e in edges:
            node = e.get("node") or {}
            disc = node.get("discount")
            if disc:
                all_pairs.append((node["id"], disc))
        pi = (d.get("data") or {}).get("discountNodes", {}).get("pageInfo", {})
        if not pi.get("hasNextPage"):
            break
        cursor = pi.get("endCursor")
        time.sleep(0.2)
    return all_pairs


def _build_input(dd):
    inp = {"title": dd["title"], "startsAt": dd["startsAt"]}
    if dd.get("endsAt"):
        inp["endsAt"] = dd["endsAt"]
    dt = dd["__typename"]
    if "codes" in dd:
        codes = [e["node"]["code"] for e in dd.get("codes", {}).get("edges", [])]
        if codes:
            inp["code"] = codes[0]
    if "customerGets" in dd:
        val = dd["customerGets"].get("value", {})
        if "percentage" in val:
            inp["customerGets"] = {"value": {"percentage": val["percentage"]}, "items": {"all": True}}
        elif "amount" in val:
            inp["customerGets"] = {"value": {"discountAmount": {"amount": val["amount"]["amount"], "appliesOnEachItem": False}}, "items": {"all": True}}
    if dt in ("DiscountCodeBasic", "DiscountCodeFreeShipping"):
        inp["customerSelection"] = {"all": True}
    if dt in ("DiscountCodeFreeShipping", "DiscountAutomaticFreeShipping"):
        inp["destination"] = {"all": True}
    if dd.get("usageLimit"):
        inp["usageLimit"] = dd["usageLimit"]
    if dd.get("appliesOncePerCustomer") is not None:
        inp["appliesOncePerCustomer"] = dd["appliesOncePerCustomer"]
    return inp


def sync_discounts(src_api: ShopifyAPI, dst_api: ShopifyAPI, state: SyncState,
                   history: SyncHistory, config) -> SyncResult:
    result = SyncResult()

    with console.status("[cyan]Lecture discounts..."):
        src_pairs = _fetch_discounts(src_api)
        dst_pairs = _fetch_discounts(dst_api)

    # Index dst by code or title
    dst_by_code = {}
    dst_by_title = {}
    for dst_id, dd in dst_pairs:
        if "codes" in dd:
            for e in dd.get("codes", {}).get("edges", []):
                dst_by_code[e["node"]["code"]] = (dst_id, dd)
        else:
            dst_by_title[(dd.get("__typename", ""), dd.get("title", ""))] = (dst_id, dd)

    console.print(f"  {len(src_pairs)} source │ {len(dst_pairs)} dest")

    for _src_id, dd in src_pairs:
        dt = dd.get("__typename")
        if dt not in CREATE_MUTS:
            result.skipped += 1
            continue

        match = None
        if "codes" in dd:
            for e in dd.get("codes", {}).get("edges", []):
                if e["node"]["code"] in dst_by_code:
                    match = dst_by_code[e["node"]["code"]]
                    break
        else:
            match = dst_by_title.get((dt, dd.get("title", "")))

        inp = _build_input(dd)

        if match:
            dst_id, _ = match
            r = dst_api.gql(UPDATE_MUTS[dt], {"id": dst_id, "d": inp})
            key = next(iter((r.get("data") or {}).keys()), None)
            ue = ((r.get("data") or {}).get(key) or {}).get("userErrors", []) if key else []
            if not ue:
                result.updated += 1
                history.record("update", "discount", dd.get("title", ""))
            else:
                result.errors += 1
        else:
            dst_api.gql(CREATE_MUTS[dt], {"d": inp})
            result.created += 1
            history.record("create", "discount", dd.get("title", ""))
        time.sleep(0.15)

    return result
