"""Scrape Letterboxd, update data files, and emit static Stremio addon JSON."""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from src.scrape import ScrapeError, enrich_missing_posters, scrape_franceinter_films

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
META_DIR = ROOT / "meta" / "movie"
CATALOG_PATH = ROOT / "catalog" / "movie" / "franceinter.json"
MANIFEST_PATH = ROOT / "manifest.json"
README_PATH = ROOT / "README.md"

README_START = "<!-- FRList:status:start -->"
README_END = "<!-- FRList:status:end -->"

# GitHub username/org for Pages URL in README status (forks: change here).
PAGES_OWNER = "EllandeVED"

MANIFEST = {
    "id": "franceinter-letterboxd-catalog",
    "version": "1.0.0",
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
| Manifest URL | `https://{PAGES_OWNER}.github.io/FRList/manifest.json` |
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
    return {"meta": meta}


def _write_catalog_and_meta(movies: list[dict]) -> None:
    metas = []
    for m in movies:
        entry = {"id": m["id"], "type": "movie", "name": m["title"]}
        if m.get("poster"):
            entry["poster"] = m["poster"]
        metas.append(entry)
    _json_dump(CATALOG_PATH, {"metas": metas})

    META_DIR.mkdir(parents=True, exist_ok=True)
    keep: set[str] = set()
    for m in movies:
        mid = m["id"]
        keep.add(mid)
        _json_dump(META_DIR / f"{mid}.json", _meta_payload(m))

    for p in META_DIR.glob("*.json"):
        stem = p.stem
        if stem not in keep:
            p.unlink()

    _json_dump(MANIFEST_PATH, MANIFEST)


def run() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    prev_doc = _load_json(DATA / "current.json")
    prev_movies = _movies_from_data(prev_doc)
    prev_ids = {m["id"] for m in prev_movies if m.get("id")}

    movies = scrape_franceinter_films(enrich=False)
    movies = enrich_missing_posters(movies)

    now = _utc_now_iso()
    current_doc = {
        "source": "https://letterboxd.com/franceinter/films/",
        "generated_at_utc": now,
        "movies": movies,
    }
    _json_dump(DATA / "current.json", current_doc)

    cur_ids = {m["id"] for m in movies}
    new_ids = cur_ids - prev_ids if prev_ids else set(cur_ids)

    hist_doc = _load_json(DATA / "history.json")
    hist_movies = _movies_from_data(hist_doc)
    by_id: dict[str, dict] = {m["id"]: m for m in hist_movies if m.get("id")}
    for m in movies:
        by_id[m["id"]] = m
    history_list = sorted(
        by_id.values(),
        key=lambda x: (x["title"].lower(), x.get("year") or 0, x["id"]),
    )
    history_out = {
        "source": "https://letterboxd.com/franceinter/films/",
        "updated_at_utc": now,
        "movies": history_list,
    }
    _json_dump(DATA / "history.json", history_out)

    new_movies = [m for m in movies if m["id"] in new_ids]
    new_movies.sort(
        key=lambda x: (x["title"].lower(), x.get("year") or 0, x["id"])
    )
    _json_dump(
        DATA / "new_since_last_run.json",
        {
            "source": "https://letterboxd.com/franceinter/films/",
            "generated_at_utc": now,
            "previous_snapshot_had_ids": sorted(prev_ids),
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
