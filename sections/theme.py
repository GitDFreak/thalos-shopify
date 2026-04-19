"""Theme sync — download, upload, smart strip (videos only), settings copy, publish."""
import json
import logging
import re
from rich.console import Console
from ..api import ShopifyAPI
from ..engine import SyncResult, run_parallel
from ..state import SyncState

console = Console()
logger = logging.getLogger(__name__)


def sync_theme(src_api: ShopifyAPI, dst_api: ShopifyAPI, state: SyncState, config) -> SyncResult:
    result = SyncResult()

    themes = src_api.get("themes.json").json()["themes"]
    live = next((t for t in themes if t["role"] == "main"), None)
    if not live:
        console.print("  [red]Pas de theme live sur source[/red]")
        return result

    console.print(f"  Theme: {live['name']}")

    # Download all assets
    assets = [a["key"] for a in src_api.get(f"themes/{live['id']}/assets.json").json().get("assets", [])]

    downloaded = {}

    def _dl(key):
        try:
            r = src_api.get(f"themes/{live['id']}/assets.json", {"asset[key]": key})
            a = r.json().get("asset", {})
            return (key, a.get("value"), a.get("attachment"))
        except Exception:
            return (key, None, None)

    results = run_parallel(_dl, assets, "Download theme", config.max_workers)
    for r in results:
        if isinstance(r, Exception):
            continue
        k, v, att = r
        if v is not None or att is not None:
            downloaded[k] = {"value": v, "attachment": att}

    console.print(f"  {len(downloaded)}/{len(assets)} téléchargés")

    # Create theme on dest (unpublished)
    tr = dst_api.post("themes.json", {"theme": {"name": f"Clone - {live['name']}", "role": "unpublished"}})
    if tr.status_code != 201:
        console.print(f"  [red]Erreur création theme: {tr.status_code}[/red]")
        return result

    tid = tr.json()["theme"]["id"]
    console.print(f"  Theme créé: {tid}")

    # Upload assets
    failed = []

    def _ul(key):
        d = downloaded[key]
        payload = {"asset": {"key": key}}
        if d["attachment"]:
            payload["asset"]["attachment"] = d["attachment"]
        elif d["value"]:
            payload["asset"]["value"] = d["value"]
        else:
            return "skip"
        r = dst_api.put(f"themes/{tid}/assets.json", payload)
        return "ok" if r.status_code == 200 else key

    results = run_parallel(_ul, list(downloaded.keys()), "Upload theme", config.max_workers)
    ok = 0
    for r in results:
        if r == "ok":
            ok += 1
        elif isinstance(r, str) and r not in ("skip", "ok"):
            failed.append(r)

    result.created = ok
    console.print(f"  {ok} uploadés, {len(failed)} échecs")

    # Retry failed: smart strip (ONLY videos, keep images/pages/collections)
    if failed:
        retried = 0
        for key in failed:
            d = downloaded[key]
            if not d.get("value"):
                continue
            try:
                data = json.loads(d["value"])

                def _strip(obj):
                    if isinstance(obj, dict):
                        return {k: ("" if isinstance(v, str) and "shopify://files/videos/" in v else _strip(v))
                                for k, v in obj.items()}
                    elif isinstance(obj, list):
                        return [_strip(i) for i in obj]
                    return obj

                r = dst_api.put(f"themes/{tid}/assets.json", {"asset": {"key": key, "value": json.dumps(_strip(data))}})
                if r.status_code == 200:
                    retried += 1
            except Exception:
                logger.debug("theme asset retry (video strip) failed: %s", key, exc_info=True)
        result.updated = retried
        console.print(f"  {retried} retried (vidéos strippées)")

    # Copy sections + blocks in settings_data.json
    try:
        src_sd = json.loads(downloaded.get("config/settings_data.json", {}).get("value", "{}"))
    except (json.JSONDecodeError, TypeError):
        src_sd = {}
    try:
        dst_sd_r = dst_api.get(f"themes/{tid}/assets.json", {"asset[key]": "config/settings_data.json"})
        dst_sd = json.loads(dst_sd_r.json().get("asset", {}).get("value", "{}"))
    except (json.JSONDecodeError, TypeError, Exception):
        dst_sd = {}

    for key in ("sections", "blocks"):
        if key in src_sd.get("current", {}):
            dst_sd.setdefault("current", {})[key] = src_sd["current"][key]

    dst_api.put(f"themes/{tid}/assets.json",
                {"asset": {"key": "config/settings_data.json", "value": json.dumps(dst_sd)}})
    console.print("  settings_data.json ✓")

    # Publish
    r = dst_api.put(f"themes/{tid}.json", {"theme": {"role": "main"}})
    console.print(f"  Theme {'publié ✓' if r.status_code == 200 else 'non publié'}")

    return result
