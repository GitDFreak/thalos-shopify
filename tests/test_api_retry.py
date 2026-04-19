"""Tests for ShopifyAPI retry + rate-limit behavior."""
from unittest.mock import patch, MagicMock
import pytest

from thalos.api import ShopifyAPI


@pytest.fixture
def api():
    return ShopifyAPI("test-shop", "shpat_xyz", api_version="2026-01")


def _resp(status=200, json_body=None, headers=None):
    r = MagicMock()
    r.status_code = status
    r.headers = headers or {}
    r.json = MagicMock(return_value=json_body or {})
    r.text = ""
    return r


def test_retries_on_429_then_succeeds(api):
    responses = [_resp(429, headers={"Retry-After": "0"}), _resp(200, {"shop": {"id": 1}})]
    with patch("time.sleep"), patch.object(api.session, "request", side_effect=responses) as mock_req:
        r = api.get("shop.json")
    assert r.status_code == 200
    assert mock_req.call_count == 2


def test_retries_on_5xx_then_succeeds(api):
    responses = [_resp(502), _resp(503), _resp(200, {"shop": {}})]
    with patch("time.sleep"), patch.object(api.session, "request", side_effect=responses) as mock_req:
        r = api.get("shop.json")
    assert r.status_code == 200
    assert mock_req.call_count == 3


def test_4xx_not_retried(api):
    responses = [_resp(404, json_body={"errors": "not found"})]
    with patch("time.sleep"), patch.object(api.session, "request", side_effect=responses) as mock_req:
        r = api.get("missing.json")
    assert r.status_code == 404
    assert mock_req.call_count == 1
