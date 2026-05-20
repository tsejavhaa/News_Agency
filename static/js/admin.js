function readJsonScript(id, fallback) {
  const node = document.getElementById(id);
  if (!node) return fallback;
  try {
    return JSON.parse(node.textContent);
  } catch (_) {
    return fallback;
  }
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

function formatCount(value) {
  return new Intl.NumberFormat().format(Number(value || 0));
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
  document.getElementById("pipeline-dot")?.classList.toggle("running", Boolean(status?.is_running));
  const runningLabel = document.getElementById("pipeline-running-label");
  if (runningLabel) runningLabel.textContent = status?.is_running ? "Running now" : "Idle";
  const lastRun = document.getElementById("pipeline-last-run");
  if (lastRun) lastRun.textContent = formatLocalDate(status?.last_run_finished_at);
  const nextRun = document.getElementById("pipeline-next-run");
  if (nextRun) nextRun.textContent = status?.next_run_at ? formatLocalDate(status.next_run_at) : "Manual only";
  const countdown = document.getElementById("pipeline-countdown");
  if (countdown) countdown.textContent = status?.is_running ? "In progress" : countdownLabel(status?.next_run_at);
  const trigger = document.getElementById("pipeline-trigger");
  if (trigger) trigger.textContent = status?.current_trigger || "n/a";
  const runButton = document.getElementById("run-pipeline");
  if (runButton) runButton.disabled = Boolean(status?.is_running);
  renderPipelineProgress(status);
}

function renderPipelineProgress(status) {
  const progress = status?.progress || {};
  const stats = status?.stats || {};
  const dedup = progress.dedup || {};
  const merge = progress.merge || {};
  const sources = Array.isArray(progress.sources) ? progress.sources : [];
  const fallbackSources = Array.isArray(stats.sources) ? stats.sources : [];
  const totalNew = sources.length
    ? sources.reduce((sum, source) => sum + Number(source.new || 0), 0)
    : fallbackSources.reduce((sum, source) => sum + Number(source.new || 0), 0);
  const totalLoaded = Number(merge.total_articles || stats.total_articles || 0);
  const duplicatesRemoved = Number(dedup.duplicates_removed ?? stats.duplicates_removed ?? stats?.dedup?.removed ?? 0);
  const uniqueTotal = Number(dedup.unique_articles || stats.total_articles || stats?.dedup?.clean_count || 0);
  const overallPercent = Number(progress.overall_percent || (status?.is_running ? 5 : uniqueTotal ? 100 : 0));

  const phaseLabel = document.getElementById("pipeline-phase-label");
  if (phaseLabel) phaseLabel.textContent = progress.phase_label || (status?.is_running ? "Pipeline running" : "Waiting for pipeline");
  const phasePercent = document.getElementById("pipeline-overall-percent");
  if (phasePercent) phasePercent.textContent = `${Math.max(0, Math.min(100, overallPercent))}%`;
  const fill = document.getElementById("pipeline-progress-fill");
  if (fill) fill.style.width = `${Math.max(0, Math.min(100, overallPercent))}%`;
  const phaseDetail = document.getElementById("pipeline-phase-detail");
  if (phaseDetail) {
    phaseDetail.textContent = progress.phase_detail
      || (status?.is_running ? "Pipeline is running." : "No pipeline activity yet.");
  }

  const totalNewNode = document.getElementById("pipeline-total-new");
  if (totalNewNode) totalNewNode.textContent = formatCount(totalNew);
  const totalLoadedNode = document.getElementById("pipeline-total-loaded");
  if (totalLoadedNode) totalLoadedNode.textContent = formatCount(totalLoaded);
  const duplicatesNode = document.getElementById("pipeline-duplicates-removed");
  if (duplicatesNode) duplicatesNode.textContent = formatCount(duplicatesRemoved);
  const uniqueNode = document.getElementById("pipeline-unique-total");
  if (uniqueNode) uniqueNode.textContent = formatCount(uniqueTotal);

  const sourceList = document.getElementById("source-progress-list");
  if (sourceList) {
    if (!sources.length && fallbackSources.length) {
      sourceList.innerHTML = fallbackSources.map((source) => `
        <div class="source-progress-item is-done">
          <div class="source-progress-head">
            <strong class="source-progress-name">${source.name}</strong>
            <span class="source-progress-meta">+${formatCount(source.new)} new • ${Number(source.duration || 0).toFixed(1)}s</span>
          </div>
          <div class="source-progress-track"><div class="source-progress-fill" style="width:100%"></div></div>
          <p class="source-progress-note">${formatCount(source.after || 0)} stored total</p>
        </div>
      `).join("");
    } else {
      sourceList.innerHTML = sources.map((source) => {
        const total = Number(source.total || 0);
        const processed = Number(source.processed || 0);
        const percent = total > 0 ? Math.min(100, Math.round((processed / total) * 100)) : (source.status === "done" ? 100 : 0);
        const statusClass = source.status === "error" ? "is-error" : source.status === "done" ? "is-done" : "is-running";
        const meta = total > 0
          ? `${formatCount(processed)} / ${formatCount(total)} scraped`
          : source.status === "done"
            ? `${formatCount(source.new)} new`
            : "Waiting for source list";
        const note = source.error
          ? source.error
          : source.last_title
            ? source.last_title
            : `+${formatCount(source.new)} new • ${formatCount(source.skipped)} skipped`;
        return `
          <div class="source-progress-item ${statusClass}">
            <div class="source-progress-head">
              <strong class="source-progress-name">${source.name}</strong>
              <span class="source-progress-meta">${meta}</span>
            </div>
            <div class="source-progress-track"><div class="source-progress-fill" style="width:${percent}%"></div></div>
            <p class="source-progress-note">${note}</p>
          </div>
        `;
      }).join("");
    }
  }

  const analysisSummary = document.getElementById("pipeline-analysis-summary");
  if (analysisSummary) {
    if (progress.current_phase === "dedup" || dedup.total_input || dedup.unique_articles) {
      const pairTotal = Number(dedup.total || 0);
      const pairDone = Number(dedup.processed || 0);
      const pairLine = pairTotal
        ? `${dedup.pass_label || "Deduplication"}: ${formatCount(pairDone)} / ${formatCount(pairTotal)}`
        : (dedup.pass_label || "Deduplication ready");
      const llmLine = dedup.llm_calls
        ? `LLM checks: ${formatCount(dedup.llm_calls)}`
        : "LLM checks: 0";
      const dupLine = `Removed: ${formatCount(dedup.duplicates_removed || 0)} • Unique: ${formatCount(dedup.unique_articles || 0)}`;
      analysisSummary.textContent = `${pairLine} • ${llmLine} • ${dupLine}`;
    } else if (stats?.dedup) {
      analysisSummary.textContent = `Last run dedup: removed ${formatCount(stats.dedup.removed || 0)} duplicates and kept ${formatCount(stats.dedup.clean_count || 0)} unique articles.`;
    } else {
      analysisSummary.textContent = "Deduplication details will appear here during a run.";
    }
  }
}

document.addEventListener("DOMContentLoaded", () => {
  let pipelineStatus = readJsonScript("pipeline-status-data", {});
  let schedulerConfig = readJsonScript("scheduler-config-data", {});

  const form = document.getElementById("scheduler-form");
  const statusNode = document.getElementById("scheduler-status");
  const runStatusNode = document.getElementById("pipeline-run-status");
  const runButton = document.getElementById("run-pipeline");
  const intervalInput = document.getElementById("interval-hours");
  const dailyTimeInput = document.getElementById("daily-time");

  function syncForm() {
    const mode = schedulerConfig.mode || "interval";
    form.querySelectorAll("input[name='mode']").forEach((input) => {
      input.checked = input.value === mode;
    });
    intervalInput.value = schedulerConfig.interval_hours || 6;
    dailyTimeInput.value = schedulerConfig.daily_time || "08:00";
  }

  async function refreshStatus() {
    try {
      const response = await fetch("/api/pipeline/status");
      if (!response.ok) return;
      pipelineStatus = await response.json();
      renderPipelineStatus(pipelineStatus);
    } catch (_) {
      return;
    }
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const mode = form.querySelector("input[name='mode']:checked")?.value || "interval";
    const payload = {
      mode,
      interval_hours: Number(intervalInput.value || 6),
      daily_time: dailyTimeInput.value || "08:00",
    };

    statusNode.textContent = "Saving scheduler...";
    try {
      const response = await fetch("/api/scheduler/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!response.ok) throw new Error("Unable to save scheduler.");
      pipelineStatus = await response.json();
      schedulerConfig = pipelineStatus.config || payload;
      syncForm();
      renderPipelineStatus(pipelineStatus);
      statusNode.textContent = "Scheduler saved.";
    } catch (error) {
      statusNode.textContent = error.message;
    }
  });

  runButton.addEventListener("click", async () => {
    runButton.disabled = true;
    runStatusNode.textContent = "Starting pipeline...";
    try {
      const response = await fetch("/api/pipeline/run", { method: "POST" });
      const data = await response.json();
      pipelineStatus = data.pipeline || pipelineStatus;
      renderPipelineStatus(pipelineStatus);
      runStatusNode.textContent = data.status === "started" ? "Pipeline started." : "Pipeline is already running.";
    } catch (_) {
      runStatusNode.textContent = "Unable to start the pipeline.";
    } finally {
      runButton.disabled = false;
    }
  });

  applyTheme();
  document.getElementById("theme-toggle")?.addEventListener("click", toggleTheme);
  renderPipelineStatus(pipelineStatus);
  syncForm();

  window.setInterval(() => renderPipelineStatus(pipelineStatus), 1000);
  window.setInterval(refreshStatus, 2500);
});
