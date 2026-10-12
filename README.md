# FRList

Static [Stremio](https://www.stremio.com/) addon built from the public **France Inter** watched list on Letterboxd:

https://letterboxd.com/franceinter/films/

The list is scraped on a schedule, titles are matched to IMDb ids via TMDB when a key is set, and the resulting JSON is served from GitHub Pages. No home server.

**Manifest:** https://ellandeved.github.io/FRList/manifest.json

<!-- FRList:status:start -->
| Metric | Value |
| --- | --- |
| Current snapshot (films) | **219** |
| Cumulative history (unique films) | **221** |
| New since previous run | **0** |
| Last successful update (UTC) | **2026-10-12T19:48:13Z** |
| Manifest URL | `https://ellandeved.github.io/FRList/manifest.json` |
<!-- FRList:status:end -->

## Install

Paste the manifest URL into Stremio (Addons → Community addon) or into AIOMetadata / similar catalog importers.

This addon only exposes **catalog** (and **meta** when needed). It does not stream anything — pair it with Torrentio or another stream addon that matches on `tt…` ids.

## How it updates

GitHub Actions runs twice a week (Monday 06:00 UTC, Thursday 18:00 UTC), or on demand from the Actions tab.

1. Fetch the Letterboxd `/films/` grid (`curl_cffi`, browser session if Cloudflare challenges).
2. Resolve titles through TMDB when `TMDB_API_KEY` is set.
3. Write `catalog/`, `meta/`, `data/`, and refresh the stats table above.
4. Commit only if something changed.

If the full grid is blocked, the job falls back to the public profile RSS feed and merges new watches into the previous snapshot so the catalog keeps moving.

Optional secret: `TMDB_API_KEY` from [TMDB API settings](https://www.themoviedb.org/settings/api). Without it, many entries stay as Letterboxd slugs and stream addons ignore them.

## Local run

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
export TMDB_API_KEY='…'   # optional
python -m src.generate_addon
```

## Layout

| Path | Purpose |
| --- | --- |
| `manifest.json` | Stremio addon descriptor |
| `catalog/movie/franceinter.json` | Catalog feed |
| `meta/movie/<id>.json` | Per-film meta when an id is not `tt…` |
| `data/*.json` | Snapshots and history |

Python 3.11. Data comes from public Letterboxd pages — respect their terms.
