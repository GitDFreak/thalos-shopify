"""Delivery profiles sync — shipping zones + rates + methods.

Without this, a cloned store has only the Shopify-generated default profile
with no rates → checkout broken for customers.

Scope v1:
- CREATE missing profiles (name-matched against dst)
- Copy zones (countries + provinces) and method definitions (rates)
- Map src location GIDs to dst via state.location
- Skip the "default" profile (Shopify manages it automatically)
- Skip profile UPDATE (mutation is complex: nested to-create/update/delete);
  existing profiles on dst are left alone. Run with delete_orphans off.
- Skip product assignment (variants reference profile via deliveryProfile
  field; advanced users can reassign manually or via subsequent sync).
"""
from rich.console import Console

from ..api import ShopifyAPI, ERRORS
from ..engine import SyncResult
from ..state import SyncState, SyncHistory

console = Console()

_PROFILES_QUERY = '''
{
  deliveryProfiles(first: 25, merchantOwnedOnly: true) {
    edges { node {
      id name default
      profileLocationGroups {
        locationGroup {
          id
          locations(first: 100) { nodes { id name } }
        }
        locationGroupZones(first: 50) {
          edges { node {
            zone {
              id name
              countries {
                code { countryCode restOfWorld }
                provinces { code }
              }
            }
            methodDefinitions(first: 25) {
              edges { node {
                id name active description
                rateProvider {
                  __typename
                  ... on DeliveryRateDefinition {
                    price { amount currencyCode }
                  }
                }
                methodConditions {
                  field operator
                  conditionCriteria {
                    __typename
                    ... on MoneyV2 { amount currencyCode }
                    ... on Weight { unit value }
                  }
                }
              } }
            }
          } }
        }
      }
    } }
  }
}
'''


def _build_profile_input(profile: dict, state: SyncState) -> dict | None:
    """Convert a src profile node into deliveryProfileCreate input.
    Returns None if no location can be mapped (profile unusable)."""
    location_groups = []
    for plg in profile.get("profileLocationGroups") or []:
        src_loc_gids = [loc["id"] for loc in plg["locationGroup"]["locations"]["nodes"]]
        # Map to dst location GIDs via state.location (int → int)
        dst_loc_gids = []
        for gid in src_loc_gids:
            try:
                src_id = int(gid.rsplit("/", 1)[-1])
            except ValueError:
                continue
            dst_id = state.location.get(src_id)
            if dst_id:
                dst_loc_gids.append(f"gid://shopify/Location/{dst_id}")
        if not dst_loc_gids:
            continue

        zones = []
        for zedge in plg.get("locationGroupZones", {}).get("edges", []):
            z = zedge["node"]["zone"]
            countries = []
            for c in z.get("countries") or []:
                cc = c["code"]
                if cc.get("restOfWorld"):
                    countries.append({"restOfWorld": True})
                elif cc.get("countryCode"):
                    entry = {"code": cc["countryCode"]}
                    provs = [{"code": p["code"]} for p in (c.get("provinces") or [])]
                    if provs:
                        entry["provinces"] = provs
                    countries.append(entry)
            if not countries:
                continue

            methods = []
            for medge in zedge["node"].get("methodDefinitions", {}).get("edges", []):
                m = medge["node"]
                rp = m.get("rateProvider") or {}
                # Only flat-rate (DeliveryRateDefinition) supported here.
                # Carrier-based (DeliveryParticipant) needs the same carrier app
                # installed on dst and is out of scope.
                if rp.get("__typename") != "DeliveryRateDefinition":
                    ERRORS.add(
                        f"delivery profile '{profile['name']}': zone '{z['name']}' "
                        f"method '{m['name']}' uses carrier '{rp.get('__typename')}' "
                        f"— skipped (install the carrier app on dst and re-create manually)"
                    )
                    continue
                md = {
                    "name": m["name"],
                    "active": m.get("active", True),
                    "rateDefinition": {"price": rp["price"]},
                }
                if m.get("description"):
                    md["description"] = m["description"]
                weight_conds, price_conds = [], []
                for cond in (m.get("methodConditions") or []):
                    crit = cond.get("conditionCriteria") or {}
                    entry = {"operator": cond["operator"]}
                    if crit.get("__typename") == "Weight":
                        entry["criteria"] = {"value": crit["value"], "unit": crit["unit"]}
                        weight_conds.append(entry)
                    elif crit.get("__typename") == "MoneyV2":
                        entry["criteria"] = {"amount": crit["amount"], "currencyCode": crit["currencyCode"]}
                        price_conds.append(entry)
                if weight_conds:
                    md["weightConditionsToCreate"] = weight_conds
                if price_conds:
                    md["priceConditionsToCreate"] = price_conds
                methods.append(md)

            zone_input = {"name": z["name"], "countries": countries}
            if methods:
                zone_input["methodDefinitionsToCreate"] = methods
            zones.append(zone_input)

        location_groups.append({
            "locationsToAdd": dst_loc_gids,
            "zonesToCreate": zones,
        })

    if not location_groups:
        return None
    return {"name": profile["name"], "locationGroupsToCreate": location_groups}


def sync_delivery(src_api: ShopifyAPI, dst_api: ShopifyAPI, state: SyncState,
                  history: SyncHistory, config) -> SyncResult:
    result = SyncResult()

    if not state.location:
        ERRORS.add("delivery: state.location vide — lance 'locations' avant pour permettre le mapping src→dst des location GIDs")
        return result

    src_data = src_api.gql(_PROFILES_QUERY).get("data") or {}
    dst_data = dst_api.gql(_PROFILES_QUERY).get("data") or {}
    src_profiles = [e["node"] for e in src_data.get("deliveryProfiles", {}).get("edges", [])]
    dst_profiles = [e["node"] for e in dst_data.get("deliveryProfiles", {}).get("edges", [])]
    dst_by_name = {p["name"]: p for p in dst_profiles if not p.get("default")}

    console.print(f"  {len(src_profiles)} source │ {len(dst_profiles)} dest (default inclus)")

    for profile in src_profiles:
        if profile.get("default"):
            result.skipped += 1
            continue
        if profile["name"] in dst_by_name:
            # v1: skip existing (update mutation is complex; user edits manually)
            result.skipped += 1
            continue

        payload = _build_profile_input(profile, state)
        if payload is None:
            ERRORS.add(f"delivery profile '{profile['name']}': aucune location mappée — skip")
            result.errors += 1
            continue

        r = dst_api.gql(
            'mutation($profile:DeliveryProfileInput!){deliveryProfileCreate(profile:$profile){profile{id name}userErrors{field message}}}',
            {"profile": payload},
        )
        ue = ((r.get("data") or {}).get("deliveryProfileCreate") or {}).get("userErrors") or []
        if ue:
            result.errors += 1
        else:
            result.created += 1
            history.record("create", "delivery_profile", profile["id"])

    return result
