"""Optional TMDB (v3) lookup: fast parallel search → IMDb tt id + poster for Stremio stream addons."""

from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import requests

TMDB_SEARCH = "https://api.themoviedb.org/3/search/movie"
TMDB_EXTERNAL = "https://api.themoviedb.org/3/movie/{}/external_ids"
TMDB_IMG = "https://image.tmdb.org/t/p/w500"

_SESSION = requests.Session()
_SESSION.headers.update({"Accept": "application/json"})


def _api_key() -> str | None:
    k = (os.environ.get("TMDB_API_KEY") or "").strip()
    return k or None


def _strip_trailing_year(title: str) -> str:
    return re.sub(r"\s*\(\d{4}\)\s*$", "", title).strip() or title


def _pick_result(results: list[dict], year: int | None) -> dict | None:
    if not results:
        return None
    if year is not None:
        ys = str(year)
        for r in results:
            rd = (r.get("release_date") or "")[:4]
            if rd == ys:
                return r
        for r in results:
            rd = (r.get("release_date") or "")[:4]
            if rd and abs(int(rd) - year) <= 1:
                return r
    return results[0]


def _search_movie(api_key: str, query: str, year: int | None) -> dict | None:
    params: dict[str, Any] = {"api_key": api_key, "query": query}
    if year is not None:
        params["year"] = year
    r = _SESSION.get(TMDB_SEARCH, params=params, timeout=20)
    r.raise_for_status()
    data = r.json()
    return _pick_result(data.get("results") or [], year)


def _external_imdb(api_key: str, tmdb_id: int) -> str | None:
    r = _SESSION.get(
        TMDB_EXTERNAL.format(tmdb_id),
        params={"api_key": api_key},
        timeout=20,
    )
    r.raise_for_status()
    raw = (r.json().get("imdb_id") or "").strip()
    if not raw:
        return None
    if raw.startswith("tt"):
        return raw
    return f"tt{raw}"


def _resolve_one(api_key: str, m: dict[str, Any]) -> dict[str, Any]:
    out = dict(m)
    q = _strip_trailing_year(out.get("title") or "")
    year = out.get("year")
    if not q:
        return out
    try:
        hit = _search_movie(api_key, q, year)
        if not hit:
            return out
        tid = hit.get("id")
        if not tid:
            return out
        tid = int(tid)
        imdb = _external_imdb(api_key, tid)
        pp = hit.get("poster_path")
        poster = f"{TMDB_IMG}{pp}" if pp else out.get("poster")
        title = (
            hit.get("title") or hit.get("original_title") or out.get("title") or ""
        ).strip()
        if not title:
            return out
        rd = hit.get("release_date") or ""
        y = int(rd[:4]) if len(rd) >= 4 and rd[:4].isdigit() else year

        if imdb:
            out["id"] = imdb
            out["imdb_id"] = imdb
        out["tmdb_id"] = tid
        out["title"] = f"{title} ({y})" if y else title
        out["year"] = y
        if poster:
            out["poster"] = poster
    except (requests.RequestException, ValueError, KeyError, TypeError):
        pass
    return out


def resolve_movies_parallel(movies: list[dict[str, Any]], *, max_workers: int = 12) -> list[dict[str, Any]]:
    """Return new movie dicts with Stremio id = tt… when TMDB finds a match."""
    key = _api_key()
    if not key or not movies:
        return [dict(m) for m in movies]

    out_map: dict[str, dict[str, Any]] = {}
    # Stable key for merging results
    for m in movies:
        lb = m.get("letterboxd_url") or m["id"]
        out_map[lb] = dict(m)

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {
            ex.submit(_resolve_one, key, dict(m)): m.get("letterboxd_url") or m["id"]
            for m in movies
        }
        for fut in as_completed(futs):
            lb = futs[fut]
            try:
                out_map[lb] = fut.result()
            except Exception:
                pass

    order = [m.get("letterboxd_url") or m["id"] for m in movies]
    return [out_map[k] for k in order]


def is_configured() -> bool:
    return _api_key() is not None
