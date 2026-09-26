"""Link-only access (api/access.py). No database needed: the checks run before any route."""

import pytest
from fastapi.testclient import TestClient

from api.access import COOKIE, cookie_value, share_link
from api.main import app

TOKEN = "s3cret-team-token"


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("ACCESS_TOKEN", TOKEN)
    monkeypatch.delenv("ALLOW_OPEN_ACCESS", raising=False)
    monkeypatch.delenv("COOKIE_SECURE", raising=False)
    return TestClient(app, follow_redirects=False)


def test_without_the_link_everything_is_refused(client):
    assert client.get("/api/search").status_code == 401
    assert client.get("/api/programs/0604181C").status_code == 401
    page = client.get("/program/0604181C")
    assert page.status_code == 401 and "This app is private" in page.text


def test_wrong_secret_is_not_found_and_sets_nothing(client):
    resp = client.get("/?k=guess")
    assert resp.status_code == 404
    assert COOKIE not in resp.cookies


def test_link_sets_cookie_and_drops_secret_from_address(client):
    resp = client.get(f"/program/0604181C?q=x&k={TOKEN}")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/program/0604181C?q=x"
    assert TOKEN not in resp.headers["location"]
    set_cookie = resp.headers["set-cookie"]
    assert f"{COOKIE}={cookie_value(TOKEN)}" in set_cookie
    assert "HttpOnly" in set_cookie and "SameSite=lax" in set_cookie
    assert TOKEN not in set_cookie                      # the cookie holds a digest, not the secret
    # the cookie now opens everything (a 404 from the router means access was granted)
    client.cookies.set(COOKIE, cookie_value(TOKEN))
    assert client.get("/api/does-not-exist").status_code == 404


def test_cookie_secure_behind_https(client):
    resp = client.get(f"/?k={TOKEN}", headers={"x-forwarded-proto": "https"})
    assert "Secure" in resp.headers["set-cookie"]


def test_header_for_scripts(client):
    assert client.get("/api/does-not-exist", headers={"x-access-token": TOKEN}).status_code == 404
    assert client.get("/api/does-not-exist", headers={"x-access-token": "nope"}).status_code == 401


def test_rotating_the_secret_revokes_old_cookies(client, monkeypatch):
    client.cookies.set(COOKIE, cookie_value(TOKEN))
    monkeypatch.setenv("ACCESS_TOKEN", "the-new-secret")
    assert client.get("/api/does-not-exist").status_code == 401


def test_health_and_robots_are_open_and_nothing_is_indexed(client):
    robots = client.get("/robots.txt")
    assert robots.status_code == 200 and "Disallow: /" in robots.text
    resp = client.get("/api/search")
    assert resp.headers["x-robots-tag"] == "noindex, nofollow"
    assert resp.headers["referrer-policy"] == "no-referrer"


def test_refuses_to_run_open_without_a_secret(client, monkeypatch):
    monkeypatch.delenv("ACCESS_TOKEN")
    assert client.get("/api/search").status_code == 503
    monkeypatch.setenv("ALLOW_OPEN_ACCESS", "1")         # explicit local-development opt-in
    assert client.get("/api/does-not-exist").status_code == 404


def test_share_link():
    assert share_link("https://budget.example.com/", "a b") == "https://budget.example.com/?k=a+b"


@pytest.mark.parametrize("given, expected", [
    ("postgresql://u:p@ep-x.neon.tech/db?sslmode=require", "postgresql+psycopg://u:p@ep-x.neon.tech/db?sslmode=require"),
    ("postgres://u@h/db", "postgresql+psycopg://u@h/db"),
    ("postgresql+psycopg://u@/db?host=/tmp", "postgresql+psycopg://u@/db?host=/tmp"),
    ("psql 'postgresql://u:p@ep-x.neon.tech/db?sslmode=require&channel_binding=require'",
     "postgresql+psycopg://u:p@ep-x.neon.tech/db?sslmode=require&channel_binding=require"),
    ('  "postgresql://u@h/db"\n', "postgresql+psycopg://u@h/db"),
])
def test_hosted_database_urls_use_psycopg(monkeypatch, given, expected):
    from pipeline.config import database_url
    monkeypatch.setenv("DATABASE_URL", given)
    assert database_url() == expected


def test_bad_database_url_explains_without_leaking(monkeypatch):
    from pipeline.config import database_url
    monkeypatch.setenv("DATABASE_URL", "neondb_owner:secretpw@ep-x.neon.tech/neondb")
    with pytest.raises(ValueError) as e:
        database_url()
    assert "postgresql://" in str(e.value) and "secretpw" not in str(e.value)
