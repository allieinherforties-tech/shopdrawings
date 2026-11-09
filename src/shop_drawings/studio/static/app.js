/* Studio landing page logic: drag-and-drop upload with inline errors,
   and the recent-jobs list. The server owns every rule (type, size,
   extraction) — this layer only renders what it answers. */

"use strict";

const $ = (id) => document.getElementById(id);

function escapeHtml(s) {
  return s.replace(/[&<>\"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

/* ---------- upload ---------- */

function showError(message) {
  $("upload-error").textContent = message;
  $("upload-error").hidden = false;
  $("upload-progress").hidden = true;
  $("dropzone").disabled = false;
}

async function upload(file) {
  $("upload-error").hidden = true;
  $("upload-progress").hidden = false;
  $("dropzone").disabled = true;

  const body = new FormData();
  body.append("file", file, file.name);
  let response;
  try {
    response = await fetch("/api/upload", { method: "POST", body });
  } catch {
    showError("The upload didn't go through — check the connection and try again.");
    return;
  }

  // Success comes back as a followed redirect to the job's review page.
  if (response.redirected) {
    window.location.href = response.url;
    return;
  }

  if (!response.ok) {
    let message = `Upload failed (HTTP ${response.status}).`;
    try {
      const json = await response.json();
      if (json.detail) message = json.detail;
    } catch {
      /* keep the generic message */
    }
    showError(message);
    return;
  }

  window.location.href = "/";
}

/* ---------- recent jobs ---------- */

function jobHref(job) {
  return job.status === "failed" ? `/failed/${job.job_id}` : `/jobs/${job.job_id}/`;
}

function badgeClass(job) {
  if (job.status === "failed") return "status-badge failed";
  if (job.review_status === "approved") return "status-badge approved";
  return "status-badge";
}

function jobLabel(job) {
  if (job.status === "failed") return "Extraction failed";
  if (job.review_status === "approved") return "Approved";
  return "In review";
}

function renderJobs(list) {
  const container = $("jobs");
  container.replaceChildren();
  if (!list.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "No jobs yet — upload your first design above.";
    container.appendChild(empty);
    return;
  }
  for (const job of list) {
    const row = document.createElement("div");
    row.className = "job";
    const name = escapeHtml(job.project_name || job.source_name);
    const detail = job.project_name ? escapeHtml(job.source_name) : escapeHtml(job.job_id);
    row.innerHTML =
      `<div><div class="job-name"><a href="${jobHref(job)}">${name}</a></div>` +
      `<div class="job-meta">${detail}</div></div>` +
      `<span class="${badgeClass(job)}">${jobLabel(job)}</span>`;
    container.appendChild(row);
  }
}

async function loadJobs() {
  try {
    const response = await fetch("/api/jobs");
    if (!response.ok) return;
    renderJobs(await response.json());
  } catch {
    /* the list is a convenience — the upload flow still works without it */
  }
}

/* ---------- wiring ---------- */

const dropzone = $("dropzone");
const fileInput = $("file-input");

dropzone.addEventListener("click", () => fileInput.click());
dropzone.addEventListener("keydown", (e) => {
  if (e.key === "Enter" || e.key === " ") {
    e.preventDefault();
    fileInput.click();
  }
});
dropzone.addEventListener("dragover", (e) => {
  e.preventDefault();
  dropzone.classList.add("dragover");
});
dropzone.addEventListener("dragleave", () => dropzone.classList.remove("dragover"));
dropzone.addEventListener("drop", (e) => {
  e.preventDefault();
  dropzone.classList.remove("dragover");
  const file = e.dataTransfer.files && e.dataTransfer.files[0];
  if (file) upload(file);
});
fileInput.addEventListener("change", () => {
  if (fileInput.files && fileInput.files[0]) upload(fileInput.files[0]);
});

loadJobs();
