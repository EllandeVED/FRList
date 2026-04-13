_This project has been fully vibe coded, but hey, it works well. Use the following link as an add-on Stremio or directly inside AIOMetadata as a catalog. Feel free to read the generated README that explains in more detail. :)_ 

# FRList

Static **[Stremio](https://www.stremio.com/)** addon that mirrors the public **France Inter** watched-films list on Letterboxd:

**Source:** [letterboxd.com/franceinter/films/](https://letterboxd.com/franceinter/films/)

The repo scrapes that page, resolves titles to **IMDb-style ids** when possible (via TMDB), and publishes **JSON** on **GitHub Pages**. A scheduled **GitHub Actions** workflow keeps everything up to date on GitHub’s runners—no home server.

---

## Using the addon

After you enable Pages (see below), your manifest URL is:

`https://<your-github-username>.github.io/FRList/manifest.json`

Use that full URL (HTTPS, ends in `manifest.json`):

- In **Stremio** → Addons → paste as a community manifest, or  
- In tools like **AIOMetadata** → custom manifest / import catalogs → **Load manifest** → select the **France Inter** movie catalog → import.

This addon exposes **catalog** and (when needed) **meta** only. It does **not** provide **streams**. Install a separate stream addon (e.g. Torrentio) if you want playback; those addons match films by **`tt…`** ids.

**Optional but recommended:** add a **`TMDB_API_KEY`** repository secret so films resolve to **`tt…`** and TMDB posters. Without it, many entries stay as Letterboxd **slugs**, which stream addons usually ignore, and artwork may rely on Letterboxd URLs that some clients block.

---

## Generated status

<!-- FRList:status:start -->
| Metric | Value |
| --- | --- |
| Current snapshot (films) | **191** |
| Cumulative history (unique films) | **191** |
| New since previous run | **1** |
| Last successful update (UTC) | **2026-04-13T07:58:42Z** |
| Manifest URL | `https://<github-username>.github.io/FRList/manifest.json` |
<!-- FRList:status:end -->

*Counts and timestamps are updated when the update workflow runs. The manifest URL row stays a generic pattern—substitute your GitHub username (and repo name if you renamed the fork).*

---

## Fork and deploy

1. **Fork** this repository (keep the name **`FRList`** if you want the default Pages path `…/FRList/`).
2. **Actions:** allow workflows for the fork (**Settings → Actions → General**).
3. **Pages:** **Settings → Pages** → deploy from branch **`main`** (or your default) at **`/` (root)**. The site will be `https://<username>.github.io/FRList/`.
4. **Secrets (optional):** **Settings → Secrets and variables → Actions** → add **`TMDB_API_KEY`** ([TMDB API](https://www.themoviedb.org/settings/api)) so ids and posters improve.
5. Run **Actions → Update Letterboxd data → Run workflow**, or wait for the weekly schedule (Mondays **06:00 UTC**).

The workflow runs `python -m src.generate_addon`, then commits and pushes **only if** something changed. Letterboxd is behind Cloudflare; the job uses **curl_cffi** and falls back to **Playwright** on the runner when needed.

---

## Local run

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
export TMDB_API_KEY='…'          # optional
python -m src.generate_addon
```

---

## Layout

| Path | Purpose |
| --- | --- |
| `manifest.json` | Stremio addon descriptor |
| `catalog/movie/franceinter.json` | Catalog feed |
| `meta/movie/<id>.json` | Per-film meta when any id is not `tt…` |
| `data/*.json` | Snapshots and history for the scraper |

---

## Requirements

Python **3.11**, dependencies in **`requirements.txt`** (`curl-cffi`, `beautifulsoup4`, `playwright`, `requests`). No database or paid hosting.

---

## License

Data is derived from public Letterboxd pages; respect [Letterboxd](https://letterboxd.com/)’s terms. This project is provided as-is.
