"""Metaobjects sync — 2-pass definitions (create + update ref fields) + entries.

Entries may reference products, collections, pages, articles, blogs, files,
other metaobjects — all must be remapped via state before being written.
"""
import json
import logging
import time
from rich.console import Console
from ..api import ShopifyAPI
from ..engine import SyncResult, run_parallel
from ..state import SyncState, SyncHistory

console = Console()
logger = logging.getLogger(__name__)

REF_TYPES = {"metaobject_reference", "list.metaobject_reference", "mixed_reference", "list.mixed_reference"}


from ..remap import build_gid_remap as _build_gid_remap  # noqa: E402


def _remap_field_value(value: str, gid_remap: dict) -> tuple[str, bool]:
    """Remap a field value that may contain gid(s). Returns (new_value, ok)."""
    if not value:
        return value, True
    if value.startswith("["):
        try:
            gids = json.loads(value)
            new = [gid_remap.get(g, g) for g in gids]
            if any(g.startswith("gid://") and g not in gid_remap for g in gids):
                new = [gid_remap[g] for g in gids if g in gid_remap]
                return json.dumps(new), bool(new)
            return json.dumps(new), True
        except Exception:
            return value, True
    if value.startswith("gid://"):
        if value in gid_remap:
            return gid_remap[value], True
        return value, False
    return value, True


def sync_metaobjects(src_api: ShopifyAPI, dst_api: ShopifyAPI, state: SyncState,
                     history: SyncHistory, config) -> SyncResult:
    result = SyncResult()

    # Fetch source definitions
    d = src_api.gql('{metaobjectDefinitions(first:250){edges{node{type name description access{admin storefront}fieldDefinitions{key name description required type{name}validations{name value}}}}}}')
    src_defs = [e["node"] for e in (d.get("data") or {}).get("metaobjectDefinitions", {}).get("edges", [])]
    console.print(f"  {len(src_defs)} definitions source")

    # Pass 1: create without ref fields
    console.print("  Pass 1: Definitions sans champs reference...")
    create_mut = 'mutation($d:MetaobjectDefinitionCreateInput!){metaobjectDefinitionCreate(definition:$d){metaobjectDefinition{id type}userErrors{field message}}}'
    created_types = set()
    deferred = {}

    for defn in src_defs:
        t = defn["type"]
        if t.startswith("shopify--"):
            continue
        fds = []
        df = []
        for f in defn["fieldDefinitions"]:
            if f["type"]["name"] in REF_TYPES:
                df.append(f)
                continue
            fd = {"key": f["key"], "name": f["name"], "type": f["type"]["name"], "required": f.get("required", False)}
            if f.get("description"):
                fd["description"] = f["description"]
            if f.get("validations"):
                safe = [{"name": v["name"], "value": v["value"]}
                        for v in f["validations"]
                        if "gid://" not in v.get("value", "") and "metaobject" not in v.get("name", "").lower()]
                if safe:
                    fd["validations"] = safe
            fds.append(fd)
        if df:
            deferred[t] = df
        var = {"d": {"type": t, "name": defn["name"],
                      "access": {"storefront": defn["access"]["storefront"] if defn["access"]["storefront"] in ("NONE", "PUBLIC_READ") else "PUBLIC_READ"},
                      "fieldDefinitions": fds}}
        if defn.get("description"):
            var["d"]["description"] = defn["description"]
        r = dst_api.gql(create_mut, var)
        ue = ((r.get("data") or {}).get("metaobjectDefinitionCreate") or {}).get("userErrors", [])
        if not ue or "taken" in str(ue).lower():
            created_types.add(t)
        time.sleep(0.15)

    console.print(f"  {len(created_types)} definitions OK, {len(deferred)} avec champs ref")

    # Pass 2: add ref fields via update
    if deferred:
        console.print("  Pass 2: Ajout champs reference...")
        # Invalidate dst cache because we just created defs
        dst_api.invalidate("metaobject_defs_t2g")
        dst_t2id = dst_api.cached("metaobject_defs_t2g", lambda: {
            e["node"]["type"]: e["node"]["id"]
            for e in (dst_api.gql('{metaobjectDefinitions(first:250){edges{node{id type}}}}').get("data") or {}).get("metaobjectDefinitions", {}).get("edges", [])
        })
        src_g2t = src_api.cached("metaobject_defs_g2t", lambda: {
            e["node"]["id"]: e["node"]["type"]
            for e in (src_api.gql('{metaobjectDefinitions(first:250){edges{node{id type}}}}').get("data") or {}).get("metaobjectDefinitions", {}).get("edges", [])
        })
        upd_mut = 'mutation($id:ID!,$d:MetaobjectDefinitionUpdateInput!){metaobjectDefinitionUpdate(id:$id,definition:$d){metaobjectDefinition{id}userErrors{field message}}}'

        for t, fields in deferred.items():
            if t not in dst_t2id:
                continue
            fcs = []
            for f in fields:
                fd = {"key": f["key"], "name": f["name"], "type": f["type"]["name"]}
                if f.get("description"):
                    fd["description"] = f["description"]
                if f.get("validations"):
                    rv = []
                    for v in f["validations"]:
                        val = v.get("value", "")
                        if val.startswith("["):
                            try:
                                gids = json.loads(val)
                                new = [dst_t2id[src_g2t[g]] for g in gids if g in src_g2t and src_g2t[g] in dst_t2id]
                                if new:
                                    rv.append({"name": v["name"], "value": json.dumps(new)})
                            except Exception:
                                logger.debug("metaobject def ref-list remap failed: %r", val, exc_info=True)
                        elif "gid://shopify/MetaobjectDefinition/" in val:
                            rt = src_g2t.get(val)
                            if rt and rt in dst_t2id:
                                rv.append({"name": v["name"], "value": dst_t2id[rt]})
                        else:
                            rv.append({"name": v["name"], "value": val})
                    if rv:
                        fd["validations"] = rv
                fcs.append(fd)
            if fcs:
                dst_api.gql(upd_mut, {"id": dst_t2id[t], "d": {"fieldDefinitions": [{"create": f} for f in fcs]}})
            time.sleep(0.15)

    # Entries — fetch existing on dest (with fields for diff)
    with console.status("[cyan]Pre-fetch metaobjects dest..."):
        existing_mo = {}
        for defn in src_defs:
            t = defn["type"]
            cursor = None
            while True:
                after = f', after: "{cursor}"' if cursor else ""
                d = dst_api.gql('{metaobjects(type:"%s",first:100%s){edges{cursor node{id handle fields{key value}}}pageInfo{hasNextPage}}}' % (t, after))
                edges = (d.get("data") or {}).get("metaobjects", {}).get("edges", [])
                for e in edges:
                    existing_mo[(t, e["node"]["handle"])] = e["node"]
                    cursor = e["cursor"]
                if not edges or not (d.get("data") or {}).get("metaobjects", {}).get("pageInfo", {}).get("hasNextPage"):
                    break

    console.print(f"  {len(existing_mo)} entries existantes")

    gid_remap = _build_gid_remap(state)
    console.print(f"  gid_remap: {len(gid_remap)} entries pour resolution refs")

    # Create entries
    for defn in src_defs:
        t = defn["type"]
        if t not in created_types and not t.startswith("shopify--"):
            continue
        cursor = None
        src_mos = []
        while True:
            after = f', after: "{cursor}"' if cursor else ""
            d = src_api.gql('{metaobjects(type:"%s",first:250%s){edges{cursor node{id handle fields{key value}}}pageInfo{hasNextPage}}}' % (t, after))
            edges = (d.get("data") or {}).get("metaobjects", {}).get("edges", [])
            src_mos.extend(e["node"] for e in edges)
            if edges:
                cursor = edges[-1]["cursor"]
            if not edges or not (d.get("data") or {}).get("metaobjects", {}).get("pageInfo", {}).get("hasNextPage"):
                break
        for mo in src_mos:
            key = (t, mo["handle"])
            fields = []
            for f in mo["fields"]:
                if not f["value"]:
                    continue
                new_val, ok = _remap_field_value(f["value"], gid_remap)
                if not ok:
                    continue
                fields.append({"key": f["key"], "value": new_val})
            if key in existing_mo:
                dst_entry = existing_mo[key]
                state.metaobject[mo["id"]] = dst_entry["id"]
                dst_fields = {f["key"]: f["value"] for f in (dst_entry.get("fields") or [])}
                src_fields = {f["key"]: f["value"] for f in fields}
                if dst_fields == src_fields:
                    result.skipped += 1
                    continue
                r = dst_api.gql(
                    'mutation($id:ID!,$m:MetaobjectUpdateInput!){metaobjectUpdate(id:$id,metaobject:$m){metaobject{id}userErrors{field message}}}',
                    {"id": dst_entry["id"], "m": {"fields": fields}},
                )
                ue = ((r.get("data") or {}).get("metaobjectUpdate") or {}).get("userErrors", [])
                if not ue:
                    result.updated += 1
                    history.record("update", "metaobject", mo["id"])
                else:
                    result.errors += 1
                time.sleep(0.1)
                continue
            r = dst_api.gql(
                'mutation($m:MetaobjectCreateInput!){metaobjectCreate(metaobject:$m){metaobject{id}userErrors{field message}}}',
                {"m": {"type": t, "handle": mo["handle"], "fields": fields}},
            )
            nm = ((r.get("data") or {}).get("metaobjectCreate") or {}).get("metaobject")
            if nm:
                result.created += 1
                state.metaobject[mo["id"]] = nm["id"]
                history.record("create", "metaobject", mo["id"])
            else:
                ue = ((r.get("data") or {}).get("metaobjectCreate") or {}).get("userErrors", [])
                if ue and "taken" in str(ue).lower():
                    ql = dst_api.gql('{metaobjectByHandle(handle:{type:"%s",handle:"%s"}){id}}' % (t, mo["handle"]))
                    eid = ((ql.get("data") or {}).get("metaobjectByHandle") or {}).get("id")
                    if eid:
                        state.metaobject[mo["id"]] = eid
                        result.skipped += 1
                else:
                    result.errors += 1
            time.sleep(0.1)

    state.save_map("metaobject", config.snapshot_dir)
    return result
