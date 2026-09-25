"""Browser smoke test of the built web app. Runs only when E2E_BASE_URL points at a running
server with data loaded (e.g. `uvicorn api.main:app` after `budget ingest-all --publish`):

    E2E_BASE_URL=http://localhost:8000 E2E_ACCESS_TOKEN=<the server's ACCESS_TOKEN> pytest tests/e2e
Set E2E_CHROMIUM to a Chromium binary if Playwright's own browser is not installed.
"""

import os

import pytest

BASE = os.environ.get("E2E_BASE_URL")
pytestmark = pytest.mark.skipif(not BASE, reason="E2E_BASE_URL not set")


@pytest.fixture(scope="module")
def page():
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get("E2E_CHROMIUM") or None)
        pg = browser.new_page(viewport={"width": 1400, "height": 900})
        # enter through the team link once; the cookie carries the rest of the session
        token = os.environ.get("E2E_ACCESS_TOKEN")
        if token:
            pg.goto(f"{BASE}/?k={token}")
        errors = []
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        pg.errors = errors
        yield pg
        browser.close()


def test_search_filter_sort_and_links(page):
    page.goto(BASE)
    page.wait_for_selector("table.results tbody tr")
    assert "lines" in page.inner_text(".summary")

    page.fill("input[type=search]", "hypersonic")
    page.wait_for_url("**q=hypersonic**")
    page.wait_for_selector("mark")                      # highlighted description snippet
    assert "hypersonic" in page.locator(".program .title").first.inner_text().lower()

    page.get_by_role("radio", name="Procurement (P-1)").click()
    page.fill("input[type=search]", "")
    page.wait_for_url("**exhibit=P-1**")
    page.get_by_role("button", name="FY", exact=False).nth(2).click()      # sort by budget year
    page.wait_for_timeout(500)
    link = page.locator("td.source a").first
    assert "#page=" in link.get_attribute("href") and link.get_attribute("target") == "_blank"
    assert page.errors == []


def test_state_survives_reload(page):
    page.goto(f"{BASE}/?q=submarine&exhibit=P-1&service=Navy")
    page.wait_for_selector("table.results tbody tr")
    assert page.input_value("input[type=search]") == "submarine"
    assert page.get_by_role("radio", name="Procurement (P-1)").get_attribute("aria-checked") == "true"
    assert page.get_by_role("button", name="Navy", exact=True).get_attribute("aria-pressed") == "true"


def test_no_horizontal_scroll_on_phone(page):
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(BASE)
    page.wait_for_selector("table.results")
    assert page.evaluate("document.documentElement.scrollWidth") <= 390


def test_program_page_chart_history_and_watch(page):
    page.goto(f"{BASE}/?q=0604181C")
    page.wait_for_selector("table.results tbody tr")
    page.locator(".program .title a").first.click()
    page.wait_for_selector(".chart svg .bar")
    assert "/program/" in page.url
    assert page.locator(".legend li").count() >= 2
    page.locator(".chart .bar").last.hover()
    assert "FY" in page.inner_text(".tooltip")
    assert page.locator(".matrix tbody tr").count() >= 3
    assert page.locator(".refs a").count() >= 1
    watch = page.locator("button.watch")
    before = watch.get_attribute("aria-pressed")
    watch.click()
    page.wait_for_function(f"document.querySelector('button.watch').getAttribute('aria-pressed') !== '{before}'")
    watch.click()      # restore
    page.wait_for_function(f"document.querySelector('button.watch').getAttribute('aria-pressed') === '{before}'")
    page.go_back()
    page.wait_for_selector("table.results")
    assert page.input_value("input[type=search]") == "0604181C"


def test_watchlist_page(page):
    page.goto(f"{BASE}/program/0604181C")
    page.wait_for_selector("button.watch")
    was_watched = page.get_attribute("button.watch", "aria-pressed") == "true"
    if not was_watched:
        page.click("button.watch")
        page.wait_for_selector("button.watch.on")
    page.get_by_role("link", name="★ Team watchlist").click()
    page.wait_for_selector("text=Hypersonic Defense")
    assert page.locator(".spark").count() >= 1
    if not was_watched:          # leave the watchlist as it was
        page.locator("tr", has_text="Hypersonic Defense").get_by_role("button", name="Remove").click()
        page.wait_for_timeout(500)


def test_private_without_the_link(page):
    """A fresh browser context, without the cookie, sees the private page."""
    if not os.environ.get("E2E_ACCESS_TOKEN"):
        pytest.skip("server may be running open (no E2E_ACCESS_TOKEN)")
    ctx = page.context.browser.new_context()
    try:
        fresh = ctx.new_page()
        resp = fresh.goto(f"{BASE}/program/0604181C")
        assert resp.status == 401 and "private" in fresh.inner_text("h1").lower()
    finally:
        ctx.close()
