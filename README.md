# The Agency

Editorial-style local news dashboard for scraped articles from BBC, CNN, and Al Jazeera.

## What it does

- Source-filtered feed with count badges for `BBC`, `CNN`, and `Al Jazeera`
- Category tabs generated from article content
- Full-text search across article titles and descriptions
- Expanded article experience:
  - quick-view modal on the homepage
  - dedicated article page at `/article/{id}`
- Metadata panel for each article:
  - `scraped_at`
  - stored path
  - source
  - image path
  - original URL
- Reading-time estimate based on word count
- "New" badge for articles scraped in the last 2 hours
- Bookmarks saved in browser `localStorage`
- Dark/light mode toggle
- Pipeline status widget:
  - running right now or idle
  - last run time
  - next scheduled run
  - live countdown
- Configurable scheduler in `/admin`
  - manual only
  - every `N` hours
  - specific daily time
- Manual pipeline trigger from `/admin`

![](/static/news_agency_video.mp4)

## Data model used by the site

The public feed is powered from:

- `database/master_articles.json`

The app will fall back to:

- `database/news.db`

if the master JSON is missing.

This repo currently contains source JSON files for:

- `database/bbc_articles.json`
- `database/cnn_articles.json`
- `database/aljazeera_articles.json`

## Run

```bash
pip install -r requirements.txt
python main.py
```

Open:

- `http://localhost:8000/` for the feed
- `http://localhost:8000/admin` for scheduler + pipeline controls

## Main files

```text
main.py
agents/
  data_manager.py      # scraper orchestration + pipeline stats output
  webmaster.py         # FastAPI app, routes, scheduler, JSON loading
templates/
  index.html           # feed shell
  article.html         # dedicated article page
  admin.html           # scheduler/admin UI
static/
  css/site.css         # shared site styles
  css/admin.css        # admin-specific styles
  js/site.js           # feed, modal, bookmarks, theme, status widget
  js/admin.js          # scheduler form + trigger controls
database/
  master_articles.json
  pipeline_stats.json
  scheduler_config.json
  scheduler_state.json
```

## Scheduler behavior

Scheduler state is stored locally in:

- `database/scheduler_config.json`
- `database/scheduler_state.json`

Available modes:

1. `manual`
2. `interval`
3. `daily`

Config payload shape:

```json
{
  "mode": "interval",
  "interval_hours": 6,
  "daily_time": "08:00"
}
```

Notes:

- `interval_hours` is clamped to `1-48`
- `daily_time` uses `HH:MM` 24-hour format
- the countdown widget polls `/api/pipeline/status`
- manual runs are blocked while a pipeline run is already active

## API routes

### Page routes

- `GET /`
- `GET /article/{article_id}`
- `GET /admin`

### JSON routes

- `GET /api/stats`
- `GET /api/articles`
- `GET /api/articles/{article_id}`
- `GET /api/pipeline/status`
- `POST /api/pipeline/run`
- `GET /api/scheduler/config`
- `POST /api/scheduler/config`

## Search and filters

Homepage filtering is client-side for fast interaction:

- source filter
- category filter
- search query
- bookmarks-only mode

Articles are loaded once, then filtered in-browser.

## Bookmarks

Bookmarks are intentionally local to the browser and stored in:

- `localStorage["agency-bookmarks"]`

No server-side bookmark table is required.

## Theme

Theme preference is stored in:

- `localStorage["agency-theme"]`

## Pipeline stats widget

The status widget reads from scheduler state plus the latest:

- `database/pipeline_stats.json`

Displayed fields:

- running/idle state
- last run time
- next scheduled run
- live countdown

## Assumptions in this implementation

- `master_articles.json` is the primary live feed because it currently contains the full BBC/CNN/Al Jazeera dataset
- category tabs are inferred from article text when the dataset does not include categories
- reading time is calculated from the stored article text or description
- metadata "stored path" points to the source JSON file for the article's origin

## Development tip

After UI changes, it is useful to open the site and admin pages together:

- `http://localhost:8000/`
- `http://localhost:8000/admin`
