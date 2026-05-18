"""
BBC News Full Crawler
─────────────────────────────────────────────────────────
Crawls the BBC News homepage + all section pages to collect
every available article (title, description, image).

Storage layout:
  image_data/
    bbc_images/          ← resized article images
  database/
    bbc_articles.json    ← all scraped articles (appended across runs)
    bbc_seen_urls.txt    ← one URL per line; skip if already seen

Run:
  python agents/scraper/bbc_scraper.py

Re-running is safe — already-scraped URLs are skipped instantly.
"""

import hashlib
import json
import os
import random
import re
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime
from io import BytesIO
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from PIL import Image
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ─────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────

BASE_URL    = "https://www.bbc.com"
IMAGE_DIR   = os.path.join("image_data", "bbc_images")
DB_DIR      = "database"
JSON_FILE   = os.path.join(DB_DIR, "bbc_articles.json")
SEEN_FILE   = os.path.join(DB_DIR, "bbc_seen_urls.txt")

IMG_MAX_W   = 800
IMG_MAX_H   = 600
IMG_QUALITY = 85

DELAY_MIN   = 1.2   # seconds between requests (be polite)
DELAY_MAX   = 3.0

# All BBC News section entry points to crawl
SECTION_URLS = [
    "https://www.bbc.com/news",
    "https://www.bbc.com/news/world",
    "https://www.bbc.com/news/uk",
    "https://www.bbc.com/news/business",
    "https://www.bbc.com/news/technology",
    "https://www.bbc.com/news/science_and_environment",
    "https://www.bbc.com/news/health",
    "https://www.bbc.com/news/entertainment_and_arts",
    "https://www.bbc.com/news/politics",
    "https://www.bbc.com/news/education",
    "https://www.bbc.com/sport",
    "https://www.bbc.com/news/world/africa",
    "https://www.bbc.com/news/world/asia",
    "https://www.bbc.com/news/world/australia",
    "https://www.bbc.com/news/world/europe",
    "https://www.bbc.com/news/world/latin_america",
    "https://www.bbc.com/news/world/middle_east",
    "https://www.bbc.com/news/world/us_and_canada",
]

# A URL must match this pattern to be treated as an article (not a section/index page)
ARTICLE_PATTERN = re.compile(
    r"https://www\.bbc\.com/"
    r"(news/articles/[a-z0-9]+|"           # /news/articles/c1234xyz
    r"news/(world|uk|business|technology|science_and_environment|"
    r"health|entertainment_and_arts|politics|education|sport|"
    r"world/africa|world/asia|world/europe|world/us_and_canada|"
    r"world/latin_america|world/middle_east|world/australia)"
    r"/[a-z0-9_-]{10,}|"                   # /news/world/slug-name
    r"sport/[a-z-]+/[a-z0-9_-]{10,})"     # /sport/football/slug
)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]


# ─────────────────────────────────────────────────────────
# Data model
# ─────────────────────────────────────────────────────────

@dataclass
class Article:
    id:          str
    title:       str
    description: str
    url:         str
    image_url:   Optional[str]
    image_path:  Optional[str]
    source:      str = "BBC News"
    scraped_at:  str = field(default_factory=lambda: datetime.now().isoformat())


# ─────────────────────────────────────────────────────────
# Seen-URL registry  (plain text file, one URL per line)
# ─────────────────────────────────────────────────────────

class SeenRegistry:
    """Persists scraped URLs across runs so we never re-scrape."""

    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._seen: set[str] = set()
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            with open(self.path, "r", encoding="utf-8") as f:
                self._seen = {line.strip() for line in f if line.strip()}
            print(f"[SEEN] Loaded {len(self._seen):,} previously scraped URLs")
        else:
            print("[SEEN] No previous run found — starting fresh")

    def is_seen(self, url: str) -> bool:
        return self._normalise(url) in self._seen

    def mark(self, url: str):
        norm = self._normalise(url)
        self._seen.add(norm)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(norm + "\n")

    @staticmethod
    def _normalise(url: str) -> str:
        """Strip query-string and fragment so variants of same URL match."""
        p = urlparse(url)
        return f"{p.scheme}://{p.netloc}{p.path}".rstrip("/")

    def __len__(self):
        return len(self._seen)


# ─────────────────────────────────────────────────────────
# HTTP helpers
# ─────────────────────────────────────────────────────────

def make_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=2.0,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def req_headers() -> dict:
    return {
        "User-Agent":      random.choice(USER_AGENTS),
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-GB,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Sec-Fetch-Dest":  "document",
        "Sec-Fetch-Mode":  "navigate",
        "Sec-Fetch-Site":  "none",
        "Cache-Control":   "no-cache",
    }


def fetch(session: requests.Session, url: str, timeout: int = 20) -> Optional[BeautifulSoup]:
    """GET a page and return parsed BeautifulSoup, or None on failure."""
    try:
        time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))
        r = session.get(url, headers=req_headers(), timeout=timeout)
        if r.status_code == 200:
            return BeautifulSoup(r.text, "lxml")
        print(f"  [HTTP] {r.status_code} — {url}")
        return None
    except Exception as e:
        print(f"  [HTTP] Error fetching {url}: {e}")
        return None


# ─────────────────────────────────────────────────────────
# Link extraction from a page
# ─────────────────────────────────────────────────────────

def extract_article_links(soup: BeautifulSoup, page_url: str) -> list[dict]:
    """
    Pull all article {title, url} pairs from a BBC page.
    Uses data-testid first, falls back to href pattern matching.
    """
    found = {}   # url → title  (dict to deduplicate within page)

    # ── Strategy 1: BBC card links (data-testid) ──────────
    for a in soup.find_all("a", attrs={"data-testid": "internal-link"}):
        href = a.get("href", "")
        if not href:
            continue
        if href.startswith("/"):
            href = BASE_URL + href
        if not ARTICLE_PATTERN.match(href):
            continue

        headline = (
            a.find(attrs={"data-testid": "card-headline"}) or
            a.find("h2") or a.find("h3") or a.find("h4")
        )
        title = headline.get_text(strip=True) if headline else a.get_text(strip=True)
        title = title.strip()
        if len(title) < 10 or href in found:
            continue
        found[href] = title

    # ── Strategy 2: any anchor whose href matches article pattern ──
    if len(found) < 3:
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.startswith("/"):
                href = BASE_URL + href
            if not ARTICLE_PATTERN.match(href) or href in found:
                continue
            title = a.get_text(strip=True)
            if len(title) < 10:
                continue
            found[href] = title

    return [{"title": t, "url": u} for u, t in found.items()]


# ─────────────────────────────────────────────────────────
# Image download + resize
# ─────────────────────────────────────────────────────────

def download_image(
    session: requests.Session, img_url: str, article_id: str
) -> Optional[str]:
    os.makedirs(IMAGE_DIR, exist_ok=True)
    try:
        r = session.get(img_url, headers=req_headers(), timeout=15)
        if r.status_code != 200:
            return None
        ct = r.headers.get("Content-Type", "")
        if "image" not in ct:
            return None

        img = Image.open(BytesIO(r.content))

        # Normalise to RGB
        if img.mode != "RGB":
            bg = Image.new("RGB", img.size, (255, 255, 255))
            if img.mode == "RGBA":
                bg.paste(img, mask=img.split()[3])
            else:
                bg.paste(img.convert("RGB"))
            img = bg

        orig = img.size
        img.thumbnail((IMG_MAX_W, IMG_MAX_H), Image.LANCZOS)
        save_path = os.path.join(IMAGE_DIR, f"{article_id}.jpg")
        img.save(save_path, "JPEG", quality=IMG_QUALITY, optimize=True)
        print(f"    [IMG] {orig[0]}x{orig[1]} → {img.size[0]}x{img.size[1]}  {save_path}")
        return save_path

    except Exception as e:
        print(f"    [IMG] Failed: {e}")
        return None


# ─────────────────────────────────────────────────────────
# Article page scraping
# ─────────────────────────────────────────────────────────

def scrape_article_page(
    session: requests.Session, url: str
) -> tuple[str, Optional[str]]:
    """
    Returns (description, image_url) from an article page.
    """
    soup = fetch(session, url)
    if not soup:
        return "", None

    # ── Description ──────────────────────────────────────
    description = ""

    # BBC article body: data-component="text-block"
    blocks = soup.find_all(attrs={"data-component": "text-block"})
    if blocks:
        paras = [b.find("p") for b in blocks[:5] if b.find("p")]
        description = " ".join(p.get_text(strip=True) for p in paras if p)

    # Fallback: <article> tag paragraphs
    if not description:
        art = soup.find("article")
        if art:
            description = " ".join(
                p.get_text(strip=True) for p in art.find_all("p")[:5]
            )

    # Fallback: og:description or meta description
    if not description:
        meta = (
            soup.find("meta", {"property": "og:description"}) or
            soup.find("meta", {"name": "description"})
        )
        if meta:
            description = meta.get("content", "")

    description = re.sub(r"\s+", " ", description).strip()[:800]

    # ── Image URL ─────────────────────────────────────────
    image_url = None

    # og:image is the canonical hero image on BBC
    og = soup.find("meta", {"property": "og:image"})
    if og and og.get("content"):
        image_url = og["content"]

    # Fallback: first <figure> img src / data-src
    if not image_url:
        fig = soup.find("figure")
        if fig:
            img_tag = fig.find("img")
            if img_tag:
                src = img_tag.get("src") or img_tag.get("data-src")
                if src:
                    image_url = urljoin(url, src)

    return description, image_url


# ─────────────────────────────────────────────────────────
# JSON persistence  (append-safe)
# ─────────────────────────────────────────────────────────

def load_existing_articles() -> list[dict]:
    if os.path.exists(JSON_FILE):
        with open(JSON_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_articles(articles: list[dict]):
    os.makedirs(DB_DIR, exist_ok=True)
    with open(JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(articles, f, indent=2, ensure_ascii=False)


# ─────────────────────────────────────────────────────────
# Main crawler
# ─────────────────────────────────────────────────────────

def run():
    print("=" * 60)
    print("BBC News Full Crawler")
    print(f"Images  → {IMAGE_DIR}")
    print(f"JSON    → {JSON_FILE}")
    print(f"Seen DB → {SEEN_FILE}")
    print("=" * 60)

    os.makedirs(IMAGE_DIR, exist_ok=True)
    os.makedirs(DB_DIR, exist_ok=True)

    seen     = SeenRegistry(SEEN_FILE)
    session  = make_session()
    existing = load_existing_articles()

    # Index existing articles by URL for fast lookup
    existing_by_url = {a["url"]: True for a in existing}
    new_articles: list[dict] = []

    # ── Phase 1: collect all article links from all section pages ──
    print(f"\n[PHASE 1] Crawling {len(SECTION_URLS)} section pages for article links...")
    print("─" * 60)

    all_links: dict[str, str] = {}   # url → title

    for sec_url in SECTION_URLS:
        print(f"\n  Section: {sec_url}")
        soup = fetch(session, sec_url)
        if not soup:
            continue
        links = extract_article_links(soup, sec_url)
        new_links = 0
        for lnk in links:
            norm = SeenRegistry._normalise(lnk["url"])
            if norm not in all_links:
                all_links[norm] = lnk["title"]
                new_links += 1
        print(f"  Found {len(links)} links (+{new_links} new), total pool: {len(all_links)}")

    print(f"\n[PHASE 1] Total unique article URLs found: {len(all_links):,}")

    # ── Phase 2: scrape each unseen article ───────────────
    to_scrape = [
        {"url": u, "title": t}
        for u, t in all_links.items()
        if not seen.is_seen(u) and u not in existing_by_url
    ]
    skipped = len(all_links) - len(to_scrape)

    print(f"\n[PHASE 2] {len(to_scrape):,} new articles to scrape  ({skipped:,} already seen, skipping)")
    print("─" * 60)

    for i, link in enumerate(to_scrape, 1):
        url   = link["url"]
        title = link["title"]

        print(f"\n[{i:04d}/{len(to_scrape):04d}] {title[:70]}")
        print(f"          {url}")

        art_id = hashlib.md5(url.encode()).hexdigest()[:14]

        desc, img_url = scrape_article_page(session, url)
        print(f"    [DESC] {len(desc)} chars | {'✓' if desc else '✗ empty'}")
        print(f"    [IMG ] {img_url[:80] if img_url else 'none'}")

        img_path = None
        if img_url:
            img_path = download_image(session, img_url, art_id)

        article = Article(
            id=art_id,
            title=title,
            description=desc,
            url=url,
            image_url=img_url,
            image_path=img_path,
        )
        new_articles.append(asdict(article))

        # Mark as seen immediately so a crash mid-run doesn't re-scrape
        seen.mark(url)

        # Save every 10 articles so progress isn't lost on crash
        if i % 10 == 0:
            save_articles(existing + new_articles)
            print(f"    [SAVE] Checkpoint: {len(existing) + len(new_articles):,} total articles saved")

    # ── Final save ────────────────────────────────────────
    all_articles = existing + new_articles
    save_articles(all_articles)

    # ── Summary ───────────────────────────────────────────
    n_desc  = sum(1 for a in new_articles if a["description"])
    n_img   = sum(1 for a in new_articles if a["image_path"])

    print("\n" + "=" * 60)
    print("CRAWL COMPLETE")
    print(f"  Previously stored : {len(existing):,}")
    print(f"  Newly scraped     : {len(new_articles):,}")
    print(f"  Skipped (seen)    : {skipped:,}")
    print(f"  Total in DB       : {len(all_articles):,}")
    print(f"  With description  : {n_desc}/{len(new_articles)}")
    print(f"  With image saved  : {n_img}/{len(new_articles)}")
    print(f"  JSON              → {JSON_FILE}")
    print(f"  Images            → {IMAGE_DIR}/")
    print(f"  Seen registry     → {SEEN_FILE}")
    print("=" * 60)

    if new_articles:
        print("\nSample new articles:")
        for a in new_articles[:3]:
            print(f"\n  [{a['id']}] {a['title']}")
            print(f"  URL  : {a['url']}")
            print(f"  DESC : {a['description'][:120]}...")
            print(f"  IMG  : {a['image_path']}")


if __name__ == "__main__":
    run()