# scraper.py — universal article parser to Markdown (Scrapling-based)

## Purpose

`scraper.py` downloads a web page by URL and saves its main article in Markdown format: title, text, images (with absolute URLs), links, tables. Menus, footers, banners, and navigation are discarded.

**Cloudflare Turnstile support:** if the site returns 403/503, the script automatically switches to a browser engine (patchright via scrapling.StealthyFetcher) with a built-in CF Turnstile solver.

## Usage

```bash
python scraper.py \
  --url "https://example.com/article" \
  --filename my_article \
  --proxy "http://127.0.0.1:8080"
```

All three flags are required (`required=True`):
- `--url` — article URL
- `--filename` — output file name without extension (creates `{filename}.md` in CWD)
- `--proxy` — proxy in `http://host:port` or `socks5://host:port` format

## Dependencies

```bash
pip install "scrapling[fetchers]" trafilatura beautifulsoup4
python -m patchright install chromium  # ~113 MB, needed for CF-fallback
```

## Architecture

```
[CLI] → [scrapling.Fetcher (curl_cffi, fast)] → HTTP 200?
                                               ├─ yes → [PARSE]
                                               ├─ 403/503 → [scrapling.StealthyFetcher
                                               │           (solve_cloudflare=True)] → HTML
                                               └─ other → sys.exit(1)
[PARSE] = extract_title (og/twitter/title/h1) + trafilatura (markdown)
        → absolutize image URLs → save {filename}.md
```

## Error handling

- **Any error → silent `sys.exit(1)` without traceback.**
- Network failures and HTTP 429/500/502/504 → retry up to `MAX_RETRIES=3` times (built into scrapling.Fetcher).
- HTTP 403 or 503 → automatic StealthyFetcher with `solve_cloudflare=True`.
- If StealthyFetcher fails to bypass CF → `sys.exit(1)`.
- If trafilatura returns None or empty string → `sys.exit(1)` (no fallback).

## Smoke test (3 URLs)

| URL | exit | Time | Result | Fetcher used |
|-----|------|-------|--------|--------------|
| site_A | 0 | 3.10s | 20656 B, 23 img, 21 H2 | Fetcher (curl_cffi) |
| site_B | 0 | 15.38s | 24152 B, 15 img, 11 H2 | **StealthyFetcher (CF solved!)** |
| site_C | 0 | 2.75s | 891 B (index-page) | Fetcher (curl_cffi) |

**All 3 URLs now work.** Previously site_B could not be bypassed — now Scrapling solves its CF Turnstile automatically in ~15 sec.

## Tech stack

| Component | Technology | Purpose |
|---|---|---|
| Fast fetcher | `scrapling.Fetcher` (curl_cffi) | TLS-impersonation, regular sites |
| Stealthy fetcher | `scrapling.StealthyFetcher` (patchright) | CF Turnstile solver, headless browser |
| Content extraction | `trafilatura` | Article → Markdown (no menus/footers) |
| Title extraction | `BeautifulSoup` | og:title → twitter:title → `<title>` → `<h1>` |
| URL absolutization | `urllib.parse.urljoin` | Images get absolute URLs |

## Known quirks

1. **`favor_recall=True` is required.** Without it, trafilatura (in default favor_precision mode) drops images and ~17% of text on several sites (e.g. Drupal-based). Empirically confirmed on site_A.

2. **Duplicate content at the top.** With `favor_recall=True` trafilatura sometimes picks up a bit of navigation/banner text at the start of the article (e.g. on site_B the title appears twice). This is a trade-off for content completeness — the alternative (favor_precision) loses images.

3. **Index pages.** If the URL points to a table of contents / index (e.g. `site_C/.../character-der/z0a83456a` — trafilatura correctly extracts whatever is on the page (usually little content). You need either a direct article URL or sub-page traversal.

## Output file structure

```markdown
# {article_title}

{article_body_in_markdown_with_absolute_image_urls}
```

The file is saved in UTF-8 in the current working directory.

## Configurable constants

At the top of `scraper.py`:

```python
MAX_RETRIES = 3                       # retries in scrapling.Fetcher
RETRY_DELAY = 5                       # seconds between retries
TIMEOUT = 30                          # Fetcher timeout, sec
CF_FALLBACK_STATUSES = {403, 503}     # statuses triggering StealthyFetcher
STEALTHY_TIMEOUT = 60000              # StealthyFetcher timeout, ms
STEALTHY_WAIT = 3000                  # extra wait after solve, ms
```

## Comparison with previous version (curl_cffi + Playwright+stealth)

| Criterion | Old version | New (Scrapling) |
|---|---|---|
| CF Turnstile bypass | ❌ Could not bypass | ✅ Solved in ~15s |
| Architecture | 270 lines, 2 engines manually | 200 lines, 1 library |
| Dependencies | 6 packages | 3 packages |
| Browser install | ~170 MB (playwright) | ~113 MB (patchright) |
| CF Turnstile solver | ❌ No | ✅ Built-in |
