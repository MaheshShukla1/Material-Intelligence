const $ = (id) => document.getElementById(id);
let RUN = null, META = null, SVC = "", TYPE = "", LEAD = 7, SUBCATS = null;
// Facet filters for the inventory (Safety/Tools) and PPE tabs, plus caches so a
// facet change re-filters the already-loaded rows without a re-fetch.
let SIZE = "", CONTRACTOR = "", INV_ROWS = [], PPE_ROWS = [], LAST_SERVICES = [];

const num = (v, d = 0) =>
  v === null || v === undefined ? "—"
    : Number(v).toLocaleString("en-IN", { maximumFractionDigits: d });

const MON = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
const short = (iso) => {
  if (!iso) return "";
  const [, m, d] = iso.split("-").map(Number);
  return `${d} ${MON[m - 1]}`;
};
const todayISO = () => new Date().toISOString().slice(0, 10);
const daysTo = (iso) =>
  Math.round((new Date(iso + "T00:00:00") - new Date(todayISO() + "T00:00:00")) / 864e5);
const esc = (s) => String(s).replace(/[&<>"]/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

/* One plain instruction per row. The reader should never have to do arithmetic
   across five number columns to work out what to do. */
function action(r) {
  const s = r.status;
  if (s === "STOCKED_OUT") return ["out", "Already out", "site is running dry"];
  if (s === "INSUFFICIENT_DATA") return ["none", "Not enough data", "log daily issues first"];
  if (s === "DEAD_STOCK") return ["none", "Never issued", "received but unused"];
  if (s === "NO_RECENT_USE") return ["none", "No recent use", ""];
  if (s === "OVERSTOCK") {
    const y = r.days_left ? (r.days_left / 365).toFixed(1) : null;
    return ["stop", "Stop ordering", y ? `${y} years of cover` : "very long cover"];
  }
  if (!r.order_by) return ["ok", "No action", ""];
  const d = daysTo(r.order_by);
  // RED = order now. AMBER = order this week. Keep the label aligned to status
  // so an item never shows "Order now" while sitting under "Order this week".
  if (s === "RED") {
    if (d < 0) return ["late", "Order now", `${-d} day${d === -1 ? "" : "s"} late`];
    if (d === 0) return ["late", "Order today", "last day to order"];
    return ["late", "Order now", d === 1 ? "tomorrow" : `in ${d} days`];
  }
  // AMBER (order this week)
  if (d <= 0) {
    // Raw numbers say the order date has passed, but status only reached
    // AMBER - that gap means engine.py's confidence cushion held it back
    // from RED (thin consumption history, or a decelerating trend). Saying
    // just "order soon" hid that reasoning; naming it stops it looking like
    // a contradiction between "runs out soon" and "only this week".
    const thin = r.confidence === "LOW" || r.confidence === "MEDIUM";
    return ["soon", "Order this week",
      thin ? "borderline — thin data, worth a look" : "borderline, worth a look"];
  }
  if (d === 1) return ["soon", `Order by ${short(r.order_by)}`, "tomorrow"];
  return ["soon", `Order by ${short(r.order_by)}`, `in ${d} days`];
}

function runsOut(r) {
  if (r.days_left === null || r.days_left === undefined) return ["—", ""];
  const d = Math.round(r.days_left);
  const main = d <= 0 ? "now" : d === 1 ? "in 1 day" : `in ${d} days`;
  let sub = "";
  if (r.exhaust_earliest && r.exhaust_latest)
    sub = r.exhaust_earliest === r.exhaust_latest
      ? short(r.exhaust_earliest)
      : `${short(r.exhaust_earliest)} – ${short(r.exhaust_latest)}`;
  return [main, sub];
}

const DOTS = { HIGH: "●●●", MEDIUM: "●●○", LOW: "●○○", NONE: "○○○" };

/* Real per-material lead time (from actual PO->GRN history) shows as a small
   tag under "What to do" - ONLY when a real number was used. A material still
   on the default global lead time gets no tag at all, same restraint as the
   rest of this app: nothing is shown unless it is real. This is also exactly
   what changed the RED/AMBER/GREEN bucket itself (not a cosmetic add-on) -
   the tag just makes that visible instead of silent. */
function leadTag(r) {
  const basis = r.lead_time_basis;
  if (basis !== "material" && basis !== "supplier" && basis !== "subcategory") return "";
  const src = basis === "material" ? "this item"
            : basis === "supplier" ? "its supplier"
            : "similar items in its category";
  const n = r.lead_time_n ? `${r.lead_time_n} orders` : "";
  const thin = !r.lead_time_confident;
  const cls = thin ? "leadtag thin" : "leadtag";
  const note = thin ? " — thin data, treat as a hint" : "";
  return `<div class="${cls}" title="Real lead time from ${esc(src)}'s PO→GRN history (${esc(n)})${esc(note)}">` +
    `${thin ? "~" : ""}Real lead ${num(r.lead_time_days, 0)}d${n ? ` · ${esc(n)}` : ""}</div>`;
}

/* ------------------------------------------------------------------ upload */
$("pick").onclick = () => $("file").click();
$("pickHome").onclick = () => $("file").click();
$("file").onchange = (e) => {
  if (e.target.files[0]) send(e.target.files[0]);
  e.target.value = "";
};

/* Home screen's Upload file / Connect Google Sheet segmented toggle. Purely
   which pane is visible -- both panes' inputs/handlers are untouched, so
   switching back and forth never loses anything typed. */
function setHomePane(which) {
  $("paneUpload").hidden = which !== "upload";
  $("paneSheet").hidden = which !== "sheet";
  $("segUpload").classList.toggle("on", which === "upload");
  $("segSheet").classList.toggle("on", which === "sheet");
  $("segUpload").setAttribute("aria-selected", which === "upload");
  $("segSheet").setAttribute("aria-selected", which === "sheet");
}
$("segUpload").onclick = () => setHomePane("upload");
$("segSheet").onclick = () => setHomePane("sheet");

const drop = $("drop");
["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (e) => {
  e.preventDefault(); drop.classList.add("over");
}));
["dragleave", "drop"].forEach((t) => drop.addEventListener(t, (e) => {
  e.preventDefault(); drop.classList.remove("over");
}));
drop.addEventListener("drop", (e) => {
  if (e.dataTransfer.files[0]) send(e.dataTransfer.files[0]);
});

/* Staged loading screen -- a progress ring + step checklist, shown while an
   upload or sync is genuinely reading sheets, detecting columns, and
   computing the forecast. Upload/sync is a single request-response (no
   real per-stage signal streams back from the backend), so the ring's
   motion between stages is an ESTIMATE -- sized from the actual file being
   processed (its byte size for an upload; no size is knowable upfront for
   a sync, so that case uses a flat typical duration) and spread across the
   real phases in the order they actually run. It never claims completion
   on its own terms: the animated fill caps at 92% and holds there if the
   estimate runs out before the real response lands. finish()/fail() -- the
   only things allowed to move it past that cap -- are driven by the actual
   fetch resolving, never by the clock. That keeps the number honest: a
   smooth, predictable feel while genuinely waiting, never a completion
   claim ahead of the real result. */
const BUSY_STAGES = [
  { label: "Fetching your Google Sheet", detail: "Downloading the latest version", to: 12 },
  { label: "Reading sheets", detail: "Every service tab, one at a time", to: 45 },
  { label: "Detecting columns", detail: "Matching headers, repairing dates", to: 70 },
  { label: "Computing forecast", detail: "Consumption rates, reorder points", to: 92 },
  { label: "Done", detail: "Opening your dashboard", to: 100 },
];
function busyEstimateMs(fileSizeBytes) {
  if (!fileSizeBytes) return 9000;   // sync: no size known upfront -- a typical mid-size run
  const mb = fileSizeBytes / (1024 * 1024);
  return Math.round(Math.max(4000, Math.min(25000, mb * 2200 + 3000)));
}
function startBusyProgress(uploadLabel, fileSizeBytes) {
  const ring = $("busy-ring");
  ring.classList.remove("indeterminate");
  const stages = BUSY_STAGES.map((s, i) => (i === 0 && uploadLabel) ? { ...s, label: uploadLabel } : s);
  $("busy-steps").innerHTML = stages.map((s, i) => `
    <div class="busy-step"><span class="busy-stepicon" data-i="${i}">${i + 1}</span>
      <div><div class="busy-steplabel" data-i="${i}">${esc(s.label)}</div>
      <div class="busy-stepdetail" data-i="${i}">${esc(s.detail)}</div></div>
    </div>`).join("");
  const total = busyEstimateMs(fileSizeBytes);
  const CIRC = 364.4;
  const t0 = Date.now();
  let done = false;
  function paint(pct, stageIdx) {
    const allDone = pct >= 100;
    $("busy-pct").textContent = Math.round(pct) + "%";
    $("busy-arc").style.strokeDashoffset = CIRC - (CIRC * pct / 100);
    $("busytxt").textContent = stages[stageIdx].label;
    stages.forEach((s, i) => {
      const icon = document.querySelector(`.busy-stepicon[data-i="${i}"]`);
      const label = document.querySelector(`.busy-steplabel[data-i="${i}"]`);
      const detail = document.querySelector(`.busy-stepdetail[data-i="${i}"]`);
      // once truly finished (100%, driven by the real fetch resolving, never
      // the clock) every step -- including the last one -- reads as done,
      // not just "current": there is nothing left in progress at that point.
      const isDone = i < stageIdx || (allDone && i === stageIdx);
      const isCurrent = i === stageIdx && !allDone;
      icon.className = "busy-stepicon" + (isDone ? " done" : isCurrent ? " current" : "");
      icon.textContent = isDone ? "✓" : String(i + 1);
      label.className = "busy-steplabel" + (isDone ? " done" : isCurrent ? " current" : "");
      detail.classList.toggle("show", isCurrent);
    });
  }
  paint(0, 0);
  const tick = setInterval(() => {
    if (done) return;
    const elapsed = Date.now() - t0;
    const pct = Math.min(92, 100 * elapsed / total);
    let stageIdx = stages.findIndex((s) => pct <= s.to);
    if (stageIdx === -1 || stageIdx === stages.length - 1) stageIdx = stages.length - 2;
    const remainMs = Math.max(total - elapsed, 0);
    const remainS = Math.ceil(remainMs / 1000);
    $("busy-eta").textContent = remainMs > 500 ? `About ${remainS} second${remainS === 1 ? "" : "s"} left` : "Almost there";
    paint(pct, stageIdx);
  }, 150);
  return {
    finish() { done = true; clearInterval(tick); $("busy-eta").textContent = ""; paint(100, stages.length - 1); },
    fail() { done = true; clearInterval(tick); },
  };
}
/* No real stages to narrate here -- switching to an already-loaded project
   just re-fetches a stored run, nothing is being re-parsed or recomputed. */
function startBusySimple(label) {
  $("busy-ring").classList.add("indeterminate");
  $("busy-steps").innerHTML = "";
  $("busy-eta").textContent = "";
  $("busy-pct").textContent = "";
  $("busytxt").textContent = label;
}

async function send(file) {
  LEAD = Number($("lead").value) || 7;
  drop.hidden = true; $("report").hidden = true; $("busy").hidden = false;
  const progress = startBusyProgress("Uploading your file", file.size);
  const fd = new FormData();
  fd.append("file", file);
  fd.append("lead_time", LEAD);
  fd.append("project", $("pname").value.trim());
  try {
    const r = await fetch("/api/upload", { method: "POST", body: fd });
    const j = await r.json();
    if (!r.ok) throw new Error(j.detail || "upload failed");
    progress.finish();
    await show(j.run_id, j.meta, j.summary);
    await loadProjects();
  } catch (err) {
    progress.fail();
    $("busy").hidden = true; drop.hidden = false;
    alert(err.message);
  }
}

/* Pull the latest data straight from a published Google Sheet. Same backend
   processing as an upload - only the source differs. The link is remembered so
   the top-bar "Sync sheet" button can re-pull with one click. */
async function syncFromSheet(link, project) {
  link = (link || "").trim();
  if (!link) { alert("Paste the published Google Sheet link first."); return; }
  LEAD = Number($("lead").value) || 7;
  drop.hidden = true; $("report").hidden = true; $("busy").hidden = false;
  const progress = startBusyProgress(null, null);
  const fd = new FormData();
  fd.append("link", link);
  fd.append("lead_time", LEAD);
  fd.append("project", (project || "").trim());
  try {
    const r = await fetch("/api/sync-sheet", { method: "POST", body: fd });
    const j = await r.json();
    if (!r.ok) throw new Error(j.detail || "sync failed");
    try { localStorage.setItem("sheetLink", link); } catch (e) {}
    progress.finish();
    await show(j.run_id, j.meta, j.summary);
    await loadProjects();
  } catch (err) {
    progress.fail();
    $("busy").hidden = true; drop.hidden = false;
    alert(err.message);
  }
}

$("syncgo").onclick = () =>
  syncFromSheet($("sheetlink").value, $("sheetname").value);

/* -------------------------------------------------------------- auto-refresh
   When the currently loaded run came from a Google Sheet, quietly re-pull the
   latest every few minutes so the dashboard stays fresh without anyone having
   to press a button. Silent: no busy screen, no scroll jump - it just updates
   the numbers and the "synced X ago" line. Only runs while the tab is open. */
const AUTO_MINUTES = 10;
let autoTimer = null;

function startAuto() {
  stopAuto();
  autoTimer = setInterval(autoSync, AUTO_MINUTES * 60 * 1000);
}
function stopAuto() {
  if (autoTimer) { clearInterval(autoTimer); autoTimer = null; }
}

async function autoSync() {
  // only if the open run is sheet-backed and the report is visible
  if (!META || !META.source_link || $("report").hidden) return;
  if (document.hidden) return;                 // tab in background - skip
  try {
    const fd = new FormData();
    fd.append("link", META.source_link);
    fd.append("lead_time", LEAD);
    fd.append("project", META.project || "");
    const r = await fetch("/api/sync-sheet", { method: "POST", body: fd });
    if (!r.ok) return;                          // stay on current data silently
    const j = await r.json();
    RUN = j.run_id; META = j.meta;
    try { localStorage.setItem("currentRun", j.run_id); } catch (e) {}
    paintHeader(j.meta);
    paintKpis(j.summary);
    await load();                              // refresh the visible table
    await loadProjects();
  } catch (e) { /* offline or transient - keep showing what we have */ }
}

/* Top-bar button re-syncs from whichever sheet was last connected. */
$("sync").onclick = () => {
  let saved = "";
  try { saved = localStorage.getItem("sheetLink") || ""; } catch (e) {}
  const link = (META && META.source_link) || saved;
  if (!link) {
    alert("No Google Sheet connected yet. Paste a link in the sync box below first.");
    drop.hidden = false; $("report").hidden = true;
    return;
  }
  syncFromSheet(link, META ? META.project : "");
};

async function show(runId, meta, summary) {
  RUN = runId; META = meta; TYPE = "";
  try { localStorage.setItem("currentRun", runId); } catch (e) {}
  LEAD = meta.lead_time || LEAD;
  $("lead").value = LEAD;
  paintHeader(meta);
  paintHealth(meta.issues);
  paintMapping(meta.mapping, meta.source);
  paintKpis(summary);
  SUBCATS = await (await fetch(`/api/subcategories/${runId}`)).json();
  LAST_SERVICES = summary.services || [];
  paintTabs(summary.services);
  paintTypes();
  {
    const lt = meta.leadtime;
    $("rule").textContent = (lt && lt.materials_with_real_lead_time)
      ? `Real PO→GRN lead time used for ${lt.materials_with_real_lead_time} of ` +
        `${lt.materials_in_run} materials; everything else uses the default ` +
        `${LEAD} days plus a 2 day buffer.`
      : `Lead time ${LEAD} days plus a 2 day buffer, so an order must be raised ` +
        `${LEAD + 2} days before stock hits zero.`;
  }
  $("dl").hidden = false; $("dl").href = `/api/export/${RUN}`;
  $("del").hidden = false;
  $("home").hidden = false;
  $("pick").hidden = false; $("sync").hidden = false; $("leadwrap").hidden = false;
  await load();
  $("busy").hidden = true; $("drop").hidden = true; $("report").hidden = false;
  syncHeaderOffset();
  // Keep sheet-backed runs fresh automatically; uploads have no live source.
  if (meta.source_link) startAuto(); else stopAuto();
}

/* Home: return to the upload / sync screen without losing the loaded data.
   The current run stays remembered, so reloading still restores it. */
function goHome() {
  $("report").hidden = true;
  $("busy").hidden = true;
  $("drop").hidden = false;
  $("home").hidden = true;
  $("pick").hidden = true; $("sync").hidden = true; $("leadwrap").hidden = true;
  stopAuto();
  // Reset the switcher to a neutral prompt so that picking ANY project next -
  // including the one that was open - registers as a change and loads it.
  loadProjects(true);
}

/* Sticky column headers must park under the app bar whatever its height is
   on this screen, so measure rather than guess. */
function syncHeaderOffset() {
  document.documentElement.style.setProperty(
    "--hdr", Math.round($("top").getBoundingClientRect().height) + "px");
}
addEventListener("resize", syncHeaderOffset);

/* ---------------------------------------------------------------- projects */
async function loadProjects(neutral) {
  const ps = await (await fetch("/api/projects")).json();
  const sel = $("proj");
  sel.hidden = ps.length === 0;
  $("del").hidden = ps.length === 0;   // manage-uploads available whenever data exists
  // `neutral` adds a placeholder first option so that picking any real project
  // afterwards counts as a change and loads it (used when returning to Home).
  const head = neutral
    ? `<option value="" selected disabled>Open a project…</option>` : "";
  sel.innerHTML = head + ps.map((p) =>
    `<option value="${esc(p.latest_run)}" data-slug="${esc(p.slug)}"
      ${!neutral && p.latest_run === RUN ? "selected" : ""}>${esc(p.project)} · ${p.runs} run${
      p.runs === 1 ? "" : "s"}</option>`).join("");
  await renderRecentProjects(ps);
}

/* Home screen's "Recent projects" list -- one click opens that project's
   latest run via the same switchProject() the header dropdown uses. Reuses
   /api/projects (already fetched by the caller) + /api/runs for the per-run
   material count and date, same two endpoints the Manage-uploads modal
   already relies on -- no new backend route needed. */
async function renderRecentProjects(ps) {
  const wrap = $("recent");
  if (!wrap) return;
  if (!ps || !ps.length) { wrap.hidden = true; return; }
  let runs = [];
  try { runs = await (await fetch("/api/runs")).json(); } catch (e) { runs = []; }
  const runById = new Map(runs.map((r) => [r.run_id, r]));
  $("recentlist").innerHTML = ps.map((p) => {
    const r = runById.get(p.latest_run);
    const mats = r && r.stats && r.stats.materials ? `${num(r.stats.materials)} materials` : "";
    const when = r && r.created ? relTime(r.created) : "";
    const bits = [`${p.runs} run${p.runs === 1 ? "" : "s"}`, mats,
      when ? `updated ${when}` : ""].filter(Boolean).join(" · ");
    return `<div class="rp" data-run="${esc(p.latest_run)}" role="button" tabindex="0">
      <div><div class="rp-name">${esc(p.project)}</div>
      <div class="rp-meta">${bits}</div></div>
      <span class="rp-open">Open →</span>
    </div>`;
  }).join("");
  $("recentlist").querySelectorAll(".rp").forEach((el) => {
    el.onclick = () => switchProject(el.dataset.run);
    el.onkeydown = (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); switchProject(el.dataset.run); }
    };
  });
  wrap.hidden = false;
}
$("proj").onchange = (e) => switchProject(e.target.value);
/* If the user reopens the switcher and picks the project that is already
   selected, onchange will not fire. Loading on focus-then-select is unreliable
   across browsers, so instead we reload the report view when Home is left, and
   keep the switcher purely for changing to a different project. */

/* Load a project's latest run explicitly. Used by the switcher and by any code
   path (e.g. returning from Home) where the dropdown value may not have changed
   and so would not fire onchange on its own. */
async function switchProject(id) {
  if (!id) return;
  $("report").hidden = true; $("drop").hidden = true; $("busy").hidden = false;
  startBusySimple("Loading project…");
  try {
    const j = await (await fetch(`/api/run/${id}`)).json();
    await show(id, j.meta, j.summary);
  } catch (e) {
    $("busy").hidden = true; $("drop").hidden = false;
    alert("Could not load that project.");
  }
}

/* ------------------------------------------------------------------ delete */
/* The delete button opens a manager listing every project and every upload
   under it. Each row has its own delete control, so removing one upload or a
   whole project is a click - no typing a name to confirm. A small confirm
   dialog still guards the actual deletion, since it cannot be undone. */
$("del").onclick = () => openManager();

/* One upload row: a file icon, filename, real timestamp + material count,
   and a compact icon-only delete button. `isCurrent` adds the green "Current"
   badge -- the run this project's header/switcher would actually open. */
function mrunRowHTML(r, isCurrent) {
  const when = (r.created || "").replace("T", " ").slice(0, 16);
  const mats = r.stats && r.stats.materials ? `${r.stats.materials} materials` : "";
  return `<div class="mrun">
    <div class="mrun-ic"><svg class="mrun-svg" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6Z" stroke="currentColor" stroke-width="1.5"/>
      <path d="M14 2v6h6" stroke="currentColor" stroke-width="1.5"/>
    </svg></div>
    <div class="mrun-info">
      <div class="mrun-toprow">
        <span class="mrun-file">${esc(r.filename || r.run_id)}</span>
        ${isCurrent ? '<span class="mrun-badge">Current</span>' : ""}
      </div>
      <span class="mrun-meta">${esc(when)}${mats ? " · " + mats : ""}</span>
    </div>
    <button class="mrun-del" data-run="${esc(r.run_id)}" aria-label="Delete this upload" title="Delete this upload">
      <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <path d="M4 7h16M9 7V4h6v3M6 7l1 13a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-13"
          stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
      </svg>
    </button>
  </div>`;
}

async function openManager() {
  $("merr").hidden = true;
  $("modal").hidden = false;
  $("mlist").innerHTML = "<p class='mempty'>Loading…</p>";
  const [projects, runs] = await Promise.all([
    (await fetch("/api/projects")).json(),
    (await fetch("/api/runs")).json(),
  ]);
  if (!projects.length) {
    $("mlist").innerHTML = "<p class='mempty'>Nothing uploaded yet.</p>";
    return;
  }
  const runsByProject = {};
  runs.forEach((r) => {
    (runsByProject[r.project_slug] = runsByProject[r.project_slug] || []).push(r);
  });

  const groups = projects.map((p) => {
    const rs = (runsByProject[p.slug] || []).slice()
      .sort((a, b) => (b.created || "").localeCompare(a.created || ""));
    const current = rs.find((r) => r.run_id === p.latest_run) || rs[0];
    const older = rs.filter((r) => r !== current);
    return `<div class="mproj" data-slug="${esc(p.slug)}">
      <div class="mproj-hd"><span class="mproj-name">${esc(p.project)}</span></div>
      ${current ? mrunRowHTML(current, true) : ""}
      ${older.length ? `
        <button type="button" class="mshowmore">Show ${older.length} older upload${older.length === 1 ? "" : "s"}</button>
        <div class="molder" hidden>${older.map((r) => mrunRowHTML(r, false)).join("")}</div>` : ""}
    </div>`;
  }).join("");

  const danger = `<div class="mdanger">
    <div class="mdanger-title">Danger zone</div>
    <div class="mdanger-list">${projects.map((p) =>
      `<button class="btn danger sm" data-project="${esc(p.slug)}" data-name="${esc(p.project)}">Delete ${esc(p.project)}</button>`
    ).join("")}</div>
  </div>`;

  $("mlist").innerHTML = groups + danger;

  $("mlist").querySelectorAll(".mshowmore").forEach((btn) => {
    btn.onclick = () => {
      const box = btn.nextElementSibling;
      const wasOpen = !box.hidden;
      box.hidden = wasOpen;
      const n = box.querySelectorAll(".mrun").length;
      btn.textContent = wasOpen
        ? `Show ${n} older upload${n === 1 ? "" : "s"}` : "Hide older uploads";
    };
  });
  $("mlist").querySelectorAll("[data-run]").forEach((b) => {
    b.onclick = () => askConfirm("run", b.dataset.run,
      "Delete this upload?",
      "This removes one uploaded file and its forecast. It cannot be undone.");
  });
  $("mlist").querySelectorAll("[data-project]").forEach((b) => {
    b.onclick = () => askConfirm("project", b.dataset.project,
      `Delete "${b.dataset.name}"?`,
      "This removes the whole project and every upload under it. It cannot be undone.");
  });
}

function closeModal() { $("modal").hidden = true; }
$("modal").onclick = (e) => { if (e.target.id === "modal") closeModal(); };

let PENDING = null;
function askConfirm(kind, id, title, text) {
  PENDING = { kind, id };
  $("ctitle").textContent = title;
  $("ctext").textContent = text;
  $("confirm").hidden = false;
}
function closeConfirm() { $("confirm").hidden = true; PENDING = null; }
$("confirm").onclick = (e) => { if (e.target.id === "confirm") closeConfirm(); };

$("cgo").onclick = async () => {
  if (!PENDING) return;
  const url = PENDING.kind === "run"
    ? `/api/run/${PENDING.id}`
    : `/api/project/${PENDING.id}?confirm=__ui__`;
  const r = await fetch(url, { method: "DELETE" });
  if (!r.ok) {
    const j = await r.json().catch(() => ({}));
    $("merr").textContent = j.detail || "delete failed";
    $("merr").hidden = false;
    closeConfirm();
    return;
  }
  const deletedCurrent = (PENDING.kind === "run" && PENDING.id === RUN) ||
    (PENDING.kind === "project" && META && PENDING.id === META.project_slug);
  // if the deleted run was the one we remembered, forget it
  try {
    if (PENDING.kind === "run" && localStorage.getItem("currentRun") === PENDING.id)
      localStorage.removeItem("currentRun");
  } catch (e) {}
  closeConfirm();

  const projects = await (await fetch("/api/projects")).json();

  if (deletedCurrent) {
    RUN = null; META = null;
    if (projects.length) {
      // Auto-load a remaining project instead of leaving a blank screen.
      // (The switcher can't be relied on to fire onchange when its value is
      // already the target, so we load explicitly.)
      const next = projects[0];
      $("modal").hidden = true;
      const j = await (await fetch(`/api/run/${next.latest_run}`)).json();
      await show(next.latest_run, j.meta, j.summary);
      await loadProjects();
      return;
    }
    // nothing left — go to the home screen cleanly
    try { localStorage.removeItem("currentRun"); } catch (e) {}
    $("report").hidden = true; $("del").hidden = true; $("dl").hidden = true;
    $("home").hidden = true;
    $("ctx").textContent = "No file loaded";
    $("drop").hidden = false;
    await openManager();
    await loadProjects();
    return;
  }

  // deleted something else — just refresh the list and switcher
  await openManager();
  await loadProjects();
};

/* ------------------------------------------------------------------ render */
/* Parse a stored timestamp safely. Runs written before the UTC fix have no
   timezone marker but were in the server's UTC clock, so append Z when missing.
   Newer runs carry +00:00 and parse as-is. */
function parseTS(iso) {
  if (!iso) return new Date(NaN);
  const hasTZ = /[zZ]|[+-]\d\d:?\d\d$/.test(iso);
  return new Date(hasTZ ? iso : iso + "Z");
}

function relTime(iso) {
  if (!iso) return "";
  const then = parseTS(iso);
  const now = new Date();
  const mins = Math.round((now - then) / 60000);
  if (isNaN(mins)) return "";
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs} hour${hrs === 1 ? "" : "s"} ago`;
  const days = Math.round(hrs / 24);
  return `${days} day${days === 1 ? "" : "s"} ago`;
}

function paintHeader(m) {
  const s = m.stats;
  const src = m.source === "projectbase" ? "ProjectBase export" : "site register";
  const synced = m.source_link ? "Synced" : "Loaded";
  const rel = relTime(m.created);
  $("ctx").textContent =
    `${m.project} · ${m.filename} · ${src} · as of ${s.asof} · ${s.materials} materials`;
  // Freshness line: how long ago this data was pulled, with a warning if it's
  // old enough that a decision might be made on stale numbers. No button here -
  // the top-bar "Sync sheet" is the single place to re-sync, to avoid two
  // controls doing the same thing.
  const bar = $("fresh");
  if (bar) {
    const ageMin = (new Date() - parseTS(m.created)) / 60000;
    const stale = !isNaN(ageMin) && ageMin > 24 * 60;   // older than a day
    bar.innerHTML = rel
      ? `${synced} ${rel}`
        + (stale ? ' · <span class="staleflag">data may be out of date</span>' : "")
      : "";
    bar.hidden = !rel;
  }
}

function paintHealth(issues) {
  if (!issues || !issues.length) { $("health").innerHTML = ""; return; }
  const block = issues.filter((i) => i.level === "block");
  const warn = issues.filter((i) => i.level !== "block");
  let h = "";
  if (block.length)
    h += `<div class="note block"><b>Forecast dates are hidden</b>${
      block.map((i) => esc(i.text)).join(" ")}</div>`;
  if (warn.length)
    h += `<div class="note warn"><b>Data health</b><ul>${
      warn.map((i) => `<li>${esc(i.text)}</li>`).join("")}</ul></div>`;
  $("health").innerHTML = h;
}

/* Show exactly which column was read as what. If detection went wrong, this is
   where it becomes visible - before anyone acts on a number. */
function paintMapping(map, source) {
  if (!map) { $("mapbox").hidden = true; return; }
  $("mapbox").hidden = false;
  const sheets = map.sheets || [], skipped = map.skipped || [];
  if (source === "projectbase") {
    $("mapsum").textContent = `${map.rows || 0} transaction rows`;
    $("mapbody").innerHTML =
      "<div class='maprow'>Read as a ProjectBase transaction export. " +
      "Negative quantities are treated as site issues.</div>";
    return;
  }
  $("mapsum").textContent =
    `${sheets.length} sheet${sheets.length === 1 ? "" : "s"} read` +
    (skipped.length ? `, ${skipped.length} skipped` : "") +
    (map.date_swaps ? `, ${map.date_swaps} dates repaired` : "");
  const chip = (label, v) =>
    v ? `<span class="chip">${esc(label)} → ${esc(v)}</span>` : "";
  $("mapbody").innerHTML = sheets.map((s) => `
    <div class="maprow">
      <b>${esc(s.sheet)}</b> — ${s.materials} materials, ${s.date_columns} dates
      (${s.date_from} → ${s.date_to}), header on row ${s.header_row}
      <div class="mapcols">
        ${chip("material", s.columns.material)}${chip("unit", s.columns.unit)}
        ${chip("opening", s.columns.opening)}${chip("in", s.columns.qty_in)}
        ${chip("out", s.columns.qty_out)}${chip("balance", s.columns.balance)}
        ${chip("group", s.columns.group)}
      </div>
    </div>`).join("") +
    (skipped.length
      ? `<p class="mapskip">Skipped: ${skipped.map(
          (s) => `${esc(s.sheet)} (${esc(s.why)})`).join(" · ")}</p>`
      : "");
}

/* Each KPI card maps to ONE filter that returns exactly the card's number, so
   clicking a card shows precisely those items. Note the two that are not plain
   single-status filters:
   - "Act today" = STOCKED_OUT + RED (a two-status set), and
   - "Order date passed" = order-by date in the past (the "__overdue__" token,
     handled in load()); it is NOT a status set - an overdue item may be RED,
     STOCKED_OUT or even AMBER, which is exactly the mismatch this fixes. */
const KPI_CARDS = [
  ["Act today", "out of stock or inside lead time", "STOCKED_OUT,RED"],
  ["Already out", "zero on hand, still consuming", "STOCKED_OUT"],
  ["Order date passed", "should already have been raised", "__overdue__"],
  ["Order this week", "inside twice the lead time", "AMBER"],
  ["Stop ordering", "overstocked, unused, or paused",
   "OVERSTOCK,DEAD_STOCK,NO_RECENT_USE"],
  ["No action", "healthy cover", "GREEN"],
];

function paintKpis(s) {
  const c = s.counts, g = (k) => c[k] || 0;
  const val = {
    "STOCKED_OUT,RED": g("STOCKED_OUT") + g("RED"),
    "STOCKED_OUT": g("STOCKED_OUT"),
    "__overdue__": s.overdue_orders,
    "AMBER": g("AMBER"),
    "OVERSTOCK,DEAD_STOCK,NO_RECENT_USE": s.idle_lines,
    "GREEN": g("GREEN"),
  };
  const hot = { "STOCKED_OUT,RED": true, "STOCKED_OUT": g("STOCKED_OUT") > 0,
                "__overdue__": s.overdue_orders > 0 };
  $("kpis").innerHTML = KPI_CARDS.map(([l, h, tok]) =>
    `<div class="kpi${hot[tok] ? " hot" : ""}" role="button" tabindex="0"
       data-tok="${esc(tok)}" title="Click to filter the table to these ${
       num(val[tok])} items">
       <p class="l">${l}</p><p class="v">${num(val[tok])}</p>
       <p class="h">${h}</p></div>`).join("");
  $("kpis").querySelectorAll(".kpi").forEach((el) => {
    el.onclick = () => clickKpi(el.dataset.tok);
    el.onkeydown = (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); clickKpi(el.dataset.tok); }
    };
  });
  syncKpiHighlight();
}

/* Clicking a card drops any trade/type filter (the numbers are whole-project),
   sets the matching status filter, and loads the table so the engineer sees
   exactly where that card's items are. */
function clickKpi(tok) {
  SVC = ""; TYPE = ""; SIZE = ""; CONTRACTOR = "";
  $("status").value = tok;
  paintTabs(LAST_SERVICES || []);
  paintTypes();
  load();
}

/* Show which card (if any) the current view corresponds to. A card is "active"
   only in the whole-project forecast view (no trade/type filter) whose status
   matches the card's token. */
function syncKpiHighlight() {
  const active = (SVC === "" && TYPE === "") ? $("status").value : null;
  $("kpis").querySelectorAll(".kpi").forEach((el) => {
    el.classList.toggle("sel", active !== null && el.dataset.tok === active);
  });
}

/* Which view a tab drives:
   - "Safety"/"Tools" -> inventory-only (count, no forecast)
   - "PPE"            -> per-person issue log
   - everything else  -> the normal MEP forecast table                     */
const INVENTORY_SVCS = ["Safety", "Tools"];
function modeFor(svc) {
  if (svc === "PPE") return "ppe";
  if (INVENTORY_SVCS.includes(svc)) return "inventory";
  return "forecast";
}

function paintTabs(services) {
  if (!services.includes(SVC) && SVC !== "PPE") SVC = "";
  // Order the tabs: All services, then MEP trades, then Safety/Tools, then PPE.
  const inv = services.filter((s) => INVENTORY_SVCS.includes(s));
  const mep = services.filter((s) => !INVENTORY_SVCS.includes(s));
  const all = ["", ...mep, ...inv];
  if (META && META.has_ppe) all.push("PPE");
  $("svc").innerHTML = all.map((s) =>
    `<button class="tab${s === SVC ? " on" : ""}" data-s="${esc(s)}">${
      esc(s || "All services")}</button>`).join("");
  $("svc").querySelectorAll(".tab").forEach((b) => {
    b.onclick = () => {
      SVC = b.dataset.s;
      TYPE = ""; SIZE = ""; CONTRACTOR = "";   // switching service clears facets
      paintTabs(services);
      paintTypes();
      load();
    };
  });
}

/* The type dropdown is service-scoped. On a chosen service it lists only the
   types present in that service. On "All services" it is hidden - until the
   user explicitly opens it via "All types", which shows every type across MEP.
   Choosing "All types" also drops back to All services, since at that point the
   user is no longer looking at one trade. */
function typesForCurrentScope() {
  if (!SUBCATS) return [];
  if (SVC) return (SUBCATS.by_service[SVC] || []);
  return SUBCATS.all;
}

function paintTypes() {
  const sel = $("type");
  // Inventory (Safety/Tools) and the PPE log have no MEP sub-types, so the
  // type filter is meaningless there - hide it to keep those views clean.
  if (modeFor(SVC) !== "forecast") { sel.hidden = true; return; }
  const list = typesForCurrentScope();
  // Always visible so the filter is discoverable. On "All services" it lists
  // every MEP type; on a chosen service, only that service's types.
  sel.hidden = false;
  const opts = [`<option value="">All types</option>`].concat(
    list.map((t) =>
      `<option value="${esc(t.name)}" ${t.name === TYPE ? "selected" : ""}>${
        esc(t.name)} (${t.count})</option>`));
  sel.innerHTML = opts.join("");
}

$("type").onchange = (e) => {
  // In the inventory / PPE tabs the type filter is a plain facet over the loaded
  // rows - just set it and re-filter from cache, no service reset.
  if (modeFor(SVC) !== "forecast") { TYPE = e.target.value; rerenderFacets(); return; }
  const v = e.target.value;
  if (v === "") {
    // "All types" = leave the single-service view entirely: reset to All
    // services and show every type across MEP, no type filter applied. The
    // dropdown stays visible so a type from any trade can then be picked.
    SVC = ""; TYPE = "";
    $("svc").querySelectorAll(".tab").forEach((b) =>
      b.classList.toggle("on", b.dataset.s === ""));
    const sel = $("type");
    sel.hidden = false;
    sel.innerHTML = [`<option value="">All types</option>`].concat(
      (SUBCATS ? SUBCATS.all : []).map((t) =>
        `<option value="${esc(t.name)}">${esc(t.name)} (${t.count})</option>`)
    ).join("");
  } else {
    TYPE = v;
  }
  load();
};

$("size").onchange = (e) => { SIZE = e.target.value; rerenderFacets(); };
$("contractor").onchange = (e) => { CONTRACTOR = e.target.value; rerenderFacets(); };

/* Re-filter the already-loaded inventory / PPE rows after a facet change,
   without a network round-trip. */
function rerenderFacets() {
  const mode = modeFor(SVC);
  if (mode === "inventory") renderInventory(INV_ROWS);
  else if (mode === "ppe") renderPPE(PPE_ROWS);
}

/* Count occurrences of each value and return them highest-first (ties by name).
   Drives every facet dropdown so the busiest type/size/contractor sits on top. */
function facetCounts(values) {
  const m = new Map();
  values.forEach((v) => m.set(v, (m.get(v) || 0) + 1));
  return [...m.entries()]
    .map(([name, count]) => ({ name, count }))
    .sort((a, b) => b.count - a.count || String(a.name).localeCompare(b.name));
}

/* Fill a facet <select> with an "all" row plus counted options, preserving the
   current selection. */
function fillFacet(sel, allLabel, list, current) {
  sel.innerHTML = [`<option value="">${allLabel}</option>`].concat(
    list.map((t) =>
      `<option value="${esc(t.name)}" ${t.name === current ? "selected" : ""}>${
        esc(t.name)} (${t.count})</option>`)).join("");
}

/* Normalise a hand-typed PPE shoe size ("6 NUMBER", "7 NIMBER", "9*NUMBER") down
   to just the number so sizes group. Empty when there is no number. */
const shoeSize = (v) => {
  const m = String(v || "").match(/([0-9]{1,2})/);
  return m ? m[1] : "";
};

function debounce(fn, ms) {
  let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}
$("q").oninput = debounce(load, 250);
$("status").onchange = load;

async function load() {
  if (!RUN) return;
  const mode = modeFor(SVC);
  applyMode(mode);
  if (mode === "ppe") return loadPPE();
  if (mode === "inventory") return loadInventory();

  // "Order date passed" is not a status - it is the overdue (order-by in the
  // past) set, so it is sent as its own flag to match the KPI card exactly.
  const stVal = $("status").value;
  const params = { service: SVC, subcategory: TYPE, q: $("q").value };
  if (stVal === "__overdue__") params.overdue = 1;
  else params.status = stVal;
  const p = new URLSearchParams(params);
  paintRows(await (await fetch(`/api/forecast/${RUN}?${p}`)).json());
  syncKpiHighlight();
}

/* Reshape the shared table chrome for the current mode. The forecast table has
   five columns; inventory needs three; PPE brings its own header. We swap the
   <thead> cells and hide forecast-only controls, then restore them on the way
   back so the MEP view is untouched. */
function applyMode(mode) {
  const head = document.querySelector("#report thead tr");
  const statuswrap = $("statuswrap");
  const rule = $("rule");
  const legend = document.querySelector(".legend");
  const note = ensureInvNote();
  const colgroup = document.querySelector("#report colgroup");
  const kpis = $("kpis");
  // Facet selects are owned by whichever loader is about to run. Hide the two
  // extra ones on every mode entry; the forecast type filter is managed by
  // paintTypes, the inventory/PPE ones by their loaders below.
  $("size").hidden = true;
  $("contractor").hidden = true;
  // The fixed 5-column widths only make sense for the forecast table. Disable
  // them in the other modes so 3/4-column layouts size naturally.
  if (colgroup) colgroup.style.display = mode === "forecast" ? "" : "none";
  // The KPI row (Act today / Already out / ...) is Forecast-specific — it
  // counts RED/AMBER/GREEN shortage status, which Safety/Tools inventory
  // rows and the PPE issue log don't have at all. It used to render
  // unconditionally regardless of mode, so it kept showing (with numbers
  // that have nothing to do with what's on screen) on Inventory and PPE too.
  if (kpis) kpis.hidden = mode !== "forecast";
  // The PPE summary banner is PPE-only -- hide it on every mode entry;
  // renderPPE() below re-shows it once a TYPE is actually selected.
  const ppeSummaryEl = ensurePpeSummary();
  if (mode !== "ppe") ppeSummaryEl.hidden = true;
  if (mode === "forecast") {
    head.innerHTML =
      `<th>Material</th><th>What to do</th><th class="n">Stock</th>` +
      `<th>Runs out</th><th>Trust</th>`;
    statuswrap.hidden = false; rule.hidden = false;
    if (legend) legend.hidden = false;
    note.hidden = true;
  } else if (mode === "inventory") {
    head.innerHTML =
      `<th>Item</th><th class="n">Stock</th><th>Type</th>`;
    statuswrap.hidden = true; rule.hidden = true;
    if (legend) legend.hidden = true;
    note.textContent = "Inventory view — count only, no forecast.";
    note.hidden = false;
  } else { // ppe
    head.innerHTML =
      `<th>Name</th><th>Issued</th><th>Contractor</th><th>Date</th>`;
    statuswrap.hidden = true; rule.hidden = true;
    if (legend) legend.hidden = true;
    note.textContent =
      "Issue log — who was issued what. Not a stock count.";
    note.hidden = false;
  }
}

/* A one-line note above the table, created once and reused. */
function ensureInvNote() {
  let el = $("invnote");
  if (!el) {
    el = document.createElement("p");
    el.id = "invnote";
    el.className = "invnote";
    el.hidden = true;
    const wrap = document.querySelector(".tablewrap");
    wrap.parentNode.insertBefore(el, wrap);
  }
  return el;
}

/* PPE's "how many of this were issued" banner -- a big bold count, created
   once and reused, sitting right above the table (after the invnote). Only
   shown while a TYPE is selected (a bare headcount across every item type
   mixed together isn't a meaningful single number); updates live as TYPE /
   SIZE / CONTRACTOR change so nobody has to count table rows by hand. */
function ensurePpeSummary() {
  let el = $("ppesummary");
  if (!el) {
    el = document.createElement("div");
    el.id = "ppesummary";
    el.className = "ppesummary";
    el.hidden = true;
    const wrap = document.querySelector(".tablewrap");
    wrap.parentNode.insertBefore(el, wrap);
  }
  return el;
}
function ppeSummaryText(type, size, contractor, count) {
  const noun = type === "Shoes"
    ? (size ? `pairs of size ${size} Shoes` : "pairs of Shoes")
    : `${type}s`;
  const who = contractor ? ` to ${esc(contractor)}` : "";
  return `<b>${count}</b><span>${esc(noun)} issued${who}</span>`;
}

async function loadInventory() {
  // Reuse the forecast endpoint (Safety/Tools rows carry status INVENTORY) and
  // the search box server-side. Cache the rows so type/size facet changes
  // re-filter without re-fetching.
  const p = new URLSearchParams({ service: SVC, q: $("q").value });
  INV_ROWS = await (await fetch(`/api/forecast/${RUN}?${p}`)).json();
  renderInventory(INV_ROWS);
}

/* Inventory table + facets. The Type column and the type filter use tool_type
   (the dedicated tool/safety classifier), NOT the material sub-category - a
   ladder is a Ladder here, not a "Cable tray". Sizes appear only when the
   register writes them into the name (safety shoes), otherwise the size filter
   is hidden and we fall back to type-only. */
function renderInventory(rows) {
  const types = facetCounts(rows.map((r) => r.tool_type).filter(Boolean));
  const sizes = facetCounts(rows.map((r) => r.tool_size).filter(Boolean));
  fillFacet($("type"), "All types", types, TYPE);
  $("type").hidden = false;
  if (sizes.length) {
    fillFacet($("size"), "All sizes", sizes, SIZE);
    $("size").hidden = false;
  } else { $("size").hidden = true; SIZE = ""; }

  let out = rows;
  if (TYPE) out = out.filter((r) => (r.tool_type || "") === TYPE);
  if (SIZE) out = out.filter((r) => (r.tool_size || "") === SIZE);
  $("empty").hidden = out.length > 0;
  $("rows").innerHTML = out.map((r) =>
    `<tr data-m="${esc(r.material)}">
      <td><div class="mat">${esc(r.material)}</div>
          <div class="sub">${esc(r.unit || "")}</div></td>
      <td class="n"><div class="big">${num(r.stock)}</div></td>
      <td>${esc(r.tool_type || "—")}${
        r.tool_size ? ` <span class="sub">· size ${esc(r.tool_size)}</span>` : ""}</td>
    </tr>`).join("");
  $("rows").querySelectorAll("tr").forEach((tr) => {
    tr.onclick = () => openSheet(tr.dataset.m);
  });
}

const ppeYes = (v) =>
  v && !["-", "0", "NONE", "NAN", ""].includes(String(v).trim().toUpperCase());

async function loadPPE() {
  const data = await (await fetch(`/api/ppe/${RUN}`)).json();
  PPE_ROWS = data.records || [];
  renderPPE(PPE_ROWS);
}

/* PPE issue log + facets. Filterable BOTH ways, as the engineer asked:
   - by TYPE (Shoes / Helmet / Jacket / Blanket) and by shoe SIZE - the primary
     filters, just like materials have; and
   - by CONTRACTOR - who was issued how much, busiest contractor on top.
   The table itself is ordered so the highest-issuing contractor's people sit at
   the top. */
function renderPPE(recs) {
  const ITEMS = [["shoes", "Shoes"], ["helmet", "Helmet"],
                 ["jacket", "Jacket"], ["blanket", "Blanket"]];

  // ---- facet option lists (built from the full record set) ----
  const typeVals = [];
  recs.forEach((r) => ITEMS.forEach(([k, label]) => { if (ppeYes(r[k])) typeVals.push(label); }));
  const sizeVals = recs.filter((r) => ppeYes(r.shoes))
    .map((r) => shoeSize(r.shoes_size)).filter(Boolean);
  const contrVals = recs.map((r) => (r.contractor || "").trim()).filter(Boolean);
  const contr = facetCounts(contrVals);

  fillFacet($("type"), "All types", facetCounts(typeVals), TYPE);
  $("type").hidden = false;
  if (sizeVals.length) {
    fillFacet($("size"), "All shoe sizes", facetCounts(sizeVals), SIZE);
    $("size").hidden = false;
  } else { $("size").hidden = true; SIZE = ""; }
  fillFacet($("contractor"), "All contractors", contr, CONTRACTOR);
  $("contractor").hidden = false;

  // rank each contractor by total issues so the table can lead with the biggest
  const rank = new Map(contr.map((c, i) => [c.name, i]));

  // ---- filtering ----
  const q = $("q").value.trim().toUpperCase();
  let out = recs.filter((r) => {
    if (q && !((r.name || "").toUpperCase().includes(q) ||
               (r.contractor || "").toUpperCase().includes(q))) return false;
    if (TYPE) {
      const key = ITEMS.find(([, l]) => l === TYPE);
      if (!key || !ppeYes(r[key[0]])) return false;
    }
    if (SIZE && !(ppeYes(r.shoes) && shoeSize(r.shoes_size) === SIZE)) return false;
    if (CONTRACTOR && (r.contractor || "").trim() !== CONTRACTOR) return false;
    return true;
  });

  // ---- summary banner: "N Jackets issued[ to CONTRACTOR]" / "N pairs of
  // size S Shoes issued[ to CONTRACTOR]" -- exactly what's in the table
  // below it (same `out`, same filters, search included), so nobody has to
  // count rows by hand. Shown only once a TYPE is picked; a bare headcount
  // across every mixed item type isn't one meaningful number. Works for any
  // type in ITEMS, not just Jacket -- nothing here is type-specific. ----
  const summary = ensurePpeSummary();
  if (TYPE) {
    summary.innerHTML = ppeSummaryText(TYPE, SIZE, CONTRACTOR, out.length);
    summary.hidden = false;
  } else {
    summary.hidden = true;
  }

  // busiest contractor first, then contractor name, then person
  out = out.slice().sort((a, b) => {
    const ra = rank.has((a.contractor || "").trim()) ? rank.get((a.contractor || "").trim()) : 1e9;
    const rb = rank.has((b.contractor || "").trim()) ? rank.get((b.contractor || "").trim()) : 1e9;
    return ra - rb || String(a.contractor || "").localeCompare(b.contractor || "")
      || String(a.name || "").localeCompare(b.name || "");
  });

  $("empty").hidden = out.length > 0;
  $("rows").innerHTML = out.map((r) => {
    // TYPE filter narrows WHICH ROWS show (a person who got a jacket), but
    // the Issued column used to always list everything that person ever
    // received - so filtering to "Jacket" still showed their Shoes/Helmet
    // too, which read as if the filter wasn't working. When a type is
    // selected, the column now only shows that one item; with no type
    // filter, it still shows everything, unchanged.
    const items = [];
    const showAll = !TYPE;
    if ((showAll || TYPE === "Shoes") && ppeYes(r.shoes))
      items.push(shoeSize(r.shoes_size) ? `Shoes (${esc(shoeSize(r.shoes_size))})` : "Shoes");
    if ((showAll || TYPE === "Helmet") && ppeYes(r.helmet)) items.push("Helmet");
    if ((showAll || TYPE === "Jacket") && ppeYes(r.jacket)) items.push("Jacket");
    if ((showAll || TYPE === "Blanket") && ppeYes(r.blanket)) items.push("Blanket");
    return `<tr>
      <td><div class="mat">${esc(r.name || "—")}</div></td>
      <td>${items.length ? items.map(esc).join(", ") : "—"}</td>
      <td>${esc(r.contractor || "—")}</td>
      <td class="n">${esc(r.date || "—")}</td>
    </tr>`;
  }).join("");
  // PPE rows are people, not materials - no drill-in drawer.
  $("rows").querySelectorAll("tr").forEach((tr) => { tr.onclick = null; });
}

function paintRows(rows) {
  $("empty").hidden = rows.length > 0;
  $("rows").innerHTML = rows.map((r) => {
    const [cls, main, sub] = action(r);
    const [ro, roSub] = runsOut(r);
    return `<tr data-m="${esc(r.material)}">
      <td><div class="mat">${esc(r.material)}</div>
          <div class="sub">${esc(r.service || "")} · ${esc(r.unit || "")}</div></td>
      <td><span class="act a-${cls}">${main}</span>
          ${sub ? `<div class="sub">${sub}</div>` : ""}
          ${leadTag(r)}</td>
      <td class="n"><div class="big">${num(r.stock)}</div>
          <div class="sub">${num(r.rate_per_day, 1)} / day</div></td>
      <td><div>${ro}</div><div class="sub">${roSub}</div></td>
      <td><div class="dots">${DOTS[r.confidence] || "○○○"}</div>
          <div class="sub">${r.consumption_days}d</div></td>
    </tr>`;
  }).join("");
  $("rows").querySelectorAll("tr").forEach((tr) => {
    tr.onclick = () => openSheet(tr.dataset.m);
  });
}

/* ------------------------------------------------------------------ drawer */
async function openSheet(name) {
  const rows = await (await fetch(
    `/api/material/${RUN}?name=${encodeURIComponent(name)}`)).json();
  $("sname").textContent = name;
  const moved = rows.filter((r) => r.qty_in > 0 || r.qty_out > 0);
  $("smeta").textContent =
    `${moved.length} days with movement · ${rows.length} days on record`;
  $("spark").innerHTML = sparkline(rows);
  $("srows").innerHTML = moved.slice(-40).reverse().map((r) =>
    `<tr><td>${r.date}</td><td class="n">${r.qty_in || ""}</td>
     <td class="n">${r.qty_out || ""}</td><td class="n">${num(r.balance)}</td></tr>`
  ).join("");
  $("sheet").hidden = false;
}
function closeSheet() { $("sheet").hidden = true; }
$("sheet").onclick = (e) => { if (e.target.id === "sheet") closeSheet(); };
addEventListener("keydown", (e) => {
  if (e.key === "Escape") { closeSheet(); closeModal(); }
});

function sparkline(rows) {
  const pts = rows.filter((r) => r.balance !== null);
  if (pts.length < 2) return "";
  const W = 500, H = 92, max = Math.max(...pts.map((p) => p.balance), 1);
  const d = pts.map((p, i) =>
    `${(i / (pts.length - 1)) * W},${H - (p.balance / max) * (H - 8) - 4}`).join(" ");
  // "Total received" = everything that ever came in -- the register's own
  // Opening Stock for this material PLUS every dated IN transaction since.
  // Opening is constant across every row (the backend broadcasts it), so
  // any row carries it; summing dated IN alone used to show "0 received"
  // for a material whose entire supply arrived as an opening balance with
  // no subsequent purchases, even though it clearly has real stock.
  const opening = rows.length && rows[0].opening != null ? Number(rows[0].opening) || 0 : 0;
  const datedIn = rows.reduce((t, r) => t + (r.qty_in > 0 ? r.qty_in : 0), 0);
  const received = opening + datedIn;
  return `<svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}">
    <polyline points="${d}" fill="none" stroke="#1c1c1b" stroke-width="1.5"/>
    <line x1="0" y1="${H - 4}" x2="${W}" y2="${H - 4}" stroke="#e7e5df"/>
  </svg><p class="sub">Balance over time · peak ${num(max)}</p>
  <p class="sub sp-totrecv">Total received · <b>${num(received)}</b>${opening > 0 ? ` <span class="sub">(${num(opening)} opening + ${num(datedIn)} received since)</span>` : ""}</p>`;
}

/* On load: restore the last viewed run if it still exists, so a browser reload
   lands back where the user was instead of the upload screen. */
async function restoreLast() {
  let last = "";
  try { last = localStorage.getItem("currentRun") || ""; } catch (e) {}
  if (!last) return;
  try {
    const r = await fetch(`/api/run/${last}`);
    if (!r.ok) { try { localStorage.removeItem("currentRun"); } catch (e) {} return; }
    const j = await r.json();
    await show(last, j.meta, j.summary);
  } catch (e) {
    try { localStorage.removeItem("currentRun"); } catch (_) {}
  }
}

loadProjects();
restoreLast();
