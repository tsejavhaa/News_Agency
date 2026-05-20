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
  window.setInterval(refreshStatus, 30000);
});
