"""Google Maps lead source via headless Chromium (free, no API keys).

Finds local businesses matching niche+location directly on the map:
name, address, phone and website for each place. The website is then
handed back to discovery.crawl_site() for emails/socials enrichment.

Gracefully degrades: if Playwright/Chromium isn't installed, callers
fall back to the plain-HTTP search engine path.
"""
import logging
import re
from urllib.parse import quote_plus

log = logging.getLogger("leadfinder")


def maps_available() -> bool:
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False


def _clean_label(label: str, prefixes: tuple) -> str | None:
    label = (label or "").strip()
    for p in prefixes:
        if label.lower().startswith(p.lower()):
            return label[len(p):].strip()
    return label or None


def maps_places(niche: str, location: str, max_places: int = 12,
                progress=None) -> list[dict]:
    """Scrape Google Maps. Returns [{company, domain, phone, address}, ...]."""
    from playwright.sync_api import sync_playwright

    query = f"{niche} in {location}".strip()
    url = f"https://www.google.com/maps/search/{quote_plus(query)}?hl=en"
    places: list[dict] = []
    seen_names: set[str] = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(locale="en-US", viewport={"width": 1400, "height": 900})
        try:
            page.goto(url, timeout=60000)
            page.wait_for_selector("a.hfpxzc", timeout=25000)

            # scroll the results feed until we have enough cards or hit the end
            for _ in range(40):
                count = page.locator("a.hfpxzc").count()
                if progress and count:
                    progress(count, max_places, f"maps: {count} places found")
                if count >= max_places * 2:
                    break
                ended = page.locator("span.HlvSq", has_text="reached the end").count()
                if ended:
                    break
                before = count
                page.eval_on_selector(
                    'div[role="feed"]', "el => el.scrollBy(0, el.scrollHeight)")
                page.wait_for_timeout(1500)
                if page.locator("a.hfpxzc").count() == before == 0:
                    break

            cards = page.eval_on_selector_all(
                "a.hfpxzc",
                "els => els.map(e => ({name: e.getAttribute('aria-label') || '', href: e.href}))",
            )

            for card in cards:
                if len(places) >= max_places:
                    break
                name = (card.get("name") or "").strip()
                if not name or name.lower() in seen_names:
                    continue
                seen_names.add(name.lower())
                info = {"company": name[:100], "domain": None, "phone": None, "address": None}
                try:
                    page.goto(card["href"], timeout=45000)
                    page.wait_for_timeout(2500)
                    web = page.locator('a[data-item-id="authority"]').first
                    if web.count():
                        host = re.sub(r"^https?://(www\.)?", "", web.get_attribute("href") or "")
                        info["domain"] = host.split("/")[0].split("?")[0].lower() or None
                    addr = page.locator('button[data-item-id="address"]').first
                    if addr.count():
                        info["address"] = _clean_label(addr.get_attribute("aria-label"), ("Address:",))[:160]
                    phone = page.locator('button[data-item-id^="phone"]:not([data-item-id="phone:tab"])').first
                    if not phone.count():
                        phone = page.locator('button[data-item-id^="phone"]').first
                    if phone.count():
                        info["phone"] = _clean_label(phone.get_attribute("aria-label"), ("Phone:",))
                except Exception as e:
                    log.info(f"maps detail fetch failed for '{name}': {e}")
                places.append(info)
                if progress:
                    progress(len(places), max_places,
                             f"maps: {name} -> {info['domain'] or 'no website'}")
        finally:
            browser.close()
    return places
