"""Optional Trakt list sync: mirror resolved movies (IMDb tt…) to a user list for AIOMetadata / Stremio."""

from __future__ import annotations

import os
import time
from typing import Any

import requests

TRAKT_API = "https://api.trakt.tv"
_CHUNK = 100
_SESSION = requests.Session()


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def is_configured() -> bool:
    return bool(
        _env("TRAKT_CLIENT_ID")
        and _env("TRAKT_CLIENT_SECRET")
        and _env("TRAKT_REFRESH_TOKEN")
        and _env("TRAKT_LIST_SLUG")
    )


def _imdb_from_movie(m: dict[str, Any]) -> str | None:
    raw = (m.get("imdb_id") or m.get("id") or "").strip()
    if not raw:
        return None
    if raw.startswith("tt"):
        return raw
    if raw.isdigit():
        return f"tt{raw}"
    return None


def _refresh_access_token() -> tuple[str, str | None]:
    """Return (access_token, new_refresh_token_or_none)."""
    r = _SESSION.post(
        f"{TRAKT_API}/oauth/token",
        json={
            "refresh_token": _env("TRAKT_REFRESH_TOKEN"),
            "client_id": _env("TRAKT_CLIENT_ID"),
            "client_secret": _env("TRAKT_CLIENT_SECRET"),
            "redirect_uri": "urn:ietf:wg:oauth:2.0:oob",
            "grant_type": "refresh_token",
        },
        headers={"Content-Type": "application/json"},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    access = (data.get("access_token") or "").strip()
    if not access:
        raise RuntimeError("Trakt token refresh: missing access_token in response")
    new_refresh = (data.get("refresh_token") or "").strip() or None
    return access, new_refresh


def _headers(access: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "trakt-api-version": "2",
        "trakt-api-key": _env("TRAKT_CLIENT_ID"),
        "Authorization": f"Bearer {access}",
    }


def _normalize_imdb(raw: str | None) -> str | None:
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


def _fetch_list_imdb_ids(access: str, list_slug: str) -> set[str]:
    out: set[str] = set()
    page = 1
    while True:
        r = _SESSION.get(
            f"{TRAKT_API}/users/me/lists/{list_slug}/items/movies",
            params={"limit": _CHUNK, "page": page},
            headers=_headers(access),
            timeout=60,
        )
        if r.status_code == 404:
            raise RuntimeError(
                f"Trakt list not found (slug={list_slug!r}). "
                "Create the list on trakt.tv and set TRAKT_LIST_SLUG to the URL segment."
            )
        r.raise_for_status()
        rows = r.json()
        if not rows:
            break
        for row in rows:
            movie = row.get("movie") or {}
            ids = movie.get("ids") or {}
            imdb = _normalize_imdb(ids.get("imdb"))
            if imdb:
                out.add(imdb)
        page_count = int(r.headers.get("X-Pagination-Page-Count") or "1")
        if page >= page_count:
            break
        page += 1
        time.sleep(0.3)
    return out


def _post_items(
    access: str, list_slug: str, imdb_ids: list[str], *, remove: bool
) -> None:
    path = "items/remove" if remove else "items"
    for i in range(0, len(imdb_ids), _CHUNK):
        chunk = imdb_ids[i : i + _CHUNK]
        body = {"movies": [{"ids": {"imdb": x}} for x in chunk]}
        r = _SESSION.post(
            f"{TRAKT_API}/users/me/lists/{list_slug}/{path}",
            json=body,
            headers=_headers(access),
            timeout=60,
        )
        if r.status_code == 429:
            time.sleep(2)
            r = _SESSION.post(
                f"{TRAKT_API}/users/me/lists/{list_slug}/{path}",
                json=body,
                headers=_headers(access),
                timeout=60,
            )
        r.raise_for_status()
        time.sleep(0.2)


def sync_list(movies: list[dict[str, Any]]) -> dict[str, int]:
    """
    Add/remove movies on the Trakt list so it matches `movies` (IMDb ids only).
    Returns counts: added, removed, skipped_no_imdb.
    """
    if not is_configured():
        return {"added": 0, "removed": 0, "skipped_no_imdb": 0}

    list_slug = _env("TRAKT_LIST_SLUG")
    wanted: set[str] = set()
    skipped = 0
    for m in movies:
        imdb = _imdb_from_movie(m)
        if imdb:
            wanted.add(imdb)
        else:
            skipped += 1

    access, _ = _refresh_access_token()

    current = _fetch_list_imdb_ids(access, list_slug)
    to_add = sorted(wanted - current)
    to_remove = sorted(current - wanted)

    if to_remove:
        _post_items(access, list_slug, to_remove, remove=True)
    if to_add:
        _post_items(access, list_slug, to_add, remove=False)

    return {"added": len(to_add), "removed": len(to_remove), "skipped_no_imdb": skipped}
