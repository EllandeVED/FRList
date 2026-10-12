"""Fetch and parse France Inter watched films from Letterboxd."""

from __future__ import annotations

import os
import re
import time
import xml.etree.ElementTree as ET
from types import SimpleNamespace
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from curl_cffi import requests as curl_requests

BASE_URL = "https://letterboxd.com"
FILMS_PATH = "/franceinter/films/"
RSS_PATH = "/franceinter/rss/"
# Prefer the rolling "chrome" profile; pin via env if a runner breaks on latest.
CURL_IMPERSONATE = os.environ.get("FRList_CURL_IMPERSONATE", "chrome")
FILM_HREF_RE = re.compile(r"^/film/([^/]+)/$")
YEAR_FROM_SLUG_RE = re.compile(r"-(\d{4})$")
LB_NS = "https://letterboxd.com"
TMDB_NS = "https://themoviedb.org"

_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
window.chrome = window.chrome || { runtime: {} };
"""


class ScrapeError(RuntimeError):
    """Raised when the page cannot be fetched or parsed."""


def _session() -> Any:
    return curl_requests.Session()


def _cf_interstitial(html: str) -> bool:
    low = html[:8000]
    return (
        ("Just a moment" in low and "challenge-platform" in low)
        or "cf-mitigated" in low
        or ("Enable JavaScript and cookies to continue" in low and len(html) < 20000)
    )


def _has_film_grid(html: str) -> bool:
    return 'data-item-link="/film/' in html or 'href="/film/' in html


def _raise_if_blocked(html: str, url: str) -> None:
    if _cf_interstitial(html):
        raise ScrapeError(
            f"Letterboxd returned a Cloudflare challenge for {url!r}. "
            "Retry later or run from a network that can complete the challenge."
        )
    if len(html) < 5000 and "Enable JavaScript and cookies" in html:
        raise ScrapeError(f"Unexpected challenge or blocked response for {url!r}.")


def _playwright_available() -> bool:
    try:
        import playwright  # noqa: F401

        return True
    except ImportError:
        return False


def _page_looks_ready(html: str, url: str) -> bool:
    if _cf_interstitial(html):
        return False
    if "/rss" in url or url.rstrip("/").endswith("rss"):
        return "<item>" in html or "<rss" in html
    if "/film/" in url and "/films/" not in url:
        return len(html) > 5000
    return _has_film_grid(html)


def _browser_bootstrap(url: str) -> tuple[str, list[dict[str, Any]]]:
    """
    Open Letterboxd in Chromium, wait out Cloudflare if needed, return HTML + cookies.
    On CI we prefer headed mode under xvfb — headless Chromium is flagged more often.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise ScrapeError(
            "Playwright is required when Letterboxd serves a Cloudflare challenge. "
            "Install: pip install playwright && playwright install chromium"
        ) from e

    headed = os.environ.get("FRList_HEADED", "").lower() in {"1", "true", "yes"}
    if os.environ.get("GITHUB_ACTIONS") == "true" and "FRList_HEADED" not in os.environ:
        # Default to headed on Actions when a display (xvfb) is available.
        headed = bool(os.environ.get("DISPLAY"))

    ua = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )
    launch_args = [
        "--disable-dev-shm-usage",
        "--no-sandbox",
        "--disable-blink-features=AutomationControlled",
    ]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not headed, args=launch_args)
        try:
            context = browser.new_context(
                user_agent=ua,
                viewport={"width": 1365, "height": 900},
                locale="en-US",
                timezone_id="Europe/Paris",
                color_scheme="light",
            )
            context.add_init_script(_STEALTH_JS)
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=120_000)

            html = page.content()
            deadline = time.time() + 90
            while time.time() < deadline:
                if _page_looks_ready(html, url):
                    break
                page.wait_for_timeout(1500)
                # Nudge the page — some managed challenges settle after interaction.
                try:
                    page.mouse.move(120, 160)
                except Exception:
                    pass
                html = page.content()

            cookies = context.cookies()
            context.close()
        finally:
            browser.close()

    if not _page_looks_ready(html, url):
        raise ScrapeError(
            f"Cloudflare still blocked after browser fetch for {url!r}."
        )
    return html, cookies


def _apply_cookies(session: Any, cookies: list[dict[str, Any]]) -> None:
    for c in cookies:
        name = c.get("name")
        value = c.get("value")
        if not name or value is None:
            continue
        try:
            session.cookies.set(
                name,
                value,
                domain=c.get("domain") or ".letterboxd.com",
                path=c.get("path") or "/",
            )
        except Exception:
            try:
                session.cookies.set(name, value)
            except Exception:
                continue


class LetterboxdClient:
    """HTTP client that bootstraps past Cloudflare once, then reuses the session."""

    def __init__(self) -> None:
        self.session = _session()
        self._bootstrapped = False

    def _curl_get(self, url: str, *, timeout: float) -> Any:
        return self.session.get(
            url,
            timeout=timeout,
            impersonate=CURL_IMPERSONATE,
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9,fr;q=0.8",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
                "Upgrade-Insecure-Requests": "1",
            },
        )

    def ensure_access(self, url: str, *, timeout: float = 60.0) -> str:
        """Return HTML for url, bootstrapping a browser session when CF challenges."""
        try:
            r = self._curl_get(url, timeout=timeout)
            html = r.text
            if r.status_code == 404:
                return html
            if r.ok and not _cf_interstitial(html):
                self._bootstrapped = True
                return html
        except Exception as e:
            if not _playwright_available():
                raise ScrapeError(f"Network error fetching {url!r}: {e}") from e

        # Browser path (CI or when curl is challenged).
        html, cookies = _browser_bootstrap(url)
        _apply_cookies(self.session, cookies)
        self._bootstrapped = True
        return html

    def get_html(self, url: str, *, timeout: float = 60.0) -> Any:
        if not self._bootstrapped:
            html = self.ensure_access(url, timeout=timeout)
            return SimpleNamespace(status_code=200, text=html, ok=True)

        try:
            r = self._curl_get(url, timeout=timeout)
        except Exception as e:
            raise ScrapeError(f"Network error fetching {url!r}: {e}") from e

        if _cf_interstitial(r.text):
            # Session expired — re-bootstrap and retry once.
            html, cookies = _browser_bootstrap(url)
            _apply_cookies(self.session, cookies)
            return SimpleNamespace(status_code=200, text=html, ok=True)

        if r.status_code == 404:
            return r
        if not r.ok:
            raise ScrapeError(
                f"HTTP {r.status_code} when fetching {url!r}. "
                "Letterboxd may be blocking automated access from this network."
            )
        _raise_if_blocked(r.text, url)
        return r


def _poster_from_og(soup: BeautifulSoup) -> str | None:
    og = soup.select_one('meta[property="og:image"][content]')
    if not og:
        return None
    raw = (og.get("content") or "").strip()
    if not raw or "empty-poster" in raw.lower():
        return None
    return urljoin(BASE_URL, raw)


def _slug_to_id(slug: str) -> str:
    return slug.strip().lower()


def _year_from_slug(slug: str) -> int | None:
    m = YEAR_FROM_SLUG_RE.search(slug)
    if m:
        return int(m.group(1))
    return None


def _extract_title_year_poster(
    soup: BeautifulSoup, film_url: str, slug: str
) -> tuple[str, int | None, str | None]:
    title = None
    year = _year_from_slug(slug)
    poster = None

    poster_el = soup.select_one(".film-poster img[src], .react-component img[src]")
    if poster_el:
        poster = poster_el.get("src")
        alt = (poster_el.get("alt") or "").strip()
        if alt:
            title = alt
    if not title:
        og = soup.select_one('meta[property="og:title"]')
        if og and og.get("content"):
            title = og["content"].strip()
    if not title:
        t = soup.select_one("h1.headline-1, h1.film-title, .film-title a")
        if t:
            title = t.get_text(strip=True)
    if not title:
        title = slug.replace("-", " ").title()

    if year is None:
        y_el = soup.select_one(".releaseyear a, span.releaseyear")
        if y_el:
            ym = re.search(r"\b(18|19|20)\d{2}\b", y_el.get_text())
            if ym:
                year = int(ym.group(0))

    if poster:
        poster = urljoin(BASE_URL, poster)

    return title, year, poster


def _movie_from_slug_href(
    slug: str,
    *,
    title_hint: str | None,
    poster_hint: str | None,
    tmdb_id: int | None = None,
) -> dict[str, Any]:
    sid = _slug_to_id(slug)
    title = (title_hint or "").strip() or None
    year = _year_from_slug(slug)
    if title:
        ym = re.search(r"\((\d{4})\)\s*$", title)
        if ym:
            year = int(ym.group(1))
    if not title:
        title = slug.replace("-", " ").title()

    poster = poster_hint
    if poster and "empty-poster" in poster:
        poster = None
    if poster:
        poster = urljoin(BASE_URL, poster)

    film_path = f"/film/{slug}/"
    letterboxd_url = urljoin(BASE_URL, film_path)

    out: dict[str, Any] = {
        "id": sid,
        "title": title,
        "year": year,
        "letterboxd_url": letterboxd_url,
        "poster": poster,
        "film_path": film_path,
    }
    if tmdb_id is not None:
        out["tmdb_id"] = tmdb_id
    return out


def _parse_films_from_listing(
    html: str, page_url: str, *, require_any: bool
) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    seen_slugs: set[str] = set()
    out: list[dict[str, Any]] = []

    # Current Letterboxd: React poster tiles use data-item-link, not <a href>.
    for el in soup.select('[data-item-link^="/film/"]'):
        href = (el.get("data-item-link") or "").strip()
        m = FILM_HREF_RE.match(href)
        if not m:
            continue
        slug = m.group(1)
        sid = _slug_to_id(slug)
        if sid in seen_slugs:
            continue
        seen_slugs.add(sid)

        title = (
            el.get("data-item-full-display-name") or el.get("data-item-name") or ""
        ).strip() or None
        img = el.find("img")
        poster = img.get("src") if img else None
        out.append(_movie_from_slug_href(slug, title_hint=title, poster_hint=poster))

    scope = soup.select_one("ul.poster-list") or soup.select_one("div#content")
    if scope is None:
        scope = soup
    for a in scope.select('a[href^="/film/"]'):
        href = a.get("href") or ""
        m = FILM_HREF_RE.match(href)
        if not m:
            continue
        slug = m.group(1)
        sid = _slug_to_id(slug)
        if sid in seen_slugs:
            continue
        seen_slugs.add(sid)

        img = a.find("img")
        title = None
        poster = None
        if img:
            title = (img.get("alt") or "").strip() or None
            poster = img.get("src")
        if not title:
            title = (a.get("title") or "").strip() or None
        if not title:
            span = a.find("span", class_="frame-title")
            if span:
                title = span.get_text(strip=True)

        out.append(_movie_from_slug_href(slug, title_hint=title, poster_hint=poster))

    if not out and require_any:
        raise ScrapeError(
            f"No film links found on listing page {page_url!r}. "
            "Letterboxd HTML may have changed or the response was not full HTML."
        )
    return out


def fetch_films_listing(client: LetterboxdClient | None = None) -> list[dict[str, Any]]:
    """Fetch all films from paginated /films/ listing."""
    client = client or LetterboxdClient()
    by_id: dict[str, dict[str, Any]] = {}
    page = 1

    while True:
        path = FILMS_PATH if page == 1 else f"{FILMS_PATH.rstrip('/')}/page/{page}/"
        url = urljoin(BASE_URL, path)
        r = client.get_html(url, timeout=60.0)
        html = r.text
        if r.status_code == 404 and page > 1:
            break

        page_films = _parse_films_from_listing(html, url, require_any=(page == 1))
        if not page_films:
            break

        before = len(by_id)
        for m in page_films:
            by_id[m["id"]] = m
        if len(by_id) == before:
            break

        page += 1
        if page > 500:
            raise ScrapeError("Pagination exceeded 500 pages; aborting.")
        # Small pause between pages — looks less like a burst from a datacenter IP.
        time.sleep(0.6)

    return list(by_id.values())


def fetch_films_from_rss(client: LetterboxdClient | None = None) -> list[dict[str, Any]]:
    """
    Recent watches from the public profile RSS feed.
    Cloudflare rarely challenges this endpoint; it only covers ~recent activity,
    not the full watched history.
    """
    client = client or LetterboxdClient()
    url = urljoin(BASE_URL, RSS_PATH)
    # Prefer plain curl first; RSS is usually allowed without a browser bootstrap.
    try:
        r = client.session.get(url, timeout=45.0, impersonate=CURL_IMPERSONATE)
        html = r.text
        if not r.ok or _cf_interstitial(html) or "<item>" not in html:
            html = client.ensure_access(url, timeout=60.0)
    except Exception:
        html = client.ensure_access(url, timeout=60.0)

    if _cf_interstitial(html) or "<item>" not in html:
        raise ScrapeError(f"Could not read Letterboxd RSS at {url!r}.")

    try:
        root = ET.fromstring(html)
    except ET.ParseError as e:
        raise ScrapeError(f"Invalid RSS XML from {url!r}: {e}") from e

    by_id: dict[str, dict[str, Any]] = {}
    for item in root.findall("./channel/item"):
        link = (item.findtext("link") or "").strip()
        # https://letterboxd.com/franceinter/film/<slug>/
        m = re.search(r"/film/([^/]+)/?", link)
        if not m:
            continue
        slug = m.group(1)
        title = (
            item.findtext(f"{{{LB_NS}}}filmTitle")
            or item.findtext("title")
            or ""
        ).strip()
        year_raw = item.findtext(f"{{{LB_NS}}}filmYear")
        year = int(year_raw) if year_raw and year_raw.isdigit() else _year_from_slug(slug)
        if title and year and not re.search(r"\(\d{4}\)\s*$", title):
            title = f"{title} ({year})"
        tmdb_raw = item.findtext(f"{{{TMDB_NS}}}movieId")
        tmdb_id = int(tmdb_raw) if tmdb_raw and tmdb_raw.isdigit() else None
        movie = _movie_from_slug_href(
            slug, title_hint=title or None, poster_hint=None, tmdb_id=tmdb_id
        )
        by_id[movie["id"]] = movie

    return list(by_id.values())


def enrich_meta(client: LetterboxdClient, film: dict[str, Any]) -> dict[str, Any]:
    """Fetch film page for og:image poster and optional title/year."""
    if film.get("poster"):
        return film
    url = film["letterboxd_url"]
    try:
        r = client.get_html(url, timeout=45.0)
        if not getattr(r, "ok", True):
            return film
    except Exception:
        return film
    html = r.text
    if _cf_interstitial(html):
        return film
    soup = BeautifulSoup(html, "html.parser")
    og_poster = _poster_from_og(soup)
    if og_poster:
        film["poster"] = og_poster
    title, year, poster = _extract_title_year_poster(soup, url, film["id"])
    if title:
        film["title"] = title
    if year is not None:
        film["year"] = year
    if poster and not film.get("poster"):
        film["poster"] = poster
    return film


def _playwright_batch_poster_indices(result: list[dict[str, Any]], idxs: list[int]) -> None:
    """Fill posters for result[i] for i in idxs using one browser session."""
    if not idxs:
        return
    from playwright.sync_api import sync_playwright

    ua = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )
    headed = bool(os.environ.get("DISPLAY")) and os.environ.get("GITHUB_ACTIONS") == "true"
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=not headed,
            args=[
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        try:
            context = browser.new_context(
                user_agent=ua,
                viewport={"width": 1280, "height": 720},
                locale="en-US",
            )
            context.add_init_script(_STEALTH_JS)
            page = context.new_page()
            for i in idxs:
                url = result[i]["letterboxd_url"]
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                    page.wait_for_timeout(800)
                    soup = BeautifulSoup(page.content(), "html.parser")
                    pu = _poster_from_og(soup)
                    if pu:
                        result[i]["poster"] = pu
                except Exception:
                    continue
            context.close()
        finally:
            browser.close()


def enrich_missing_posters(films: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fill poster URLs from each film's Letterboxd page when the listing had placeholders."""
    missing = [m for m in films if not m.get("poster")]
    if not missing:
        return films

    client = LetterboxdClient()
    result = [dict(m) for m in films]
    idxs = [i for i, m in enumerate(result) if not m.get("poster")]

    # Warm the session on the listing page first (sets CSRF / clearance when possible).
    try:
        client.ensure_access(urljoin(BASE_URL, FILMS_PATH), timeout=60.0)
    except Exception:
        pass

    for i in idxs:
        enrich_meta(client, result[i])

    still = [i for i in idxs if not result[i].get("poster")]
    if still and (
        os.environ.get("GITHUB_ACTIONS") == "true" or os.environ.get("FRList_BROWSER_POSTERS")
    ):
        _playwright_batch_poster_indices(result, still)
    return result


def _dedupe_sort(films: list[dict[str, Any]]) -> list[dict[str, Any]]:
    films = sorted(
        films, key=lambda m: (m["title"].lower(), m.get("year") or 0, m["id"])
    )
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for m in films:
        if m["id"] in seen:
            continue
        seen.add(m["id"])
        deduped.append(m)
    return deduped


def scrape_franceinter_films(*, enrich: bool = False) -> list[dict[str, Any]]:
    """
    Scrape https://letterboxd.com/franceinter/films/ (all pages).
    When enrich=True, GET each film page once to improve poster/title/year.
    """
    client = LetterboxdClient()
    films = fetch_films_listing(client)
    films = _dedupe_sort(films)

    if enrich:
        enriched = []
        for m in films:
            enriched.append(enrich_meta(client, dict(m)))
        films = _dedupe_sort(enriched)

    return films


def scrape_with_retries(
    *,
    attempts: int | None = None,
    pause_sec: float | None = None,
) -> list[dict[str, Any]]:
    """Full listing scrape with retries (useful on flaky CI IPs)."""
    attempts = attempts or int(os.environ.get("FRList_SCRAPE_ATTEMPTS", "3"))
    pause_sec = pause_sec if pause_sec is not None else float(
        os.environ.get("FRList_SCRAPE_PAUSE", "20")
    )
    last: Exception | None = None
    for i in range(1, max(1, attempts) + 1):
        try:
            return scrape_franceinter_films(enrich=False)
        except ScrapeError as e:
            last = e
            print(f"FRList: scrape attempt {i}/{attempts} failed: {e}", flush=True)
            if i < attempts:
                time.sleep(pause_sec)
    assert last is not None
    raise last
