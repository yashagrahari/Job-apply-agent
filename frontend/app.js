const dropzone = document.getElementById("dropzone");
const fileInput = document.getElementById("file-input");
const browseBtn = document.getElementById("browse-btn");
const searchBtn = document.getElementById("search-btn");
const fileNameEl = document.getElementById("file-name");
const statusEl = document.getElementById("status");
const statusTitle = document.getElementById("status-title");
const statusDetail = document.getElementById("status-detail");
const errorBanner = document.getElementById("error-banner");
const resultsEl = document.getElementById("results");
const expBadge = document.getElementById("exp-badge");
const rolesList = document.getElementById("roles-list");
const skillsList = document.getElementById("skills-list");
const jobsCount = document.getElementById("jobs-count");
const jobsGrid = document.getElementById("jobs-grid");
const applyModal = document.getElementById("apply-modal");
const modalTitle = document.getElementById("modal-title");
const modalBody = document.getElementById("modal-body");
const modalClose = document.getElementById("modal-close");
const modalCopyBtn = document.getElementById("modal-copy-btn");
const modalOpenApply = document.getElementById("modal-open-apply");

const API_BASE = "";
const MAX_BYTES = 10 * 1024 * 1024;
const APPLIED_KEY = "job-apply-agent:applied";

let selectedFile = null;
let lastSearchData = null;
let modalPlainText = "";

function apiUrl(path) {
  return `${API_BASE}${path}`;
}

function setFile(file) {
  if (!file || !file.name.toLowerCase().endsWith(".pdf")) {
    showError("Please upload a PDF resume.");
    return;
  }
  if (file.size > MAX_BYTES) {
    showError("File must be under 10 MB.");
    return;
  }
  selectedFile = file;
  dropzone.classList.add("has-file");
  fileNameEl.hidden = false;
  fileNameEl.textContent = file.name;
  searchBtn.disabled = false;
  hideError();
}

function showError(message) {
  errorBanner.hidden = false;
  errorBanner.textContent = message;
}

function hideError() {
  errorBanner.hidden = true;
}

function setLoading(loading) {
  statusEl.hidden = !loading;
  searchBtn.disabled = loading || !selectedFile;
  browseBtn.disabled = loading;
  dropzone.style.pointerEvents = loading ? "none" : "";
  if (loading) {
    resultsEl.hidden = true;
    statusTitle.textContent = "Analyzing your resume…";
    statusDetail.textContent =
      "Extracting skills, checking ATS feeds, and validating live job links. This may take a minute.";
  }
}

function renderChips(container, items) {
  container.innerHTML = "";
  items.forEach((item) => {
    const li = document.createElement("li");
    li.textContent = item;
    container.appendChild(li);
  });
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text ?? "";
  return div.innerHTML;
}

function jobKey(job) {
  return `${job.role}|${job.platform}|${job.apply_link}`;
}

function getAppliedSet() {
  try {
    return new Set(JSON.parse(localStorage.getItem(APPLIED_KEY) || "[]"));
  } catch {
    return new Set();
  }
}

function markJobApplied(job) {
  const set = getAppliedSet();
  set.add(jobKey(job));
  localStorage.setItem(APPLIED_KEY, JSON.stringify([...set]));
}

function renderJobs(jobs) {
  jobsGrid.innerHTML = "";
  jobsCount.textContent = `${jobs.length} found`;
  const applied = getAppliedSet();

  if (!jobs.length) {
    jobsGrid.innerHTML =
      '<p class="dropzone-hint">No jobs returned. Try updating your resume or searching again.</p>';
    return;
  }

  jobs.forEach((job, index) => {
    const card = document.createElement("article");
    card.className = "job-card";

    const contact =
      job.contact_info &&
      job.contact_info !== "null" &&
      job.contact_info.trim()
        ? `<p class="job-contact"><strong>Contact:</strong> ${escapeHtml(job.contact_info)}</p>`
        : "";

    const rawLink = (job.apply_link || "").trim();
    const link =
      rawLink.startsWith("http://") || rawLink.startsWith("https://")
        ? rawLink
        : rawLink
          ? `https://${rawLink}`
          : "";

    const appliedBadge = applied.has(jobKey(job))
      ? '<span class="job-applied-badge">Prepared / applied</span>'
      : "";

    const applyLink = link
      ? `<a class="job-apply" href="${escapeHtml(link)}" target="_blank" rel="noopener noreferrer">Open job page →</a>`
      : "";

    card.innerHTML = `
      <div class="job-card-header">
        <h3 class="job-role">${escapeHtml(job.role)}</h3>
        <span class="job-platform">${escapeHtml(job.platform)}</span>
      </div>
      <div class="job-meta">
        <span>📍 ${escapeHtml(job.location)}</span>
        <span>⏱ ${escapeHtml(String(job.Exp))}+ yrs exp</span>
      </div>
      ${contact}
      <div class="job-actions">
        <button type="button" class="job-prepare" data-job-index="${index}">Prepare application</button>
        ${applyLink}
        ${appliedBadge}
      </div>
    `;
    jobsGrid.appendChild(card);
  });

  jobsGrid.querySelectorAll(".job-prepare").forEach((btn) => {
    btn.addEventListener("click", () => {
      const job = jobs[Number(btn.dataset.jobIndex)];
      if (job) prepareApplication(job, btn);
    });
  });
}

function renderResults(data) {
  lastSearchData = data;
  const candidate = data.candidate ?? {};
  const jobs = data.jobs ?? [];
  expBadge.textContent = `${candidate.Exp ?? "—"} years experience`;
  renderChips(rolesList, candidate.roles ?? []);
  renderChips(skillsList, candidate.skills ?? []);
  renderJobs(jobs);
  resultsEl.hidden = false;
}

function closeModal() {
  applyModal.hidden = true;
  document.body.style.overflow = "";
}

function openModal(job, application) {
  const link = (job.apply_link || "").trim();
  modalTitle.textContent = `Application kit — ${job.role}`;
  modalOpenApply.href = link || "#";
  modalOpenApply.hidden = !link;

  const answers = application.common_answers || {};
  const answersHtml = Object.entries(answers)
    .map(
      ([q, a]) =>
        `<li><strong>${escapeHtml(q)}</strong><br>${escapeHtml(a)}</li>`
    )
    .join("");

  modalBody.innerHTML = `
    <p class="modal-note">${escapeHtml(application.auto_apply_note || "")}</p>
    <h3>Cover letter</h3>
    <pre class="cover-letter">${escapeHtml(application.cover_letter || "")}</pre>
    <h3>Why you fit</h3>
    <ul>${(application.why_you_fit || []).map((b) => `<li>${escapeHtml(b)}</li>`).join("")}</ul>
    <h3>Common form answers</h3>
    <ul>${answersHtml || "<li>No answers generated</li>"}</ul>
    <h3>Apply checklist</h3>
    <ul>${(application.apply_checklist || []).map((s) => `<li>${escapeHtml(s)}</li>`).join("")}</ul>
  `;

  modalPlainText = [
    application.cover_letter,
    "",
    "Why you fit:",
    ...(application.why_you_fit || []).map((b) => `• ${b}`),
    "",
    "Form answers:",
    ...Object.entries(answers).map(([q, a]) => `${q}: ${a}`),
    "",
    "Checklist:",
    ...(application.apply_checklist || []).map((s, i) => `${i + 1}. ${s}`),
  ].join("\n");

  applyModal.hidden = false;
  document.body.style.overflow = "hidden";
}

async function prepareApplication(job, button) {
  if (!lastSearchData?.candidate) {
    showError("Search for jobs first so we can tailor your application.");
    return;
  }

  hideError();
  button.disabled = true;
  const label = button.textContent;
  button.textContent = "Preparing…";

  try {
    const response = await fetch(apiUrl("/api/prepare-application"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        candidate: lastSearchData.candidate,
        job,
      }),
    });

    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.detail || "Failed to prepare application.");
    }

    markJobApplied(job);
    openModal(job, data.application);
    renderJobs(lastSearchData.jobs ?? []);
  } catch (err) {
    showError(err.message || "Failed to prepare application.");
  } finally {
    button.disabled = false;
    button.textContent = label;
  }
}

async function searchJobs() {
  if (!selectedFile) return;

  hideError();
  setLoading(true);

  const formData = new FormData();
  formData.append("resume", selectedFile, selectedFile.name);

  const url = apiUrl("/api/search-jobs");
  console.info("[Job Apply] POST", url, selectedFile.name, selectedFile.size, "bytes");

  try {
    const response = await fetch(url, {
      method: "POST",
      body: formData,
    });

    const data = await response.json().catch(() => ({}));

    if (!response.ok) {
      const detail = data.detail;
      let message;
      if (Array.isArray(detail)) {
        message = detail
          .map((d) => d.msg || d.message || JSON.stringify(d))
          .join(", ");
      } else if (typeof detail === "object" && detail !== null) {
        message = detail.msg || detail.message || JSON.stringify(detail);
      } else {
        message = detail || `Request failed (${response.status})`;
      }
      throw new Error(message);
    }

    renderResults(data);
  } catch (err) {
    const message =
      err.message === "Failed to fetch"
        ? "Cannot reach the server. Start it with: uvicorn api:app --reload --host 0.0.0.0 --port 8000"
        : err.message || "Failed to fetch jobs.";
    showError(message);
  } finally {
    setLoading(false);
  }
}

browseBtn.addEventListener("click", (e) => {
  e.stopPropagation();
  fileInput.click();
});

dropzone.addEventListener("click", () => fileInput.click());

dropzone.addEventListener("keydown", (e) => {
  if (e.key === "Enter" || e.key === " ") {
    e.preventDefault();
    fileInput.click();
  }
});

fileInput.addEventListener("change", () => {
  if (fileInput.files?.[0]) setFile(fileInput.files[0]);
});

dropzone.addEventListener("dragover", (e) => {
  e.preventDefault();
  dropzone.classList.add("dragover");
});

dropzone.addEventListener("dragleave", () => {
  dropzone.classList.remove("dragover");
});

dropzone.addEventListener("drop", (e) => {
  e.preventDefault();
  dropzone.classList.remove("dragover");
  const file = e.dataTransfer.files?.[0];
  if (file) setFile(file);
});

searchBtn.addEventListener("click", searchJobs);

modalClose.addEventListener("click", closeModal);
applyModal.addEventListener("click", (e) => {
  if (e.target === applyModal) closeModal();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !applyModal.hidden) closeModal();
});
modalCopyBtn.addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(modalPlainText);
    modalCopyBtn.textContent = "Copied!";
    setTimeout(() => {
      modalCopyBtn.textContent = "Copy all";
    }, 2000);
  } catch {
    showError("Could not copy to clipboard.");
  }
});

async function checkServer() {
  if (window.location.protocol === "file:") {
    showError(
      "Open the app via the API server: http://localhost:8000 (not as a local HTML file)."
    );
    return;
  }

  try {
    const response = await fetch(apiUrl("/api/health"));
    if (!response.ok) throw new Error(`Health check failed (${response.status})`);
    const data = await response.json();
    console.info("[Job Apply] API ready", data);
    hideError();
  } catch {
    showError(
      "Cannot reach the API. Start it with: uvicorn api:app --reload --host 0.0.0.0 --port 8000"
    );
  }
}

checkServer();
