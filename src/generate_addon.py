"""Scrape Letterboxd, update data files, and emit static Stremio addon JSON."""

from __future__ import annotations

import copy
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from src.scrape import ScrapeError, enrich_missing_posters, scrape_franceinter_films
from src.tmdb_resolve import is_configured, resolve_movies_parallel

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
META_DIR = ROOT / "meta" / "movie"
CATALOG_PATH = ROOT / "catalog" / "movie" / "franceinter.json"
MANIFEST_PATH = ROOT / "manifest.json"
README_PATH = ROOT / "README.md"

README_START = "<!-- FRList:status:start -->"
README_END = "<!-- FRList:status:end -->"

MANIFEST = {
    # Dot-separated id per Stremio addon SDK (better client compatibility).
    "id": "org.franceinter.letterboxd",
    "version": "1.1.0",
    "name": "France Inter Letterboxd",
    "description": "Auto-updated catalog from the France Inter watched films page on Letterboxd",
    "resources": ["catalog", "meta"],
    "types": ["movie"],
    "catalogs": [
        {
            "type": "movie",
            "id": "franceinter",
            "name": "France Inter",
        }
    ],
    "behaviorHints": {"configurable": False, "configurationRequired": False},
}


def _letterboxd_key(m: dict) -> str:
    return (m.get("letterboxd_url") or "").strip()


def _manifest_for_catalog(movies: list[dict], *, all_imdb_ids: bool) -> dict:
    """If every catalog id is tt…, expose catalog only; Cinemeta supplies meta (Stremio protocol)."""
    m = copy.deepcopy(MANIFEST)
    if all_imdb_ids:
        # https://github.com/Stremio/stremio-addon-sdk/blob/master/docs/api/responses/manifest.md
        m["idPrefixes"] = ["tt"]
        m["resources"] = [{"name": "catalog", "types": ["movie"]}]
    return m


def _json_dump(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    path.write_text(text, encoding="utf-8")


def _load_json(path: Path) -> dict | list | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _movies_from_data(doc: dict | list | None) -> list[dict]:
    if doc is None:
        return []
    if isinstance(doc, list):
        return [x for x in doc if isinstance(x, dict)]
    m = doc.get("movies")
    if isinstance(m, list):
        return [x for x in m if isinstance(x, dict)]
    return []


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _update_readme(
    *,
    current_n: int,
    history_n: int,
    new_n: int,
    last_run_utc: str,
) -> None:
    if not README_PATH.is_file():
        return
    text = README_PATH.read_text(encoding="utf-8")
    block = f"""{README_START}
| Metric | Value |
| --- | --- |
| Current snapshot (films) | **{current_n}** |
| Cumulative history (unique films) | **{history_n}** |
| New since previous run | **{new_n}** |
| Last successful update (UTC) | **{last_run_utc}** |
| Manifest URL | `https://<github-username>.github.io/FRList/manifest.json` |
{README_END}"""
    if README_START in text and README_END in text:
        text = re.sub(
            re.escape(README_START) + r".*?" + re.escape(README_END),
            block,
            text,
            count=1,
            flags=re.DOTALL,
        )
    else:
        text = text.rstrip() + "\n\n" + block + "\n"
    README_PATH.write_text(text, encoding="utf-8")


def _meta_payload(movie: dict) -> dict:
    mid = movie["id"]
    name = movie["title"]
    year = movie.get("year")
    poster = movie.get("poster")
    lb = movie["letterboxd_url"]
    desc = f"Letterboxd: {lb}"
    meta: dict = {
        "id": mid,
        "type": "movie",
        "name": name,
        "description": desc,
    }
    if year is not None:
        meta["releaseInfo"] = str(year)
    if poster:
        meta["poster"] = poster
    # Stream addons (Torrentio, etc.) key off IMDb ids; Stremio merges meta more reliably when this is set.
    imdb = (movie.get("imdb_id") or "").strip()
    if mid.startswith("tt"):
        meta["imdb_id"] = imdb if imdb else mid
    elif imdb:
        meta["imdb_id"] = imdb
    return {"meta": meta}


def _write_catalog_and_meta(movies: list[dict]) -> None:
    metas = []
    for m in movies:
        entry = {"id": m["id"], "type": "movie", "name": m["title"]}
        if m.get("poster"):
            entry["poster"] = m["poster"]
        metas.append(entry)
    _json_dump(CATALOG_PATH, {"metas": metas})

    ids = [str(x.get("id", "")) for x in movies]
    all_imdb_ids = bool(ids) and all(x.startswith("tt") for x in ids)

    META_DIR.mkdir(parents=True, exist_ok=True)
    if all_imdb_ids:
        for p in META_DIR.glob("*.json"):
            p.unlink()
    else:
        keep: set[str] = set()
        for m in movies:
            mid = m["id"]
            keep.add(mid)
            _json_dump(META_DIR / f"{mid}.json", _meta_payload(m))

        for p in META_DIR.glob("*.json"):
            if p.stem not in keep:
                p.unlink()

    _json_dump(MANIFEST_PATH, _manifest_for_catalog(movies, all_imdb_ids=all_imdb_ids))


def run() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    prev_doc = _load_json(DATA / "current.json")
    prev_movies = _movies_from_data(prev_doc)
    prev_lb = {_letterboxd_key(m) for m in prev_movies if _letterboxd_key(m)}

    movies = scrape_franceinter_films(enrich=False)
    if is_configured():
        movies = resolve_movies_parallel(movies)
    movies = enrich_missing_posters(movies)

    now = _utc_now_iso()
    current_doc = {
        "source": "https://letterboxd.com/franceinter/films/",
        "generated_at_utc": now,
        "movies": movies,
    }
    _json_dump(DATA / "current.json", current_doc)

    cur_lb = {_letterboxd_key(m) for m in movies if _letterboxd_key(m)}
    new_lb = cur_lb - prev_lb if prev_lb else set(cur_lb)

    hist_doc = _load_json(DATA / "history.json")
    hist_movies = _movies_from_data(hist_doc)
    by_lb: dict[str, dict] = {}
    for m in hist_movies:
        k = _letterboxd_key(m)
        if k:
            by_lb[k] = m
    for m in movies:
        k = _letterboxd_key(m)
        if k:
            by_lb[k] = m
    history_list = sorted(
        by_lb.values(),
        key=lambda x: (x["title"].lower(), x.get("year") or 0, x.get("id", "")),
    )
    history_out = {
        "source": "https://letterboxd.com/franceinter/films/",
        "updated_at_utc": now,
        "movies": history_list,
    }
    _json_dump(DATA / "history.json", history_out)

    new_movies = [m for m in movies if _letterboxd_key(m) in new_lb]
    new_movies.sort(
        key=lambda x: (x["title"].lower(), x.get("year") or 0, x.get("id", ""))
    )
    _json_dump(
        DATA / "new_since_last_run.json",
        {
            "source": "https://letterboxd.com/franceinter/films/",
            "generated_at_utc": now,
            "previous_snapshot_had_letterboxd_urls": sorted(prev_lb),
            "new_movies": new_movies,
        },
    )

    _write_catalog_and_meta(movies)

    _update_readme(
        current_n=len(movies),
        history_n=len(history_list),
        new_n=len(new_movies),
        last_run_utc=now,
    )


def main() -> None:
    try:
        run()
    except ScrapeError as e:
        print(f"FRList: {e}", file=sys.stderr)
        raise SystemExit(1) from e


if __name__ == "__main__":
    main()
