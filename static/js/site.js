function readJsonScript(id, fallback) {
  const node = document.getElementById(id);
  if (!node) return fallback;
  try {
    return JSON.parse(node.textContent);
  } catch (_) {
    return fallback;
  }
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function formatLocalDate(value) {
  if (!value) return "Unknown";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function countdownLabel(value) {
  if (!value) return "Manual only";
  const target = new Date(value);
  if (Number.isNaN(target.getTime())) return "--";
  const delta = target.getTime() - Date.now();
  if (delta <= 0) return "Due now";
  const totalSeconds = Math.floor(delta / 1000);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) return `${hours}h ${minutes}m ${seconds}s`;
  if (minutes > 0) return `${minutes}m ${seconds}s`;
  return `${seconds}s`;
}

function loadBookmarks() {
  try {
    return new Set(JSON.parse(localStorage.getItem("agency-bookmarks") || "[]"));
  } catch (_) {
    return new Set();
  }
}

function saveBookmarks(bookmarks) {
  localStorage.setItem("agency-bookmarks", JSON.stringify(Array.from(bookmarks)));
}

function applyTheme() {
  const saved = localStorage.getItem("agency-theme") || "light";
  document.documentElement.dataset.theme = saved;
  const button = document.getElementById("theme-toggle");
  if (!button) return;
  const dark = saved === "dark";
  button.textContent = dark ? "Light mode" : "Dark mode";
  button.classList.toggle("active", dark);
}

function toggleTheme() {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  localStorage.setItem("agency-theme", next);
  applyTheme();
}

function renderPipelineStatus(status) {
  const dot = document.getElementById("pipeline-dot");
  const label = document.getElementById("pipeline-running-label");
  const lastRun = document.getElementById("pipeline-last-run");
  const nextRun = document.getElementById("pipeline-next-run");
  const countdown = document.getElementById("pipeline-countdown");
  const scheduleMode = document.getElementById("pipeline-schedule-mode");
  if (dot) dot.classList.toggle("running", Boolean(status?.is_running));
  if (label) label.textContent = status?.is_running ? "Running now" : "Idle";
  if (lastRun) lastRun.textContent = formatLocalDate(status?.last_run_finished_at) || "Unknown";
  if (nextRun) nextRun.textContent = status?.next_run_at ? formatLocalDate(status.next_run_at) : "Manual only";
  if (countdown) countdown.textContent = status?.is_running ? "In progress" : countdownLabel(status?.next_run_at);
  if (scheduleMode && status?.config) {
    if (status.config.mode === "interval") {
      scheduleMode.textContent = `Every ${status.config.interval_hours}h`;
    } else if (status.config.mode === "daily") {
      scheduleMode.textContent = `Daily at ${status.config.daily_time}`;
    } else {
      scheduleMode.textContent = "Manual only";
    }
  }
}

function startPipelinePolling(initialStatus) {
  let latestStatus = initialStatus || {};
  renderPipelineStatus(latestStatus);
  window.setInterval(() => renderPipelineStatus(latestStatus), 1000);
  window.setInterval(async () => {
    try {
      const response = await fetch("/api/pipeline/status");
      if (!response.ok) return;
      latestStatus = await response.json();
      renderPipelineStatus(latestStatus);
    } catch (_) {
      return;
    }
  }, 30000);
}

function createParagraphs(paragraphs) {
  return (paragraphs || []).map((paragraph) => `<p>${escapeHtml(paragraph)}</p>`).join("");
}

function sourceLabel(key, labels) {
  return labels[key] || key;
}

function initHomePage() {
  const articles = readJsonScript("articles-data", []);
  const sourceLabels = readJsonScript("source-labels-data", {});
  const initialFilters = readJsonScript("initial-filters-data", {
    source: "all",
    category: "all",
    q: "",
    bookmarks: false,
  });
  let pipelineStatus = readJsonScript("pipeline-status-data", {});

  const state = {
    source: initialFilters.source || "all",
    category: initialFilters.category || "all",
    q: initialFilters.q || "",
    bookmarksOnly: Boolean(initialFilters.bookmarks),
  };

  const bookmarks = loadBookmarks();
  const searchInput = document.getElementById("search-input");
  const sourceTabs = document.getElementById("source-tabs");
  const categoryTabs = document.getElementById("category-tabs");
  const heroSlot = document.getElementById("hero-slot");
  const articleGrid = document.getElementById("article-grid");
  const emptyState = document.getElementById("empty-state");
  const resultsCount = document.getElementById("results-count");
  const resultsTitle = document.getElementById("results-title");
  const activeFilters = document.getElementById("active-filters");
  const bookmarkToggle = document.getElementById("bookmark-toggle");
  const clearFilters = document.getElementById("clear-filters");
  const modal = document.getElementById("article-modal");
  const modalContent = document.getElementById("modal-content");

  function matches(article, options, ignoreKey = null) {
    if (ignoreKey !== "source" && options.source !== "all" && article.source_key !== options.source) return false;
    if (ignoreKey !== "category" && options.category !== "all" && article.category !== options.category) return false;
    if (ignoreKey !== "bookmarks" && options.bookmarksOnly && !bookmarks.has(article.id)) return false;
    if (ignoreKey !== "q" && options.q) {
      const term = options.q.trim().toLowerCase();
      if (term && !article.search_blob.includes(term)) return false;
    }
    return true;
  }

  function getFiltered(ignoreKey = null) {
    return articles.filter((article) => matches(article, state, ignoreKey));
  }

  function ensureValidCategory() {
    if (state.category === "all") return;
    const categories = new Set(getFiltered("category").map((article) => article.category));
    if (!categories.has(state.category)) state.category = "all";
  }

  function updateQuery() {
    const params = new URLSearchParams();
    if (state.source !== "all") params.set("source", state.source);
    if (state.category !== "all") params.set("category", state.category);
    if (state.q.trim()) params.set("q", state.q.trim());
    if (state.bookmarksOnly) params.set("bookmarks", "1");
    const query = params.toString();
    history.replaceState({}, "", query ? `/?${query}` : "/");
  }

  function renderSourceTabs() {
    const counts = { all: getFiltered("source").length };
    ["bbc", "cnn", "aljazeera"].forEach((key) => {
      counts[key] = getFiltered("source").filter((article) => article.source_key === key).length;
    });
    sourceTabs.innerHTML = ["all", "bbc", "cnn", "aljazeera"]
      .map((key) => {
        const active = state.source === key ? "active" : "";
        return `
          <button type="button" class="filter-tab ${active}" data-source-tab="${key}">
            <span>${escapeHtml(sourceLabel(key, sourceLabels))}</span>
            <span class="count">${counts[key] ?? 0}</span>
          </button>
        `;
      })
      .join("");
  }

  function renderCategoryTabs() {
    const counts = new Map([["all", getFiltered("category").length]]);
    getFiltered("category").forEach((article) => {
      counts.set(article.category, (counts.get(article.category) || 0) + 1);
    });
    const categories = [["all", "All Categories"], ...Array.from(counts.entries())
      .filter(([key]) => key !== "all")
      .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
      .map(([key]) => [key, key])];

    categoryTabs.innerHTML = categories
      .map(([key, label]) => {
        const active = state.category === key ? "active" : "";
        return `
          <button type="button" class="filter-tab ${active}" data-category-tab="${escapeHtml(key)}">
            <span>${escapeHtml(label)}</span>
            <span class="count">${counts.get(key) ?? 0}</span>
          </button>
        `;
      })
      .join("");
  }

  function renderHero(filtered) {
    const hero = filtered[0];
    if (!hero) {
      heroSlot.innerHTML = "";
      return;
    }
    heroSlot.innerHTML = `
      <article class="hero-card">
        <div class="hero-image-wrap">
          ${hero.image_path ? `<img src="${escapeHtml(hero.image_path)}" alt="${escapeHtml(hero.title)}">` : ""}
        </div>
        <div class="hero-content">
          <div class="card-topline">
            <span class="card-source">${escapeHtml(hero.source)}</span>
            <span class="card-reading-time">${hero.reading_time_minutes} min read</span>
          </div>
          <h2>${escapeHtml(hero.title)}</h2>
          <div class="hero-meta">
            <span>${escapeHtml(hero.category)}</span>
            <span>${escapeHtml(hero.scraped_label)}</span>
            ${hero.is_new ? '<span class="pill new-pill">New</span>' : ""}
          </div>
          <p class="hero-summary">${escapeHtml(hero.description || "")}</p>
          <div class="hero-actions">
            <button type="button" class="primary-link" data-open-modal="${escapeHtml(hero.id)}">Quick view</button>
            <a class="secondary-link" href="/article/${encodeURIComponent(hero.id)}">Open full page</a>
            <button type="button" class="bookmark-button ${bookmarks.has(hero.id) ? "active" : ""}" data-bookmark-id="${escapeHtml(hero.id)}">
              ${bookmarks.has(hero.id) ? "Bookmarked" : "Bookmark"}
            </button>
          </div>
        </div>
      </article>
    `;
  }

  function renderCards(filtered) {
    const rest = filtered.slice(1);
    articleGrid.innerHTML = rest.map((article) => `
      <article class="news-card">
        ${article.image_path ? `<img src="${escapeHtml(article.image_path)}" alt="${escapeHtml(article.title)}" class="card-image">` : ""}
        <div class="card-body">
          <div class="card-topline">
            <span class="card-source">${escapeHtml(article.source)}</span>
            <span class="card-reading-time">${article.reading_time_minutes} min read</span>
          </div>
          <div class="card-topline">
            <span class="card-category">${escapeHtml(article.category)}</span>
            ${article.is_new ? '<span class="pill new-pill">New</span>' : ""}
          </div>
          <h3><a href="/article/${encodeURIComponent(article.id)}">${escapeHtml(article.title)}</a></h3>
          <p>${escapeHtml(article.description || "")}</p>
          <div class="card-footer">
            <span>${escapeHtml(article.scraped_label)}</span>
            <span>${article.word_count} words</span>
          </div>
          <div class="card-actions">
            <button type="button" class="ghost-action" data-open-modal="${escapeHtml(article.id)}">Quick view</button>
            <button type="button" class="bookmark-button ${bookmarks.has(article.id) ? "active" : ""}" data-bookmark-id="${escapeHtml(article.id)}">
              ${bookmarks.has(article.id) ? "Bookmarked" : "Bookmark"}
            </button>
          </div>
        </div>
      </article>
    `).join("");
  }

  function renderActiveFilters() {
    const chips = [];
    if (state.source !== "all") chips.push(`Source: ${sourceLabel(state.source, sourceLabels)}`);
    if (state.category !== "all") chips.push(`Category: ${state.category}`);
    if (state.q.trim()) chips.push(`Search: ${state.q.trim()}`);
    if (state.bookmarksOnly) chips.push("Bookmarks only");
    activeFilters.innerHTML = chips.map((label) => `<span class="filter-chip">${escapeHtml(label)}</span>`).join("");
  }

  function updateBookmarkUi() {
    const countNode = document.getElementById("bookmark-count");
    if (countNode) countNode.textContent = String(bookmarks.size);
    bookmarkToggle.classList.toggle("active", state.bookmarksOnly);
  }

  function openModal(articleId) {
    const article = articles.find((item) => item.id === articleId);
    if (!article) return;
    modalContent.innerHTML = `
      <div class="modal-story">
        <div class="modal-story-body">
          ${article.image_path ? `<img src="${escapeHtml(article.image_path)}" alt="${escapeHtml(article.title)}" class="story-image">` : ""}
          <p class="story-kicker">${escapeHtml(article.source)} / ${escapeHtml(article.category)}</p>
          <h2>${escapeHtml(article.title)}</h2>
          <div class="story-meta-row">
            <span>${escapeHtml(article.scraped_label)}</span>
            <span>${article.reading_time_minutes} min read</span>
            <span>${article.word_count} words</span>
            ${article.is_new ? '<span class="pill new-pill">New</span>' : ""}
          </div>
          ${createParagraphs(article.paragraphs)}
          <div class="hero-actions">
            <a class="secondary-link" href="/article/${encodeURIComponent(article.id)}">Open full page</a>
            ${article.url ? `<a class="primary-link" href="${escapeHtml(article.url)}" target="_blank" rel="noopener noreferrer">Read original</a>` : ""}
          </div>
        </div>
        <aside class="modal-meta-panel">
          <p class="eyebrow">Metadata</p>
          <dl class="meta-list">
            <div><dt>Scraped at</dt><dd>${escapeHtml(article.scraped_label)}</dd></div>
            <div><dt>Stored path</dt><dd><code>${escapeHtml(article.stored_path)}</code></dd></div>
            <div><dt>Source</dt><dd>${escapeHtml(article.source)}</dd></div>
            <div><dt>Image path</dt><dd><code>${escapeHtml(article.image_path || "Not stored")}</code></dd></div>
            <div><dt>URL</dt><dd class="meta-break">${article.url ? `<a href="${escapeHtml(article.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(article.url)}</a>` : "Missing"}</dd></div>
          </dl>
        </aside>
      </div>
    `;
    modal.classList.remove("hidden");
    modal.setAttribute("aria-hidden", "false");
  }

  function closeModal() {
    modal.classList.add("hidden");
    modal.setAttribute("aria-hidden", "true");
    modalContent.innerHTML = "";
  }

  function render() {
    ensureValidCategory();
    const filtered = getFiltered();
    renderSourceTabs();
    renderCategoryTabs();
    renderHero(filtered);
    renderCards(filtered);
    renderActiveFilters();
    updateBookmarkUi();
    resultsCount.textContent = `${filtered.length} result${filtered.length === 1 ? "" : "s"}`;
    resultsTitle.textContent = state.bookmarksOnly ? "Saved for later" : "Top stories";
    emptyState.classList.toggle("hidden", filtered.length > 0);
    articleGrid.classList.toggle("hidden", filtered.length === 0);
    heroSlot.classList.toggle("hidden", filtered.length === 0);
    updateQuery();
  }

  searchInput.value = state.q;
  searchInput.addEventListener("input", (event) => {
    state.q = event.target.value;
    render();
  });

  sourceTabs.addEventListener("click", (event) => {
    const button = event.target.closest("[data-source-tab]");
    if (!button) return;
    state.source = button.dataset.sourceTab;
    render();
  });

  categoryTabs.addEventListener("click", (event) => {
    const button = event.target.closest("[data-category-tab]");
    if (!button) return;
    state.category = button.dataset.categoryTab;
    render();
  });

  [heroSlot, articleGrid].forEach((node) => {
    node.addEventListener("click", (event) => {
      const modalButton = event.target.closest("[data-open-modal]");
      if (modalButton) {
        openModal(modalButton.dataset.openModal);
        return;
      }
      const bookmarkButton = event.target.closest("[data-bookmark-id]");
      if (!bookmarkButton) return;
      const id = bookmarkButton.dataset.bookmarkId;
      if (bookmarks.has(id)) {
        bookmarks.delete(id);
      } else {
        bookmarks.add(id);
      }
      saveBookmarks(bookmarks);
      render();
    });
  });

  bookmarkToggle.addEventListener("click", () => {
    state.bookmarksOnly = !state.bookmarksOnly;
    render();
  });

  clearFilters.addEventListener("click", () => {
    state.source = "all";
    state.category = "all";
    state.q = "";
    state.bookmarksOnly = false;
    searchInput.value = "";
    render();
  });

  modal.addEventListener("click", (event) => {
    if (event.target.closest("[data-close-modal='true']")) closeModal();
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !modal.classList.contains("hidden")) closeModal();
  });

  applyTheme();
  document.getElementById("theme-toggle")?.addEventListener("click", toggleTheme);
  startPipelinePolling(pipelineStatus);
  render();
}

function initArticlePage() {
  const article = readJsonScript("page-article-data", {});
  const pipelineStatus = readJsonScript("pipeline-status-data", {});
  const bookmarks = loadBookmarks();
  const button = document.getElementById("article-bookmark-button");

  function syncBookmarkButton() {
    if (!button) return;
    const active = bookmarks.has(article.id);
    button.textContent = active ? "Bookmarked" : "Bookmark";
    button.classList.toggle("active", active);
  }

  button?.addEventListener("click", () => {
    if (bookmarks.has(article.id)) {
      bookmarks.delete(article.id);
    } else {
      bookmarks.add(article.id);
    }
    saveBookmarks(bookmarks);
    syncBookmarkButton();
  });

  applyTheme();
  document.getElementById("theme-toggle")?.addEventListener("click", toggleTheme);
  startPipelinePolling(pipelineStatus);
  syncBookmarkButton();
}

document.addEventListener("DOMContentLoaded", () => {
  applyTheme();
  const page = document.body.dataset.page;
  if (page === "home") initHomePage();
  if (page === "article") initArticlePage();
});
