"""
News Agency Dashboard — Flask
─────────────────────────────────────────────────────────────
Shows:
  - Per-source article counts and scrape duration
  - Total / scrape / LLM timing
  - Duplicate removal stats
  - Each scraper's last-run timestamp
  - Latest 10 articles with image + source badge

Install:
  pip install flask

Run:
  python dashboard.py
  open http://localhost:5000
"""

import json
import os
from datetime import datetime
from flask import Flask, render_template_string, jsonify

ROOT        = os.path.dirname(os.path.abspath(__file__))
DB_DIR      = os.path.join(ROOT, "database")
STATS_FILE  = os.path.join(DB_DIR, "pipeline_stats.json")
MASTER_FILE = os.path.join(DB_DIR, "master_articles.json")

app = Flask(__name__, static_folder=os.path.join(ROOT, "image_data"))

SOURCE_COLORS = {
    "BBC News":   "#bb1919",
    "CNN":        "#cc0000",
    "Al Jazeera": "#f5a623",
}

# ─────────────────────────────────────────────────────────────
# Data helpers
# ─────────────────────────────────────────────────────────────

def load_stats() -> dict:
    if not os.path.exists(STATS_FILE):
        return {}
    with open(STATS_FILE) as f:
        return json.load(f)


def load_latest(n: int = 10) -> list[dict]:
    if not os.path.exists(MASTER_FILE):
        return []
    with open(MASTER_FILE) as f:
        arts = json.load(f)
    # Sort by scraped_at descending, take first n
    arts.sort(key=lambda a: a.get("scraped_at", ""), reverse=True)
    return arts[:n]


def fmt_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    m, s = divmod(int(seconds), 60)
    return f"{m}m {s}s"


def fmt_ts(iso: str) -> str:
    if not iso:
        return "—"
    try:
        dt = datetime.fromisoformat(iso)
        return dt.strftime("%b %d, %Y  %H:%M")
    except Exception:
        return iso[:16]


# ─────────────────────────────────────────────────────────────
# Dashboard HTML template
# ─────────────────────────────────────────────────────────────

TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="refresh" content="120">
<title>News Agency Dashboard</title>
<style>
  :root {
    --bg:      #0f1117;
    --surface: #1a1d27;
    --border:  #2a2d3a;
    --text:    #e2e8f0;
    --muted:   #64748b;
    --accent:  #6366f1;
    --green:   #22c55e;
    --red:     #ef4444;
    --yellow:  #f59e0b;
    --cyan:    #06b6d4;
    --bbc:     #bb1919;
    --cnn:     #cc0000;
    --aj:      #f5a623;
  }

  * { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    font-size: 14px;
    min-height: 100vh;
  }

  /* ── Header ── */
  header {
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    padding: 1rem 2rem;
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .logo {
    font-size: 1.2rem;
    font-weight: 700;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    color: var(--accent);
  }
  .logo span { color: var(--text); }
  .header-meta {
    font-size: 0.75rem;
    color: var(--muted);
    text-align: right;
    line-height: 1.6;
  }
  .live-dot {
    display: inline-block;
    width: 6px; height: 6px;
    background: var(--green);
    border-radius: 50%;
    margin-right: 4px;
    animation: pulse 2s infinite;
  }
  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50%       { opacity: 0.3; }
  }

  /* ── Layout ── */
  .page { max-width: 1280px; margin: 0 auto; padding: 2rem; }

  .grid-3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1.5rem; margin-bottom: 1.5rem; }
  .grid-4 { display: grid; grid-template-columns: repeat(4, 1fr); gap: 1.5rem; margin-bottom: 1.5rem; }
  .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 1.5rem; margin-bottom: 1.5rem; }

  /* ── Card ── */
  .card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 1.5rem;
  }
  .card-title {
    font-size: 0.7rem;
    font-weight: 600;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--muted);
    margin-bottom: 0.75rem;
  }
  .stat-num {
    font-size: 2.4rem;
    font-weight: 700;
    line-height: 1;
    margin-bottom: 0.3rem;
  }
  .stat-sub {
    font-size: 0.75rem;
    color: var(--muted);
  }

  /* ── Timing card ── */
  .timing-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0.5rem 0;
    border-bottom: 1px solid var(--border);
  }
  .timing-row:last-child { border-bottom: none; }
  .timing-label { color: var(--muted); font-size: 0.8rem; }
  .timing-val   { font-weight: 600; font-size: 0.95rem; }

  /* ── Source cards ── */
  .source-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 1.25rem 1.5rem;
    border-top: 3px solid var(--accent);
    position: relative;
    overflow: hidden;
  }
  .source-card::before {
    content: "";
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 3px;
  }
  .source-bbc   { border-top-color: var(--bbc); }
  .source-cnn   { border-top-color: var(--cnn); }
  .source-aj    { border-top-color: var(--aj); }

  .source-name {
    font-weight: 700;
    font-size: 0.95rem;
    margin-bottom: 1rem;
  }
  .source-stat {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    margin-bottom: 0.4rem;
  }
  .source-stat-label { font-size: 0.72rem; color: var(--muted); }
  .source-stat-val   { font-weight: 600; font-size: 0.9rem; }

  .progress-bar {
    height: 4px;
    background: var(--border);
    border-radius: 2px;
    margin-top: 0.75rem;
    overflow: hidden;
  }
  .progress-fill {
    height: 100%;
    border-radius: 2px;
    transition: width 0.5s ease;
  }

  .badge {
    display: inline-block;
    font-size: 0.65rem;
    font-weight: 700;
    letter-spacing: 0.06em;
    padding: 0.2rem 0.5rem;
    border-radius: 4px;
    text-transform: uppercase;
    margin-top: 0.75rem;
  }
  .badge-ok  { background: rgba(34,197,94,0.15); color: var(--green); }
  .badge-err { background: rgba(239,68,68,0.15);  color: var(--red); }

  .ts-label {
    font-size: 0.68rem;
    color: var(--muted);
    margin-top: 0.6rem;
  }

  /* ── Dedup card ── */
  .dedup-row {
    display: flex;
    align-items: center;
    gap: 1rem;
    padding: 0.6rem 0;
    border-bottom: 1px solid var(--border);
  }
  .dedup-row:last-child { border-bottom: none; }
  .dedup-icon {
    width: 28px; height: 28px;
    border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    font-size: 0.8rem;
    flex-shrink: 0;
  }
  .dedup-icon.green { background: rgba(34,197,94,0.15); color: var(--green); }
  .dedup-icon.red   { background: rgba(239,68,68,0.15);  color: var(--red); }
  .dedup-icon.yellow{ background: rgba(245,158,11,0.15); color: var(--yellow); }
  .dedup-icon.cyan  { background: rgba(6,182,212,0.15);  color: var(--cyan); }

  .dedup-info { flex: 1; }
  .dedup-label { font-size: 0.78rem; color: var(--muted); }
  .dedup-val   { font-size: 1rem; font-weight: 700; }

  /* ── Section headers ── */
  .section-head {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    margin-bottom: 1rem;
    margin-top: 0.5rem;
  }
  .section-head h2 {
    font-size: 0.75rem;
    font-weight: 700;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--muted);
  }
  .section-head .line {
    flex: 1;
    height: 1px;
    background: var(--border);
  }

  /* ── News feed ── */
  .news-grid {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 1rem;
    margin-bottom: 2rem;
  }
  .news-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    display: flex;
    gap: 1rem;
    padding: 1rem;
    transition: border-color 0.2s;
  }
  .news-card:hover { border-color: var(--accent); }
  .news-thumb {
    width: 90px;
    height: 65px;
    border-radius: 6px;
    object-fit: cover;
    flex-shrink: 0;
    background: var(--border);
  }
  .news-thumb-ph {
    width: 90px;
    height: 65px;
    border-radius: 6px;
    background: var(--border);
    flex-shrink: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 1.5rem;
    color: var(--muted);
  }
  .news-body { flex: 1; min-width: 0; }
  .news-source {
    font-size: 0.65rem;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    margin-bottom: 0.3rem;
  }
  .news-title {
    font-size: 0.85rem;
    font-weight: 600;
    line-height: 1.35;
    margin-bottom: 0.4rem;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
  .news-desc {
    font-size: 0.72rem;
    color: var(--muted);
    line-height: 1.45;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
  .news-ts {
    font-size: 0.65rem;
    color: var(--muted);
    margin-top: 0.35rem;
  }

  /* ── LLM bar ── */
  .llm-card { grid-column: span 2; }
  .llm-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 0.45rem 0;
    border-bottom: 1px solid var(--border);
  }
  .llm-row:last-child { border-bottom: none; }

  /* ── Empty state ── */
  .empty {
    grid-column: span 2;
    text-align: center;
    padding: 3rem;
    color: var(--muted);
    font-size: 0.9rem;
  }

  /* ── No-run banner ── */
  .no-run {
    text-align: center;
    padding: 4rem 2rem;
    color: var(--muted);
  }
  .no-run h2 { font-size: 1.2rem; margin-bottom: 0.5rem; color: var(--text); }
  .no-run code {
    display: inline-block;
    background: var(--surface);
    border: 1px solid var(--border);
    padding: 0.5rem 1rem;
    border-radius: 6px;
    margin-top: 1rem;
    font-size: 0.85rem;
    color: var(--cyan);
  }

  @media (max-width: 900px) {
    .grid-3, .grid-4 { grid-template-columns: 1fr 1fr; }
    .news-grid { grid-template-columns: 1fr; }
    .llm-card  { grid-column: span 1; }
  }
  @media (max-width: 600px) {
    .grid-3, .grid-4, .grid-2 { grid-template-columns: 1fr; }
    .page { padding: 1rem; }
  }
</style>
</head>
<body>

<header>
  <div class="logo">The <span>Agency</span> &mdash; Dashboard</div>
  <div class="header-meta">
    <span class="live-dot"></span>Auto-refresh every 2 min
    {% if stats %}
    <br>Last run: {{ fmt_ts(stats.run_at) }}
    {% endif %}
  </div>
</header>

<div class="page">

{% if not stats %}
<div class="no-run">
  <h2>No pipeline data yet</h2>
  <p>Run the data manager to populate this dashboard.</p>
  <code>python agents/data_manager.py</code>
</div>

{% else %}

<!-- ── Top stat cards ─────────────────────────────────────── -->
<div class="section-head"><h2>Overview</h2><div class="line"></div></div>
<div class="grid-4">
  <div class="card">
    <div class="card-title">Total Articles</div>
    <div class="stat-num" style="color:var(--accent)">{{ stats.total_articles }}</div>
    <div class="stat-sub">after deduplication</div>
  </div>
  <div class="card">
    <div class="card-title">New This Run</div>
    <div class="stat-num" style="color:var(--green)">
      +{{ stats.sources | sum(attribute='new') }}
    </div>
    <div class="stat-sub">across {{ stats.sources | length }} sources</div>
  </div>
  <div class="card">
    <div class="card-title">Duplicates Removed</div>
    <div class="stat-num" style="color:var(--red)">{{ stats.duplicates_removed }}</div>
    <div class="stat-sub">cross-source matches</div>
  </div>
  <div class="card">
    <div class="card-title">LLM Calls</div>
    <div class="stat-num" style="color:var(--cyan)">{{ stats.llm_calls }}</div>
    <div class="stat-sub">{{ stats.llm_model }}</div>
  </div>
</div>

<!-- ── Timing ─────────────────────────────────────────────── -->
<div class="section-head"><h2>Timing</h2><div class="line"></div></div>
<div class="grid-3">
  <div class="card">
    <div class="card-title">Scraping Time</div>
    <div class="stat-num" style="color:var(--yellow)">{{ fmt_dur(stats.scrape_seconds) }}</div>
    <div class="stat-sub">all sources combined</div>
  </div>
  <div class="card">
    <div class="card-title">LLM / Dedup Time</div>
    <div class="stat-num" style="color:var(--cyan)">{{ fmt_dur(stats.llm_seconds) }}</div>
    <div class="stat-sub">Ollama analysis</div>
  </div>
  <div class="card">
    <div class="card-title">Total Wall Time</div>
    <div class="stat-num" style="color:var(--text)">{{ fmt_dur(stats.wall_seconds) }}</div>
    <div class="stat-sub">end-to-end pipeline</div>
  </div>
</div>

<!-- ── Per-source breakdown ───────────────────────────────── -->
<div class="section-head"><h2>Sources</h2><div class="line"></div></div>
<div class="grid-3">
  {% set max_count = stats.sources | map(attribute='after') | max %}
  {% for src in stats.sources %}
  {% set cls = "source-bbc" if "BBC" in src.name else ("source-cnn" if "CNN" in src.name else "source-aj") %}
  {% set col = "#bb1919" if "BBC" in src.name else ("#cc0000" if "CNN" in src.name else "#f5a623") %}
  <div class="source-card {{ cls }}">
    <div class="source-name">{{ src.name }}</div>

    <div class="source-stat">
      <span class="source-stat-label">Total stored</span>
      <span class="source-stat-val">{{ src.after | default(0) }}</span>
    </div>
    <div class="source-stat">
      <span class="source-stat-label">New this run</span>
      <span class="source-stat-val" style="color:var(--green)">+{{ src.new }}</span>
    </div>
    <div class="source-stat">
      <span class="source-stat-label">Skipped (seen)</span>
      <span class="source-stat-val">{{ src.skipped }}</span>
    </div>
    <div class="source-stat">
      <span class="source-stat-label">Scrape time</span>
      <span class="source-stat-val">{{ fmt_dur(src.duration) }}</span>
    </div>

    <div class="progress-bar">
      <div class="progress-fill"
           style="width:{{ ((src.after / max_count) * 100) | int if max_count > 0 else 0 }}%;
                  background:{{ col }}"></div>
    </div>

    <span class="badge {{ 'badge-err' if src.error else 'badge-ok' }}">
      {{ 'Error' if src.error else 'OK' }}
    </span>
    <div class="ts-label">Last run: {{ fmt_ts(src.timestamp) }}</div>
  </div>
  {% endfor %}
</div>

<!-- ── Dedup + LLM detail ─────────────────────────────────── -->
<div class="section-head"><h2>Deduplication</h2><div class="line"></div></div>
<div class="grid-2">
  <div class="card">
    <div class="card-title">Dedup Analysis</div>
    <div class="dedup-row">
      <div class="dedup-icon cyan">⊞</div>
      <div class="dedup-info">
        <div class="dedup-label">Articles analysed</div>
        <div class="dedup-val">{{ stats.dedup.total_input }}</div>
      </div>
    </div>
    <div class="dedup-row">
      <div class="dedup-icon yellow">≈</div>
      <div class="dedup-info">
        <div class="dedup-label">Duplicate pairs found</div>
        <div class="dedup-val">{{ stats.dedup.duplicates }}</div>
      </div>
    </div>
    <div class="dedup-row">
      <div class="dedup-icon red">✕</div>
      <div class="dedup-info">
        <div class="dedup-label">Articles removed</div>
        <div class="dedup-val" style="color:var(--red)">{{ stats.dedup.removed }}</div>
      </div>
    </div>
    <div class="dedup-row">
      <div class="dedup-icon green">✓</div>
      <div class="dedup-info">
        <div class="dedup-label">Unique articles kept</div>
        <div class="dedup-val" style="color:var(--green)">{{ stats.dedup.clean_count }}</div>
      </div>
    </div>
  </div>

  <div class="card">
    <div class="card-title">Detection Methods</div>
    {% if stats.dedup.methods %}
      {% for method, count in stats.dedup.methods.items() %}
      <div class="llm-row">
        <span style="color:var(--muted); font-size:0.78rem;">{{ method }}</span>
        <span style="font-weight:600;">{{ count }}</span>
      </div>
      {% endfor %}
    {% else %}
      <div style="color:var(--muted); font-size:0.8rem; padding-top:0.5rem;">
        No duplicates found this run.
      </div>
    {% endif %}
    <div style="margin-top:1.25rem; padding-top:1rem; border-top:1px solid var(--border);">
      <div class="card-title" style="margin-bottom:0.5rem;">LLM Usage</div>
      <div class="llm-row">
        <span style="color:var(--muted); font-size:0.78rem;">Model</span>
        <span style="font-weight:600; font-size:0.78rem;">{{ stats.llm_model }}</span>
      </div>
      <div class="llm-row">
        <span style="color:var(--muted); font-size:0.78rem;">Calls made</span>
        <span style="font-weight:600;">{{ stats.llm_calls }}</span>
      </div>
      <div class="llm-row">
        <span style="color:var(--muted); font-size:0.78rem;">Time spent</span>
        <span style="font-weight:600;">{{ fmt_dur(stats.llm_seconds) }}</span>
      </div>
    </div>
  </div>
</div>

<!-- ── Latest 10 news ─────────────────────────────────────── -->
<div class="section-head"><h2>Latest Articles</h2><div class="line"></div></div>
<div class="news-grid">
  {% if latest %}
    {% for art in latest %}
    {% set src_col = "#bb1919" if "BBC" in art.source else ("#cc0000" if "CNN" in art.source else "#f5a623") %}
    <div class="news-card">
      {% if art.image_path %}
        <img class="news-thumb"
             src="/img/{{ art.image_path | replace('image_data/', '') }}"
             alt="{{ art.title }}"
             onerror="this.style.display='none'">
      {% else %}
        <div class="news-thumb-ph">📰</div>
      {% endif %}
      <div class="news-body">
        <div class="news-source" style="color:{{ src_col }}">{{ art.source }}</div>
        <div class="news-title">{{ art.title }}</div>
        {% if art.description %}
        <div class="news-desc">{{ art.description[:140] }}</div>
        {% endif %}
        <div class="news-ts">{{ fmt_ts(art.get('scraped_at', '')) }}</div>
      </div>
    </div>
    {% endfor %}
  {% else %}
    <div class="empty">No articles yet — run the pipeline first.</div>
  {% endif %}
</div>

{% endif %}

</div><!-- /page -->
</body>
</html>
"""

# ─────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────

@app.route("/")
def dashboard():
    stats  = load_stats()
    latest = load_latest(10)
    return render_template_string(
        TEMPLATE,
        stats=stats or None,
        latest=latest,
        fmt_ts=fmt_ts,
        fmt_dur=fmt_duration,
    )


@app.route("/api/stats")
def api_stats():
    return jsonify(load_stats())


@app.route("/api/articles")
def api_articles():
    arts = load_latest(20)
    return jsonify(arts)


@app.route("/img/<path:filename>")
def serve_image(filename):
    """Serve images from image_data/ folder."""
    from flask import send_from_directory
    return send_from_directory(
        os.path.join(ROOT, "image_data"), filename
    )


# ─────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 50)
    print("  News Agency Dashboard")
    print("  http://localhost:5000")
    print("=" * 50)
    app.run(debug=False, host="0.0.0.0", port=8003)