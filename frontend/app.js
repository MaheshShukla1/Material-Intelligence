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
  if (d === 0) return ["late", "Order today", ""];
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

async function show(runId, meta, summary) {
  RUN = runId; META = meta; TYPE = "";
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
  await load();
  $("busy").hidden = true; $("drop").hidden = true; $("report").hidden = false;
  syncHeaderOffset();
}

/* Sticky column headers must park under the app bar whatever its height is
   on this screen, so measure rather than guess. */
function syncHeaderOffset() {
  document.documentElement.style.setProperty(
    "--hdr", Math.round($("top").getBoundingClientRect().height) + "px");
}
addEventListener("resize", syncHeaderOffset);

/* ---------------------------------------------------------------- projects */
async function loadProjects() {
  const ps = await (await fetch("/api/projects")).json();
  const sel = $("proj");
  sel.hidden = ps.length === 0;
  sel.innerHTML = ps.map((p) =>
    `<option value="${esc(p.latest_run)}" data-slug="${esc(p.slug)}"
      ${p.latest_run === RUN ? "selected" : ""}>${esc(p.project)} · ${p.runs} run${
      p.runs === 1 ? "" : "s"}</option>`).join("");
}
$("proj").onchange = async (e) => {
  const id = e.target.value;
  $("report").hidden = true; $("busy").hidden = false;
  $("busytxt").textContent = "Loading project…";
  const j = await (await fetch(`/api/run/${id}`)).json();
  await show(id, j.meta, j.summary);
};

/* ------------------------------------------------------------------ delete */
$("del").onclick = () => {
  if (!META) return;
  $("mtext").innerHTML =
    `This removes every run and every uploaded file for <b>${esc(META.project)}</b>. ` +
    "It cannot be undone. Type the project name to confirm.";
  $("mconfirm").value = ""; $("merr").hidden = true;
  $("modal").hidden = false; $("mconfirm").focus();
};
function closeModal() { $("modal").hidden = true; }
$("modal").onclick = (e) => { if (e.target.id === "modal") closeModal(); };
$("mgo").onclick = async () => {
  const typed = $("mconfirm").value;
  const r = await fetch(
    `/api/project/${META.project_slug}?confirm=${encodeURIComponent(typed)}`,
    { method: "DELETE" });
  if (!r.ok) {
    const j = await r.json().catch(() => ({}));
    $("merr").textContent = j.detail || "delete failed";
    $("merr").hidden = false;
    return;
  }
  closeModal();
  RUN = null; META = null;
  $("report").hidden = true; $("del").hidden = true; $("dl").hidden = true;
  $("ctx").textContent = "No file loaded";
  $("drop").hidden = false;
  await loadProjects();
};

/* ------------------------------------------------------------------ render */
function paintHeader(m) {
  const s = m.stats;
  const src = m.source === "projectbase" ? "ProjectBase export" : "site register";
  $("ctx").textContent =
    `${m.project} · ${m.filename} · ${src} · as of ${s.asof} · ${s.materials} materials`;
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
    ["Stop ordering", s.idle_lines, "overstocked or never issued", false],
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
  return `<svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}">
    <polyline points="${d}" fill="none" stroke="#1c1c1b" stroke-width="1.5"/>
    <line x1="0" y1="${H - 4}" x2="${W}" y2="${H - 4}" stroke="#e7e5df"/>
  </svg><p class="sub">Balance over time · peak ${num(max)}</p>`;
}

loadProjects();
