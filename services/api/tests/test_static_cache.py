"""Static serving: the SPA HTML is revalidated on every load, hashed assets are cached for a year, HEAD works."""
import pytest

IMMUTABLE = "public, max-age=31536000, immutable"


@pytest.mark.parametrize("path", ["/", "/index.html", "/review", "/sample/demo-01", "/workspace/rules"])
def test_spa_html_is_no_cache(anon, path):
    r = anon.get(path)
    assert r.status_code == 200 and "<title>cc</title>" in r.text
    assert r.headers["cache-control"] == "no-cache"
    assert r.headers["content-type"].startswith("text/html")


def test_hashed_assets_are_immutable(anon):
    r = anon.get("/assets/app.js")
    assert r.status_code == 200 and r.text == "console.log(1)"
    assert r.headers["cache-control"] == IMMUTABLE


@pytest.mark.parametrize("path", ["/", "/review", "/sample/demo-01"])
def test_head_on_spa_routes_is_200_without_body(anon, path):
    r = anon.head(path)
    assert r.status_code == 200
    assert r.content == b""
    assert r.headers["cache-control"] == "no-cache"


def test_head_on_an_asset(anon):
    r = anon.head("/assets/app.js")
    assert r.status_code == 200 and r.content == b""
    assert r.headers["cache-control"] == IMMUTABLE


def test_api_keeps_no_store_and_unknown_api_paths_stay_404(anon):
    assert anon.get("/api/health").headers["cache-control"] == "no-store"
    assert anon.get("/api/nope").status_code == 404
    assert anon.head("/api/nope").status_code == 404
