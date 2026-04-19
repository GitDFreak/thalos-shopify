"""Publication — publish products + collections to Online Store channel."""
from rich.console import Console
from ..api import ShopifyAPI
from ..engine import SyncResult, run_parallel
from ..state import SyncState

console = Console()


def sync_publication(src_api: ShopifyAPI, dst_api: ShopifyAPI, state: SyncState, config) -> SyncResult:
    result = SyncResult()

    # Find all publications
    d = dst_api.gql('{publications(first:20){edges{node{id catalog{title}}}}}')
    pubs = (d.get("data") or {}).get("publications", {}).get("edges", [])
    pub_ids = [p["node"]["id"] for p in pubs if p.get("node")]

    if not pub_ids:
        console.print("  [yellow]Aucune publication trouvée[/yellow]")
        return result

    console.print(f"  {len(pub_ids)} publications")
    mut = 'mutation($id:ID!,$i:[PublicationInput!]!){publishablePublish(id:$id,input:$i){userErrors{message}}}'
    inputs = [{"publicationId": p} for p in pub_ids]

    # Publish products
    if state.product:
        def _make_pub(prefix):
            def _pub(nid):
                return dst_api.gql(mut, {"id": f"gid://shopify/{prefix}/{nid}", "i": inputs})
            return _pub

        console.print(f"  Produits: {len(state.product)}")
        run_parallel(_make_pub("Product"), list(state.product.values()), "Publish produits", config.max_workers)
        result.updated += len(state.product)

    # Publish collections
    if state.collection:
        console.print(f"  Collections: {len(state.collection)}")
        run_parallel(_make_pub("Collection"), list(state.collection.values()), "Publish collections", config.max_workers)
        result.updated += len(state.collection)

    return result
