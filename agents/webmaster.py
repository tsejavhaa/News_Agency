import json
import math
import re
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel


ROOT = Path(__file__).resolve().parent.parent
DB_DIR = ROOT / "database"
MASTER_FILE = DB_DIR / "master_articles.json"
SQLITE_FILE = DB_DIR / "news.db"
PIPELINE_STATS_FILE = DB_DIR / "pipeline_stats.json"
PIPELINE_PROGRESS_FILE = DB_DIR / "pipeline_progress.json"
SCHEDULER_CONFIG_FILE = DB_DIR / "scheduler_config.json"
SCHEDULER_STATE_FILE = DB_DIR / "scheduler_state.json"
STATIC_DIR = ROOT / "static"
TEMPLATES_DIR = ROOT / "templates"
IMAGE_DIR = ROOT / "image_data"

SOURCE_LABELS = {
    "all": "All Sources",
    "bbc": "BBC",
    "cnn": "CNN",
    "aljazeera": "Al Jazeera",
}

SOURCE_STORAGE_PATHS = {
    "bbc": "database/bbc_articles.json",
    "cnn": "database/cnn_articles.json",
    "aljazeera": "database/aljazeera_articles.json",
    "other": "database/master_articles.json",
}

CATEGORY_RULES = [
    ("Politics", ("election", "government", "president", "prime minister", "senate", "congress", "parliament", "minister", "campaign", "vote", "voting", "white house", "policy", "politic")),
    ("Business", ("market", "markets", "economy", "economic", "trade", "tariff", "stocks", "stock", "bank", "banks", "company", "companies", "earnings", "business", "deal", "ceo", "inflation")),
    ("Technology", ("tech", "technology", "ai", "artificial intelligence", "chip", "chips", "software", "app", "cyber", "digital", "robot", "startup", "tesla", "spacex")),
    ("Science", ("science", "scientist", "research", "space", "nasa", "climate study", "experiment", "physics", "biology")),
    ("Health", ("health", "hospital", "medical", "medicine", "virus", "disease", "covid", "doctor", "patient", "vaccine")),
    ("Sports", ("sport", "sports", "football", "soccer", "nba", "nfl", "mlb", "olympic", "tennis", "golf", "formula 1", "fifa", "cricket")),
    ("Entertainment", ("movie", "film", "music", "celebrity", "tv", "television", "eurovision", "festival", "concert", "actor", "actress", "streaming")),
    ("Environment", ("climate", "wildfire", "storm", "flood", "earthquake", "weather", "environment", "emissions", "hurricane")),
    ("Crime", ("police", "murder", "killed", "arrest", "court", "crime", "trial", "shooting", "investigation")),
    ("World", ("war", "conflict", "diplomacy", "missile", "drone", "border", "summit", "ukraine", "russia", "china", "gaza", "israel", "global")),
]


templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def now_local() -> datetime:
    return datetime.now().astimezone().replace(tzinfo=None)


def iso_now() -> str:
    return now_local().isoformat(timespec="seconds")


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def format_display_ts(value: str | None) -> str:
    dt = parse_dt(value)
    if not dt:
        return "Unknown"
    return dt.strftime("%b %d, %Y %H:%M")


def humanize_source(source: str | None) -> str:
    key = normalize_source(source)
    return SOURCE_LABELS.get(key, source or "Unknown")


def normalize_source(source: str | None) -> str:
    text = (source or "").strip().lower()
    if "bbc" in text:
        return "bbc"
    if "cnn" in text:
        return "cnn"
    if "al jazeera" in text or "aljazeera" in text:
        return "aljazeera"
    return "other"


def normalize_image_path(path: str | None) -> str | None:
    if not path:
        return None
    if path.startswith("http://") or path.startswith("https://"):
        return path
    if path.startswith("/"):
        return path
    return "/" + path.lstrip("./")


def infer_category(existing: str | None, title: str, description: str) -> str:
    if existing and str(existing).strip() and str(existing).strip().lower() not in {"none", "uncategorized"}:
        return str(existing).strip()
    text = f"{title} {description}".lower()
    for category, keywords in CATEGORY_RULES:
        if any(keyword in text for keyword in keywords):
            return category
    return "General"


def estimate_reading_time(text: str) -> tuple[int, int]:
    words = len(re.findall(r"\w+", text or ""))
    minutes = max(1, math.ceil(words / 200)) if words else 1
    return words, minutes


def split_paragraphs(text: str) -> list[str]:
    clean = re.sub(r"\s+", " ", (text or "")).strip()
    if not clean:
        return []
    if "\n" in text:
        return [p.strip() for p in text.splitlines() if p.strip()]
    sentences = re.split(r"(?<=[.!?])\s+", clean)
    grouped: list[str] = []
    chunk: list[str] = []
    for sentence in sentences:
        if not sentence:
            continue
        chunk.append(sentence)
        if len(chunk) == 3:
            grouped.append(" ".join(chunk))
            chunk = []
    if chunk:
        grouped.append(" ".join(chunk))
    return grouped or [clean]


def load_json_file(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def load_articles_from_sqlite() -> list[dict[str, Any]]:
    if not SQLITE_FILE.exists():
        return []
    import sqlite3

    query = """
        SELECT
            id,
            title,
            COALESCE(summary, content, '') AS description,
            content,
            url,
            source,
            published_at,
            image_path,
            category,
            created_at AS scraped_at
        FROM articles
        ORDER BY COALESCE(published_at, updated_at, created_at) DESC
    """
    try:
        conn = sqlite3.connect(SQLITE_FILE)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query).fetchall()
        conn.close()
    except sqlite3.Error:
        return []

    items = []
    for row in rows:
        items.append(dict(row))
    return items


def load_raw_articles() -> list[dict[str, Any]]:
    records = load_json_file(MASTER_FILE, [])
    if isinstance(records, list) and records:
        return records
    return load_articles_from_sqlite()


def serialize_article(raw: dict[str, Any]) -> dict[str, Any]:
    title = (raw.get("title") or "Untitled").strip()
    description = (raw.get("description") or raw.get("summary") or raw.get("content") or "").strip()
    content = (raw.get("content") or description).strip()
    source_key = normalize_source(raw.get("source"))
    source_label = SOURCE_LABELS.get(source_key, raw.get("source") or "Unknown")
    category = infer_category(raw.get("category"), title, description)
    scraped_at = raw.get("scraped_at") or raw.get("created_at") or raw.get("updated_at")
    published_at = raw.get("published_at") or scraped_at
    word_count, reading_time = estimate_reading_time(content or description)
    image_path = normalize_image_path(raw.get("image_path"))
    article_dt = parse_dt(scraped_at) or parse_dt(published_at)
    is_new = bool(article_dt and now_local() - article_dt <= timedelta(hours=2))

    return {
        "id": raw.get("id") or raw.get("url") or title,
        "title": title,
        "description": description,
        "content": content,
        "paragraphs": split_paragraphs(content or description),
        "url": raw.get("url"),
        "source": source_label,
        "source_key": source_key,
        "published_at": published_at,
        "published_label": format_display_ts(published_at),
        "scraped_at": scraped_at,
        "scraped_label": format_display_ts(scraped_at),
        "image_path": image_path,
        "image_url": raw.get("image_url"),
        "category": category,
        "word_count": word_count,
        "reading_time_minutes": reading_time,
        "is_new": is_new,
        "stored_path": SOURCE_STORAGE_PATHS.get(source_key, SOURCE_STORAGE_PATHS["other"]),
        "master_path": "database/master_articles.json",
        "search_blob": f"{title} {description}".lower(),
        "sort_key": (parse_dt(scraped_at) or parse_dt(published_at) or datetime.min).isoformat(),
    }


def load_articles() -> list[dict[str, Any]]:
    items = [serialize_article(record) for record in load_raw_articles()]
    items.sort(key=lambda item: item["sort_key"], reverse=True)
    return items


def build_stats(articles: list[dict[str, Any]]) -> dict[str, Any]:
    source_counts = {"bbc": 0, "cnn": 0, "aljazeera": 0}
    category_counts: dict[str, int] = {}
    new_count = 0
    for article in articles:
        if article["source_key"] in source_counts:
            source_counts[article["source_key"]] += 1
        category_counts[article["category"]] = category_counts.get(article["category"], 0) + 1
        if article["is_new"]:
            new_count += 1
    return {
        "total_articles": len(articles),
        "source_counts": source_counts,
        "category_counts": dict(sorted(category_counts.items(), key=lambda item: (-item[1], item[0]))),
        "new_last_2h": new_count,
        "last_updated": format_display_ts(load_pipeline_stats().get("run_at")),
    }


def filter_articles(
    articles: list[dict[str, Any]],
    source: str = "all",
    category: str = "all",
    search: str = "",
) -> list[dict[str, Any]]:
    term = search.strip().lower()
    results = []
    for article in articles:
        if source != "all" and article["source_key"] != source:
            continue
        if category != "all" and article["category"] != category:
            continue
        if term and term not in article["search_blob"]:
            continue
        results.append(article)
    return results


def load_pipeline_stats() -> dict[str, Any]:
    return load_json_file(PIPELINE_STATS_FILE, {})


def load_pipeline_progress() -> dict[str, Any]:
    return load_json_file(PIPELINE_PROGRESS_FILE, {})


def load_scheduler_config() -> dict[str, Any]:
    raw = load_json_file(SCHEDULER_CONFIG_FILE, {})
    return normalize_scheduler_config(raw)


def normalize_scheduler_config(raw: dict[str, Any]) -> dict[str, Any]:
    mode = str(raw.get("mode", "interval")).strip().lower()
    if mode not in {"manual", "interval", "daily"}:
        mode = "interval"

    try:
        interval_hours = int(raw.get("interval_hours", 6))
    except (TypeError, ValueError):
        interval_hours = 6
    interval_hours = min(max(interval_hours, 1), 48)

    daily_time = str(raw.get("daily_time", "08:00")).strip()
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", daily_time):
        daily_time = "08:00"

    return {
        "mode": mode,
        "interval_hours": interval_hours,
        "daily_time": daily_time,
    }


def default_scheduler_state() -> dict[str, Any]:
    return {
        "is_running": False,
        "current_trigger": None,
        "last_run_started_at": None,
        "last_run_finished_at": None,
        "last_run_status": "idle",
        "last_error": None,
        "next_run_at": None,
    }


class SchedulerConfigPayload(BaseModel):
    mode: str
    interval_hours: int | None = None
    daily_time: str | None = None


class PipelineScheduler:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.loop_thread: threading.Thread | None = None
        self.job_thread: threading.Thread | None = None
        self.config = load_scheduler_config()
        self.state = self._load_state()
        self._repair_state()
        with self.lock:
            self.state["next_run_at"] = self._compute_next_run_locked()
            self._persist_locked()

    def _load_state(self) -> dict[str, Any]:
        state = default_scheduler_state()
        state.update(load_json_file(SCHEDULER_STATE_FILE, {}))
        return state

    def _repair_state(self) -> None:
        if self.state.get("is_running"):
            self.state["is_running"] = False
            self.state["current_trigger"] = None
            self.state["last_run_status"] = "interrupted"
            self.state["last_error"] = "Recovered after process restart."

        stats = load_pipeline_stats()
        if not self.state.get("last_run_finished_at") and stats.get("run_at"):
            self.state["last_run_finished_at"] = stats["run_at"]
            self.state["last_run_status"] = self.state.get("last_run_status") or "success"

    def _persist_locked(self) -> None:
        DB_DIR.mkdir(parents=True, exist_ok=True)
        SCHEDULER_CONFIG_FILE.write_text(json.dumps(self.config, indent=2), encoding="utf-8")
        SCHEDULER_STATE_FILE.write_text(json.dumps(self.state, indent=2), encoding="utf-8")

    def _compute_next_run_locked(self, from_dt: datetime | None = None) -> str | None:
        if self.config["mode"] == "manual":
            return None

        now = from_dt or now_local()
        if self.config["mode"] == "interval":
            base = parse_dt(self.state.get("last_run_finished_at")) or parse_dt(load_pipeline_stats().get("run_at")) or now
            next_run = base + timedelta(hours=self.config["interval_hours"])
            while next_run <= now:
                next_run += timedelta(hours=self.config["interval_hours"])
            return next_run.isoformat(timespec="seconds")

        hours, minutes = [int(part) for part in self.config["daily_time"].split(":", 1)]
        candidate = now.replace(hour=hours, minute=minutes, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate.isoformat(timespec="seconds")

    def start(self) -> None:
        if self.loop_thread and self.loop_thread.is_alive():
            return
        self.loop_thread = threading.Thread(target=self._run_loop, name="pipeline-scheduler", daemon=True)
        self.loop_thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.loop_thread and self.loop_thread.is_alive():
            self.loop_thread.join(timeout=2)

    def _run_loop(self) -> None:
        while not self.stop_event.wait(1):
            with self.lock:
                next_run_at = parse_dt(self.state.get("next_run_at"))
                should_run = (
                    self.config["mode"] != "manual"
                    and not self.state.get("is_running")
                    and next_run_at is not None
                    and now_local() >= next_run_at
                )
                if should_run:
                    self._start_job_locked("scheduled")

    def _start_job_locked(self, trigger: str) -> bool:
        if self.state.get("is_running"):
            return False
        self.state["is_running"] = True
        self.state["current_trigger"] = trigger
        self.state["last_run_started_at"] = iso_now()
        self.state["last_run_status"] = "running"
        self.state["last_error"] = None
        self.state["next_run_at"] = None
        self._persist_locked()
        self.job_thread = threading.Thread(target=self._run_pipeline_job, args=(trigger,), daemon=True)
        self.job_thread.start()
        return True

    def _run_pipeline_job(self, trigger: str) -> None:
        status = "success"
        error = None
        try:
            from agents import data_manager

            data_manager.main()
        except Exception as exc:  # pragma: no cover - defensive runtime path
            status = "error"
            error = str(exc)

        with self.lock:
            self.state["is_running"] = False
            self.state["current_trigger"] = None
            self.state["last_run_finished_at"] = iso_now()
            self.state["last_run_status"] = status
            self.state["last_error"] = error
            self.state["next_run_at"] = self._compute_next_run_locked()
            self._persist_locked()

    def trigger_manual(self) -> tuple[bool, dict[str, Any]]:
        with self.lock:
            started = self._start_job_locked("manual")
            return started, self.snapshot_locked()

    def update_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            self.config = normalize_scheduler_config(payload)
            if not self.state.get("is_running"):
                self.state["next_run_at"] = self._compute_next_run_locked()
            self._persist_locked()
            return self.snapshot_locked()

    def snapshot_locked(self) -> dict[str, Any]:
        payload = dict(self.state)
        payload["config"] = dict(self.config)
        payload["stats"] = load_pipeline_stats()
        payload["progress"] = load_pipeline_progress()
        payload["last_run_finished_label"] = format_display_ts(self.state.get("last_run_finished_at"))
        payload["last_run_started_label"] = format_display_ts(self.state.get("last_run_started_at"))
        payload["next_run_label"] = format_display_ts(self.state.get("next_run_at")) if self.state.get("next_run_at") else "Manual only"
        return payload

    def get_status(self) -> dict[str, Any]:
        with self.lock:
            return self.snapshot_locked()

    def get_config(self) -> dict[str, Any]:
        with self.lock:
            return dict(self.config)


scheduler = PipelineScheduler()


@asynccontextmanager
async def lifespan(_: FastAPI):
    scheduler.start()
    yield
    scheduler.stop()


app = FastAPI(title="The Agency", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
if IMAGE_DIR.exists():
    app.mount("/image_data", StaticFiles(directory=str(IMAGE_DIR)), name="image_data")


@app.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    source: str = Query(default="all"),
    category: str = Query(default="all"),
    q: str = Query(default=""),
    bookmarks: int = Query(default=0),
):
    articles = load_articles()
    context = {
        "request": request,
        "articles": articles,
        "stats": build_stats(articles),
        "initial_filters": {
            "source": source if source in {"all", "bbc", "cnn", "aljazeera"} else "all",
            "category": category,
            "q": q,
            "bookmarks": bool(bookmarks),
        },
        "pipeline_status": scheduler.get_status(),
        "source_labels": SOURCE_LABELS,
        "today_label": now_local().strftime("%A, %B %d, %Y"),
    }
    return templates.TemplateResponse(request=request, name="index.html", context=context)


@app.get("/article/{article_id}", response_class=HTMLResponse)
async def article_detail(request: Request, article_id: str):
    articles = load_articles()
    article = next((item for item in articles if item["id"] == article_id), None)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")

    related = [
        item for item in articles
        if item["id"] != article_id and (
            item["category"] == article["category"] or item["source_key"] == article["source_key"]
        )
    ][:4]

    context = {
        "request": request,
        "article": article,
        "related": related,
        "pipeline_status": scheduler.get_status(),
    }
    return templates.TemplateResponse(request=request, name="article.html", context=context)


@app.get("/admin", response_class=HTMLResponse)
async def admin(request: Request):
    articles = load_articles()
    context = {
        "request": request,
        "stats": build_stats(articles),
        "pipeline_status": scheduler.get_status(),
        "scheduler_config": scheduler.get_config(),
    }
    return templates.TemplateResponse(request=request, name="admin.html", context=context)


@app.get("/api/stats")
async def api_stats():
    articles = load_articles()
    return {
        "site": build_stats(articles),
        "pipeline": scheduler.get_status(),
    }


@app.get("/api/articles")
async def api_articles(
    source: str = Query(default="all"),
    category: str = Query(default="all"),
    q: str = Query(default=""),
    limit: int = Query(default=100, ge=1, le=1000),
):
    articles = load_articles()
    results = filter_articles(articles, source=source, category=category, search=q)
    return {
        "count": len(results),
        "items": results[:limit],
    }


@app.get("/api/articles/{article_id}")
async def api_article_detail(article_id: str):
    articles = load_articles()
    article = next((item for item in articles if item["id"] == article_id), None)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    return article


@app.get("/api/pipeline/status")
async def api_pipeline_status():
    return scheduler.get_status()


@app.post("/api/pipeline/run")
async def api_pipeline_run():
    started, payload = scheduler.trigger_manual()
    status_code = 202 if started else 409
    status = "started" if started else "already_running"
    return JSONResponse(
        status_code=status_code,
        content={
            "status": status,
            "timestamp": iso_now(),
            "pipeline": payload,
        },
    )


@app.get("/api/scheduler/config")
async def api_scheduler_config():
    return scheduler.get_config()


@app.post("/api/scheduler/config")
async def api_scheduler_update(payload: SchedulerConfigPayload):
    config = {
        "mode": payload.mode,
        "interval_hours": payload.interval_hours if payload.interval_hours is not None else 6,
        "daily_time": payload.daily_time if payload.daily_time is not None else "08:00",
    }
    return scheduler.update_config(config)
