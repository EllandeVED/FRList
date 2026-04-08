"""Optional TMDB (v3) lookup: fast parallel search → IMDb tt id + poster for Stremio stream addons."""

from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import requests

TMDB_SEARCH = "https://api.themoviedb.org/3/search/movie"
TMDB_MOVIE = "https://api.themoviedb.org/3/movie/{}"
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


def _norm_imdb(raw: str | None) -> str | None:
    if not raw:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if s.startswith("tt"):
        return s
    if s.isdigit():
        return f"tt{s}"
    return None


def _movie_detail(api_key: str, tmdb_id: int) -> dict[str, Any]:
    r = _SESSION.get(
        TMDB_MOVIE.format(tmdb_id),
        params={"api_key": api_key},
        timeout=20,
    )
    r.raise_for_status()
    return r.json()


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
        detail = _movie_detail(api_key, tid)
        imdb = _norm_imdb(detail.get("imdb_id"))
        pp = detail.get("poster_path") or hit.get("poster_path")
        # TMDB-hosted posters load reliably in Stremio; Letterboxd CDN often does not.
        poster = f"{TMDB_IMG}{pp}" if pp else out.get("poster")
        title = (
            (detail.get("title") or detail.get("original_title") or "").strip()
            or (hit.get("title") or hit.get("original_title") or "").strip()
            or (out.get("title") or "")
        ).strip()
        if not title:
            return out
        rd = (detail.get("release_date") or hit.get("release_date") or "") or ""
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


def _backfill_slug_from_tmdb_id(api_key: str, m: dict[str, Any]) -> dict[str, Any]:
    """If search missed IMDb but we already have a TMDB id (e.g. stale data), refresh from /movie/{id}."""
    out = dict(m)
    mid = str(out.get("id") or "")
    if mid.startswith("tt"):
        return out
    tid = out.get("tmdb_id")
    if not tid:
        return out
    try:
        detail = _movie_detail(api_key, int(tid))
        imdb = _norm_imdb(detail.get("imdb_id"))
        pp = detail.get("poster_path")
        if pp:
            out["poster"] = f"{TMDB_IMG}{pp}"
        if imdb:
            out["id"] = imdb
            out["imdb_id"] = imdb
        t = (detail.get("title") or detail.get("original_title") or "").strip()
        if t:
            rd = (detail.get("release_date") or "")[:4]
            y = int(rd) if rd.isdigit() else out.get("year")
            out["title"] = f"{t} ({y})" if y else t
            if y:
                out["year"] = y
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
    resolved = [out_map[k] for k in order]
    # Second pass: slug id + tmdb_id (TMDB had no imdb on an older run, or search was off).
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {
            ex.submit(_backfill_slug_from_tmdb_id, key, dict(m)): i
            for i, m in enumerate(resolved)
            if not str(m.get("id", "")).startswith("tt") and m.get("tmdb_id")
        }
        for fut in as_completed(futs):
            i = futs[fut]
            try:
                resolved[i] = fut.result()
            except Exception:
                pass
    return resolved


def is_configured() -> bool:
    return _api_key() is not None
