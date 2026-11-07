/* Review gate UI logic.
   Renders the review queue in priority order and applies reviewer actions
   through the API; the server (and the schema behind it) enforce the gate. */

"use strict";

const state = { spec: null, queue: null, gateErrors: [], specStatus: "", log: [] };

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

function cropHtml(source) {
  const [x0, y0, x1, y1] = source.region;
  const box = `<span class="box" style="left:${x0 * 100}%;top:${y0 * 100}%;` +
    `width:${(x1 - x0) * 100}%;height:${(y1 - y0) * 100}%"></span>`;
  return `<button class="crop" title="Page ${source.page}: “${escapeHtml(source.text)}” — click to enlarge"` +
    ` data-page="${source.page}" data-x0="${x0}" data-y0="${y0}" data-x1="${x1}" data-y1="${y1}">` +
    `<img src="/api/page/${source.page}/image" alt="source page ${source.page}">${box}</button>`;
}

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
  const res = await fetch("/api/review");
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
  act(post("/api/values", { address, status: "approved" }));

const correctValue = (address, value) =>
  act(post("/api/values", { address, value, status: "approved" }));

const resolveQuestion = (index, answer) =>
  act(post("/api/questions/resolve", { index, answer }));

const confirmBatch = (addresses) =>
  act(post("/api/values/confirm-batch", { addresses }));

const approve = () => act(post("/api/approve", {}));

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
  renderLog();
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
      <p>${escapeHtml(text)}</p>
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
      act(post("/api/parts", { part_id: btn.dataset.part, status: "approved" })));
  });
  wrap.querySelectorAll("[data-constraint]").forEach((btn) => {
    btn.addEventListener("click", () =>
      act(post("/api/constraints", { index: Number(btn.dataset.constraint), status: "approved" })));
  });
  for (const values of byPart.values()) bindValueRows(wrap, values);
}

function constraintRowHtml(c, approved) {
  return `
    <div class="value-row">
      <div class="big">${fmt(c.value)}</div>
      <div class="ident">
        <div class="label">assembly · ${escapeHtml(c.type)}</div>
        <div class="src">“${escapeHtml(c.source.text)}” · page ${c.source.page}</div>
      </div>
      ${cropHtml(c.source)}
      <span class="status-chip">${c.status.replace("_", " ")}</span>
      <button class="small" data-constraint="${c.index}" ${approved ? "disabled" : ""}>Confirm</button>
    </div>`;
}

function valueRowHtml(v, approved, variant) {
  const addr = v.address;
  return `
    <div class="value-row" data-key="${escapeHtml(addrKey(addr))}">
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
      <span class="status-chip">${v.status.replace("_", " ")}</span>
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

/* ---------- crop modal ---------- */

function openCrop(btn) {
  const d = btn.dataset;
  const body = $("crop-modal-body");
  const box = `<span class="box" style="left:${d.x0 * 100}%;top:${d.y0 * 100}%;` +
    `width:${(d.x1 - d.x0) * 100}%;height:${(d.y1 - d.y0) * 100}%"></span>`;
  body.innerHTML = `<img src="/api/page/${d.page}/image" alt="source page ${d.page}">${box}`;
  $("crop-modal").hidden = false;
}

document.addEventListener("click", (event) => {
  const crop = event.target.closest("button.crop");
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
