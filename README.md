# FRList — France Inter Letterboxd → Stremio (static)

This repository mirrors the public **watched films** list from Letterboxd:

**Source page:** [letterboxd.com/franceinter/films/](https://letterboxd.com/franceinter/films/)

It generates a **static Stremio addon** (catalog + meta only, no streams) and publishes it with **GitHub Pages**. Updates run on a schedule in **GitHub Actions** using **GitHub-hosted runners** only — nothing recurring runs on your computer.

### AIOMetadata (custom catalog)

AIOMetadata expects a normal **Stremio v2 manifest** served over **HTTPS**.

1. Open your AIOMetadata instance → **Configure** → **Catalogs**.
2. Add a **custom / external** catalog (UI labels vary) and paste **exactly** the manifest URL — it must end with **`manifest.json`**, e.g. `https://EllandeVED.github.io/FRList/manifest.json`. Do **not** use the repo homepage alone.
3. **Save** the configuration, then refresh or reinstall the **AIOMetadata** addon in Stremio so the merged manifest picks up the new catalog.

If the catalog still misbehaves, confirm **`TMDB_API_KEY`** is set on the repo so every row resolves to **`tt…`** ids (see below). With all-`tt` ids, FRList advertises **catalog only** and Stremio’s built-in Cinemeta layer supplies detail pages — that pattern matches the [Stremio addon manifest](https://github.com/Stremio/stremio-addon-sdk/blob/master/docs/api/responses/manifest.md) guidance for IMDb ids.

**Standalone Stremio:** you can also paste the same manifest URL directly into Stremio as a community addon.

### Stremio: streams, scores, and posters

- **Stream addons (Torrentio, etc.)** match movies by **IMDb-style ids** (`tt…`). Letterboxd **slugs** are invisible to them, so you get *“No addons were requested for this meta!”* unless each row uses a **`tt…` id**.
- **Optional (recommended):** set a free **[TMDB API key](https://www.themoviedb.org/settings/api)** in **`TMDB_API_KEY`** (local env or GitHub **Settings → Secrets → Actions → `TMDB_API_KEY`**). The generator then resolves titles to **`tt…` + TMDB posters** in parallel (fast JSON). After that, your usual streaming addons can attach to the same meta.
- **Without TMDB:** ids stay as Letterboxd slugs; the addon still lists films, but **streams / IMDb–style score bars** from other addons usually **won’t** hook in.
- This addon does **not** ship a **`stream`** resource. With **Letterboxd slug** ids it provides **catalog + meta**. When **every** id is **`tt…`** (TMDB resolution), it provides **catalog only** so Cinemeta can own meta and stream addons attach cleanly.

## Generated status

<!-- FRList:status:start -->
| Metric | Value |
| --- | --- |
| Current snapshot (films) | **190** |
| Cumulative history (unique films) | **190** |
| New since previous run | **0** |
| Last successful update (UTC) | **2026-04-08T22:37:24Z** |
| Manifest URL | `https://EllandeVED.github.io/FRList/manifest.json` |
<!-- FRList:status:end -->

## Manifest URL (after Pages is enabled)

`https://EllandeVED.github.io/FRList/manifest.json`

If you had an older install, remove the previous addon entry first: the addon **`id`** is now `org.franceinter.letterboxd` (Stremio SDK–style), not `franceinter-letterboxd-catalog`.

## How it works

1. `src/scrape.py` fetches the Letterboxd HTML with **curl_cffi** (Chrome TLS impersonation so Cloudflare often allows GitHub-hosted runners) and parses it with **BeautifulSoup** (paginated `/films/` listing).
2. If **`TMDB_API_KEY`** is set, **`src/tmdb_resolve.py`** maps each film to **`tt…`** (and a TMDB poster) via the TMDB API (parallel requests).  
3. `python -m src.generate_addon` writes:
   - `data/current.json` — latest full snapshot  
   - `data/history.json` — cumulative unique films ever seen  
   - `data/new_since_last_run.json` — new **Letterboxd URLs** since the previous snapshot  
   - `manifest.json`, `catalog/movie/franceinter.json`, and (if any id is not `tt…`) `meta/movie/<id>.json` per film  
   - the **Generated status** table in this README  
4. GitHub Actions runs the same command weekly and commits changes so GitHub Pages always serves fresh static JSON. The workflow passes **`secrets.TMDB_API_KEY`** when configured.

Letterboxd sits behind **Cloudflare**. The scheduled workflow uses **curl_cffi** first, then **Playwright (Chromium)** on GitHub Actions only if the interstitial appears—so the same job can succeed on runners where plain HTTP gets blocked.

## Data files

| File | Meaning |
| --- | --- |
| `data/current.json` | Latest scrape: full list of films from the watched page at run time. |
| `data/history.json` | Union of all films ever seen across runs (keyed by **Letterboxd film URL** so ids can migrate to `tt…`). |
| `data/new_since_last_run.json` | Films whose **Letterboxd URL** is new vs the previous snapshot (`previous_snapshot_had_letterboxd_urls`). |

## Local one-off run (optional)

```bash
cd /path/to/FRList
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export TMDB_API_KEY='your_tmdb_v3_key'   # optional; enables tt… ids + TMDB posters
python -m src.generate_addon
```

## Enable GitHub Actions

1. Open the repo on GitHub → **Settings** → **Actions** → **General**.  
2. Under **Actions permissions**, allow **Allow all actions and reusable workflows** (or restrict as your org requires while keeping Actions enabled).  
3. The workflow file is `.github/workflows/update.yml` (scheduled **Monday 06:00 UTC** and **workflow_dispatch**).

### Run the workflow manually

**Actions** → **Update Letterboxd data** → **Run workflow**.

### TMDB secret (optional, for `tt…` ids / streams)

1. Create a **TMDB** account → **Settings** → **API** → request an **API key (v3 auth)**.  
2. Repo **Settings** → **Secrets and variables** → **Actions** → **New repository secret** → name **`TMDB_API_KEY`**, value your key.  
3. Re-run the workflow. Catalog/meta ids become **`tt…`** where TMDB returns an IMDb id, so stream addons can attach.

## Enable GitHub Pages

1. **Settings** → **Pages**.  
2. **Build and deployment** → **Source**: **Deploy from a branch**.  
3. **Branch**: your default branch (e.g. `main`), folder **/ (root)**.  
4. Save. After the first deployment, the site will be at `https://EllandeVED.github.io/FRList/` (repo name `FRList`).

This repo includes **`.nojekyll`** so GitHub Pages does not process the site with Jekyll.

## Requirements

- Python **3.11**  
- Dependencies: `curl-cffi`, `beautifulsoup4`, `playwright`, `requests` (see `requirements.txt`)  
- No database, no Docker, no paid services, no local server — only static files and Actions.

## License

Data is derived from public Letterboxd pages; respect [Letterboxd](https://letterboxd.com/) terms of use. This tooling is provided as-is.
