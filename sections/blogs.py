"""Blogs + Articles sync — blogs then articles as sub-resource."""
from rich.console import Console
from ..api import ShopifyAPI
from ..engine import sync_resources, SyncResult, run_parallel
from ..state import SyncState, SyncHistory
from ..remap import remap_body_html

console = Console()


def sync_blogs_and_articles(src_api: ShopifyAPI, dst_api: ShopifyAPI, state: SyncState,
                             history: SyncHistory, config) -> SyncResult:
    result = SyncResult()

    # ── Blogs ──
    src_blogs = src_api.paginate("blogs.json", "blogs")
    dst_blogs = dst_api.paginate("blogs.json", "blogs")
    dst_bh = {b["handle"]: b for b in dst_blogs if b.get("handle")}

    for blog in src_blogs:
        h = blog.get("handle")
        if h and h in dst_bh:
            state.blog[blog["id"]] = dst_bh[h]["id"]
            result.skipped += 1
        else:
            r = dst_api.post("blogs.json", {"blog": {
                "title": blog["title"], "handle": h,
                "commentable": blog.get("commentable", "no")}})
            if r.status_code == 201:
                state.blog[blog["id"]] = r.json()["blog"]["id"]
                result.created += 1
                history.record("create", "blog", blog["id"])

    # ── Articles (sub-resource of blogs) ──
    total_art = 0
    src_params = config.since_params()
    for src_bid, dst_bid in state.blog.items():
        src_articles = src_api.paginate(f"blogs/{src_bid}/articles.json", "articles", params=src_params)
        dst_articles = dst_api.paginate(f"blogs/{dst_bid}/articles.json", "articles")

        def _compare(s, d):
            for f in ["title", "body_html", "tags", "published_at"]:
                if str(s.get(f, "")) != str(d.get(f, "")):
                    return True
            return False

        def _create(art):
            r = dst_api.post(f"blogs/{dst_bid}/articles.json", {"article": {
                "title": art["title"],
                "body_html": remap_body_html(art.get("body_html", ""), config.source_store, config.dest_store),
                "handle": art.get("handle"), "author": art.get("author", ""),
                "tags": art.get("tags", ""), "published_at": art.get("published_at"),
                "published": art.get("published_at") is not None}}, silent=True)
            if r.status_code == 201:
                history.record("create", "article", art["id"])
                return r.json()["article"]["id"]
            return None

        def _update(dst_id, src_art):
            r = dst_api.put(f"blogs/{dst_bid}/articles/{dst_id}.json", {"article": {
                "id": dst_id, "title": src_art["title"],
                "body_html": remap_body_html(src_art.get("body_html", ""), config.source_store, config.dest_store),
                "tags": src_art.get("tags", "")}})
            if r.status_code == 200:
                history.record("update", "article", dst_id)
                return True
            return False

        def _delete(dst_id):
            r = dst_api.delete(f"blogs/{dst_bid}/articles/{dst_id}.json")
            if r.status_code == 200:
                history.record("delete", "article", dst_id)
                return True
            return False

        art_result, art_map = sync_resources(
            src_items=src_articles, dst_items=dst_articles,
            key_field="handle", create_fn=_create, update_fn=_update,
            delete_fn=_delete, compare_fn=_compare,
            delete_orphans=config.should_delete_orphans("blogs"), workers=config.max_workers,
            desc=f"articles blog {src_bid}",
        )
        state.article.update(art_map)
        total_art += art_result.created + art_result.updated

    result.updated += total_art
    state.save_map("blog", config.snapshot_dir)
    state.save_map("article", config.snapshot_dir)
    console.print(f"  {len(state.blog)} blogs, {len(state.article)} articles")
    return result
