# FRList — France Inter Letterboxd → Stremio (static)

This repository mirrors the public **watched films** list from Letterboxd:

**Source page:** [letterboxd.com/franceinter/films/](https://letterboxd.com/franceinter/films/)

It generates a **static Stremio addon** (catalog + meta only, no streams) and publishes it with **GitHub Pages**. Updates run on a schedule in **GitHub Actions** using **GitHub-hosted runners** only — nothing recurring runs on your computer.

Paste the manifest URL into **AIOMetadata** (or Stremio) as a **Custom Manifest**.

## Generated status

<!-- FRList:status:start -->
| Metric | Value |
| --- | --- |
| Current snapshot (films) | **190** |
| Cumulative history (unique films) | **190** |
| New since previous run | **190** |
| Last successful update (UTC) | **2026-04-08T21:25:38Z** |
| Manifest URL | `https://EllandeVED.github.io/FRList/manifest.json` |
<!-- FRList:status:end -->

## Manifest URL (after Pages is enabled)

`https://EllandeVED.github.io/FRList/manifest.json`

## How it works

1. `src/scrape.py` fetches the Letterboxd HTML with **curl_cffi** (Chrome TLS impersonation so Cloudflare often allows GitHub-hosted runners) and parses it with **BeautifulSoup** (paginated `/films/` listing).
2. `python -m src.generate_addon` writes:
   - `data/current.json` — latest full snapshot  
   - `data/history.json` — cumulative unique films ever seen  
   - `data/new_since_last_run.json` — films in the new snapshot that were not in the previous `current.json`  
   - `manifest.json`, `catalog/movie/franceinter.json`, and `meta/movie/<id>.json` for each film  
   - the **Generated status** table in this README  
3. GitHub Actions runs the same command weekly and commits changes so GitHub Pages always serves fresh static JSON.

Letterboxd sits behind **Cloudflare**. A local run may fail with a challenge or HTTP error even though the scheduled **GitHub-hosted** workflow usually receives normal HTML on a first fetch.

## Data files

| File | Meaning |
| --- | --- |
| `data/current.json` | Latest scrape: full list of films from the watched page at run time. |
| `data/history.json` | Union of all films ever seen across runs (by stable Letterboxd-derived id). |
| `data/new_since_last_run.json` | Films that appear in the new snapshot but were not in the *previous* `current.json`. |

## Local one-off run (optional)

```bash
cd /path/to/FRList
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m src.generate_addon
```

## Enable GitHub Actions

1. Open the repo on GitHub → **Settings** → **Actions** → **General**.  
2. Under **Actions permissions**, allow **Allow all actions and reusable workflows** (or restrict as your org requires while keeping Actions enabled).  
3. The workflow file is `.github/workflows/update.yml` (scheduled **Monday 06:00 UTC** and **workflow_dispatch**).

### Run the workflow manually

**Actions** → **Update Letterboxd data** → **Run workflow**.

## Enable GitHub Pages

1. **Settings** → **Pages**.  
2. **Build and deployment** → **Source**: **Deploy from a branch**.  
3. **Branch**: your default branch (e.g. `main`), folder **/ (root)**.  
4. Save. After the first deployment, the site will be at `https://EllandeVED.github.io/FRList/` (repo name `FRList`).

This repo includes **`.nojekyll`** so GitHub Pages does not process the site with Jekyll.

## Requirements

- Python **3.11**  
- Dependencies: `curl-cffi`, `beautifulsoup4` (see `requirements.txt`)  
- No database, no Docker, no paid services, no local server — only static files and Actions.

## License

Data is derived from public Letterboxd pages; respect [Letterboxd](https://letterboxd.com/) terms of use. This tooling is provided as-is.
