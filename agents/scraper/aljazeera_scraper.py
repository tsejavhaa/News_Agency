"""
Al Jazeera English News Scraper
─────────────────────────────────────────────────────────────
Uses plain requests + BeautifulSoup (no Playwright needed).
Al Jazeera has no bot protection.

URL discovery:
  - Primary: RSS feeds per section (all.xml + per-topic feeds)
  - Fallback: section page HTML crawl

Article detail:
  - Body: .wysiwyg-paragraph p  (Al Jazeera article body class)
  - Image: og:image meta tag

Storage layout:
  image_data/
    aljazeera_images/           <- resized article images
  database/
    aljazeera_articles.json     <- all scraped articles
    aljazeera_seen_urls.txt     <- skip registry

Install:
  pip install requests feedparser beautifulsoup4 pillow lxml

Run:
  python agents/scraper/aljazeera_scraper.py
"""

import feedparser
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

# ─────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────

BASE_URL    = "https://www.aljazeera.com"
IMAGE_DIR   = os.path.join("image_data", "aljazeera_images")
DB_DIR      = "database"
JSON_FILE   = os.path.join(DB_DIR, "aljazeera_articles.json")
SEEN_FILE   = os.path.join(DB_DIR, "aljazeera_seen_urls.txt")

IMG_MAX_W   = 800
IMG_MAX_H   = 600
IMG_QUALITY = 85

DELAY_MIN   = 1.5
DELAY_MAX   = 3.5

# ── RSS feeds (primary discovery) ────────────────────────────
RSS_FEEDS = [
    "https://www.aljazeera.com/xml/rss/all.xml",          # all news
    "https://www.aljazeera.com/xml/rss/sports.xml",
    "https://www.aljazeera.com/xml/rss/features.xml",
    "https://www.aljazeera.com/xml/rss/opinions.xml",
]

# ── Section pages (fallback / extra coverage) ─────────────────
SECTION_URLS = [
    # Main sections (working)
    "https://www.aljazeera.com/news/",
    "https://www.aljazeera.com/economy/",
    "https://www.aljazeera.com/sports/",
    "https://www.aljazeera.com/features/",
    "https://www.aljazeera.com/opinions/",
    "https://www.aljazeera.com/investigations/",
    "https://www.aljazeera.com/interactives/",
    # Topic tag pages (correct URL structure as of 2025)
    "https://www.aljazeera.com/tag/technology/",
    "https://www.aljazeera.com/tag/science-and-technology/",
    "https://www.aljazeera.com/tag/health/",
    "https://www.aljazeera.com/tag/environment/",
    "https://www.aljazeera.com/tag/human-rights/",
    "https://www.aljazeera.com/tag/climate-crisis/",
    "https://www.aljazeera.com/tag/business-and-economy/",
    "https://www.aljazeera.com/tag/war-and-conflict/",
    "https://www.aljazeera.com/tag/politics/",
    "https://www.aljazeera.com/tag/united-states/",
    "https://www.aljazeera.com/tag/europe/",
    "https://www.aljazeera.com/tag/middle-east/",
    "https://www.aljazeera.com/tag/africa/",
    "https://www.aljazeera.com/tag/asia/",
    "https://www.aljazeera.com/tag/asia-pacific/",
    "https://www.aljazeera.com/tag/latin-america/",
    "https://www.aljazeera.com/tag/china/",
    "https://www.aljazeera.com/tag/russia/",
    "https://www.aljazeera.com/tag/ukraine/",
    "https://www.aljazeera.com/tag/israel-palestine-conflict/",
    "https://www.aljazeera.com/tag/iran/",
    "https://www.aljazeera.com/tag/india/",
]

# Al Jazeera article URL pattern
ARTICLE_RE = re.compile(
    r"https://www\.aljazeera\.com/"
    r"(news|economy|sports|science-and-technology|health|"
    r"environment|human-rights|features|opinions|where|"
    r"program|investigations|interactives)/"
    r".+/\d{4}/\d{1,2}/\d{1,2}/"
)

# Newer URL style: /news/2025/5/15/slug
ARTICLE_RE2 = re.compile(
    r"https://www\.aljazeera\.com/"
    r"(news|economy|sports|science-and-technology|health|"
    r"environment|human-rights|features|opinions|where|"
    r"program|investigations|interactives)/"
    r"\d{4}/\d{1,2}/\d{1,2}/.+"
)

# Tag page articles: /tag/technology/2025/5/15/slug
ARTICLE_RE3 = re.compile(
    r"https://www\.aljazeera\.com/[a-z/-]+/\d{4}/\d{1,2}/\d{1,2}/.+"
)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
]


# ─────────────────────────────────────────────────────────────
# Data model
# ─────────────────────────────────────────────────────────────

@dataclass
class Article:
    id:           str
    title:        str
    description:  str
    url:          str
    image_url:    Optional[str]
    image_path:   Optional[str]
    published_at: str = ""
    author:       str = ""
    source:       str = "Al Jazeera"
    scraped_at:   str = field(default_factory=lambda: datetime.now().isoformat())


# ─────────────────────────────────────────────────────────────
# Seen-URL registry
# ─────────────────────────────────────────────────────────────

class SeenRegistry:
    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._seen: set[str] = set()
        if os.path.exists(path):
            with open(path) as f:
                self._seen = {l.strip() for l in f if l.strip()}
            print(f"[SEEN] Loaded {len(self._seen):,} previously scraped URLs")
        else:
            print("[SEEN] No previous run — starting fresh")

    def is_seen(self, url: str) -> bool:
        return self._norm(url) in self._seen

    def mark(self, url: str):
        n = self._norm(url)
        self._seen.add(n)
        with open(self.path, "a") as f:
            f.write(n + "\n")

    @staticmethod
    def _norm(url: str) -> str:
        p = urlparse(url)
        return f"{p.scheme}://{p.netloc}{p.path}".rstrip("/")


# ─────────────────────────────────────────────────────────────
# HTTP session
# ─────────────────────────────────────────────────────────────

def make_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(total=3, backoff_factor=1.5, status_forcelist=[429, 500, 502, 503, 504])
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def req_headers() -> dict:
    return {
        "User-Agent":      random.choice(USER_AGENTS),
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control":   "no-cache",
    }


def fetch(session: requests.Session, url: str) -> Optional[BeautifulSoup]:
    time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))
    try:
        r = session.get(url, headers=req_headers(), timeout=20)
        if r.status_code == 200:
            return BeautifulSoup(r.text, "lxml")
        print(f"  [HTTP] {r.status_code} — {url}")
        return None
    except Exception as e:
        print(f"  [HTTP] Error: {e}")
        return None


# ─────────────────────────────────────────────────────────────
# Phase 1a: RSS discovery
# ─────────────────────────────────────────────────────────────

def discover_via_rss() -> dict[str, dict]:
    """Parse all RSS feeds. Returns norm_url -> {url, title, description, published_at, image_url}."""
    pool: dict[str, dict] = {}

    for feed_url in RSS_FEEDS:
        print(f"  RSS: {feed_url}")
        try:
            feed = feedparser.parse(feed_url)
            new  = 0
            for entry in feed.entries:
                url = entry.get("link", "")
                if not url:
                    continue
                if not url.startswith("http"):
                    url = BASE_URL + url
                norm = SeenRegistry._norm(url)
                if norm in pool:
                    continue

                title = entry.get("title", "").strip()
                if not title:
                    continue

                # Description from RSS summary
                summary = entry.get("summary", "")
                desc = BeautifulSoup(summary, "html.parser").get_text(" ").strip()
                desc = re.sub(r"\s+", " ", desc)[:800]

                # Published date
                pub = ""
                if entry.get("published_parsed"):
                    try:
                        pub = datetime(*entry.published_parsed[:6]).isoformat()
                    except Exception:
                        pass

                # Image from media:content or enclosure
                img_url = None
                if entry.get("media_content"):
                    for m in entry.media_content:
                        if "image" in m.get("type", "") or m.get("url", "").endswith(
                            (".jpg", ".jpeg", ".png", ".webp")
                        ):
                            img_url = m.get("url")
                            break
                if not img_url and entry.get("enclosures"):
                    for enc in entry.enclosures:
                        if "image" in enc.get("type", ""):
                            img_url = enc.get("href") or enc.get("url")
                            break
                if not img_url and entry.get("media_thumbnail"):
                    img_url = entry.media_thumbnail[0].get("url")

                pool[norm] = {
                    "url":          url,
                    "title":        title,
                    "description":  desc,
                    "published_at": pub,
                    "image_url":    img_url,
                    "author":       "",
                }
                new += 1

            print(f"    → {len(feed.entries)} entries, +{new} new  pool={len(pool)}")
        except Exception as e:
            print(f"    → Error: {e}")

    return pool


# ─────────────────────────────────────────────────────────────
# Phase 1b: Section page discovery (fallback / extra)
# ─────────────────────────────────────────────────────────────

def is_article_url(url: str) -> bool:
    # Skip tag index pages, video pages, live blogs
    if any(s in url for s in ["/tag/", "/video/", "/liveblog/", "/gallery/"]):
        # tag pages are index pages — skip unless they contain a date path
        if "/tag/" in url and not re.search(r"/\d{4}/\d{1,2}/\d{1,2}/", url):
            return False
    return bool(ARTICLE_RE.match(url) or ARTICLE_RE2.match(url) or ARTICLE_RE3.match(url))


def discover_via_sections(session: requests.Session, pool: dict) -> dict[str, dict]:
    """Crawl section pages for article links not caught by RSS."""
    print(f"\n  Crawling {len(SECTION_URLS)} section pages...")

    for sec_url in SECTION_URLS:
        print(f"  Section: {sec_url}")
        soup = fetch(session, sec_url)
        if not soup:
            continue

        new = 0
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.startswith("/"):
                href = BASE_URL + href
            if not is_article_url(href):
                continue
            norm = SeenRegistry._norm(href)
            if norm in pool:
                continue
            title = a.get_text(strip=True)
            if len(title) < 10:
                # Try parent element for title
                parent = a.find_parent(["article", "div", "li"])
                if parent:
                    h = parent.find(["h1", "h2", "h3", "h4"])
                    if h:
                        title = h.get_text(strip=True)
            if len(title) < 10:
                continue
            pool[norm] = {
                "url":          href,
                "title":        title,
                "description":  "",
                "published_at": "",
                "image_url":    None,
                "author":       "",
            }
            new += 1

        print(f"    → +{new} new  pool={len(pool)}")

    return pool


# ─────────────────────────────────────────────────────────────
# Article page scraping
# ─────────────────────────────────────────────────────────────

def scrape_article(session: requests.Session, url: str) -> tuple[str, str, str, Optional[str]]:
    """
    Returns (description, published_at, author, image_url).
    Al Jazeera article body uses:
      - .wysiwyg-paragraph p   (main article body)
      - p.article__paragraph   (older structure)
    """
    soup = fetch(session, url)
    if not soup:
        return "", "", "", None

    # ── Description ──────────────────────────────────────────
    desc = ""

    # Current Al Jazeera structure
    paras = soup.select(".wysiwyg-paragraph p")
    if paras:
        desc = " ".join(p.get_text(strip=True) for p in paras[:5])

    # Older structure
    if not desc:
        paras = soup.select("p.article__paragraph")
        if paras:
            desc = " ".join(p.get_text(strip=True) for p in paras[:5])

    # Generic article fallback
    if not desc:
        art = soup.find("article")
        if art:
            desc = " ".join(p.get_text(strip=True) for p in art.find_all("p")[:5])

    # og:description fallback
    if not desc:
        meta = (soup.find("meta", {"property": "og:description"}) or
                soup.find("meta", {"name": "description"}))
        if meta:
            desc = meta.get("content", "")

    desc = re.sub(r"\s+", " ", desc).strip()[:800]

    # ── Published date ────────────────────────────────────────
    pub = ""
    time_el = soup.find("time")
    if time_el:
        pub = time_el.get("datetime", "") or time_el.get_text(strip=True)
    if not pub:
        meta_pub = (soup.find("meta", {"property": "article:published_time"}) or
                    soup.find("meta", {"name": "published_time"}))
        if meta_pub:
            pub = meta_pub.get("content", "")

    # ── Author ────────────────────────────────────────────────
    author = ""
    author_el = (soup.find(class_=re.compile("article-author|author-name|byline")) or
                 soup.find("a", {"rel": "author"}))
    if author_el:
        author = author_el.get_text(strip=True)

    # ── Image ─────────────────────────────────────────────────
    img_url = None
    og = soup.find("meta", {"property": "og:image"})
    if og and og.get("content"):
        img_url = og["content"]
    if not img_url:
        fig = soup.find("figure")
        if fig:
            img_tag = fig.find("img")
            if img_tag:
                src = img_tag.get("src") or img_tag.get("data-src") or img_tag.get("data-lazy-src")
                if src:
                    img_url = urljoin(url, src)

    return desc, pub, author, img_url


# ─────────────────────────────────────────────────────────────
# Image download + resize
# ─────────────────────────────────────────────────────────────

def download_image(session: requests.Session, img_url: str, article_id: str) -> Optional[str]:
    os.makedirs(IMAGE_DIR, exist_ok=True)
    try:
        time.sleep(random.uniform(0.3, 0.8))
        r = session.get(img_url, headers=req_headers(), timeout=15)
        if r.status_code != 200:
            return None
        if "image" not in r.headers.get("Content-Type", ""):
            return None

        img = Image.open(BytesIO(r.content))
        if img.mode != "RGB":
            bg = Image.new("RGB", img.size, (255, 255, 255))
            if img.mode == "RGBA":
                bg.paste(img, mask=img.split()[3])
            else:
                bg.paste(img.convert("RGB"))
            img = bg

        orig = img.size
        img.thumbnail((IMG_MAX_W, IMG_MAX_H), Image.LANCZOS)
        path = os.path.join(IMAGE_DIR, f"{article_id}.jpg")
        img.save(path, "JPEG", quality=IMG_QUALITY, optimize=True)
        print(f"    [IMG] {orig[0]}x{orig[1]} -> {img.size[0]}x{img.size[1]}  {path}")
        return path
    except Exception as e:
        print(f"    [IMG] Failed: {e}")
        return None


# ─────────────────────────────────────────────────────────────
# JSON persistence
# ─────────────────────────────────────────────────────────────

def load_existing() -> list[dict]:
    if os.path.exists(JSON_FILE):
        with open(JSON_FILE) as f:
            return json.load(f)
    return []


def save_articles(articles: list[dict]):
    os.makedirs(DB_DIR, exist_ok=True)
    with open(JSON_FILE, "w") as f:
        json.dump(articles, f, indent=2, ensure_ascii=False)


# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────

def run():
    print("=" * 60)
    print("Al Jazeera English News Scraper")
    print(f"Images -> {IMAGE_DIR}")
    print(f"JSON   -> {JSON_FILE}")
    print(f"Seen   -> {SEEN_FILE}")
    print("=" * 60)

    os.makedirs(IMAGE_DIR, exist_ok=True)
    os.makedirs(DB_DIR, exist_ok=True)

    seen          = SeenRegistry(SEEN_FILE)
    session       = make_session()
    existing      = load_existing()
    existing_urls = {a["url"] for a in existing}
    new_articles: list[dict] = []

    # ── Phase 1: discover via RSS + sections ─────────────────
    print(f"\n[PHASE 1] Discovering articles via RSS feeds...")
    print("─" * 60)
    pool = discover_via_rss()

    print(f"\n[PHASE 1] Adding section page links...")
    pool = discover_via_sections(session, pool)
    print(f"\n[PHASE 1] Total unique articles found: {len(pool):,}")

    # ── Phase 2: filter unseen ───────────────────────────────
    to_scrape = [
        v for k, v in pool.items()
        if not seen.is_seen(v["url"]) and v["url"] not in existing_urls
    ]
    skipped = len(pool) - len(to_scrape)
    print(f"\n[PHASE 2] {len(to_scrape):,} new  |  {skipped:,} already seen")
    print("─" * 60)

    # ── Phase 3: scrape each article ─────────────────────────
    for i, item in enumerate(to_scrape, 1):
        url   = item["url"]
        title = item["title"]

        print(f"\n[{i:04d}/{len(to_scrape):04d}] {title[:70]}")
        print(f"          {url}")

        art_id = hashlib.md5(url.encode()).hexdigest()[:14]

        # Use RSS data if already complete, only fetch page if missing desc or image
        desc    = item.get("description", "")
        pub     = item.get("published_at", "")
        author  = item.get("author", "")
        img_url = item.get("image_url")

        if not desc or not img_url:
            page_desc, page_pub, page_author, page_img = scrape_article(session, url)
            if not desc:
                desc = page_desc
            if not pub:
                pub = page_pub
            if not author:
                author = page_author
            if not img_url:
                img_url = page_img

        print(f"    [DESC] {len(desc)} chars | {'OK' if desc else 'EMPTY'}")
        print(f"    [IMG ] {str(img_url or '')[:80] or 'none'}")

        img_path = None
        if img_url:
            img_path = download_image(session, img_url, art_id)

        new_articles.append(asdict(Article(
            id=art_id,
            title=title,
            description=desc,
            url=url,
            image_url=img_url,
            image_path=img_path,
            published_at=pub,
            author=author,
        )))
        seen.mark(url)

        if i % 10 == 0:
            save_articles(existing + new_articles)
            print(f"    [SAVE] {len(existing) + len(new_articles):,} total saved")

    # ── Final save + summary ─────────────────────────────────
    all_articles = existing + new_articles
    save_articles(all_articles)

    n_desc = sum(1 for a in new_articles if a["description"])
    n_img  = sum(1 for a in new_articles if a["image_path"])

    print("\n" + "=" * 60)
    print("COMPLETE")
    print(f"  Previously stored : {len(existing):,}")
    print(f"  Newly scraped     : {len(new_articles):,}")
    print(f"  Skipped (seen)    : {skipped:,}")
    print(f"  Total in DB       : {len(all_articles):,}")
    print(f"  With description  : {n_desc}/{len(new_articles)}")
    print(f"  With image saved  : {n_img}/{len(new_articles)}")
    print(f"  JSON              -> {JSON_FILE}")
    print(f"  Images            -> {IMAGE_DIR}/")
    print("=" * 60)

    if new_articles:
        print("\nSample:")
        for a in new_articles[:3]:
            print(f"\n  {a['title']}")
            print(f"  {a['url']}")
            print(f"  DESC : {a['description'][:120]}...")
            print(f"  IMG  : {a['image_path']}")
            if a.get("author"):
                print(f"  BY   : {a['author']}")


if __name__ == "__main__":
    run()