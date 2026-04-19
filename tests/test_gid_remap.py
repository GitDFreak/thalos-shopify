from thalos.remap import build_gid_remap
from thalos.state import SyncState


def test_covers_int_id_types():
    s = SyncState()
    s.product[1] = 100
    s.variant[2] = 200
    s.collection[3] = 300
    s.page[4] = 400
    s.article[5] = 500
    s.blog[6] = 600
    s.order[7] = 700
    s.location[8] = 800
    s.inventory_item[9] = 900
    r = build_gid_remap(s)
    assert r["gid://shopify/Product/1"]       == "gid://shopify/Product/100"
    assert r["gid://shopify/ProductVariant/2"] == "gid://shopify/ProductVariant/200"
    assert r["gid://shopify/Collection/3"]    == "gid://shopify/Collection/300"
    assert r["gid://shopify/Order/7"]         == "gid://shopify/Order/700"
    assert r["gid://shopify/Location/8"]      == "gid://shopify/Location/800"
    assert r["gid://shopify/InventoryItem/9"] == "gid://shopify/InventoryItem/900"


def test_covers_page_and_article_aliases():
    s = SyncState()
    s.page[1] = 100
    s.article[2] = 200
    s.blog[3] = 300
    r = build_gid_remap(s)
    assert r["gid://shopify/Page/1"]              == "gid://shopify/Page/100"
    assert r["gid://shopify/OnlineStorePage/1"]   == "gid://shopify/OnlineStorePage/100"
    assert r["gid://shopify/Article/2"]           == "gid://shopify/Article/200"
    assert r["gid://shopify/OnlineStoreArticle/2"] == "gid://shopify/OnlineStoreArticle/200"
    assert r["gid://shopify/Blog/3"]              == "gid://shopify/Blog/300"
    assert r["gid://shopify/OnlineStoreBlog/3"]   == "gid://shopify/OnlineStoreBlog/300"


def test_covers_gid_keyed_attrs():
    s = SyncState()
    s.metaobject["gid://shopify/Metaobject/aaa"] = "gid://shopify/Metaobject/bbb"
    s.file["gid://shopify/MediaImage/111"]       = "gid://shopify/MediaImage/222"
    r = build_gid_remap(s)
    assert r["gid://shopify/Metaobject/aaa"] == "gid://shopify/Metaobject/bbb"
    assert r["gid://shopify/MediaImage/111"] == "gid://shopify/MediaImage/222"


def test_empty_state():
    assert build_gid_remap(SyncState()) == {}
