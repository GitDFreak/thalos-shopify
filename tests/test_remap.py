from thalos.remap import remap_body_html


def test_rewrites_absolute_source_url():
    out = remap_body_html(
        '<a href="https://paul-and-joe.myshopify.com/pages/about">about</a>',
        "paul-and-joe", "paul-and-joe-dev00",
    )
    assert out == '<a href="https://paul-and-joe-dev00.myshopify.com/pages/about">about</a>'


def test_none_passthrough():
    assert remap_body_html(None, "a", "b") is None


def test_empty_string_passthrough():
    assert remap_body_html("", "a", "b") == ""


def test_non_string_passthrough():
    assert remap_body_html(123, "a", "b") == 123


def test_same_store_is_noop():
    html = '<a href="https://x.myshopify.com/">x</a>'
    assert remap_body_html(html, "x", "x") == html


def test_empty_source_or_dest_is_noop():
    html = '<a href="https://x.myshopify.com/">x</a>'
    assert remap_body_html(html, "", "x") == html
    assert remap_body_html(html, "x", "") == html


def test_leaves_unrelated_urls():
    html = 'see https://cdn.shopify.com/files/xyz.png'
    assert remap_body_html(html, "a", "b") == html
