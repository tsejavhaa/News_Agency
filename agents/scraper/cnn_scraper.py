"""
CNN News Scraper  v2
─────────────────────────────────────────────────────────────
Key findings from debug run:
  - rss.cnn.com is dead (SSL EOF on all feeds)
  - CNN serves from edition.cnn.com, not www.cnn.com
  - Sitemaps confirmed in robots.txt:
      https://www.cnn.com/sitemap/news.xml         <- Google News sitemap (recent)
      https://www.cnn.com/sitemaps/sitemap-section.xml  <- all sections

URL discovery:
  Primary  : CNN news sitemap (gives clean URLs + titles)
  Fallback : Section page crawl on edition.cnn.com

Article detail:
  Body     : div.article__content p
  Image    : og:image meta (media.cnn.com CDN)

Storage:
  image_data/cnn_images/
  database/cnn_articles.json
  database/cnn_seen_urls.txt

Run:
  python agents/scraper/cnn_scraper.py
"""

import hashlib
import json
import os
import random
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, asdict, field
from datetime import datetime
from io import BytesIO
from typing import Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from PIL import Image
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Brotli decompression (CNN uses br encoding)
try:
    import brotli
    BROTLI_OK = True
except ImportError:
    BROTLI_OK = False
    print("[WARN] brotli not installed — run: pip install brotli")

# ─────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────

BASE_URL    = "https://edition.cnn.com"     # ← correct domain
IMAGE_DIR   = os.path.join("image_data", "cnn_images")
DB_DIR      = "database"
JSON_FILE   = os.path.join(DB_DIR, "cnn_articles.json")
SEEN_FILE   = os.path.join(DB_DIR, "cnn_seen_urls.txt")

IMG_MAX_W   = 800
IMG_MAX_H   = 600
IMG_QUALITY = 85
DELAY_MIN   = 1.5
DELAY_MAX   = 3.5

# ── Sitemaps (from robots.txt) ────────────────────────────────
SITEMAPS = [
    "https://www.cnn.com/sitemap/news.xml",              # Google News sitemap — recent articles
    "https://www.cnn.com/sitemaps/sitemap-section.xml",  # all section index → child sitemaps
]

# ── Section pages on edition.cnn.com (fallback) ───────────────
SECTION_URLS = [
    "https://edition.cnn.com/world",
    "https://edition.cnn.com/us",
    "https://edition.cnn.com/politics",
    "https://edition.cnn.com/business",
    "https://edition.cnn.com/health",
    "https://edition.cnn.com/entertainment",
    "https://edition.cnn.com/sports",
    "https://edition.cnn.com/science",
    "https://edition.cnn.com/climate",
    "https://edition.cnn.com/style",
    "https://edition.cnn.com/travel",
    "https://edition.cnn.com/business/tech",
    "https://edition.cnn.com/world/africa",
    "https://edition.cnn.com/world/americas",
    "https://edition.cnn.com/world/asia",
    "https://edition.cnn.com/world/europe",
    "https://edition.cnn.com/world/middle-east",
    "https://edition.cnn.com/world/china",
    "https://edition.cnn.com/world/india",
    "https://edition.cnn.com/world/united-kingdom",
    "https://edition.cnn.com/us/crime-and-justice",
    "https://edition.cnn.com/us/immigration",
]

# CNN article URL patterns — debug confirmed current format:
# https://www.cnn.com/2026/05/17/politics/national-mall-prayer-event
# https://www.cnn.com/2026/05/17/asia/maldives-diving-italians-die-cave-hnk-intl
# https://edition.cnn.com/2026/05/14/europe/ukraine-kyiv-apartment-building-...
ARTICLE_RE = re.compile(
    r"https://(?:www|edition|us)\.cnn\.com/"
    r"(?:"
    r"\d{4}/\d{2}/\d{2}/.+"           # NEW: /YYYY/MM/DD/section/slug  ← primary format
    r"|[a-z][a-z0-9/-]+/\d{4}/\d{2}/\d{2}/.+"  # /section/YYYY/MM/DD/slug
    r")"
)

# Skip these regardless
SKIP_PATTERNS = [
    "/video/", "/gallery/", "/live-news/", "/audio/",
    "/interactive/", "/games/", "/cnn-underscored/",
    "/markets/", "/weather/", "/watch/", "/live-tv",
    "/account/", "/newsletters", "/follow", "/polling",
    "/financial-calculators", "/transcripts", "/profiles",
    "/tv/", "/fast/", "/podcasts/",
]

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
    source:       str = "CNN"
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
        # Treat www / edition / us as same domain
        host = re.sub(r"^(www|edition|us)\.cnn\.com$", "edition.cnn.com", p.netloc)
        return f"{p.scheme}://{host}{p.path}".rstrip("/")


# ─────────────────────────────────────────────────────────────
# HTTP session
# ─────────────────────────────────────────────────────────────

def make_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(total=3, backoff_factor=1.5,
                  status_forcelist=[429, 500, 502, 503, 504])
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def req_headers(xml: bool = False) -> dict:
    return {
        "User-Agent":      random.choice(USER_AGENTS),
        "Accept":          "application/xml,text/xml,*/*;q=0.8" if xml else "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        # Exclude br so CNN sends gzip/deflate which requests handles natively.
        # If brotli is installed we handle it manually anyway.
        "Accept-Encoding": "gzip, deflate",
        "Cache-Control":   "no-cache",
    }


def _decompress(r: requests.Response) -> str:
    """Decompress response body handling brotli, gzip, or plain text."""
    enc = r.headers.get("Content-Encoding", "")
    if "br" in enc:
        if BROTLI_OK:
            return brotli.decompress(r.content).decode("utf-8", errors="replace")
        else:
            raise RuntimeError("Response is brotli-encoded but brotli not installed. Run: pip install brotli")
    # requests handles gzip/deflate automatically via r.text
    return r.text


def fetch_html(session: requests.Session, url: str) -> Optional[BeautifulSoup]:
    time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))
    try:
        r = session.get(url, headers=req_headers(), timeout=20)
        if r.status_code == 200:
            return BeautifulSoup(_decompress(r), "lxml")
        print(f"  [HTTP] {r.status_code} — {url}")
        return None
    except Exception as e:
        print(f"  [HTTP] Error: {e}")
        return None


def fetch_xml(session: requests.Session, url: str) -> Optional[str]:
    try:
        time.sleep(random.uniform(0.5, 1.5))
        r = session.get(url, headers=req_headers(xml=True), timeout=20)
        if r.status_code == 200:
            return _decompress(r)
        print(f"  [XML] {r.status_code} — {url}")
        return None
    except Exception as e:
        print(f"  [XML] Error: {e}")
        return None


# ─────────────────────────────────────────────────────────────
# URL helpers
# ─────────────────────────────────────────────────────────────

def is_article(url: str) -> bool:
    if not url or not url.startswith("http"):
        return False
    if any(s in url for s in SKIP_PATTERNS):
        return False
    return bool(ARTICLE_RE.match(url))


def normalise_cnn_url(url: str) -> str:
    """Always use edition.cnn.com."""
    return re.sub(
        r"https?://(www|us)\.cnn\.com/",
        "https://edition.cnn.com/",
        url
    )


# ─────────────────────────────────────────────────────────────
# Phase 1a: Sitemap discovery
# ─────────────────────────────────────────────────────────────

def parse_sitemap(session: requests.Session, url: str,
                  pool: dict, depth: int = 0) -> dict:
    if depth > 2:
        return pool

    xml_text = fetch_xml(session, url)
    if not xml_text:
        return pool

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        print(f"  [XML] Parse error for {url}: {e}")
        return pool

    # Use Clark notation {namespace}tag — works regardless of prefix declarations
    # Debug showed root tag: {http://www.sitemaps.org/schemas/sitemap/0.9}urlset
    SM    = "http://www.sitemaps.org/schemas/sitemap/0.9"
    NEWS  = "http://www.google.com/schemas/sitemap-news/0.9"
    IMAGE = "http://www.google.com/schemas/sitemap-image/1.1"

    def sm(tag):   return f"{{{SM}}}{tag}"
    def news(tag): return f"{{{NEWS}}}{tag}"
    def img(tag):  return f"{{{IMAGE}}}{tag}"

    def find_text(elem, *path):
        """Walk a tag path, return text of final element or ''."""
        cur = elem
        for tag in path:
            cur = cur.find(tag)
            if cur is None:
                return ""
        return (cur.text or "").strip()

    # Sitemap index → recurse
    child_sitemaps = root.findall(f".//{sm('sitemap')}/{sm('loc')}")
    if child_sitemaps:
        print(f"  [SITEMAP] Index with {len(child_sitemaps)} children: {url}")
        for child in child_sitemaps:
            child_url = (child.text or "").strip()
            if child_url:
                print(f"    -> {child_url}")
                parse_sitemap(session, child_url, pool, depth + 1)
        return pool

    # Article sitemap → extract URLs
    url_elems = root.findall(f".//{sm('url')}")
    new = 0
    for url_elem in url_elems:
        loc = find_text(url_elem, sm("loc"))
        if not loc:
            continue
        loc = normalise_cnn_url(loc)
        if not is_article(loc):
            continue
        norm = SeenRegistry._norm(loc)
        if norm in pool:
            continue

        # Title and date from news:news block
        news_elem = url_elem.find(news("news"))
        title = find_text(news_elem, news("title")) if news_elem is not None else ""
        pub   = find_text(news_elem, news("publication_date")) if news_elem is not None else ""

        # Image from image:image block
        img_elem = url_elem.find(img("image"))
        img_url  = find_text(img_elem, img("loc")) if img_elem is not None else None

        pool[norm] = {
            "url":          loc,
            "title":        title,
            "description":  "",
            "published_at": pub,
            "image_url":    img_url or None,
            "author":       "",
        }
        new += 1

    print(f"  [SITEMAP] {new} new articles from {url.split('/')[-1]}  pool={len(pool)}")
    return pool


def discover_via_sitemaps(session: requests.Session) -> dict:
    pool: dict = {}
    for sm_url in SITEMAPS:
        print(f"\n  Sitemap: {sm_url}")
        parse_sitemap(session, sm_url, pool)
    return pool


# ─────────────────────────────────────────────────────────────
# Phase 1b: Section page fallback
# ─────────────────────────────────────────────────────────────

def discover_via_sections(session: requests.Session, pool: dict) -> dict:
    print(f"\n  Crawling {len(SECTION_URLS)} section pages on edition.cnn.com...")

    for sec_url in SECTION_URLS:
        print(f"  Section: {sec_url}")
        soup = fetch_html(session, sec_url)
        if not soup:
            continue

        new = 0
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.startswith("/"):
                href = "https://edition.cnn.com" + href
            href = normalise_cnn_url(href)
            if not is_article(href):
                continue
            norm = SeenRegistry._norm(href)
            if norm in pool:
                continue

            # Title from anchor text or nearby heading
            title = a.get_text(strip=True)
            if len(title) < 10:
                parent = a.find_parent(["article", "div", "li"])
                if parent:
                    h = parent.find(["h1", "h2", "h3"])
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

        print(f"    -> +{new} new  pool={len(pool)}")

    return pool


# ─────────────────────────────────────────────────────────────
# Article page scraping
# ─────────────────────────────────────────────────────────────

def scrape_article(session: requests.Session, url: str) \
        -> tuple[str, str, str, Optional[str]]:
    """Returns (description, published_at, author, image_url)."""
    soup = fetch_html(session, url)
    if not soup:
        return "", "", "", None

    # ── Description ──────────────────────────────────────────
    desc = ""
    for sel in [
        "div.article__content p",
        "div.article-body-text p",
        "div[class*='body-text'] p",
        "div.zn-body__paragraph",
        "section[class*='article'] p",
    ]:
        paras = soup.select(sel)
        if paras:
            desc = " ".join(p.get_text(strip=True) for p in paras[:5])
            if desc:
                break

    if not desc:
        art = soup.find("article")
        if art:
            desc = " ".join(p.get_text(strip=True)
                            for p in art.find_all("p")[:5])
    if not desc:
        meta = (soup.find("meta", {"property": "og:description"}) or
                soup.find("meta", {"name": "description"}))
        if meta:
            desc = meta.get("content", "")

    desc = re.sub(r"\s+", " ", desc).strip()[:800]

    # ── Published date ────────────────────────────────────────
    pub = ""
    t = soup.find("time")
    if t:
        pub = t.get("datetime", "") or t.get_text(strip=True)
    if not pub:
        m = soup.find("meta", {"property": "article:published_time"})
        if m:
            pub = m.get("content", "")

    # ── Author ────────────────────────────────────────────────
    author = ""
    byline = (soup.find(class_=re.compile(r"byline|author", re.I)) or
              soup.find("a", {"rel": "author"}))
    if byline:
        author = byline.get_text(strip=True)[:100]

    # ── Image ─────────────────────────────────────────────────
    img_url = None
    og = soup.find("meta", {"property": "og:image"})
    if og and og.get("content"):
        img_url = og["content"]
        if "cnn-placeholder" in img_url:
            img_url = None
    if not img_url:
        fig = soup.find("figure")
        if fig:
            img_tag = fig.find("img")
            if img_tag:
                src = (img_tag.get("src") or img_tag.get("data-src"))
                if src and src.startswith("http"):
                    img_url = src

    return desc, pub, author, img_url


# ─────────────────────────────────────────────────────────────
# Image download + resize
# ─────────────────────────────────────────────────────────────

def download_image(session: requests.Session,
                   img_url: str, article_id: str) -> Optional[str]:
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
    print("CNN News Scraper  v2  (Sitemap + edition.cnn.com)")
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

    # ── Phase 1: discover ────────────────────────────────────
    print(f"\n[PHASE 1] Discovering via sitemaps...")
    print("─" * 60)
    pool = discover_via_sitemaps(session)

    if len(pool) < 10:
        print(f"\n[PHASE 1] Sitemap gave {len(pool)} articles — adding section pages...")
        pool = discover_via_sections(session, pool)

    print(f"\n[PHASE 1] Total unique articles: {len(pool):,}")

    # ── Phase 2: filter unseen ───────────────────────────────
    to_scrape = [
        v for k, v in pool.items()
        if not seen.is_seen(v["url"]) and v["url"] not in existing_urls
    ]
    skipped = len(pool) - len(to_scrape)
    print(f"\n[PHASE 2] {len(to_scrape):,} new  |  {skipped:,} already seen")
    print("─" * 60)

    # ── Phase 3: scrape articles ─────────────────────────────
    for i, item in enumerate(to_scrape, 1):
        url   = item["url"]
        title = item["title"]

        print(f"\n[{i:04d}/{len(to_scrape):04d}] {title[:70] or url}")
        print(f"          {url}")

        art_id  = hashlib.md5(url.encode()).hexdigest()[:14]
        desc    = item.get("description", "")
        pub     = item.get("published_at", "")
        author  = item.get("author", "")
        img_url = item.get("image_url")

        # Only fetch article page if we're missing data
        if not desc or not img_url:
            pg_desc, pg_pub, pg_author, pg_img = scrape_article(session, url)
            if not desc:    desc    = pg_desc
            if not pub:     pub     = pg_pub
            if not author:  author  = pg_author
            if not img_url: img_url = pg_img

        # Sitemap titles are sometimes empty — use og:title if so
        if not title and img_url:
            title = url.split("/")[-2].replace("-", " ").title()

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

    # ── Final save ───────────────────────────────────────────
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