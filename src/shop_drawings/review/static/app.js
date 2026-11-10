/* Review gate UI logic.
   Renders the review queue in priority order and applies reviewer actions
   through the API; the server (and the schema behind it) enforce the gate. */

"use strict";

const state = { spec: null, queue: null, gateErrors: [], specStatus: "", log: [], vision: null, outputs: [] };

const $ = (id) => document.getElementById(id);

const fmt = (n) => Number(n).toLocaleString("en-US", { maximumFractionDigits: 3 });

const addrKey = (a) =>
  `${a.part_id}|${a.feature_index === null ? "" : a.feature_index}|${a.kind}`;

/* ---------- data helpers ---------- */

function findValue(address) {
  // Live value for an address, walked from the raw spec.
  for (const part of state.spec.parts) {
    if (part.id !== address.part_id) continue;
    if (address.feature_index === null) {
      return part.dimensions[address.kind];
    }
    return part.features[address.feature_index].dimensions[address.kind];
  }
  return null;
}

/* Provenance: one click from any row to the exact page region its value
   was read from. The button opens the crop modal (page image + highlighted
   region + the quoted source text). */
function sourceHtml(source, label) {
  const [x0, y0, x1, y1] = source.region;
  return `<button class="source-btn" title="Page ${source.page}: “${escapeHtml(source.text)}” — click to enlarge"` +
    ` data-page="${source.page}" data-x0="${x0}" data-y0="${y0}" data-x1="${x1}" data-y1="${y1}"` +
    ` data-text="${escapeHtml(source.text)}">${escapeHtml(label)} · page ${source.page} ↗</button>`;
}

const cropHtml = (source) => sourceHtml(source, "Source");

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

/* ---------- API ---------- */

async function post(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const json = await res.json();
  if (!res.ok) {
    return { ok: false, gateErrors: json.gate_errors || [json.detail || `HTTP ${res.status}`] };
  }
  Object.assign(state, json);
  return { ok: true };
}

async function refresh() {
  const res = await fetch("api/review");
  const json = await res.json();
  Object.assign(state, json);
}

/* ---------- actions ---------- */

async function act(promise) {
  const result = await promise;
  if (!result.ok) showBanner("error", result.gateErrors);
  else hideBanner();
  render();
}

const confirmValue = (address) =>
  act(post("api/values", { address, status: "approved" }));

const correctValue = (address, value) =>
  act(post("api/values", { address, value, status: "approved" }));

const resolveQuestion = (index, answer) =>
  act(post("api/questions/resolve", { index, answer }));

const confirmBatch = (addresses) =>
  act(post("api/values/confirm-batch", { addresses }));

const approve = () => act(post("api/approve", {}));

/* ---------- render ---------- */

function showBanner(kind, messages) {
  const el = $("banner");
  el.className = `banner ${kind}`;
  el.innerHTML = kind === "success"
    ? escapeHtml(messages[0])
    : `<strong>Blocked by the review gate:</strong><ul>` +
      messages.map((m) => `<li>${escapeHtml(m)}</li>`).join("") + `</ul>`;
  el.hidden = false;
}

function hideBanner() { $("banner").hidden = true; }

function render() {
  const approved = state.specStatus === "approved";
  const q = state.queue;

  $("project-meta").textContent =
    `${state.spec.project.name} · ${state.spec.project.units} · from ${state.spec.project.source}`;

  const badge = $("status-badge");
  badge.textContent = state.specStatus.replace("_", " ");
  badge.classList.toggle("approved", approved);

  const blockers = (
    q.questions.length + q.conflicts.length + q.low_confidence.length
    + q.unreviewed.length + q.parts.length + q.constraints.length
  );
  const chip = $("blocking-chip");
  chip.textContent = approved ? "gate passed" : `${blockers} value${blockers === 1 ? "" : "s"} + ${q.questions.length} question${q.questions.length === 1 ? "" : "s"} blocking`;
  chip.classList.toggle("clear", approved);

  $("approve-btn").disabled = approved;
  $("approve-btn").textContent = approved ? "Approved" : "Approve spec";

  renderQuestions(q.questions, approved);
  renderConflicts(q.conflicts, approved);
  renderLowConfidence(q.low_confidence, approved);
  renderValues(approved);
  renderOutputs();
  renderLog();
  renderNav(q);
}

/* Queue-by-kind navigation: one glance shows how much work of each kind
   there is, and each chip jumps to its section. */
function renderNav(q) {
  const remaining = q.unreviewed.length + q.parts.length + q.constraints.length;
  $("nav-count-questions").textContent = q.questions.length;
  $("nav-count-conflicts").textContent = q.conflicts.length;
  $("nav-count-lowconf").textContent = q.low_confidence.length;
  $("nav-count-values").textContent = remaining;
}

function renderOutputs() {
  const section = $("sec-outputs");
  const list = $("outputs");
  if (!state.outputs.length) {
    section.hidden = true;
    list.innerHTML = "";
    return;
  }
  list.innerHTML = state.outputs
    .map((o) => `<li><a href="${escapeHtml(o.url)}" download>${escapeHtml(o.label)}</a></li>`)
    .join("");
  section.hidden = false;
}

function renderQuestions(questions, approved) {
  $("count-questions").textContent = questions.length;
  $("sec-questions").classList.toggle("blocked", questions.length > 0);
  const wrap = $("questions");
  if (!questions.length) {
    wrap.innerHTML = `<p class="section-hint">No blocked questions.</p>`;
    return;
  }
  wrap.innerHTML = questions.map((text, i) => `
    <div class="question">
      <p><span class="q-num">Q${i + 1} of ${questions.length}</span>${escapeHtml(text)}</p>
      <div class="answer-row">
        <input type="text" id="answer-${i}" placeholder="Your answer…" ${approved ? "disabled" : ""}>
        <button data-q="${i}" ${approved ? "disabled" : ""}>Resolve</button>
      </div>
    </div>`).join("");
  wrap.querySelectorAll("button[data-q]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const i = Number(btn.dataset.q);
      const input = $(`answer-${i}`);
      if (!input.value.trim()) { input.focus(); return; }
      resolveQuestion(i, input.value.trim());
    });
  });
}

function renderConflicts(conflicts, approved) {
  $("count-conflicts").textContent = conflicts.length;
  $("sec-conflicts").classList.toggle("blocked", conflicts.length > 0);
  const wrap = $("conflicts");
  if (!conflicts.length) {
    wrap.innerHTML = `<p class="section-hint">No conflicts detected.</p>`;
    return;
  }
  wrap.innerHTML = conflicts.map((c, ci) => {
    const sides = c.addresses.map((addr, ai) => {
      const dim = findValue(addr);
      const src = dim ? dim.source : c.sources[ai];
      const live = dim ? dim.value : "?";
      return `
        <div class="side">
          <div class="big">${fmt(live)}</div>
          <div class="meta">${escapeHtml(addr.part_id)} · ${escapeHtml(addr.kind)}${addr.feature_index !== null ? ` (feature ${addr.feature_index})` : ""}</div>
          <div class="src meta">reads “${escapeHtml(src.text)}” · page ${src.page} · confidence ${dim ? dim.confidence : "—"}</div>
          ${cropHtml(src)}
        </div>`;
    }).join("");
    const actions = c.addresses.map((addr, ai) => `
      <button class="small" data-conf="${ci}" data-ai="${ai}" ${approved ? "disabled" : ""}>
        Correct ${escapeHtml(addr.kind)} of ${escapeHtml(addr.part_id)}
      </button>`).join("");
    return `
      <div class="conflict">
        <p class="why">${escapeHtml(c.description)}</p>
        <div class="sides">${sides}</div>
        <div class="edit-row" style="margin-top:12px">
          <input type="number" id="conf-val-${ci}" min="0" step="0.1" placeholder="correct value" ${approved ? "disabled" : ""}>
          ${actions}
        </div>
      </div>`;
  }).join("");
  wrap.querySelectorAll("button[data-conf]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const input = $(`conf-val-${btn.dataset.conf}`);
      if (input.value === "") { input.focus(); return; }
      const conflict = conflicts[Number(btn.dataset.conf)];
      const addr = conflict.addresses[Number(btn.dataset.ai)];
      correctValue(addr, Number(input.value));
    });
  });
}

function renderLowConfidence(items, approved) {
  $("count-lowconf").textContent = items.length;
  $("sec-lowconf").classList.toggle("warn", items.length > 0);
  const wrap = $("lowconf");
  if (!items.length) {
    wrap.innerHTML = `<p class="section-hint">Nothing at low confidence.</p>`;
    return;
  }
  wrap.innerHTML = items.map((v) => valueRowHtml(v, approved, "low")).join("");
  bindValueRows(wrap, items);
}

function renderValues(approved) {
  const q = state.queue;
  const remaining = q.unreviewed.length + q.parts.length + q.constraints.length;
  $("count-values").textContent = `${q.approved_values}/${q.total_values} dimensions approved`;
  $("confirm-all-btn").disabled = approved || remaining === 0;
  $("confirm-all-btn").textContent = remaining
    ? `Confirm all remaining (${remaining})`
    : "All values confirmed";
  $("confirm-all-btn").onclick = () =>
    confirmBatch({
      addresses: q.unreviewed.map((v) => v.address),
      part_ids: q.parts.map((p) => p.part_id),
      constraint_indexes: q.constraints.map((c) => c.index),
    });

  const byPart = new Map();
  for (const v of [...q.unreviewed, ...q.low_confidence]) {
    if (!byPart.has(v.address.part_id)) byPart.set(v.address.part_id, []);
    byPart.get(v.address.part_id).push(v);
  }
  const wrap = $("values");
  const groups = [...byPart.entries()].map(([partId, values]) => {
    const part = q.parts.find((p) => p.part_id === partId);
    return `
      <div class="part-group">
        <h3>${escapeHtml(partId)}
          ${part && !approved ? `<button class="small" data-part="${escapeHtml(partId)}">Approve part</button>` : ""}
        </h3>
        <div class="stack">${values.map((v) => valueRowHtml(v, approved, "plain")).join("")}</div>
      </div>`;
  });
  if (q.constraints.length) {
    groups.push(`
      <div class="part-group">
        <h3>assembly constraints</h3>
        <div class="stack">${q.constraints.map((c) => constraintRowHtml(c, approved)).join("")}</div>
      </div>`);
  }
  wrap.innerHTML = groups.join("")
    || `<p class="section-hint">No values waiting — everything is confirmed.</p>`;

  wrap.querySelectorAll("[data-part]").forEach((btn) => {
    btn.addEventListener("click", () =>
      act(post("api/parts", { part_id: btn.dataset.part, status: "approved" })));
  });
  wrap.querySelectorAll("[data-constraint]").forEach((btn) => {
    btn.addEventListener("click", () =>
      act(post("api/constraints", { index: Number(btn.dataset.constraint), status: "approved" })));
  });
  for (const values of byPart.values()) bindValueRows(wrap, values);
}

function constraintRowHtml(c, approved) {
  return `
    <div class="value-row ${rowClassFor(c.status)}">
      <div class="big">${fmt(c.value)}</div>
      <div class="ident">
        <div class="label">assembly · ${escapeHtml(c.type)}</div>
        <div class="src">“${escapeHtml(c.source.text)}” · page ${c.source.page}</div>
      </div>
      ${cropHtml(c.source)}
      ${statusChipHtml(c.status)}
      <button class="small" data-constraint="${c.index}" ${approved ? "disabled" : ""}>Confirm</button>
    </div>`;
}

/* A row is an assumption until the reviewer rules on it: flagged rows read
   as unverified (amber accent), approved rows as verified (green). */
const rowClassFor = (status) => (status === "approved" ? "approved" : "flagged");

function statusChipHtml(status) {
  const label = status === "approved" ? "verified ✓" : "assumption — unverified";
  return `<span class="status-chip status-${escapeHtml(status)}">${label}</span>`;
}

function valueRowHtml(v, approved, variant) {
  const addr = v.address;
  return `
    <div class="value-row ${rowClassFor(v.status)}" data-key="${escapeHtml(addrKey(addr))}">
      <div class="big">${fmt(v.value)}</div>
      <div class="ident">
        <div class="label">${escapeHtml(v.label)}</div>
        <div class="src">“${escapeHtml(v.source.text)}” · page ${v.source.page}</div>
      </div>
      ${variant === "low" ? `
        <div class="confidence" title="extraction confidence ${v.confidence}">
          <span style="width:${Math.round(v.confidence * 100)}%"></span>
        </div>
        <div class="meta">${Math.round(v.confidence * 100)}%</div>` : ""}
      ${cropHtml(v.source)}
      ${statusChipHtml(v.status)}
      <span class="edit-row">
        <button class="small" data-confirm="${escapeHtml(addrKey(addr))}" ${approved ? "disabled" : ""}>Confirm</button>
        <button class="small" data-edit="${escapeHtml(addrKey(addr))}" ${approved ? "disabled" : ""}>Edit</button>
      </span>
      <span class="edit-row hidden" data-editor="${escapeHtml(addrKey(addr))}">
        <input type="number" min="0" step="0.1" placeholder="${fmt(v.value)}">
        <button class="small" data-save="${escapeHtml(addrKey(addr))}">Save</button>
      </span>
    </div>`;
}

function bindValueRows(wrap, items) {
  const byKey = new Map(items.map((v) => [addrKey(v.address), v]));
  wrap.querySelectorAll("[data-confirm]").forEach((btn) => {
    const v = byKey.get(btn.dataset.confirm);
    if (!v) return;
    btn.addEventListener("click", () => confirmValue(v.address));
  });
  wrap.querySelectorAll("[data-edit]").forEach((btn) => {
    const key = btn.dataset.edit;
    if (!byKey.get(key)) return;
    btn.addEventListener("click", () => {
      wrap.querySelector(`[data-editor="${CSS.escape(key)}"]`).classList.remove("hidden");
    });
  });
  wrap.querySelectorAll("[data-save]").forEach((btn) => {
    const key = btn.dataset.save;
    const v = byKey.get(key);
    if (!v) return;
    btn.addEventListener("click", () => {
      const input = wrap.querySelector(`[data-editor="${CSS.escape(key)}"] input`);
      if (input.value === "") { input.focus(); return; }
      correctValue(v.address, Number(input.value));
    });
  });
}

function renderLog() {
  const wrap = $("log");
  if (!state.log.length) {
    wrap.innerHTML = `<li class="empty">No reviewer actions yet.</li>`;
    return;
  }
  wrap.innerHTML = state.log.map((e) => `
    <li>
      <span class="ts">${escapeHtml(e.ts.replace("T", " ").slice(0, 19))}</span>
      <span class="action ${escapeHtml(e.action)}">${escapeHtml(e.action)}</span>
      <span>${escapeHtml(e.detail)}</span>
    </li>`).reverse().join("");
}

/* ---------- vision pass panel ---------- */

const OUTCOME_LABELS = {
  upgraded: "re-read a text line — its confidence caps that value",
  proposed: "proposed a value — queued below for your confirmation",
  question: "flagged unclear — became a blocked question",
};

async function loadVision() {
  try {
    const res = await fetch("api/vision");
    if (!res.ok) return; // no record (vision off / review-only mode): panel stays hidden
    state.vision = await res.json();
    renderVision();
  } catch (err) {
    console.warn("vision record unavailable:", err);
  }
}

function renderVision() {
  const v = state.vision;
  if (!v) return;
  $("sec-vision").hidden = false;

  const status = $("vision-status");
  if (v.error) {
    status.innerHTML = `<strong>Vision pass degraded to text-only extraction:</strong> ${escapeHtml(v.error)}`;
    status.hidden = false;
  } else if (!v.enabled) {
    status.textContent = "Vision assist was off for this job — text-only extraction ran.";
    status.hidden = false;
  } else {
    status.hidden = true;
  }

  const wrap = $("vision-observations");
  if (!v.observations.length) {
    wrap.innerHTML = v.error
      ? ""
      : `<p class="section-hint">The vision pass reported no readings.</p>`;
    return;
  }
  wrap.innerHTML = v.observations.map((o) => `
    <div class="value-row vision-row">
      ${cropHtml({ page: o.page, region: o.region, text: o.text })}
      <div class="ident">
        <div class="label">“${escapeHtml(o.text)}”</div>
        <div class="src">page ${o.page}${o.role ? ` · role ${escapeHtml(o.role)}` : " · role unclassified"}${o.part_label ? ` · attributed to ${escapeHtml(o.part_label)}` : ""}</div>
        <div class="src">${escapeHtml(OUTCOME_LABELS[o.outcome] || o.outcome)}</div>
      </div>
      <div class="confidence" title="model confidence ${o.confidence}">
        <span style="width:${Math.round(o.confidence * 100)}%"></span>
      </div>
      <div class="meta">${Math.round(o.confidence * 100)}%</div>
      <span class="status-chip outcome-${escapeHtml(o.outcome)}">${escapeHtml(o.outcome)}</span>
    </div>`).join("");
}

/* ---------- crop modal ---------- */

function openCrop(btn) {
  const d = btn.dataset;
  const body = $("crop-modal-body");
  const box = `<span class="box" style="left:${d.x0 * 100}%;top:${d.y0 * 100}%;` +
    `width:${(d.x1 - d.x0) * 100}%;height:${(d.y1 - d.y0) * 100}%"></span>`;
  const caption = `<figcaption class="crop-caption">page ${d.page} — “${escapeHtml(d.text || "")}”</figcaption>`;
  body.innerHTML = `<img src="api/page/${d.page}/image" alt="source page ${d.page}">${box}${caption}`;
  $("crop-modal").hidden = false;
}

document.addEventListener("click", (event) => {
  const crop = event.target.closest("button.source-btn");
  if (crop) openCrop(crop);
  if (event.target.matches("[data-close]")) $("crop-modal").hidden = true;
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") $("crop-modal").hidden = true;
});

$("approve-btn").addEventListener("click", () => {
  approve().then(() => {
    if (state.specStatus === "approved") {
      showBanner("success", [
        "Spec approved — it can now be consumed by the generator. " +
        "Corrections are locked; the exported JSON is the single source of truth.",
      ]);
    }
  });
});

refresh().then(render);
loadVision();
