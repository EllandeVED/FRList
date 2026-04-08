"""Fetch and parse France Inter watched films from Letterboxd."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://letterboxd.com"
FILMS_PATH = "/franceinter/films/"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
FILM_HREF_RE = re.compile(r"^/film/([^/]+)/$")
YEAR_FROM_SLUG_RE = re.compile(r"-(\d{4})$")


class ScrapeError(RuntimeError):
    """Raised when the page cannot be fetched or parsed."""


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,*/*;q=0.8"
            ),
            "Accept-Language": "en-US,en;q=0.9,fr;q=0.8",
            "Cache-Control": "no-cache",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
        }
    )
    return s


def _raise_if_blocked(html: str, url: str) -> None:
    if "Just a moment" in html and "challenge-platform" in html:
        raise ScrapeError(
            f"Letterboxd returned a Cloudflare challenge for {url!r}. "
            "Retry later or run from a network that can complete the challenge."
        )
    if len(html) < 5000 and "Enable JavaScript and cookies" in html:
        raise ScrapeError(f"Unexpected challenge or blocked response for {url!r}.")


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


def _parse_films_from_listing(
    html: str, page_url: str, *, require_any: bool
) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    seen_slugs: set[str] = set()
    out: list[dict[str, Any]] = []

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

        year = _year_from_slug(slug)
        if not title:
            title = slug.replace("-", " ").title()

        if poster:
            poster = urljoin(BASE_URL, poster)

        film_path = f"/film/{slug}/"
        letterboxd_url = urljoin(BASE_URL, film_path)

        out.append(
            {
                "id": sid,
                "title": title,
                "year": year,
                "letterboxd_url": letterboxd_url,
                "poster": poster,
                "film_path": film_path,
            }
        )

    if not out and require_any:
        raise ScrapeError(
            f"No film links found on listing page {page_url!r}. "
            "Letterboxd HTML may have changed or the response was not full HTML."
        )
    return out


def fetch_films_listing(session: requests.Session) -> list[dict[str, Any]]:
    """Fetch all films from paginated /films/ listing."""
    by_id: dict[str, dict[str, Any]] = {}
    page = 1

    while True:
        path = FILMS_PATH if page == 1 else f"{FILMS_PATH.rstrip('/')}/page/{page}/"
        url = urljoin(BASE_URL, path)
        try:
            r = session.get(url, timeout=60)
        except requests.RequestException as e:
            raise ScrapeError(f"Network error fetching {url!r}: {e}") from e

        html = r.text
        _raise_if_blocked(html, url)
        if r.status_code == 404 and page > 1:
            break
        if not r.ok:
            raise ScrapeError(
                f"HTTP {r.status_code} when fetching {url!r}. "
                "Letterboxd may be blocking automated access from this network."
            )

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


def enrich_meta(session: requests.Session, film: dict[str, Any]) -> dict[str, Any]:
    """Optional per-film page fetch for poster/title/year if missing from listing."""
    url = film["letterboxd_url"]
    try:
        r = session.get(url, timeout=45)
        r.raise_for_status()
    except requests.RequestException:
        return film
    html = r.text
    if "Just a moment" in html:
        return film
    soup = BeautifulSoup(html, "html.parser")
    title, year, poster = _extract_title_year_poster(soup, url, film["id"])
    if title:
        film["title"] = title
    if year is not None:
        film["year"] = year
    if poster:
        film["poster"] = poster
    return film


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
