"""
Data Manager
─────────────────────────────────────────────────────────────
Orchestrates the full news pipeline:

  1. Runs BBC, CNN, Al Jazeera scrapers with live progress bars
  2. Merges all articles into one master JSON
  3. Uses Ollama (llama3.2:1b) to detect cross-source duplicates
  4. Saves clean deduplicated master_articles.json
  5. Prints full statistical report

Install:
  pip install tqdm requests

Requires Ollama (optional — falls back to Jaccard if offline):
  ollama serve
  ollama pull llama3.2:1b

Run:
  python agents/data_manager.py
"""

import importlib.util
import json
import os
import re
import sys
import time
from datetime import datetime
from typing import Optional

import requests
from tqdm import tqdm

# ─────────────────────────────────────────────────────────────
# Paths
# ─────────────────────────────────────────────────────────────

ROOT        = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_DIR      = os.path.join(ROOT, "database")
MASTER_FILE = os.path.join(DB_DIR, "master_articles.json")
STATS_FILE  = os.path.join(DB_DIR, "pipeline_stats.json")
LOG_FILE    = os.path.join(DB_DIR, "data_manager.log")
SCRAPER_DIR = os.path.join(ROOT, "agents", "scraper")

SCRAPERS = [
    {
        "name":   "BBC News",
        "module": "bbc_scraper",
        "file":   os.path.join(SCRAPER_DIR, "bbc_scraper.py"),
        "json":   os.path.join(DB_DIR, "bbc_articles.json"),
    },
    {
        "name":   "CNN",
        "module": "cnn_scraper",
        "file":   os.path.join(SCRAPER_DIR, "cnn_scraper.py"),
        "json":   os.path.join(DB_DIR, "cnn_articles.json"),
    },
    {
        "name":   "Al Jazeera",
        "module": "aljazeera_scraper",
        "file":   os.path.join(SCRAPER_DIR, "aljazeera_scraper.py"),
        "json":   os.path.join(DB_DIR, "aljazeera_articles.json"),
    },
]

OLLAMA_URL   = "http://localhost:11434"
OLLAMA_MODEL = "llama3.2:1b"

# ─────────────────────────────────────────────────────────────
# ANSI colours
# ─────────────────────────────────────────────────────────────

RST    = "\033[0m"
BOLD   = "\033[1m"
GREEN  = "\033[92m"
RED    = "\033[91m"
CYAN   = "\033[96m"
YELLOW = "\033[93m"
BLUE   = "\033[94m"
DIM    = "\033[2m"

def c(col, text): return f"{col}{text}{RST}"

ANSI_RE = re.compile(r"\033\[[0-9;]*m")
def strip_ansi(s): return ANSI_RE.sub("", s)


# ─────────────────────────────────────────────────────────────
# Logger — stdout + file
# ─────────────────────────────────────────────────────────────

class Logger:
    def __init__(self, path: str):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._f = open(path, "a", encoding="utf-8")
        self._f.write(f"\n{'='*60}\n{datetime.now().isoformat()}\n{'='*60}\n")

    def log(self, msg: str = ""):
        print(msg)
        self._f.write(strip_ansi(msg) + "\n")
        self._f.flush()

    def close(self):
        self._f.write(f"Ended: {datetime.now().isoformat()}\n")
        self._f.close()


# ─────────────────────────────────────────────────────────────
# Dynamic module loader
# ─────────────────────────────────────────────────────────────

def load_module(name: str, filepath: str):
    spec   = importlib.util.spec_from_file_location(name, filepath)
    mod    = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ─────────────────────────────────────────────────────────────
# Stdout interceptor — feeds tqdm bar from scraper output
# ─────────────────────────────────────────────────────────────

class _ScrapeCapture:
    """
    Replaces sys.stdout while a scraper runs.
    Ticks the progress bar on each article line.
    Writes everything to log file, suppresses terminal noise.
    """
    _ART_LINE = re.compile(r"^\[(\d+)/(\d+)\]")
    _PHASE2   = re.compile(r"(\d[\d,]*)\s+new articles to scrape")
    _SKIPPED  = re.compile(r"\((\d[\d,]*),?\s*already seen")

    def __init__(self, bar: tqdm, log_file):
        self.bar      = bar
        self.log_file = log_file
        self.skipped  = 0
        self.phase2_n = 0

    def write(self, text: str):
        t = text.strip()
        if not t:
            return

        # Detect phase 2 total
        m = self._PHASE2.search(t)
        if m:
            n = int(m.group(1).replace(",", ""))
            self.phase2_n = n
            self.bar.total = n
            self.bar.refresh()

        # Detect skipped count
        m = self._SKIPPED.search(t)
        if m:
            self.skipped = int(m.group(1).replace(",", ""))

        # Tick bar on article line
        m = self._ART_LINE.match(t)
        if m:
            self.bar.update(1)
            title = t[len(m.group(0)):].strip()[:50]
            self.bar.set_postfix_str(title, refresh=True)

        # Log to file (no terminal spam)
        self.log_file.write("    " + strip_ansi(t) + "\n")
        self.log_file.flush()

    def flush(self): pass


# ─────────────────────────────────────────────────────────────
# Run one scraper
# ─────────────────────────────────────────────────────────────

def count_json(path: str) -> int:
    if not os.path.exists(path):
        return 0
    try:
        with open(path) as f:
            return len(json.load(f))
    except Exception:
        return 0


def run_scraper(scraper: dict, log: Logger) -> dict:
    name   = scraper["name"]
    before = count_json(scraper["json"])
    t0     = time.time()

    bar = tqdm(
        total=None,
        desc=f"    {name:<14}",
        unit=" art",
        colour="cyan",
        bar_format="{l_bar}{bar:28}{r_bar}",
        dynamic_ncols=True,
        leave=True,
    )

    capture      = _ScrapeCapture(bar, log._f)
    orig_stdout  = sys.stdout
    orig_cwd     = os.getcwd()
    sys.stdout   = capture

    error = None
    try:
        mod = load_module(scraper["module"], scraper["file"])
        os.chdir(ROOT)
        mod.run()
    except Exception as e:
        error = str(e)
    finally:
        os.chdir(orig_cwd)
        sys.stdout = orig_stdout

    bar.close()

    after    = count_json(scraper["json"])
    duration = time.time() - t0
    new_n    = after - before

    status = c(RED, f"ERROR: {error}") if error else c(GREEN, "✓ done")
    log.log(
        f"    {name:<14}  "
        f"{c(GREEN, f'+{new_n:,}')} new  "
        f"{capture.skipped:,} skipped  "
        f"{duration:.1f}s  {status}"
    )

    return {
        "source":   name,
        "before":   before,
        "after":    after,
        "new":      new_n,
        "skipped":  capture.skipped,
        "error":    error,
        "duration": duration,
    }


# ─────────────────────────────────────────────────────────────
# Merge source JSONs
# ─────────────────────────────────────────────────────────────

def load_all_articles() -> list[dict]:
    arts = []
    for s in SCRAPERS:
        if not os.path.exists(s["json"]):
            continue
        try:
            with open(s["json"]) as f:
                batch = json.load(f)
            for a in batch:
                a.setdefault("source", s["name"])
            arts.extend(batch)
        except Exception:
            pass
    return arts


# ─────────────────────────────────────────────────────────────
# Ollama
# ─────────────────────────────────────────────────────────────

def ollama_available() -> bool:
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        if r.status_code != 200:
            return False
        models = [m["name"] for m in r.json().get("models", [])]
        return any(OLLAMA_MODEL in m for m in models)
    except Exception:
        return False


def ollama_is_dup(ta: str, tb: str) -> tuple[bool, float]:
    """
    Title-only comparison — much faster than sending full descriptions.
    llama3.2:1b only needs titles to judge if two articles cover the same story.
    Timeout 8s — if the model is slower than that, treat as non-duplicate.
    """
    prompt = (
        f'Do these two news headlines cover the same story?\n'
        f'A: "{ta[:120]}"\n'
        f'B: "{tb[:120]}"\n'
        f'Reply ONLY: {{"duplicate":true/false,"confidence":0.0-1.0}}'
    )
    try:
        r = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={
                "model":   OLLAMA_MODEL,
                "prompt":  prompt,
                "stream":  False,
                "options": {
                    "temperature": 0.0,
                    "num_predict": 32,   # just need the tiny JSON
                    "num_ctx":     512,  # small context = faster
                },
            },
            timeout=8,
        )
        text = r.json().get("response", "")
        m = re.search(r'\{.*?\}', text, re.DOTALL)
        if not m:
            return False, 0.0
        data = json.loads(m.group())
        return bool(data.get("duplicate", False)), float(data.get("confidence", 0.5))
    except Exception:
        return False, 0.0


# ─────────────────────────────────────────────────────────────
# Fast Jaccard pre-filter
# ─────────────────────────────────────────────────────────────

def jaccard(a: str, b: str) -> float:
    sa, sb = set(a.lower().split()), set(b.lower().split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


# ─────────────────────────────────────────────────────────────
# Deduplication
# ─────────────────────────────────────────────────────────────

def deduplicate(articles: list[dict], log: Logger, use_llm: bool) -> dict:
    """
    Three-tier dedup:
      Tier 1 — URL exact match          → instant duplicate
      Tier 2 — Jaccard ≥ 0.75          → duplicate (no LLM needed)
      Tier 3 — Jaccard 0.50–0.74       → LLM title-only check (parallel, 8s timeout)

    Parallel LLM calls via ThreadPoolExecutor so we don't sit idle
    waiting for Ollama one-at-a-time.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    total   = len(articles)
    removed = set()
    dup_log = []

    llm_calls = 0
    llm_total = 0.0
    llm_times = []

    log.log(f"\n  Comparing {total:,} articles...")

    # ── Pass 1: URL + Jaccard (instant, no LLM) ───────────────
    candidates = []   # pairs to send to LLM: (i, j)

    bar = tqdm(
        total=total,
        desc="    Pass 1/2    ",
        unit=" art",
        colour="yellow",
        bar_format="{l_bar}{bar:28}{r_bar}",
        dynamic_ncols=True,
        leave=True,
    )

    for i in range(total):
        bar.update(1)
        if i in removed:
            continue

        a       = articles[i]
        title_a = a.get("title", "")
        url_a   = (a.get("url") or "").rstrip("/")
        desc_a  = a.get("description", "")

        for j in range(i + 1, total):
            if j in removed:
                continue

            b       = articles[j]
            title_b = b.get("title", "")
            url_b   = (b.get("url") or "").rstrip("/")
            desc_b  = b.get("description", "")

            # Tier 1: exact URL
            if url_a and url_a == url_b:
                keep, drop = (i, j) if len(desc_a) >= len(desc_b) else (j, i)
                removed.add(drop)
                dup_log.append((keep, drop, 1.0, "url-exact"))
                continue

            sim = jaccard(title_a, title_b)
            if sim < 0.50:
                continue

            # Tier 2: very high Jaccard
            if sim >= 0.75:
                keep, drop = (i, j) if len(desc_a) >= len(desc_b) else (j, i)
                removed.add(drop)
                dup_log.append((keep, drop, sim, "jaccard-high"))
                continue

            # Tier 3: borderline — queue for LLM
            if use_llm:
                candidates.append((i, j, sim))

    bar.close()

    # ── Pass 2: LLM on candidates (parallel) ──────────────────
    if use_llm and candidates:
        log.log(f"\n  LLM check on {len(candidates)} borderline pairs"
                f" (parallel, 8s timeout each)...")

        llm_bar = tqdm(
            total=len(candidates),
            desc="    Pass 2/2    ",
            unit=" pair",
            colour="blue",
            bar_format="{l_bar}{bar:28}{r_bar}",
            dynamic_ncols=True,
            leave=True,
        )

        def check_pair(args):
            i, j, sim = args
            ta = articles[i].get("title", "")
            tb = articles[j].get("title", "")
            t0 = time.time()
            is_dup, conf = ollama_is_dup(ta, tb)
            elapsed = time.time() - t0
            return i, j, is_dup, conf, elapsed

        # 3 workers — llama3.2:1b handles ~3 concurrent calls well
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {pool.submit(check_pair, c_): c_ for c_ in candidates}
            for future in as_completed(futures):
                i, j, is_dup, conf, elapsed = future.result()
                llm_calls += 1
                llm_total += elapsed
                llm_times.append(elapsed)
                llm_bar.update(1)
                llm_bar.set_postfix_str(
                    f"last:{elapsed:.1f}s avg:{llm_total/llm_calls:.1f}s",
                    refresh=True,
                )

                if is_dup and conf >= 0.70 and i not in removed and j not in removed:
                    desc_a = articles[i].get("description", "")
                    desc_b = articles[j].get("description", "")
                    keep, drop = (i, j) if len(desc_a) >= len(desc_b) else (j, i)
                    removed.add(drop)
                    dup_log.append((keep, drop, conf, "llm"))

        llm_bar.close()

    clean = [a for idx, a in enumerate(articles) if idx not in removed]

    return {
        "total_input":    total,
        "duplicates":     len(dup_log),
        "removed":        len(removed),
        "clean_count":    len(clean),
        "llm_calls":      llm_calls,
        "llm_total_s":    llm_total,
        "llm_times":      llm_times,
        "dup_detail":     dup_log,
        "clean_articles": clean,
    }


# ─────────────────────────────────────────────────────────────
# Report
# ─────────────────────────────────────────────────────────────

def report(scrape_stats: list[dict], ds: dict,
           total_wall: float, log: Logger, use_llm: bool):

    log.log(f"\n{'='*60}")
    log.log(c(BOLD, "  FULL REPORT"))
    log.log(f"{'='*60}")

    # Scraping table
    log.log(f"\n{c(BOLD, '  SCRAPING RESULTS')}")
    log.log(f"  {'Source':<16} {'Before':>7} {'  +New':>6} {'Skipped':>8} {'  Time':>7}")
    log.log(f"  {'-'*52}")

    total_new  = total_skip = 0
    total_time = 0.0
    for s in scrape_stats:
        tag     = c(RED, " ERR") if s["error"] else c(GREEN, "  OK")
        new_str = c(GREEN, f"+{s['new']:,}")
        log.log(
            f"  {s['source']:<16}"
            f"{s['before']:>7,}"
            f"  {new_str}"
            f"  {s['skipped']:>8,}"
            f"  {s['duration']:>5.1f}s"
            f"  [{tag}]"
        )
        total_new  += s["new"]
        total_skip += s["skipped"]
        total_time += s["duration"]

    total_new_str = c(GREEN, f"+{total_new:,}")
    log.log(f"  {'-'*52}")
    log.log(
        f"  {'TOTAL':<16}"
        f"{'':>7}"
        f"  {total_new_str}"
        f"  {total_skip:>8,}"
        f"  {total_time:>5.1f}s"
    )

    # Dedup table
    log.log(f"\n{c(BOLD, '  DEDUPLICATION RESULTS')}")
    log.log(f"  Articles analysed   : {ds['total_input']:,}")
    log.log(f"  Duplicates found    : {c(YELLOW, str(ds['duplicates']))}")
    log.log(f"  Articles removed    : {c(RED, str(ds['removed']))}")
    log.log(f"  Unique articles     : {c(GREEN, str(ds['clean_count']))}")

    # Detection method breakdown
    methods: dict[str, int] = {}
    for _, _, _, method in ds["dup_detail"]:
        methods[method] = methods.get(method, 0) + 1
    if methods:
        log.log(f"\n  Detection methods:")
        for method, cnt in sorted(methods.items()):
            log.log(f"    {method:<22} {cnt:>5} duplicates")

    # LLM stats
    if use_llm and ds["llm_calls"] > 0:
        lt = ds["llm_times"]
        log.log(f"\n{c(BOLD, f'  OLLAMA ({OLLAMA_MODEL}) ANALYSIS')}")
        log.log(f"  Total LLM calls     : {ds['llm_calls']:,}")
        log.log(f"  Total LLM time      : {ds['llm_total_s']:.1f}s")
        log.log(f"  Avg per comparison  : {ds['llm_total_s']/ds['llm_calls']:.2f}s")
        log.log(f"  Fastest comparison  : {min(lt):.2f}s")
        log.log(f"  Slowest comparison  : {max(lt):.2f}s")
    elif not use_llm:
        log.log(f"\n  {c(YELLOW, 'Ollama offline — used Jaccard similarity only')}")

    # Timing
    log.log(f"\n{c(BOLD, '  TIMING SUMMARY')}")
    log.log(f"  Scraping total      : {total_time:.1f}s  ({total_time/60:.1f} min)")
    log.log(f"  LLM analysis total  : {ds['llm_total_s']:.1f}s  ({ds['llm_total_s']/60:.1f} min)")
    log.log(f"  Wall clock total    : {total_wall:.1f}s  ({total_wall/60:.1f} min)")

    log.log(f"\n{c(BOLD, '  OUTPUT FILES')}")
    log.log(f"  Master JSON         : {MASTER_FILE}")
    log.log(f"  Log file            : {LOG_FILE}")

    log.log(f"\n{'='*60}")
    log.log(c(BOLD + GREEN, "  DONE"))
    log.log(f"{'='*60}\n")


# ─────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────

def main():
    wall_start = time.time()
    log = Logger(LOG_FILE)

    log.log(f"\n{'='*60}")
    log.log(c(BOLD, "  NEWS AGENCY — DATA MANAGER"))
    log.log(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.log(f"{'='*60}")

    # Check Ollama
    log.log(f"\n{c(BOLD, '[0] Checking Ollama...')}")
    use_llm = ollama_available()
    if use_llm:
        log.log(c(GREEN, f"  ✓ Ollama ready — {OLLAMA_MODEL}"))
    else:
        log.log(c(YELLOW,
            f"  ⚠ Ollama not found — Jaccard-only dedup\n"
            f"    To enable LLM: ollama serve && ollama pull {OLLAMA_MODEL}"
        ))

    # Phase 1: scrape
    log.log(f"\n{c(BOLD, '[1] SCRAPING')}")
    scrape_stats = []
    for scraper in SCRAPERS:
        log.log(f"\n  {c(BOLD, scraper['name'])}")
        stats = run_scraper(scraper, log)
        scrape_stats.append(stats)

    total_new = sum(s["new"] for s in scrape_stats)
    log.log(f"\n  {c(GREEN, f'✓ {total_new:,} new articles collected across all sources')}")

    # Phase 2: merge
    log.log(f"\n{c(BOLD, '[2] MERGING')}")
    all_articles = load_all_articles()
    log.log(f"  {len(all_articles):,} total articles loaded")
    by_src: dict[str, int] = {}
    for a in all_articles:
        s = a.get("source", "Unknown")
        by_src[s] = by_src.get(s, 0) + 1
    for src, n in sorted(by_src.items()):
        log.log(f"    {src:<18} {n:,}")

    # Phase 3: deduplicate
    log.log(f"\n{c(BOLD, '[3] DEDUPLICATION')}")
    t_dup = time.time()
    ds    = deduplicate(all_articles, log, use_llm)
    ds["wall_s"] = time.time() - t_dup
    log.log(
        f"\n  {c(GREEN, '✓')} "
        f"{c(RED, str(ds['removed']))} duplicates removed  →  "
        f"{c(GREEN, str(ds['clean_count']))} unique articles"
    )

    # Phase 4: save
    log.log(f"\n{c(BOLD, '[4] SAVING')}")
    os.makedirs(DB_DIR, exist_ok=True)
    with open(MASTER_FILE, "w", encoding="utf-8") as f:
        json.dump(ds["clean_articles"], f, indent=2, ensure_ascii=False)
    log.log(c(GREEN, f"  ✓ {ds['clean_count']:,} articles → {MASTER_FILE}"))

    # Save pipeline stats for dashboard
    total_wall = time.time() - wall_start
    stats_payload = {
        "run_at":          datetime.now().isoformat(),
        "wall_seconds":    round(total_wall, 1),
        "scrape_seconds":  round(sum(s["duration"] for s in scrape_stats), 1),
        "llm_seconds":     round(ds["llm_total_s"], 1),
        "llm_calls":       ds["llm_calls"],
        "llm_model":       OLLAMA_MODEL,
        "total_articles":  ds["clean_count"],
        "duplicates_removed": ds["removed"],
        "sources": [
            {
                "name":      s["source"],
                "before":    s["before"],
                "after":     s["after"],
                "new":       s["new"],
                "skipped":   s["skipped"],
                "duration":  round(s["duration"], 1),
                "error":     s["error"],
                "timestamp": datetime.now().isoformat(),
            }
            for s in scrape_stats
        ],
        "dedup": {
            "total_input":  ds["total_input"],
            "duplicates":   ds["duplicates"],
            "removed":      ds["removed"],
            "clean_count":  ds["clean_count"],
            "methods":      {
                method: sum(1 for _, _, _, m in ds["dup_detail"] if m == method)
                for method in set(m for _, _, _, m in ds["dup_detail"])
            },
        },
    }
    with open(STATS_FILE, "w", encoding="utf-8") as f:
        json.dump(stats_payload, f, indent=2, ensure_ascii=False)
    log.log(c(GREEN, f"  ✓ Pipeline stats → {STATS_FILE}"))

    # Phase 5: report
    report(scrape_stats, ds, total_wall, log, use_llm)
    log.close()


if __name__ == "__main__":
    main()