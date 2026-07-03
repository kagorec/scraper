#!/usr/bin/env python3
"""
scraper.py — universal article parser (Scrapling-based).

Pipeline: [CLI args] -> [scrapling.Fetcher (curl_cffi)] -> HTTP 200?
                                            | yes -> [PARSE]
                                            | 403/503 -> [scrapling.StealthyFetcher
                                            |            with solve_cloudflare=True] -> HTML
                                            | other -> sys.exit(1)
        -> [PARSE TITLE + CONTENT] -> [URL ABSOLUTIZATION] -> [SAVE]

Strict exit policy: any failure -> silent sys.exit(1), no traceback.
If trafilatura returns None -> sys.exit(1), no fallback.
"""

import argparse
import html
import os
import re
import sys
from urllib.parse import urljoin

from bs4 import BeautifulSoup
import trafilatura

# Directory where scraper.py itself lives. Used as the default output
# directory when --filename contains no path component.
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #
MAX_RETRIES = 3                       # retry attempts for scrapling.Fetcher
RETRY_DELAY = 5                       # seconds between Fetcher retries
TIMEOUT = 30                          # Fetcher per-request timeout, seconds

# Output toggles
IMAGES = 0                            # 0 = drop image markdown tags;
                                      # 1 = keep them (with absolute URLs)

# Article length limits (in characters). Applied to the extracted Markdown
# body (before the title prefix is added). Articles outside this range are
# treated as junk and silently discarded via sys.exit(1).
MIN_SYMBOLS = 1000                    # too short = junk (nav/index page, stub)
MAX_SYMBOLS = 30000                   # too long  = junk (portal/listing, dump)

# Cloudflare-fallback configuration
CF_FALLBACK_STATUSES = {403, 503}     # statuses that trigger StealthyFetcher
STEALTHY_TIMEOUT = 60000              # StealthyFetcher total timeout, ms
STEALTHY_WAIT = 3000                  # extra wait after CF solve, ms


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def parse_args() -> argparse.Namespace:
    """Parse the three required CLI flags: --url, --filename, --proxy."""
    p = argparse.ArgumentParser(
        description="Universal article scraper -> Markdown (Scrapling-based).",
        add_help=True,
    )
    p.add_argument("--url", required=True,
                   help="URL of the article to scrape.")
    p.add_argument(
        "--filename",
        required=True,
        help=(
            "Output target. Two modes:\n"
            "  1) Bare filename, e.g. 'my_article' — saves as "
            "'{script_dir}/my_article.md' next to scraper.py.\n"
            "  2) Full path, e.g. 'C:\\Users\\me\\art\\name.md' or "
            "'/home/me/art/name' — saves there; parent dirs are created "
            "automatically. The '.md' extension is appended if missing."
        ),
    )
    p.add_argument("--proxy", required=True,
                   help="Proxy string, e.g. 'socks5://127.0.0.1:3210' or 'http://127.0.0.1:8080'.")
    return p.parse_args()


# --------------------------------------------------------------------------- #
# Fetch with Cloudflare-fallback
# --------------------------------------------------------------------------- #
def fetch_html(url: str, proxy: str) -> str:
    """
    Fetch HTML text from `url`.

    Strategy:
      1. Try scrapling.Fetcher (curl_cffi-based, fast).
         Retry on network errors / 429/500/502/504 is handled internally by
         scrapling via `retries=` and `retry_delay=`.
      2. If HTTP status is 403 or 503 (Cloudflare challenge), invoke
         StealthyFetcher with solve_cloudflare=True — patchright-based
         browser with automatic CF Turnstile solver.
      3. Any other non-200 status, exception, or CF-solve failure
         -> silent sys.exit(1).
    """
    # Lazy import: scrapling imports playwright/patchright under the hood,
    # which is expensive; only load when fetch is actually called.
    try:
        from scrapling.fetchers import Fetcher, StealthyFetcher
    except Exception:
        sys.exit(1)

    # --- 2a. Fast path: scrapling.Fetcher (curl_cffi) ---
    try:
        page = Fetcher.get(
            url,
            stealthy_headers=True,
            proxy=proxy,
            timeout=TIMEOUT * 1000,        # scrapling expects ms
            retries=MAX_RETRIES,
            retry_delay=RETRY_DELAY,
        )
        status = page.status
        if status == 200:
            return str(page.html_content)
        if status not in CF_FALLBACK_STATUSES:
            # 404/410/418/etc — no point trying StealthyFetcher
            sys.exit(1)
        # fall through to StealthyFetcher for 403/503
    except SystemExit:
        raise
    except Exception:
        # Fetcher raised (network error after all retries, etc.) — exit.
        # NB: StealthyFetcher would hit the same network failure, so no fallback.
        sys.exit(1)

    # --- 2b. Cloudflare-fallback: StealthyFetcher with solve_cloudflare=True ---
    try:
        page = StealthyFetcher.fetch(
            url,
            headless=True,
            solve_cloudflare=True,
            proxy=proxy,
            timeout=STEALTHY_TIMEOUT,
            wait=STEALTHY_WAIT,
        )
        if page.status == 200:
            return str(page.html_content)
        sys.exit(1)
    except SystemExit:
        raise
    except Exception:
        sys.exit(1)


# --------------------------------------------------------------------------- #
# Title extraction (multi-level fallback)
# --------------------------------------------------------------------------- #
def _clean(text: str) -> str:
    """Decode HTML entities and collapse whitespace."""
    if not text:
        return ""
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def extract_title(html_text: str) -> str:
    """
    Extract article title with strict priority:
      1. <meta property="og:title" content="...">
      2. <meta name="twitter:title" content="...">
      3. <title>...</title>
      4. <h1>...</h1> (first on page)
      5. "Untitled" if nothing matched.
    """
    soup = BeautifulSoup(html_text, "html.parser")

    # 1. og:title
    tag = soup.find("meta", attrs={"property": "og:title"})
    if tag and tag.get("content"):
        title = _clean(tag["content"])
        if title:
            return title

    # 2. twitter:title
    tag = soup.find("meta", attrs={"name": "twitter:title"})
    if tag and tag.get("content"):
        title = _clean(tag["content"])
        if title:
            return title

    # 3. <title>
    if soup.title and soup.title.string:
        title = _clean(soup.title.string)
        if title:
            return title

    # 4. first <h1>
    h1 = soup.find("h1")
    if h1 and h1.get_text():
        title = _clean(h1.get_text())
        if title:
            return title

    # 5. fallback
    return "Untitled"


# --------------------------------------------------------------------------- #
# Content extraction (trafilatura -> markdown directly)
# --------------------------------------------------------------------------- #
def extract_content(html_text: str) -> str:
    """
    Extract main article content as Markdown via trafilatura.

    Image inclusion is governed by the module-level constant `IMAGES`:
      0 -> include_images=False (image markdown tags are stripped)
      1 -> include_images=True  (image markdown tags are kept)

    No fallback: if trafilatura returns None or empty -> sys.exit(1).
    """
    md = trafilatura.extract(
        html_text,
        output_format="markdown",
        include_images=bool(IMAGES),
        include_links=True,
        include_tables=True,
        with_metadata=False,
        # favor_recall=True: maximize content recall over precision.
        # Without this, trafilatura drops images and ~17% of body text on
        # sites like gamersdecide.com (Drupal-based) — verified during testing.
        favor_recall=True,
    )
    if not md or not md.strip():
        sys.exit(1)
    return md


# --------------------------------------------------------------------------- #
# Article length validation
# --------------------------------------------------------------------------- #
def check_length(md: str) -> None:
    """
    Validate that the extracted article length falls within [MIN_SYMBOLS, MAX_SYMBOLS].

    The check uses the stripped length of the Markdown body (without the
    `# {title}` prefix that is added later in save_markdown).

    Out-of-range articles are treated as junk -> silent sys.exit(1).
    """
    n = len(md.strip())
    if n < MIN_SYMBOLS or n > MAX_SYMBOLS:
        sys.exit(1)


# --------------------------------------------------------------------------- #
# Absolutize image URLs
# --------------------------------------------------------------------------- #
_MD_IMG_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


def absolutize_images(md: str, base_url: str) -> str:
    """
    Convert relative image URLs in Markdown to absolute URLs using urljoin.
    Already-absolute URLs are returned unchanged by urljoin.
    """
    def _repl(m: re.Match) -> str:
        alt = m.group(1)
        src = m.group(2)
        abs_src = urljoin(base_url, src)
        return f"![{alt}]({abs_src})"

    return _MD_IMG_RE.sub(_repl, md)


# --------------------------------------------------------------------------- #
# Output path resolution + save
# --------------------------------------------------------------------------- #
def resolve_output_path(filename_arg: str) -> str:
    """
    Resolve the --filename CLI argument into a final absolute file path.

    Two modes:
      1. Bare filename (no directory component) — file is saved into the
         directory where scraper.py lives. The '.md' extension is appended
         if missing.
      2. Full path with directory component — file is saved there. Parent
         directories are created automatically (os.makedirs(..., exist_ok=True)).
         The '.md' extension is appended if the supplied name has no extension;
         if the supplied name already ends in '.md' it is kept as-is.

    Examples (Linux):
      'my_article'                          -> /path/to/scraper_dir/my_article.md
      '/home/me/art/name'                   -> /home/me/art/name.md
      '/home/me/art/name.md'                -> /home/me/art/name.md
      '/home/me/art/sub1/sub2/deep.md'      -> /home/me/art/sub1/sub2/deep.md
    """
    # Decide directory + name parts.
    head, tail = os.path.split(filename_arg)

    if not head:
        # Mode 1: bare filename -> place next to scraper.py
        out_dir = _SCRIPT_DIR
        name = tail
    else:
        # Mode 2: path with directory component -> use as-is (absolute or
        # relative to CWD, depending on what the user passed).
        out_dir = head
        name = tail

    # Normalize the directory (expand ~, make absolute). Skip '~' expansion
    # if head is empty (already handled above) but harmless.
    out_dir = os.path.abspath(os.path.expanduser(out_dir))

    # Append .md if the name has no extension at all.
    root, ext = os.path.splitext(name)
    if not ext:
        name = name + ".md"
    elif ext.lower() != ".md":
        # If user supplied a different extension, keep their name as-is —
        # they explicitly chose that extension. But for clarity, we only
        # enforce '.md' when there is no extension.
        pass

    return os.path.join(out_dir, name)


def save_markdown(out_path: str, title: str, body: str) -> str:
    """
    Write the final Markdown to `out_path` (an absolute file path).
    Parent directories are created if they don't exist.
    Layout:
        # {title}

        {body}
    Returns the path written.
    """
    parent = os.path.dirname(out_path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    content = f"# {title}\n\n{body}"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)
    return out_path


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> None:
    args = parse_args()

    html_text = fetch_html(args.url, args.proxy)
    article_title = extract_title(html_text)
    article_md = extract_content(html_text)
    # Reject junk articles (too short = nav/index/stub, too long = portal/dump)
    # before any further processing. The check runs on the raw extracted body,
    # BEFORE image-URL absolutization (which would inflate length).
    check_length(article_md)
    # Only absolutize image URLs when images are actually kept in the output.
    if IMAGES:
        article_md = absolutize_images(article_md, args.url)

    out_path = resolve_output_path(args.filename)
    save_markdown(out_path, article_title, article_md)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        # Silent exit on any unexpected error (project rule: no tracebacks)
        sys.exit(1)
