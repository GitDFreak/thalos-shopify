from thalos.orchestrator import preflight_check, SECTION_ORDER
from thalos.state import SyncState


def test_empty_state_running_full_pipeline_has_no_warnings():
    warns = preflight_check([s for s, _ in SECTION_ORDER], SyncState())
    assert warns == []


def test_stock_alone_without_state_warns():
    warns = preflight_check(["stock"], SyncState())
    assert len(warns) == 1
    msg = warns[0]
    assert "stock" in msg
    assert "location" in msg and "inventory_item" in msg and "variant" in msg


def test_metafields_alone_without_state_warns_about_every_dep():
    warns = preflight_check(["metafields"], SyncState())
    assert len(warns) == 1
    msg = warns[0]
    for attr in ("product", "variant", "collection", "page", "article", "blog", "metaobject", "file"):
        assert attr in msg


def test_stock_alone_with_resume_state_has_no_warnings():
    s = SyncState()
    s.location[1] = 10
    s.inventory_item[2] = 20
    s.variant[3] = 30
    warns = preflight_check(["stock"], s)
    assert warns == []


def test_products_before_stock_in_same_run_has_no_warnings():
    warns = preflight_check(["files", "locations", "products", "stock"], SyncState())
    assert warns == []


def test_stock_after_products_without_files_or_locations_warns():
    # products produces inventory_item + variant but needs 'file' (missing).
    # stock needs location + inventory_item + variant — location missing.
    warns = preflight_check(["products", "stock"], SyncState())
    assert len(warns) == 2
    products_warn = next(w for w in warns if "'products'" in w)
    stock_warn    = next(w for w in warns if "'stock'" in w)
    assert "file" in products_warn
    assert "location" in stock_warn


def test_theme_alone_warns_about_file():
    warns = preflight_check(["theme"], SyncState())
    assert len(warns) == 1
    assert "file" in warns[0]


def test_independent_sections_have_no_warnings():
    # files, locations, pages, blogs, metafield_definitions, redirects = no deps
    warns = preflight_check(
        ["files", "locations", "pages", "blogs", "metafield_definitions", "redirects"],
        SyncState(),
    )
    assert warns == []
