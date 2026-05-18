# The Agency — AI-Powered News Platform

A multi-agent news aggregation system that collects, deduplicates, edits, and publishes news automatically.

## Architecture

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐    ┌──────────────────┐
│ Collector Agent │───▶│ DataManager Agent│───▶│  Editor Agent   │───▶│Webmaster Agent   │
│                 │    │                  │    │                 │    │                  │
│ • RSS scraping  │    │ • SQLite DB      │    │ • Ollama LLM    │    │ • FastAPI server │
│ • Web scraping  │    │ • Deduplication  │    │ • Categorize    │    │ • Jinja2 templates│
│ • Image resize  │    │ • URL/hash/title │    │ • Rank 0–10     │    │ • Auto-refresh   │
│ • No videos     │    │   similarity     │    │ • Grammar check │    │ • Top-rank hero  │
└─────────────────┘    └──────────────────┘    └─────────────────┘    └──────────────────┘
```

## Quick Start

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Install and start Ollama (for Editor Agent LLM)
```bash
# Install Ollama: https://ollama.com
ollama serve                    # Start server (separate terminal)
ollama pull llama3.2            # Download model (~2GB)
# Or lighter alternatives:
# ollama pull phi3              # ~2.3GB
# ollama pull mistral           # ~4.1GB
# ollama pull gemma2:2b         # ~1.6GB
```

### 3. Run the application
```bash
python main.py
```

### 4. Open in browser
- **Website:** http://localhost:8000
- **Admin:**   http://localhost:8000/admin
- **API:**     http://localhost:8000/api/stats

---

## Agents

### 1. Collector Agent (`agents/collector.py`)
- Scrapes **5 RSS feeds**: BBC, Reuters, Al Jazeera, NPR, AP News
- Extracts full article text using BeautifulSoup
- Downloads and **resizes images** to max 800×600px JPEG (no videos)
- Falls back to web scraping if RSS content is too short

### 2. Data Manager Agent (`agents/data_manager.py`)
- Creates and manages **SQLite database** (`database/news.db`)
- **Duplicate detection** with 3 strategies:
  1. Exact URL match
  2. MD5 content hash match
  3. Jaccard title similarity (>75% = duplicate)
- Stores articles, images, categories, ranks, grammar status
- Tracks scrape logs and source statistics

### 3. Editor Agent (`agents/editor.py`)
- Uses **local Ollama LLM** (llama3.2, mistral, phi3, etc.)
- **Categorizes** into 12 categories: Politics, World, Business, Technology, Sports, Health, Science, Entertainment, Environment, Education, Crime, Other
- **Ranks** articles 0–10 by newsworthiness
- **Checks grammar and spelling**, adds notes
- Gracefully falls back to keyword-based rules if Ollama is offline

### 4. Webmaster Agent (`agents/webmaster.py`)
- **FastAPI** web server with Jinja2 templates
- **Minimal editorial design** — newspaper aesthetic
- Top-ranked article shown in **hero section**
- Category filtering, breaking news ticker
- Auto-runs pipeline on startup + every 30 minutes
- Admin dashboard at `/admin`

---

## Configuration

### Change Ollama model
Edit `agents/editor.py`:
```python
OLLAMA_MODEL = "llama3.2"   # Change to "mistral", "phi3", "gemma2", etc.
```

### Add news sources
Edit `agents/collector.py`:
```python
NEWS_SOURCES = [
    {"name": "My Source", "rss": "https://example.com/rss.xml", "type": "rss"},
    ...
]
```

### Change scrape interval
Edit `agents/webmaster.py`:
```python
schedule.every(30).minutes.do(run_pipeline)  # Change 30 to desired minutes
```

### Image settings
Edit `agents/collector.py`:
```python
IMAGE_MAX_WIDTH = 800
IMAGE_MAX_HEIGHT = 600
IMAGE_QUALITY = 85
```

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Homepage with top-ranked articles |
| GET | `/?category=Sports` | Filter by category |
| GET | `/article/{id}` | Article detail page |
| GET | `/admin` | Admin dashboard |
| GET | `/api/stats` | JSON database statistics |
| GET | `/api/articles` | JSON article list |
| POST | `/api/pipeline/run` | Manually trigger pipeline |
| GET | `/api/ollama/status` | Ollama LLM status |

---

## Project Structure

```
news_agency/
├── main.py                  # Entry point
├── requirements.txt
├── agents/
│   ├── __init__.py
│   ├── collector.py         # News Collector Agent
│   ├── data_manager.py      # Data Manager Agent
│   ├── editor.py            # Editor Agent (Ollama)
│   └── webmaster.py         # Webmaster Agent (FastAPI)
├── templates/
│   ├── index.html           # Homepage
│   ├── article.html         # Article detail
│   └── admin.html           # Admin dashboard
├── static/
│   └── images/              # Resized article images
└── database/
    └── news.db              # SQLite database (auto-created)
```
