"""Fetch and parse France Inter watched films from Letterboxd."""

from __future__ import annotations

import os
import re
from types import SimpleNamespace
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from curl_cffi import requests as curl_requests

BASE_URL = "https://letterboxd.com"
FILMS_PATH = "/franceinter/films/"
# TLS fingerprint impersonation helps pass Letterboxd/Cloudflare on CI runners.
CURL_IMPERSONATE = os.environ.get("FRList_CURL_IMPERSONATE", "chrome124")
FILM_HREF_RE = re.compile(r"^/film/([^/]+)/$")
YEAR_FROM_SLUG_RE = re.compile(r"-(\d{4})$")


class ScrapeError(RuntimeError):
    """Raised when the page cannot be fetched or parsed."""


def _session() -> Any:
    return curl_requests.Session()


def _get(session: Any, url: str, *, timeout: float) -> Any:
    return session.get(url, timeout=timeout, impersonate=CURL_IMPERSONATE)


def _cf_interstitial(html: str) -> bool:
    return "Just a moment" in html and "challenge-platform" in html


def _playwright_fetch(url: str) -> str:
    """Headless browser fetch for GitHub Actions when Cloudflare blocks curl_cffi."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise ScrapeError(
            "Playwright is required when Letterboxd serves a Cloudflare challenge on CI. "
            "Install: pip install playwright && playwright install chromium"
        ) from e

    _ua = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        try:
            context = browser.new_context(
                user_agent=_ua,
                viewport={"width": 1280, "height": 720},
                locale="en-US",
            )
            context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            )
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=120000)
            for _ in range(40):
                page.wait_for_timeout(2000)
                html = page.content()
                if '[data-item-link="' in html and "/film/" in html:
                    break
            else:
                html = page.content()
            context.close()
        finally:
            browser.close()

    if _cf_interstitial(html):
        raise ScrapeError(
            f"Cloudflare still blocked after Playwright fetch for {url!r}."
        )
    return html


def _get_html_response(session: Any, url: str, *, timeout: float) -> Any:
    """HTTP via curl_cffi; on GitHub Actions fall back to Playwright if CF interstitial."""
    try:
        r = _get(session, url, timeout=timeout)
    except Exception as e:
        if os.environ.get("GITHUB_ACTIONS") == "true":
            html = _playwright_fetch(url)
            return SimpleNamespace(status_code=200, text=html, ok=True)
        raise ScrapeError(f"Network error fetching {url!r}: {e}") from e

    if _cf_interstitial(r.text) and os.environ.get("GITHUB_ACTIONS") == "true":
        html = _playwright_fetch(url)
        return SimpleNamespace(status_code=200, text=html, ok=True)

    _raise_if_blocked(r.text, url)
    if r.status_code == 404:
        return r
    if not r.ok:
        raise ScrapeError(
            f"HTTP {r.status_code} when fetching {url!r}. "
            "Letterboxd may be blocking automated access from this network."
        )
    return r


def _raise_if_blocked(html: str, url: str) -> None:
    if _cf_interstitial(html):
        raise ScrapeError(
            f"Letterboxd returned a Cloudflare challenge for {url!r}. "
            "Retry later or run from a network that can complete the challenge."
        )
    if len(html) < 5000 and "Enable JavaScript and cookies" in html:
        raise ScrapeError(f"Unexpected challenge or blocked response for {url!r}.")


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

    return {
        "id": sid,
        "title": title,
        "year": year,
        "letterboxd_url": letterboxd_url,
        "poster": poster,
        "film_path": film_path,
    }


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

        title = (el.get("data-item-full-display-name") or el.get("data-item-name") or "").strip() or None
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


def fetch_films_listing(session: Any) -> list[dict[str, Any]]:
    """Fetch all films from paginated /films/ listing."""
    by_id: dict[str, dict[str, Any]] = {}
    page = 1

    while True:
        path = FILMS_PATH if page == 1 else f"{FILMS_PATH.rstrip('/')}/page/{page}/"
        url = urljoin(BASE_URL, path)
        r = _get_html_response(session, url, timeout=60.0)
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

    return list(by_id.values())


def enrich_meta(session: Any, film: dict[str, Any]) -> dict[str, Any]:
    """Fetch film page for og:image poster and optional title/year (local / curl-friendly paths)."""
    if film.get("poster"):
        return film
    url = film["letterboxd_url"]
    try:
        r = _get(session, url, timeout=45.0)
        r.raise_for_status()
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


def _playwright_batch_posters(films: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One Chromium session: open each film page and read og:image (for CI when listing had no real posters)."""
    from playwright.sync_api import sync_playwright

    result = [dict(m) for m in films]
    idxs = [i for i, m in enumerate(result) if not m.get("poster")]
    if not idxs:
        return result

    _ua = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        try:
            context = browser.new_context(
                user_agent=_ua,
                viewport={"width": 1280, "height": 720},
                locale="en-US",
            )
            context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            )
            page = context.new_page()
            for i in idxs:
                url = result[i]["letterboxd_url"]
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=90000)
                    page.wait_for_timeout(2500)
                    soup = BeautifulSoup(page.content(), "html.parser")
                    pu = _poster_from_og(soup)
                    if pu:
                        result[i]["poster"] = pu
                except Exception:
                    continue
            context.close()
        finally:
            browser.close()

    return result


def enrich_missing_posters(films: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fill poster URLs from each film's Letterboxd page (og:image) when the listing only had placeholders."""
    missing = [m for m in films if not m.get("poster")]
    if not missing:
        return films

    if os.environ.get("GITHUB_ACTIONS") == "true":
        return _playwright_batch_posters(films)

    session = _session()
    return [enrich_meta(session, dict(m)) if not m.get("poster") else dict(m) for m in films]


def scrape_franceinter_films(*, enrich: bool = False) -> list[dict[str, Any]]:
    """
    Scrape https://letterboxd.com/franceinter/films/ (all pages).
    When enrich=True, GET each film page once to improve poster/title/year.
    """
    session = _session()
    films = fetch_films_listing(session)
    films.sort(key=lambda m: (m["title"].lower(), m.get("year") or 0, m["id"]))

    if enrich:
        enriched = []
        for m in films:
            enriched.append(enrich_meta(session, dict(m)))
        films = enriched
        films.sort(key=lambda x: (x["title"].lower(), x.get("year") or 0, x["id"]))

    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for m in films:
        if m["id"] in seen:
            continue
        seen.add(m["id"])
        deduped.append(m)

    return deduped
