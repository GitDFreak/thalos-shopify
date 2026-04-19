"""Files & Videos sync — images, generic files, videos via staged upload."""
import logging
import re
import time
import requests as req
from urllib.parse import urlparse
from rich.console import Console

from ..api import ShopifyAPI
from ..engine import run_parallel, SyncResult
from ..state import SyncState, SyncHistory

console = Console()
logger = logging.getLogger(__name__)


def _file_key(f):
    url = (f.get("image") or {}).get("url") or f.get("url") or ""
    if not url:
        return None
    return (urlparse(url).path.split("/")[-1].split("?")[0], (f.get("alt") or "").strip())


def _fetch_all_files(api: ShopifyAPI):
    items = []
    cursor = None
    while True:
        after = f', after: "{cursor}"' if cursor else ""
        d = api.gql('{files(first:50,sortKey:CREATED_AT%s){edges{cursor node{id ...on MediaImage{image{url altText}alt}...on GenericFile{url alt}}}pageInfo{hasNextPage}}}' % after)
        edges = (d.get("data") or {}).get("files", {}).get("edges", [])
        for e in edges:
            items.append(e["node"])
            cursor = e["cursor"]
        if not edges or not (d.get("data") or {}).get("files", {}).get("pageInfo", {}).get("hasNextPage"):
            break
    return items


def _fetch_all_videos(api: ShopifyAPI):
    items = []
    cursor = None
    while True:
        after = f', after: "{cursor}"' if cursor else ""
        d = api.gql('{files(first:50,query:"media_type:VIDEO"%s){edges{cursor node{...on Video{id alt filename originalSource{url mimeType}}}}pageInfo{hasNextPage}}}' % after)
        edges = (d.get("data") or {}).get("files", {}).get("edges", [])
        for e in edges:
            n = e["node"]
            if n.get("originalSource") and n["originalSource"].get("url"):
                items.append(n)
                cursor = e["cursor"]
        if not edges or not (d.get("data") or {}).get("files", {}).get("pageInfo", {}).get("hasNextPage"):
            break
    return items


def _get_theme_video_names(src_api: ShopifyAPI) -> set:
    """Scan theme templates to find video refs — these upload FIRST."""
    names = set()
    try:
        themes = src_api.get("themes.json").json()["themes"]
        live = next((t for t in themes if t["role"] == "main"), None)
        if not live:
            return names
        assets = src_api.get(f"themes/{live['id']}/assets.json").json().get("assets", [])
        json_keys = [a["key"] for a in assets
                     if a["key"].endswith(".json") and any(a["key"].startswith(p) for p in ("templates/", "sections/", "config/"))]
        for key in json_keys:
            try:
                content = src_api.get(f"themes/{live['id']}/assets.json", {"asset[key]": key}).json().get("asset", {}).get("value", "")
                if content:
                    for m in re.findall(r'files[/\\]+videos[/\\]+([\w._%-]+)', content):
                        names.add(m)
            except Exception:
                logger.debug("theme asset scan failed for key=%s", key, exc_info=True)
    except Exception:
        logger.debug("theme video reference scan failed", exc_info=True)
    return names


def sync_files(src_api: ShopifyAPI, dst_api: ShopifyAPI, state: SyncState,
               history: SyncHistory, config) -> SyncResult:
    result = SyncResult()

    # ── Images + Generic files ──
    with console.status("[cyan]Lecture fichiers source..."):
        src_files = _fetch_all_files(src_api)
    with console.status("[cyan]Lecture fichiers dest..."):
        dst_files = _fetch_all_files(dst_api)

    dst_keys = {_file_key(f): f.get("id") for f in dst_files if _file_key(f)}

    to_create = []
    for f in src_files:
        k = _file_key(f)
        if k and k in dst_keys:
            result.skipped += 1
            old_gid = f.get("id")
            if old_gid:
                state.file[old_gid] = dst_keys[k]
        else:
            to_create.append(f)

    console.print(f"  {len(src_files)} source │ {result.skipped} existants │ {len(to_create)} à créer")

    mut = 'mutation($f:[FileCreateInput!]!){fileCreate(files:$f){files{id}userErrors{field message}}}'

    def _create_file(f):
        inp = None
        if f.get("image") and f["image"].get("url"):
            inp = {"alt": f.get("alt", ""), "contentType": "IMAGE", "originalSource": f["image"]["url"]}
        elif f.get("url"):
            inp = {"alt": f.get("alt", ""), "contentType": "FILE", "originalSource": f["url"]}
        if not inp:
            return ("skip", None, None)
        r = dst_api.gql(mut, {"f": [inp]})
        fc = (r.get("data") or {}).get("fileCreate") or {}
        if fc.get("files") and not fc.get("userErrors"):
            return ("ok", f.get("id"), fc["files"][0].get("id"))
        return ("err", None, None)

    if to_create:
        results = run_parallel(_create_file, to_create, "Fichiers", config.max_workers)
        for r in results:
            if isinstance(r, Exception):
                result.errors += 1
                continue
            s, old, new = r
            if s == "ok":
                result.created += 1
                if old and new:
                    state.file[old] = new
            elif s == "err":
                result.errors += 1

    state.save_map("file", config.snapshot_dir)

    # ── Videos (staged upload) ──
    console.print("\n  [cyan]Vidéos...[/cyan]")
    with console.status("[cyan]Lecture vidéos source..."):
        src_vids = _fetch_all_videos(src_api)

    dst_vfn = set()
    cursor = None
    while True:
        after = f', after: "{cursor}"' if cursor else ""
        d = dst_api.gql('{files(first:50,query:"media_type:VIDEO"%s){edges{cursor node{...on Video{filename}}}pageInfo{hasNextPage}}}' % after)
        edges = (d.get("data") or {}).get("files", {}).get("edges", [])
        for e in edges:
            fn = e["node"].get("filename")
            if fn:
                dst_vfn.add(fn)
                cursor = e["cursor"]
        if not edges or not (d.get("data") or {}).get("files", {}).get("pageInfo", {}).get("hasNextPage"):
            break

    vids = [v for v in src_vids if v.get("filename") not in dst_vfn]

    # Prioritize theme videos
    theme_vid_names = _get_theme_video_names(src_api)
    priority = [v for v in vids if v.get("filename") in theme_vid_names]
    rest = [v for v in vids if v.get("filename") not in theme_vid_names]
    vids = priority + rest

    console.print(f"  {len(src_vids)} source │ {len(dst_vfn)} existantes │ {len(vids)} à uploader")
    if priority:
        console.print(f"  [green]▲ {len(priority)} vidéos theme (prioritaires)[/green]")
    if vids:
        console.print(f"  [yellow]⚠ Limite Shopify: 1000 vidéos/semaine[/yellow]")

    v_ok = v_rl = v_err = 0

    def _upload_vid(video):
        fn = video["filename"]
        url = video["originalSource"]["url"]
        mime = video["originalSource"].get("mimeType", "video/mp4")
        try:
            vdata = req.get(url, timeout=300).content
            sz = len(vdata)
        except Exception:
            logger.warning("video download failed: %s (%s)", fn, url, exc_info=True)
            return ("err", fn)
        # Staged upload
        s = dst_api.gql(
            'mutation($i:[StagedUploadInput!]!){stagedUploadsCreate(input:$i){stagedTargets{url resourceUrl parameters{name value}}userErrors{field message}}}',
            {"i": [{"filename": fn, "mimeType": mime, "fileSize": str(sz), "httpMethod": "POST", "resource": "VIDEO"}]},
        )
        tgts = ((s.get("data") or {}).get("stagedUploadsCreate") or {}).get("stagedTargets", [])
        if not tgts:
            return ("err", fn)
        t = tgts[0]
        params = {p["name"]: p["value"] for p in t["parameters"]}
        try:
            ur = req.post(t["url"], files={**{k: (None, v) for k, v in params.items()}, "file": (fn, vdata, mime)}, timeout=600)
            if ur.status_code not in (200, 201, 204):
                logger.warning("video staged upload HTTP %d: %s", ur.status_code, fn)
                return ("err", fn)
        except Exception:
            logger.warning("video staged upload failed: %s", fn, exc_info=True)
            return ("err", fn)
        res = dst_api.gql(
            'mutation($f:[FileCreateInput!]!){fileCreate(files:$f){files{id}userErrors{field message}}}',
            {"f": [{"alt": video.get("alt", ""), "contentType": "VIDEO", "originalSource": t["resourceUrl"]}]},
        )
        fc = (res.get("data") or {}).get("fileCreate") or {}
        if fc.get("files") and not fc.get("userErrors"):
            return ("ok", fn)
        if "1,000" in str(res.get("errors", "")):
            return ("ratelimit", fn)
        return ("err", fn)

    if vids:
        results = run_parallel(_upload_vid, vids, "Vidéos", workers=min(config.max_workers, 5))
        for r in results:
            if isinstance(r, Exception):
                v_err += 1
                continue
            s, fn = r
            if s == "ok":
                v_ok += 1
            elif s == "ratelimit":
                v_rl += 1
            else:
                v_err += 1
        console.print(f"  Vidéos: {v_ok} ok, {v_err} err, {v_rl} rate-limited")
        if v_rl:
            console.print(f"  [yellow]⏳ {v_rl} vidéos en attente — relancer dans 7 jours[/yellow]")

    result.created += v_ok
    result.errors += v_err
    return result
