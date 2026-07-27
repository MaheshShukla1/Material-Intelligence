const $ = (id) => document.getElementById(id);
let RUN = null, META = null, SVC = "", TYPE = "", LEAD = 7, SUBCATS = null;

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
  if (d < 0) return ["late", "Order now", `${-d} day${d === -1 ? "" : "s"} late`];
  if (d === 0) return ["late", "Order today", "last day to order"];
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

/* ------------------------------------------------------------------ upload */
$("pick").onclick = () => $("file").click();
$("file").onchange = (e) => {
  if (e.target.files[0]) send(e.target.files[0]);
  e.target.value = "";
};

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

async function send(file) {
  LEAD = Number($("lead").value) || 7;
  drop.hidden = true; $("report").hidden = true; $("busy").hidden = false;
  $("busytxt").textContent =
    "Reading the file, detecting columns, repairing dates…";
  const fd = new FormData();
  fd.append("file", file);
  fd.append("lead_time", LEAD);
  fd.append("project", $("pname").value.trim());
  try {
    const r = await fetch("/api/upload", { method: "POST", body: fd });
    const j = await r.json();
    if (!r.ok) throw new Error(j.detail || "upload failed");
    await show(j.run_id, j.meta, j.summary);
    await loadProjects();
  } catch (err) {
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
  $("busytxt").textContent = "Fetching the latest data from your Google Sheet…";
  const fd = new FormData();
  fd.append("link", link);
  fd.append("lead_time", LEAD);
  fd.append("project", (project || "").trim());
  try {
    const r = await fetch("/api/sync-sheet", { method: "POST", body: fd });
    const j = await r.json();
    if (!r.ok) throw new Error(j.detail || "sync failed");
    try { localStorage.setItem("sheetLink", link); } catch (e) {}
    await show(j.run_id, j.meta, j.summary);
    await loadProjects();
  } catch (err) {
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
  paintTabs(summary.services);
  paintTypes();
  $("rule").textContent =
    `Lead time ${LEAD} days plus a 2 day buffer, so an order must be raised ` +
    `${LEAD + 2} days before stock hits zero.`;
  $("dl").hidden = false; $("dl").href = `/api/export/${RUN}`;
  $("del").hidden = false;
  $("home").hidden = false;
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
  $("busytxt").textContent = "Loading project…";
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
  $("mlist").innerHTML = projects.map((p) => {
    const rs = runsByProject[p.slug] || [];
    const rows = rs.map((r) => {
      const when = (r.created || "").replace("T", " ").slice(0, 16);
      const mats = r.stats && r.stats.materials ? `${r.stats.materials} materials` : "";
      return `<div class="mrun">
        <div class="mrun-info">
          <span class="mrun-file">${esc(r.filename || r.run_id)}</span>
          <span class="mrun-meta">${esc(when)}${mats ? " · " + mats : ""}</span>
        </div>
        <button class="btn danger sm" data-run="${esc(r.run_id)}">Delete</button>
      </div>`;
    }).join("");
    return `<div class="mproj">
      <div class="mproj-hd">
        <div>
          <span class="mproj-name">${esc(p.project)}</span>
          <span class="mproj-count">${rs.length} upload${rs.length === 1 ? "" : "s"}</span>
        </div>
        <button class="btn danger sm" data-project="${esc(p.slug)}" data-name="${esc(p.project)}">Delete project</button>
      </div>
      ${rows}
    </div>`;
  }).join("");

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

function paintKpis(s) {
  const c = s.counts, g = (k) => c[k] || 0;
  const cards = [
    ["Act today", g("STOCKED_OUT") + g("RED"), "out of stock or inside lead time", true],
    ["Already out", g("STOCKED_OUT"), "zero on hand, still consuming", g("STOCKED_OUT") > 0],
    ["Order date passed", s.overdue_orders, "should already have been raised", s.overdue_orders > 0],
    ["Order this week", g("AMBER"), "inside twice the lead time", false],
    ["Stop ordering", s.idle_lines, "overstocked, unused, or paused", false],
    ["No action", g("GREEN"), "healthy cover", false],
  ];
  $("kpis").innerHTML = cards.map(([l, v, h, hot]) =>
    `<div class="kpi${hot ? " hot" : ""}"><p class="l">${l}</p>
     <p class="v">${num(v)}</p><p class="h">${h}</p></div>`).join("");
}

function paintTabs(services) {
  if (!services.includes(SVC)) SVC = "";
  const all = ["", ...services];
  $("svc").innerHTML = all.map((s) =>
    `<button class="tab${s === SVC ? " on" : ""}" data-s="${esc(s)}">${
      esc(s || "All services")}</button>`).join("");
  $("svc").querySelectorAll(".tab").forEach((b) => {
    b.onclick = () => {
      SVC = b.dataset.s;
      TYPE = "";                       // switching service clears the type
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

function debounce(fn, ms) {
  let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}
$("q").oninput = debounce(load, 250);
$("status").onchange = load;

async function load() {
  if (!RUN) return;
  const p = new URLSearchParams({
    status: $("status").value, service: SVC,
    subcategory: TYPE, q: $("q").value,
  });
  paintRows(await (await fetch(`/api/forecast/${RUN}?${p}`)).json());
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
          ${sub ? `<div class="sub">${sub}</div>` : ""}</td>
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
  const received = rows.reduce((t, r) => t + (r.qty_in > 0 ? r.qty_in : 0), 0);
  return `<svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}">
    <polyline points="${d}" fill="none" stroke="#1c1c1b" stroke-width="1.5"/>
    <line x1="0" y1="${H - 4}" x2="${W}" y2="${H - 4}" stroke="#e7e5df"/>
  </svg><p class="sub">Balance over time · peak ${num(max)}</p>
  <p class="sub">Total received · ${num(received)}</p>`;
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
