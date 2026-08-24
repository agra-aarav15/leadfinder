"""Lead discovery engine - free public sources only.

Flow: search DuckDuckGo for businesses matching the niche -> visit each
website -> extract emails, phones, socials, location -> score -> store.
No API keys, no paid data providers.
"""
import base64
import logging
import random
import re
import time
from urllib.parse import parse_qs, urlparse

import httpx
from bs4 import BeautifulSoup

from . import browser_source
from . import db

log = logging.getLogger("leadfinder")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
PHONE_RE = re.compile(r"(?:\+?\d{1,3}[\s.-]?)?(?:\(\d{2,4}\)[\s.-]?)?\d{3}[\s.-]?\d{3,4}")
SOCIAL_HOSTS = {
    "instagram.com": "instagram",
    "facebook.com": "facebook",
    "twitter.com": "twitter",
    "x.com": "twitter",
    "linkedin.com": "linkedin",
}
BLOCKED_DOMAINS = (
    "google.", "youtube.com", "wikipedia.org", "amazon.", "facebook.com",
    "instagram.com", "twitter.com", "x.com", "linkedin.com", "reddit.com",
    "yelp.com", "tripadvisor.", "pinterest.", "tiktok.com", "bing.com",
    "duckduckgo.com", "medium.com", "quora.com",
    # directories / marketplaces that rank for everything but aren't leads
    "goodfirms.", "sortlist.", "clutch.co", "topdevelopers.", "50pros.",
    "upwork.", "fiverr.", "designrush.", "trustpilot.", "g2.com", "capterra.",
    "thumbtack.", "angi.com", "houzz.", "bark.com", "expertise.com", "porch.com",
)
BAD_EMAIL_PARTS = ("example.", "sentry.io", "wixpress", "@2x", ".png", ".jpg",
                   ".webp", ".gif", ".svg", "domain.com", "email.com", "yourname")


def _client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": UA}, timeout=12, follow_redirects=True)


def _resolve_and_filter(href: str, seen: set, out: list, max_results: int) -> None:
    """Normalize a result link to a bare domain, applying blocklist + dedupe."""
    if not href:
        return
    if "uddg=" in href:
        try:
            qs = parse_qs(urlparse(href if "://" in href else "https:" + href).query)
            href = qs.get("uddg", [href])[0]
        except Exception:
            pass
    host = urlparse(href).netloc.lower().replace("www.", "")
    if not host or host in seen or any(b in host for b in BLOCKED_DOMAINS):
        return
    seen.add(host)
    out.append(host)


def _decode_bing_redirect(href: str) -> str:
    """Bing wraps result URLs in /ck/a redirects: u=a1<base64-of-target>."""
    try:
        u = parse_qs(urlparse(href).query).get("u", [""])[0]
        if u.startswith("a1"):
            b64 = u[2:] + "=" * (-len(u[2:]) % 4)
            return base64.b64decode(b64).decode("utf-8", "ignore")
    except Exception:
        pass
    return ""


def _search_bing(cl: httpx.Client, query: str, max_results: int) -> list[str]:
    r = cl.get("https://www.bing.com/search", params={"q": query, "count": max(max_results, 10)})
    r.raise_for_status()
    out, seen = [], set()
    for a in BeautifulSoup(r.text, "html.parser").select("li.b_algo h2 a"):
        target = _decode_bing_redirect(a.get("href", "")) or a.get("href", "")
        _resolve_and_filter(target, seen, out, max_results)
        if len(out) >= max_results:
            break
    return out


def _search_ddg_html(cl: httpx.Client, query: str, max_results: int) -> list[str]:
    r = cl.post("https://html.duckduckgo.com/html/", data={"q": query})
    r.raise_for_status()
    out, seen = [], set()
    for a in BeautifulSoup(r.text, "html.parser").select("a.result__a"):
        _resolve_and_filter(a.get("href", ""), seen, out, max_results)
        if len(out) >= max_results:
            break
    return out


def _search_ddg_lite(cl: httpx.Client, query: str, max_results: int) -> list[str]:
    r = cl.post("https://lite.duckduckgo.com/lite/", data={"q": query})
    r.raise_for_status()
    out, seen = [], set()
    for a in BeautifulSoup(r.text, "html.parser").find_all("a", href=True):
        _resolve_and_filter(a["href"], seen, out, max_results)
        if len(out) >= max_results:
            break
    return out


ENGINES = None          # built lazily
_fail_until = 0.0       # global backoff timestamp when every engine fails


def ddg_search(query: str, max_results: int = 15) -> list[str]:
    """Free web search across multiple engines (no API keys). Returns domains."""
    global ENGINES, _fail_until
    if ENGINES is None:
        # DDG gives the cleanest results but throttles aggressively;
        # Bing still answers during DDG cool-downs (sometimes with junk).
        ENGINES = [_search_ddg_html, _search_ddg_lite, _search_bing]
    if time.time() < _fail_until:
        return []
    for engine in ENGINES:
        try:
            with _client() as cl:
                hosts = engine(cl, query, max_results)
            if hosts:
                return hosts
        except Exception as e:
            log.info(f"engine {engine.__name__} failed: {e}")
    _fail_until = time.time() + 600
    log.warning("all search engines refused - backing off for 10 minutes")
    return []


def crawl_site(domain: str) -> dict:
    """Visit a site (home + contact-ish pages) and pull everything useful."""
    info = {"domain": domain, "emails": set(), "phones": set(), "socials": {},
            "company": None, "description": None, "location": None}
    to_visit = [f"https://{domain}/"]
    checked = set()
    with _client() as cl:
        while to_visit and len(checked) < 4:
            url = to_visit.pop(0)
            if url in checked:
                continue
            checked.add(url)
            try:
                r = cl.get(url)
                if r.status_code != 200 or "text/html" not in r.headers.get("content-type", ""):
                    continue
                html = r.text
            except Exception:
                continue
            soup = BeautifulSoup(html, "html.parser")
            title = soup.title.get_text(strip=True) if soup.title else ""
            if not info["company"] and title:
                info["company"] = re.split(r"[|\-–—·]", title)[0].strip()[:80]
            meta = soup.find("meta", attrs={"name": "description"})
            if meta and not info["description"]:
                info["description"] = (meta.get("content") or "")[:300]

            text_blob = soup.get_text(" ", strip=True)[:20000]
            for e in EMAIL_RE.findall(html):
                el = e.lower()
                if not any(b in el for b in BAD_EMAIL_PARTS) and not el.endswith((".png", ".jpg")):
                    info["emails"].add(el)
            for p in PHONE_RE.findall(text_blob):
                digits = re.sub(r"\D", "", p)
                if 9 <= len(digits) <= 14:
                    info["phones"].add(p.strip())

            for a in soup.find_all("a", href=True):
                h = a["href"]
                low = h.lower()
                for host, name in SOCIAL_HOSTS.items():
                    if host in low and "share" not in low and "/sharer" not in low:
                        info["socials"].setdefault(name, h.split("?")[0][:150])
                page_path = urlparse(h).path.lower()
                if any(k in page_path for k in ("contact", "about", "impressum", "reach")):
                    base = f"https://{domain}"
                    full = h if h.startswith("http") else base + (h if h.startswith("/") else "/" + h)
                    if urlparse(full).netloc.replace("www.", "") == domain.replace("www.", ""):
                        to_visit.append(full)

            m = re.search(
                r"(\d{1,5}\s+[\w\s,.]+(?:Street|St\.?|Avenue|Ave|Road|Rd|Boulevard|Blvd|Suite|Ste)\b[^,<|\n]{0,40})",
                text_blob, re.I,
            )
            if m and not info["location"]:
                info["location"] = m.group(1).strip()[:120]
    info["emails"] = sorted(info["emails"])
    info["phones"] = sorted(info["phones"])[:2]
    return info


def score_lead(info: dict, niche_words: list[str]) -> int:
    s = 10
    if info.get("emails"):
        s += 35
    if info.get("phones"):
        s += 20
    if info.get("socials"):
        s += 10
    blob = ((info.get("description") or "") + " " + (info.get("company") or "")).lower()
    hits = sum(1 for w in niche_words if w and w in blob)
    s += min(hits * 8, 24)
    if info.get("location"):
        s += 5
    return min(s, 99)


def discover(niche: str, location: str = "", max_leads: int = 25, progress=None) -> dict:
    """Full discovery run. Source 1: Google Maps (best for local businesses).
    Source 2: multi-engine web search top-up. progress(done,total,msg) optional."""
    settings = db.get_settings()
    niche_words = [w for w in re.split(r"\W+", niche.lower()) if len(w) > 2]
    created = updated = 0
    checked = 0

    def store(info: dict, source: str) -> bool:
        nonlocal created, updated
        emails = info.get("emails") or []
        primary_email = next((e for e in emails
                              if not any(x in e for x in ("noreply", "no-reply", "donotreply"))),
                             emails[0] if emails else None)
        dom = info.get("domain")
        if db.is_opted_out(primary_email or "", dom or ""):
            return False
        # places without a website or email dedupe on company name
        if not dom and not primary_email and info.get("company"):
            if db.find_lead_by_company(info["company"]):
                return False
        score = score_lead(info, niche_words)
        lid, is_new = db.upsert_lead({
            "source": source,
            "company": info.get("company") or dom,
            "domain": dom,
            "email": primary_email,
            "phone": (info.get("phones") or [None])[0],
            "socials": info.get("socials") or {},
            "location": info.get("location"),
            "niche": niche,
            "score": score,
        })
        if is_new:
            created += 1
            db.log_activity("discover", f"new lead: {info.get('company') or dom} (score {score})", lid)
        else:
            updated += 1
        return True

    # ---- source 1: Google Maps ----
    if location and settings.get("use_maps", "1") == "1" and browser_source.maps_available():
        try:
            places = browser_source.maps_places(
                niche, location, min(max_leads, 15),
                progress=lambda d, t, m: progress(d, t, m) if progress else None,
            )
            for place in places:
                if created + updated >= max_leads:
                    break
                checked += 1
                info = {
                    "domain": place.get("domain"),
                    "emails": [],
                    "phones": [place["phone"]] if place.get("phone") else [],
                    "socials": {},
                    "company": place.get("company"),
                    "description": None,
                    "location": place.get("address"),
                }
                if info["domain"]:
                    crawled = crawl_site(info["domain"])
                    info.update({"emails": crawled["emails"], "socials": crawled["socials"]})
                    info["description"] = crawled.get("description")
                    if not info["location"]:
                        info["location"] = crawled.get("location")
                store(info, "maps")
        except Exception as e:
            log.warning(f"maps source failed: {e}")
            db.log_activity("discover", f"google maps skipped this run ({str(e)[:100]})")

    # ---- source 2: web search engines (top-up) ----
    if created + updated < max_leads:
        queries = [
            f"{niche} {location}".strip(),
            f"best {niche} near {location}" if location else f"{niche} company website",
        ]
        domains, seen = [], set()
        for i, q in enumerate(queries):
            if i:  # polite spacing so the search endpoint doesn't throttle us
                time.sleep(random.uniform(3, 6))
            found = ddg_search(q, max_results=max_leads)
            for d in found:
                if d not in seen:
                    seen.add(d)
                    domains.append(d)
            if len(domains) >= max_leads * 2:
                break

        for dom in domains[: max_leads * 2]:
            if created + updated >= max_leads:
                break
            checked += 1
            store(crawl_site(dom), "discovery")

    summary = f"discovery done - {created} new, {updated} updated leads"
    db.log_activity("discover", summary)
    return {"created": created, "updated": updated, "checked": checked}
