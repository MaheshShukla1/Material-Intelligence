/* Site Progress — additive front-end module (clean rewrite).
 *
 * Loaded AFTER app.js. Self-contained IIFE. Injects a 3-tab nav
 * (Forecast · Site Progress · Inventory), owns its own #siteprogress section,
 * and talks only to /api/siteprogress/*. Every class it renders is sp- prefixed
 * so the existing Forecast/Inventory UI is never restyled.
 *
 * Design: LEFT rail = editable structure tree (site_progress_mockup feel);
 * MIDDLE = hero + activity cards + BOQ item rows + drawer (site_progress_calm
 * feel). The tree renders any structure shape (hotel/mall/hospital/custom).
 *
 * Fixes vs the previous version:
 *  - setup no longer resets the template picker after "Create structure"; it
 *    updates the step in place and enables "Open" — so success is obvious.
 *  - Inventory tab shows the forecast table filtered to Safety/Tools/PPE;
 *    Forecast tab shows the MEP services only.
 *  - the item drawer now shows the realistic forecast (stock enough to finish?).
 */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const api = (p) => "/api/siteprogress" + p;
  const INVENTORY = ["safety", "tools", "ppe"];

  async function jget(u) {
    const r = await fetch(u);
    if (!r.ok) throw Object.assign(new Error("http"), { status: r.status, body: await r.text() });
    return r.json();
  }
  async function jpost(u, b) {
    const r = await fetch(u, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(b) });
    if (!r.ok) throw Object.assign(new Error("http"), { status: r.status, body: await r.text() });
    return r.json();
  }
  async function upload(u, file, extra) {
    const fd = new FormData(); fd.append("file", file);
    Object.entries(extra || {}).forEach(([k, v]) => fd.append(k, v));
    const r = await fetch(u, { method: "POST", body: fd });
    if (!r.ok) throw Object.assign(new Error("http"), { status: r.status, body: await r.text() });
    return r.json();
  }
  function inr(v) {
    if (v == null) return "—";
    const n = Math.round(v);
    if (Math.abs(n) >= 1e7) return "₹" + (n / 1e7).toFixed(2) + " Cr";
    if (Math.abs(n) >= 1e5) return "₹" + (n / 1e5).toFixed(2) + " L";
    return "₹" + n.toLocaleString("en-IN");
  }
  const qf = (v) => (v == null ? "—" : (Math.round(v * 10) / 10).toLocaleString("en-IN"));
  const briefErr = (e) => { try { return JSON.parse(e.body).detail || e.body; } catch (_) { return e.body || e.message || "error"; } };
  const sel = (s) => document.querySelector(s);

  const S = { view: "forecast", slug: null, projects: [], state: null,
              service: null, svc: null, pnl: null, real: null, tmpl: "tracker",
              foreSnap: null, room: null, roomName: null, openActivity: null };

  // ============================================================ nav / views
  function injectNav() {
    const header = sel("header.top");
    if (!header || sel(".spnav")) return;
    const nav = document.createElement("nav");
    nav.className = "spnav";
    nav.innerHTML = `<button data-v="forecast" class="on">Forecast</button>
      <button data-v="siteprogress">Site Progress</button>
      <button data-v="inventory">Inventory</button>`;
    header.insertBefore(nav, header.querySelector(".topact"));
    nav.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => setView(b.dataset.v)));
    // any forecast action returns to Forecast tab
    ["pick", "syncgo", "sync", "home"].forEach((id) => { const e = $(id); if (e) e.addEventListener("click", () => setView("forecast"), true); });
    // when the forecast table re-renders, re-apply the active pill filter
    const svc = $("svc");
    if (svc) new MutationObserver(() => applyPillFilter()).observe(svc, { childList: true });
  }

  function ensureSection() {
    let s = $("siteprogress");
    if (!s) { s = document.createElement("section"); s.id = "siteprogress"; s.className = "sp"; s.hidden = true; document.querySelector("main").appendChild(s); }
    return s;
  }
  const FORE = ["drop", "busy", "report"];
  function setNavActive(v) { document.querySelectorAll(".spnav button").forEach((b) => b.classList.toggle("on", b.dataset.v === v)); }

  function setView(v) {
    S.view = v; setNavActive(v);
    const sp = ensureSection();
    if (v === "siteprogress") {
      S.foreSnap = {}; FORE.forEach((id) => { const e = $(id); if (e) { S.foreSnap[id] = e.hidden; e.hidden = true; } });
      sp.hidden = false; openSiteProgress();
    } else {
      sp.hidden = true;
      if (S.foreSnap) FORE.forEach((id) => { const e = $(id); if (e) e.hidden = S.foreSnap[id]; });
      S.foreSnap = null;
      applyPillFilter();               // Forecast = MEP only, Inventory = Safety/Tools/PPE
    }
  }

  // Forecast vs Inventory: filter the existing #svc service pills by name.
  function applyPillFilter() {
    if (S.view === "siteprogress") return;
    const svc = $("svc"); if (!svc) return;
    const pills = [...svc.querySelectorAll(".tab")];
    if (!pills.length) return;
    const isInv = (t) => INVENTORY.includes(t.trim().toLowerCase());
    let firstVisible = null;
    pills.forEach((p) => {
      const inv = isInv(p.textContent);
      const show = S.view === "inventory" ? inv : !inv;
      p.style.display = show ? "" : "none";
      if (show && !firstVisible) firstVisible = p;
    });
    // if the currently-selected pill is now hidden, click the first visible one
    const onPill = svc.querySelector(".tab.on");
    if (firstVisible && (!onPill || onPill.style.display === "none")) firstVisible.click();
  }

  // ============================================================ open / load
  async function openSiteProgress() {
    const sp = ensureSection();
    let projects = [];
    try { projects = await jget("/api/projects"); } catch (e) {}
    S.projects = projects;
    if (!projects.length) { sp.innerHTML = `<div class="sp-empty">No project yet. Upload a stock register on the <b>Forecast</b> tab first, then set up Site Progress here.</div>`; return; }
    if (!S.slug || !projects.find((p) => p.slug === S.slug)) {
      const fromF = (typeof META !== "undefined" && META && META.project_slug);
      S.slug = (fromF && projects.find((p) => p.slug === fromF)) ? fromF : projects[0].slug;
    }
    await loadState();
  }
  const projName = () => { const p = S.projects.find((x) => x.slug === S.slug); return p ? p.project : S.slug; };

  function projBar() {
    return `<div class="sp-crumb" style="display:flex;gap:10px;align-items:center;margin-bottom:14px">
      <select id="sp-proj" class="ctl">${S.projects.map((p) => `<option value="${esc(p.slug)}" ${p.slug === S.slug ? "selected" : ""}>${esc(p.project)}</option>`).join("")}</select>
      <span id="sp-where" style="color:var(--ink3)"></span></div>`;
  }

  async function loadState(fromOpen) {
    const sp = ensureSection();
    sp.innerHTML = `<div class="sp-empty">Loading…</div>`;
    let st;
    try { st = await jget(api("/" + S.slug)); }
    catch (e) { if (e.status === 404) { S.state = null; renderSetup(); if (fromOpen) flagStep("sp-step1", "First create the building structure (step 1)."); return; } sp.innerHTML = `<div class="sp-empty">${esc(briefErr(e))}</div>`; return; }
    S.state = st;
    if (!st.structure || !st.has_boq) {
      renderSetup();
      if (fromOpen) flagStep(!st.structure ? "sp-step1" : "sp-step2",
        !st.structure ? "First create the building structure (step 1)." : "Upload the BOQ file first (step 2) — Open needs both.");
      return;
    }
    if (!S.service || !st.services.includes(S.service)) S.service = st.services[0];
    await loadService();
  }

  // ============================================================ setup
  function renderSetup() {
    const sp = ensureSection(); const st = S.state || {};
    const hasStruct = st.structure; const hasBoq = st.has_boq;
    sp.innerHTML = `${projBar()}
      <div class="sp-setup">
        <h2>Set up Site Progress</h2>
        <p class="sub">Three quick steps, once per project. After this, daily work is just dragging a slider.</p>
        <div class="sp-step" id="sp-step1">
          <span class="n">1</span><h3>Building structure</h3><span id="sp-s1" class="sp-done" ${hasStruct ? "" : "hidden"}>✓ ${st.rooms || 0} rooms</span>
          <div class="body">
            <div class="sp-tmpl" id="sp-tmpl">
              <button data-k="tracker" class="on">From tracker (auto)</button>
              <button data-k="hotel">Hotel</button><button data-k="mall">Mall</button>
              <button data-k="hospital">Hospital</button><button data-k="custom">Custom</button>
            </div>
            <div id="sp-tmpl-body"></div>
          </div>
        </div>
        <div class="sp-step" id="sp-step2">
          <span class="n">2</span><h3>Bill of Quantities</h3><span id="sp-s2" class="sp-done" ${hasBoq ? "" : "hidden"}>✓ ${Object.keys(st.activities || {}).length} services</span>
          <div class="body">
            <p class="sub" style="margin:0 0 10px">Upload the BOQ workbook. Every service sheet is parsed; the planned quantity per room is the baseline.</p>
            <input type="file" id="sp-boq-file" accept=".xlsx,.xlsm,.xls" style="display:none">
            <button class="btn" onclick="document.getElementById('sp-boq-file').click()">Choose BOQ file</button>
            <span id="sp-boq-status" class="sp-done" hidden></span>
          </div>
        </div>
        <div class="sp-step" id="sp-step3">
          <span class="n">3</span><h3>Open</h3>
          <div class="body"><button class="btn primary" id="sp-open">Open Site Progress</button>
            <span id="sp-open-hint" class="sub" style="margin-left:10px;color:var(--amber)"></span></div>
        </div>
      </div>`;
    $("sp-proj").onchange = (e) => { S.slug = e.target.value; S.service = null; loadState(); };
    $("sp-tmpl").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
      $("sp-tmpl").querySelectorAll("button").forEach((x) => x.classList.remove("on"));
      b.classList.add("on"); S.tmpl = b.dataset.k; renderTmplBody();
    }));
    $("sp-boq-file").onchange = onBoqFile;
    $("sp-open").onclick = () => loadState(true);   // always re-fetch fresh state
    // keep whatever picker is active (default tracker)
    $("sp-tmpl").querySelectorAll("button").forEach((x) => x.classList.toggle("on", x.dataset.k === S.tmpl));
    renderTmplBody();
    refreshOpen();
  }

  // Open needs BOTH structure and BOQ. Rather than a dead disabled button, keep
  // it clickable and, when something is missing, say exactly what and jump there.
  function refreshOpen() {
    const st = S.state || {};
    const ready = st.structure && st.has_boq;
    const hint = $("sp-open-hint");
    if (hint) hint.textContent = ready ? "" : (!st.structure ? "← create the structure first (step 1)" : "← upload the BOQ first (step 2)");
    const btn = $("sp-open"); if (btn) btn.style.opacity = ready ? "1" : ".5";
  }
  function flagStep(id, msg) {
    toast(msg);
    const el = $(id); if (!el) return;
    if (el.scrollIntoView) el.scrollIntoView({ behavior: "smooth", block: "center" });
    el.style.transition = "box-shadow .2s"; el.style.boxShadow = "0 0 0 2px var(--amber)";
    setTimeout(() => { el.style.boxShadow = ""; }, 1700);
  }
  async function reloadStateQuiet() { try { S.state = await jget(api("/" + S.slug)); } catch (e) {} }

  function renderTmplBody() {
    const box = $("sp-tmpl-body"); if (!box) return;
    if (S.tmpl === "tracker") {
      box.innerHTML = `<p class="sub" style="margin:0 0 10px">Upload the progress tracker — floors, rooms and activities are read automatically (zero setup).</p>
        <input type="file" id="sp-trk-file" accept=".xlsx,.xlsm,.xls" style="display:none">
        <button class="btn" onclick="document.getElementById('sp-trk-file').click()">Choose tracker file</button>
        <span id="sp-trk-status" class="sp-done" hidden></span>`;
      $("sp-trk-file").onchange = async (e) => {
        const f = e.target.files[0]; if (!f) return;
        const st = $("sp-trk-status"); st.hidden = false; st.textContent = "Reading…";
        try { const j = await upload(api("/" + S.slug + "/init-from-tracker"), f, { project_name: projName() });
          st.textContent = `✓ ${j.rooms} rooms, ${j.services.length} services`;
          await reloadStateQuiet(); markStep1(j.rooms); refreshOpen();
        } catch (err) { st.textContent = "✕ " + briefErr(err); }
      };
    } else {
      const common = `<label class="sub">Project name <input id="sp-tn" class="ctl" value="${esc(projName())}"></label>`;
      let fields = "";
      if (S.tmpl === "hotel") fields = `<label class="sub">Floors <input id="sp-a" class="ctl" type="number" value="6" style="width:66px"></label><label class="sub">Rooms/floor <input id="sp-b" class="ctl" type="number" value="18" style="width:66px"></label>`;
      else if (S.tmpl === "mall") fields = `<label class="sub">Levels <input id="sp-a" class="ctl" type="number" value="3" style="width:66px"></label><label class="sub">Zones/level <input id="sp-b" class="ctl" type="number" value="8" style="width:66px"></label>`;
      else if (S.tmpl === "hospital") fields = `<label class="sub">Wings <input id="sp-a" class="ctl" type="number" value="2" style="width:66px"></label><label class="sub">Floors <input id="sp-b" class="ctl" type="number" value="4" style="width:66px"></label><label class="sub">Rooms/floor <input id="sp-c" class="ctl" type="number" value="10" style="width:66px"></label>`;
      box.innerHTML = `<div style="display:flex;gap:12px;flex-wrap:wrap;align-items:end;margin-bottom:10px">${common}${fields}</div>
        <button class="btn" id="sp-tmpl-go">Create structure</button><span id="sp-tmpl-status" class="sp-done" hidden></span>
        <p class="sub" style="margin:10px 0 0">You can rename or add floors/rooms after creating.</p>`;
      $("sp-tmpl-go").onclick = async () => {
        const stx = $("sp-tmpl-status"); stx.hidden = false; stx.textContent = "Creating…";
        const nv = (id) => { const e = $(id); return e ? Number(e.value) : 0; };
        const names = (p, n) => Array.from({ length: Math.max(1, n | 0) }, (_, i) => `${p} ${i + 1}`);
        const body = { kind: S.tmpl, name: ($("sp-tn").value || S.slug) };
        const a = nv("sp-a"), b = nv("sp-b"), cc = nv("sp-c");
        if (S.tmpl === "hotel") { body.floors = names("Floor", a); body.rooms_per_floor = b; }
        if (S.tmpl === "mall") { body.levels = names("Level", a); body.zones_per_level = b; }
        if (S.tmpl === "hospital") { body.wings = names("Wing", a); body.floors = names("Floor", b); body.rooms_per_floor = cc; }
        try { const j = await jpost(api("/" + S.slug + "/structure/template"), body);
          stx.textContent = `✓ ${j.rooms} rooms created`;
          await reloadStateQuiet(); markStep1(j.rooms); refreshOpen();
        } catch (err) { stx.textContent = "✕ " + briefErr(err); }
      };
    }
  }
  function markStep1(rooms) { const s = $("sp-s1"); if (s) { s.hidden = false; s.textContent = `✓ ${rooms} rooms`; } }

  async function onBoqFile(e) {
    const f = e.target.files[0]; if (!f) return;
    const st = $("sp-boq-status"); st.hidden = false; st.textContent = "Parsing…";
    try { const j = await upload(api("/" + S.slug + "/boq"), f);
      st.textContent = "✓ " + Object.entries(j.services).map(([k, v]) => `${k} (${v})`).join(", ");
      await reloadStateQuiet();
      if (S.state) S.state.has_boq = true;   // upload succeeded — reflect it now
      const s2 = $("sp-s2"); if (s2) { s2.hidden = false; s2.textContent = `✓ ${Object.keys(j.services).length} services`; }
      if (j.auto_rated && Object.keys(j.auto_rated).length) {
        const n = Object.values(j.auto_rated).reduce((a, b) => a + b, 0);
        toast(`Rates read automatically for ${n} items — ${Object.keys(j.auto_rated).join(", ")}`);
      }
      refreshOpen();
      showQtyModeWizard(j.needs_qty_mode);
    } catch (err) { st.textContent = "✕ " + briefErr(err); }
  }

  // ============================================================ main
  function roomQ() { return S.room ? ("?room=" + encodeURIComponent(S.room)) : ""; }
  function pnlUrl() { return api("/" + S.slug + "/pnl/" + encodeURIComponent(S.service) + roomQ()); }

  async function loadService() {
    const sp = ensureSection();
    if (S.service === "__overall__") return renderOverall();
    if (!S.service) { sp.innerHTML = `<div class="sp-empty">No services parsed from the BOQ.</div>`; return; }
    const q = roomQ();
    try { S.svc = await jget(api("/" + S.slug + "/service/" + encodeURIComponent(S.service) + q)); }
    catch (e) { sp.innerHTML = `<div class="sp-empty">${esc(briefErr(e))}</div>`; return; }
    try { S.pnl = await jget(pnlUrl()); } catch (e) { S.pnl = null; }
    try { S.real = await jget(api("/" + S.slug + "/realistic/" + encodeURIComponent(S.service))); } catch (e) { S.real = null; }
    renderMain();
  }

  function renderMain() {
    const sp = ensureSection(); const st = S.state;
    sp.innerHTML = `${projBar()}
      <div class="sp-layout">
        <aside class="sp-rail">
          <div class="sp-railhd"><span>Structure</span><span><button class="linkbtn" id="sp-addfloor">+ Add</button> &nbsp;·&nbsp; <button class="linkbtn" id="sp-rebuildstruct">Rebuild</button></span></div>
          <div class="sp-tree" id="sp-tree"></div>
          <p class="sub" style="margin:10px 4px">Type: ${esc((st.structure && st.structure.kind) || "custom")} · ${st.rooms} rooms. Hover a row to rename/delete.</p>
        </aside>
        <div>
          <div class="sp-pills" id="sp-pills"></div>
          <div class="sp-hero">
            <div class="sp-ring" id="sp-ring"><i><b id="sp-hpct">0%</b><span>complete</span></i></div>
            <div class="sp-stats">
              <div class="sp-stat"><p class="l">Work done</p><div class="v g" id="sp-done">₹—</div><p class="h" id="sp-doneh"></p></div>
              <div class="sp-stat"><p class="l">Remaining</p><div class="v a" id="sp-rem">₹—</div><p class="h">to finish planned work</p></div>
              <div class="sp-stat"><p class="l">Material waste</p><div class="v r" id="sp-waste">₹—</div><p class="h" id="sp-wasteh"></p></div>
            </div>
          </div>
          <p class="sp-unmapped" id="sp-unmapped" hidden></p>
          <div class="sp-secttl"><span>Activities · ${esc(S.service)}${S.room ? ` · <b>${esc(S.roomName)}</b> <button class="linkbtn" id="sp-allrooms">← all rooms</button>` : " · all rooms"}</span>
            <span><button class="linkbtn" id="sp-newact">+ New activity</button> &nbsp;·&nbsp; <button class="linkbtn" id="sp-rates">Set rates</button> &nbsp;·&nbsp; <button class="linkbtn" id="sp-linkbtn">Link stock</button> &nbsp;·&nbsp; <button class="linkbtn" id="sp-reboq">Re-upload BOQ</button> &nbsp;·&nbsp; <button class="linkbtn" id="sp-refresh">Refresh</button>
            <input type="file" id="sp-reboq-file" accept=".xlsx,.xlsm,.xls" hidden></span></div>
          <div id="sp-acts"></div>
        </div>
      </div>`;
    $("sp-proj").onchange = (e) => { S.slug = e.target.value; S.service = null; loadState(); };
    $("sp-refresh").onclick = () => loadService();
    $("sp-linkbtn").onclick = () => openLinkModal();
    $("sp-rates").onclick = () => openRatesModal();
    $("sp-newact").onclick = () => newActivity();
    $("sp-reboq").onclick = () => $("sp-reboq-file").click();
    $("sp-reboq-file").onchange = reuploadBoq;
    const ar = $("sp-allrooms"); if (ar) ar.onclick = () => clearRoom();
    $("sp-addfloor").onclick = () => addNode(S.state.structure.id, topChildType());
    $("sp-rebuildstruct").onclick = () => rebuildStructure();
    renderTree(); renderPills(); renderActs(); renderHero();
  }

  // Re-parse the BOQ workbook without going back through first-time setup.
  // Existing progress/rates/mapping are keyed by item_code and untouched by
  // this -- only the parsed line items (and any newly-fixed grouping, e.g.
  // duplicate-service merging) refresh. Same endpoint the setup wizard uses.
  async function reuploadBoq(e) {
    const f = e.target.files[0]; if (!f) return;
    if (!confirm(`Re-parse "${f.name}" as the BOQ for this project? Planned quantities and service grouping refresh from the file; progress already recorded (by item code) is not affected.`)) {
      e.target.value = ""; return;
    }
    toast("Re-parsing BOQ…");
    try {
      const j = await upload(api("/" + S.slug + "/boq"), f);
      toast("✓ " + Object.entries(j.services).map(([k, v]) => `${k} (${v})`).join(", "));
      await reloadStateQuiet();
      await loadService();
      showQtyModeWizard(j.needs_qty_mode);
    } catch (err) { toast("Failed: " + briefErr(err)); }
    e.target.value = "";
  }

  // ---------- left tree (editable) ----------
  function topChildType() {
    const k = (S.state.structure || {}).kind;
    return k === "mall" ? "level" : k === "hospital" ? "wing" : "floor";
  }
  // The leaf unit's own name, by project kind -- a hotel/hospital's leaf is
  // genuinely a "Room"; a mall's is a "Zone" (rooms don't exist there at
  // all). Used anywhere UI copy needs to name the leaf without hard-coding
  // "room" and reading wrong for a mall project.
  function leafLabel(kind) {
    return kind === "mall" ? "Zone" : "Room";
  }
  function renderTree() {
    const host = $("sp-tree"); host.innerHTML = nodeHTML(S.state.structure, 0);
    host.querySelectorAll("[data-toggle]").forEach((el) => el.addEventListener("click", (e) => {
      if (e.target.closest(".sp-treeact")) return;
      const node = el.closest(".sp-node");
      if (node.classList.contains("has-kids")) node.classList.toggle("exp");
      else selectRoom(el);
    }));
    host.querySelectorAll("[data-add]").forEach((b) => b.addEventListener("click", (e) => { e.stopPropagation(); addNode(b.dataset.add, b.dataset.addtype); }));
    host.querySelectorAll("[data-edit]").forEach((b) => b.addEventListener("click", (e) => { e.stopPropagation(); renameNode(b.dataset.edit); }));
    host.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", (e) => { e.stopPropagation(); delNode(b.dataset.del); }));
  }
  function nodeHTML(node, depth) {
    const kids = node.children || [];
    const isLeaf = node.type === "room" || (kids.length === 0 && node.type !== "project");
    const childType = node.type === "project" ? topChildType() : (node.type === "wing" ? "floor" : "room");
    const acts = node.type === "project" ? "" :
      `<span class="sp-treeact">
        <button class="sp-ic" data-edit="${esc(node.id)}" title="Rename">✎</button>
        ${!isLeaf ? `<button class="sp-ic" data-add="${esc(node.id)}" data-addtype="${childType}" title="Add">＋</button>` : ""}
        <button class="sp-ic" data-del="${esc(node.id)}" title="Delete">×</button></span>`;
    return `<div class="sp-node ${kids.length ? "has-kids" : ""} ${depth <= 0 ? "exp" : ""}" data-id="${esc(node.id)}">
      <div class="row ${isLeaf ? "leaf" : ""}" data-toggle="${esc(node.id)}">
        ${kids.length ? '<span class="sp-chev">▶</span>' : '<span style="width:9px;flex:none"></span>'}
        <span class="nm">${esc(node.name)}</span>
        ${kids.length ? `<span class="sp-nodepct">${kids.length}</span>` : ""}
        ${acts}
      </div>
      ${kids.length ? `<div class="kids">${kids.map((k) => nodeHTML(k, depth + 1)).join("")}</div>` : ""}
    </div>`;
  }
  function selectRoom(row) {
    document.querySelectorAll("#sp-tree .row.on").forEach((r) => r.classList.remove("on"));
    row.classList.add("on");
    const node = row.closest(".sp-node");
    S.room = node ? node.dataset.id : null;
    S.roomName = row.querySelector(".nm") ? row.querySelector(".nm").textContent : null;
    loadService();
  }
  function clearRoom() { S.room = null; S.roomName = null; document.querySelectorAll("#sp-tree .row.on").forEach((r) => r.classList.remove("on")); loadService(); }
  // client-side tree mutation helpers (find/add/rename/remove), then POST whole tree
  function findNode(id, n) { n = n || S.state.structure; if (n.id === id) return n; for (const c of n.children || []) { const h = findNode(id, c); if (h) return h; } return null; }
  function nextId(t) { S.state.structure._seq = (S.state.structure._seq || 0) + 1; return t.slice(0, 3) + S.state.structure._seq; }
  async function saveTree() {
    try { const r = await jpost(api("/" + S.slug + "/structure"), { structure: S.state.structure }); S.state.rooms = r.rooms; renderTree(); }
    catch (e) { toast("Save failed: " + briefErr(e)); }
  }
  function addNode(parentId, type) {
    const name = prompt(`New ${type} name:`, type.charAt(0).toUpperCase() + type.slice(1)); if (!name) return;
    const p = findNode(parentId); if (!p) return;
    (p.children = p.children || []).push({ id: nextId(type), type, name: name.trim(), children: [] });
    saveTree();
  }
  function renameNode(id) { const n = findNode(id); if (!n) return; const name = prompt("Rename:", n.name); if (!name) return; n.name = name.trim(); saveTree(); }
  function delNode(id) {
    if (!confirm("Delete this and everything under it?")) return;
    (function rm(parent) { parent.children = (parent.children || []).filter((c) => c.id !== id); parent.children.forEach(rm); })(S.state.structure);
    saveTree();
  }
  async function rebuildStructure() {
    if (!confirm("Rebuild the structure? This lets you pick a different shape "
      + "(hotel / mall / hospital / custom) or re-import from a tracker. "
      + "BOQ, activities, mapping, rates and stock links are kept — but ALL "
      + "recorded progress (every item, every service, every room) is "
      + "cleared, since it was measured against the room structure you're "
      + "replacing and would be meaningless against the new one.")) return;
    try { await jpost(api("/" + S.slug + "/structure/reset"), {}); }
    catch (e) { return toast("Reset failed: " + briefErr(e)); }
    S.room = null; S.roomName = null;
    await reloadStateQuiet();
    if (S.state) S.state.structure = null;
    renderSetup();
    toast("Structure cleared — pick a shape below to rebuild.");
  }

  // ---------- pills ----------
  function renderPills() {
    const ov = `<button class="sp-pill ${S.service === "__overall__" ? "on" : ""}" data-s="__overall__">◒ Overall</button>`;
    $("sp-pills").innerHTML = ov + (S.state.services || []).map((s) => `<button class="sp-pill ${s === S.service ? "on" : ""}" data-s="${esc(s)}">${esc(s)}</button>`).join("");
    $("sp-pills").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => { S.service = b.dataset.s; S.room = null; S.roomName = null; S.openActivity = null; loadService(); }));
  }

  // ---------- overall (whole-site) rollup ----------
  // Mockup 1: one ring (₹-value-weighted, matches the "% of value" figure
  // that used to sit as a second, disagreeing number under Work done -- that
  // subtitle is gone now, the ring IS that number), a 4th "Rooms — whole
  // site" stat, and the by-service list is now a 3-level accordion
  // (Service -> Activity -> Item). Activities/items are fetched lazily from
  // the existing /service/{x} endpoint the first time a service is opened
  // (cached in S._ovCache) rather than fattening /overall's payload.
  async function renderOverall() {
    const sp = ensureSection();
    sp.innerHTML = `${projBar()}<div class="sp-empty">Loading overall…</div>`;
    let o; try { o = await jget(api("/" + S.slug + "/overall")); } catch (e) { sp.innerHTML = `${projBar()}<div class="sp-empty">${esc(briefErr(e))}</div>`; return; }
    S._ovCache = {};
    const rs = o.rooms_summary || { done: 0, in_progress: 0, not_started: 0, total: 0 };
    const svcRows = Object.entries(o.by_service).map(([s, v]) =>
      `<div class="sp-card" data-ovsvc="${esc(s)}">
        <div class="sp-ovrow">
          <i class="sp-ovchev">▶</i>
          <div class="sp-ovname">${esc(s)}<div class="s">${v.items} items</div></div>
          <div class="sp-ovstat pct">${Math.round(v.pct)}%<span>complete</span></div>
          <div class="sp-ovstat done">${inr(v.done_value)}<span>done</span></div>
          <div class="sp-ovstat rem">${inr(v.remaining_value)}<span>remaining</span></div>
        </div>
        <div class="sp-cbody"><div class="sp-ovacts" data-ovacts="${esc(s)}"></div></div>
      </div>`).join("");
    sp.innerHTML = `${projBar()}
      <div style="max-width:1560px;margin:0 auto">
        <div class="sp-pills" id="sp-pills"></div>
        <div class="sp-hero">
          <div class="sp-ring" id="sp-oring"><i><b>${Math.round(o.pct_value_done)}%</b><span>value complete</span></i></div>
          <div class="sp-stats four">
            <div class="sp-stat"><p class="l">Work done</p><div class="v g">${inr(o.done_value)}</div><p class="h">of ${inr(o.planned_value)} planned</p></div>
            <div class="sp-stat"><p class="l">Remaining</p><div class="v a">${inr(o.remaining_value)}</div><p class="h">to finish planned work</p></div>
            <div class="sp-stat"><p class="l">Material waste</p><div class="v r">${inr(o.waste_value)}</div><p class="h">${o.waste_caveat ? esc(o.waste_caveat) : (o.waste_value ? "over-consumed vs work done" : "link stock to measure")}</p></div>
            <div class="sp-stat sp-statdiv"><p class="l">Rooms — whole site</p><div class="v">${rs.done} <span style="font-size:13px;font-weight:400;color:var(--ink3)">done</span> · ${rs.in_progress} <span style="font-size:13px;font-weight:400;color:var(--ink3)">in progress</span></div><p class="h">of ${rs.total} rooms</p></div>
          </div>
        </div>
        <div class="sp-secttl"><span>By service — tap to see activities, tap an activity to see items</span></div>
        <div id="sp-ovlist">${svcRows}</div>
      </div>`;
    $("sp-proj").onchange = (e) => { S.slug = e.target.value; S.service = null; S.room = null; loadState(); };
    renderPills();
    $("sp-oring").style.setProperty("--p", (o.pct_value_done || 0).toFixed(1));
    $("sp-ovlist").querySelectorAll(".sp-ovrow").forEach((row) =>
      row.addEventListener("click", () => toggleOvService(row.closest(".sp-card"))));
  }

  async function toggleOvService(card) {
    const svc = card.dataset.ovsvc;
    const wasOpen = card.classList.contains("open");
    $("sp-ovlist").querySelectorAll(".sp-card.open").forEach((c) => c.classList.remove("open"));
    if (wasOpen) return;
    card.classList.add("open");
    const acts = card.querySelector(`[data-ovacts="${cssA(svc)}"]`);
    if (!S._ovCache[svc]) {
      acts.innerHTML = `<div class="sp-empty" style="padding:14px 2px;font-size:12.5px">Loading…</div>`;
      try { S._ovCache[svc] = await jget(api("/" + S.slug + "/service/" + encodeURIComponent(svc))); }
      catch (e) { acts.innerHTML = `<div class="sp-empty" style="padding:14px 2px;font-size:12.5px">${esc(briefErr(e))}</div>`; return; }
    }
    renderOvActivities(acts, S._ovCache[svc]);
  }

  function renderOvActivities(container, data) {
    const byCodeOv = Object.fromEntries((data.items || []).map((i) => [i.code, i]));
    const acts = (data.activities || []).map((a) => {
      const items = (data.mapping[a] || []).map((c) => byCodeOv[c]).filter((i) => i && (i.qty > 0 || i.quick));
      return { a, items };
    }).filter((x) => x.items.length);
    if (!acts.length) {
      container.innerHTML = `<div class="sp-empty" style="padding:14px 2px;font-size:12.5px">No activities with items yet.</div>`;
      return;
    }
    container.innerHTML = acts.map(({ a, items }) => {
      // combine qty/unit across the activity's items only when they actually
      // share one unit -- a BOQ activity routinely mixes MTR pipe with NOS
      // fittings/bends (e.g. Wall Piping = pipe in MTR + bends in NOS), and
      // silently adding "37 MTR + 18 NOS = 55" and labelling it "MTR" is a
      // wrong number, not a rounding nuance. When units differ, just say how
      // many items -- the ₹ done/remaining columns (unit-agnostic) still
      // carry the real combined signal.
      const units = new Set(items.map((i) => i.unit));
      const sameUnit = units.size === 1;
      const actSub = sameUnit
        ? `${items.length} items · <b class="sp-qtynum">${qf(items.reduce((s, i) => s + (i.used || 0), 0))}</b> of <b class="sp-qtynum">${qf(items.reduce((s, i) => s + (i.planned || 0), 0))}</b> ${esc(items[0].unit)}`
        : `${items.length} items`;
      const rated = items.filter((i) => i.rate != null);
      const done = rated.length ? rated.reduce((s, i) => s + (i.done_val || 0), 0) : null;
      const rem = rated.length ? rated.reduce((s, i) => s + (i.rem_val || 0), 0) : null;
      return `<div class="sp-ovact" data-ovact="${esc(a)}">
        <div class="sp-ovactrow">
          <i class="sp-ovachev">▶</i>
          <div style="min-width:0;flex:1"><div class="sp-ovactname">${esc(a)}</div><div class="sp-ovactsub">${actSub}</div></div>
          <div class="sp-ovactmoney done"><b>${inr(done)}</b><span>done</span></div>
          <div class="sp-ovactmoney rem"><b>${inr(rem)}</b><span>remaining</span></div>
        </div>
        <div class="sp-ovitems">${items.map(ovItemRowHTML).join("")}</div>
      </div>`;
    }).join("");
    container.querySelectorAll(".sp-ovactrow").forEach((row) => row.addEventListener("click", () => {
      const act = row.closest(".sp-ovact");
      const wasOpen = act.classList.contains("open");
      container.querySelectorAll(".sp-ovact.open").forEach((x) => x.classList.remove("open"));
      if (!wasOpen) act.classList.add("open");
    }));
  }

  function ovItemRowHTML(it) {
    const rem = it.remaining || 0;
    const sub = rem > 0
      ? `<b class="sp-qtynum">${qf(it.used)}</b> of <b class="sp-qtynum">${qf(it.planned)}</b> ${esc(it.unit)} done, <b class="sp-qtynum">${qf(rem)}</b> remaining`
      : `<b class="sp-qtynum">${qf(it.used)}</b> of <b class="sp-qtynum">${qf(it.planned)}</b> ${esc(it.unit)} done`;
    return `<div class="sp-ovitemrow">
      <div style="min-width:0;flex:1"><div class="sp-ovitemname">${esc(short(it.desc))}</div><div class="sp-ovitemsub">${esc(it.code)} · ${sub}</div></div>
      <div class="sp-ovitemmoney done"><b>${inr(it.done_val)}</b><span>done</span></div>
      <div class="sp-ovitemmoney rem"><b>${inr(it.rem_val)}</b><span>remaining</span></div>
    </div>`;
  }

  // ---------- activities + items (calm style) ----------
  function byCode() { return (S._byCode = Object.fromEntries(S.svc.items.map((i) => [i.code, i]))); }
  function realOf(code) { if (!S.real) return null; return (S.real.items || []).find((x) => x.item_code === code) || null; }
  // qty>0 hides labour-only BOQ lines with no material; a quick item always
  // has real material (it was picked from the stock register), it just has
  // no "per room" qty of its own -- its quantity lives in the planned
  // override instead -- so it must never be filtered out by this check.
  function itemsFor(a) { const bc = S._byCode; return (S.svc.mapping[a] || []).map((c) => bc[c]).filter(Boolean).filter((i) => i.qty > 0 || i.quick); }
  function actMoney(a) { let d = 0, r = false; (S.svc.mapping[a] || []).forEach((c) => { const it = S._byCode[c]; if (it && it.rate != null) { d += it.done_val || 0; r = true; } }); return r ? d : null; }

  function renderActs() {
    byCode();
    const acts = S.svc.activities || [];
    if (!acts.length) {
      $("sp-acts").innerHTML = `<div class="sp-empty">No activities yet.<br><br>
        <button class="btn primary" id="sp-firstact">+ Create your first activity</button>
        <p class="sub" style="margin-top:14px">Create an activity (e.g. “Wall Piping”), then add the BOQ items it consumes.</p></div>`;
      $("sp-firstact").onclick = () => newActivity();
      return;
    }
    $("sp-acts").innerHTML = acts.map((a) => {
      const items = itemsFor(a), p = S.svc.act_pct[a], money = actMoney(a), labour = (S.svc.mapping[a] || []).length === 0;
      return `<div class="sp-card" data-a="${esc(a)}">
        <div class="sp-chd">
          <span class="sp-cchev">▶</span>
          <div class="sp-cname">${esc(a)}<div class="s">${labour ? "no items yet — add BOQ items" : items.length + " BOQ items"}</div></div>
          <div class="sp-cbar"><div class="t"><div class="f" data-fill="${esc(a)}" style="width:${p || 0}%"></div></div>
            <div class="l"><span>progress</span><span data-apct="${esc(a)}">${p == null ? "—" : Math.round(p) + "%"}</span></div></div>
          <div class="sp-cmoney"><b data-adone="${esc(a)}">${money == null ? "—" : inr(money)}</b><span>done</span></div>
          <span class="sp-treeact" style="opacity:1;flex:none"><button class="sp-ic" data-actedit="${esc(a)}" title="Rename">✎</button><button class="sp-ic" data-actdel="${esc(a)}" title="Delete">×</button></span>
        </div>
        <div class="sp-cbody">
          ${items.map((it) => rowHTML(it, a)).join("")}
          <div style="padding-top:12px"><button class="btn" data-map="${esc(a)}">+ Add BOQ items</button>
            <button class="btn" data-quickadd="${esc(a)}" style="margin-left:8px">+ Add item</button></div>
        </div>
      </div>`;
    }).join("");
    $("sp-acts").querySelectorAll(".sp-chd").forEach((h) => h.addEventListener("click", (e) => {
      if (e.target.closest(".sp-treeact")) return;
      const card = h.parentElement;
      const wasOpen = card.classList.contains("open");
      // accordion: opening one closes the others, matching the single .open
      // card this UI has always visually supported
      $("sp-acts").querySelectorAll(".sp-card.open").forEach((c) => c.classList.remove("open"));
      if (!wasOpen) { card.classList.add("open"); S.openActivity = card.dataset.a; }
      else S.openActivity = null;
    }));
    // restore whichever activity the engineer actually had open across this
    // re-render (typing a quantity, removing an item, etc. all re-render the
    // whole list) -- fall back to the first activity with items only on a
    // fresh load, when nothing has been opened yet.
    const acts_els = [...$("sp-acts").querySelectorAll(".sp-card")];
    let toOpen = S.openActivity ? acts_els.find((c) => c.dataset.a === S.openActivity) : null;
    if (!toOpen) toOpen = acts_els.find((c) => c.querySelector(".sp-brow")) || acts_els[0];
    if (toOpen) { toOpen.classList.add("open"); S.openActivity = toOpen.dataset.a; }
    $("sp-acts").querySelectorAll(".sp-bslide").forEach(bindSlider);
    $("sp-acts").querySelectorAll(".sp-qty").forEach(bindEntry);
    $("sp-acts").querySelectorAll("[data-map]").forEach((b) => b.addEventListener("click", () => openMapModal(b.dataset.map)));
    $("sp-acts").querySelectorAll("[data-quickadd]").forEach((b) => b.addEventListener("click", () => openQuickAddModal(b.dataset.quickadd)));
    $("sp-acts").querySelectorAll("[data-actedit]").forEach((b) => b.addEventListener("click", (e) => { e.stopPropagation(); renameActivity(b.dataset.actedit); }));
    $("sp-acts").querySelectorAll("[data-actdel]").forEach((b) => b.addEventListener("click", (e) => { e.stopPropagation(); deleteActivity(b.dataset.actdel); }));
    $("sp-acts").querySelectorAll(".sp-setrate").forEach((b) => b.addEventListener("click", () => setRate(b.dataset.rate)));
    $("sp-acts").querySelectorAll(".sp-planned").forEach((b) => b.addEventListener("click", () =>
      S.room ? editPlannedFromRoom(b.dataset.planned) : editPlanned(b.dataset.planned)));
    $("sp-acts").querySelectorAll("[data-linkedit]").forEach((b) => b.addEventListener("click", () => editItemLink(b.dataset.linkedit)));
    $("sp-acts").querySelectorAll("[data-roomsedit]").forEach((b) => b.addEventListener("click", () => openRoomsModal(b.dataset.roomsedit)));
    $("sp-acts").querySelectorAll("[data-removeitem]").forEach((b) => b.addEventListener("click", () => removeItemFromActivity(b.dataset.removeitem, b.dataset.removeact)));
    $("sp-acts").querySelectorAll("[data-fx]").forEach((b) => b.addEventListener("click", () => openDrawer(b.dataset.fx)));
  }
  function plannedControlHTML(it) {
    // Planned qty is always whole-project (auto = qty_per_room × applicable
    // rooms, or a manual override) -- there is no per-room planned in this
    // data model. Markup is back to exactly the original, plain look --
    // "{qty} ✎" + "{unit} planned[ here]" -- no extra tag, no extra wording.
    // The only real difference from the original is invisible: the click
    // handler (see rowHTML's binding below) routes through
    // editPlannedFromRoom() while a room is selected, which switches to the
    // whole-project view before opening the edit prompt, so whatever gets
    // typed is unambiguously the real whole-project number and can never
    // again silently overwrite it with a single room's share.
    return `<button class="sp-planned" data-planned="${esc(it.code)}" title="Edit planned">${qf(it.planned)} ✎</button><span>${esc(it.unit)} planned${S.room ? " here" : ""}</span>`;
  }

  function rowHTML(it, activity) {
    const rl = realOf(it.code); const alert = rl && rl.verdict === "SHORTAGE";
    const links = rl && rl.links ? rl.links : [];
    const cpd = links.reduce((s, L) => s + (L.rate_per_day || 0), 0);
    const cons = cpd > 0 ? `<span class="sp-tag" title="consumption per day from the register">≈${Math.round(cpd)}/day</span>` : "";
    const linked = links.length > 0;
    const qtyVal = Math.round((it.used || 0) * 100) / 100;
    const roomBadge = S.room ? `<span class="sp-tag" style="color:var(--violet)" title="This row shows only ${esc(S.roomName || "this room")}'s own progress, not the whole project's">${esc(S.roomName || "this room")} only</span>` : "";
    return `<div class="sp-brow" data-code="${esc(it.code)}">
      <div class="sp-bname"><span class="code">${esc(it.code)}</span>${esc(short(it.desc))}
        <div class="sp-bmeta"><span class="sp-tag">${esc(it.sub)}</span>${cons}${roomBadge}
          ${it.quick ? `<span class="sp-tag" title="added straight from the stock register, not the BOQ file">from stock</span>` : ""}
          ${alert ? `<span class="sp-tag rev">shortage</span>` : ""}
          <button class="sp-linkchip ${linked ? "on" : ""}" data-linkedit="${esc(it.code)}">${linked ? "🔗 linked · edit" : "＋ link stock"}</button>
          <button class="sp-linkchip" data-roomsedit="${esc(it.code)}">🏠 ${esc(roomsChipLabel(it.code))}</button></div></div>
      <div class="sp-bqty">${plannedControlHTML(it)}</div>
      <div class="sp-entry">
        <div class="sp-entrymain">
          <input class="sp-qty" type="number" min="0" step="any" inputmode="decimal"
                 value="${qtyVal}" data-qty="${esc(it.code)}" aria-label="Installed quantity">
          <span class="sp-entryunit">${esc(it.unit)}</span>
          <span class="sp-entrysaved" data-saved="${esc(it.code)}">✓ saved</span>
        </div>
        <div class="sp-entrysub">
          <input class="sp-bslide" type="range" min="0" max="100" value="${Math.round(it.pct || 0)}" data-slide="${esc(it.code)}" aria-label="Quick drag (rough)">
          <span class="sp-bpct" data-bpct="${esc(it.code)}">${Math.round(it.pct || 0)}%</span>
        </div>
      </div>
      <div class="sp-bmoney" data-money="${esc(it.code)}">${it.rate == null ? `<button class="sp-setrate" data-rate="${esc(it.code)}">set rate</button>` : `<b>${inr(it.done_val)}</b>`}</div>
      <button class="sp-fx" data-removeitem="${esc(it.code)}" data-removeact="${esc(activity)}" title="Remove from this activity">×</button>
      <button class="sp-fx ${alert ? "alert" : ""}" data-fx="${esc(it.code)}">${alert ? "!" : "↗"}</button></div>`;
  }

  // ---------- rooms (typical + exceptions) ----------
  function allRoomsList() {
    const out = [];
    (function walk(node, path) {
      if (!node) return;
      if (node.type === "room") { out.push({ id: node.id, name: node.name, path: path.join(" › ") || "Rooms" }); return; }
      (node.children || []).forEach((c) => walk(c, node.type === "project" ? path : path.concat(node.name)));
    })(S.state.structure, []);
    return out;
  }
  function roomsChipLabel(code) {
    const ids = (S.svc.item_rooms || {})[code];
    const groups = (S.svc.item_room_qty || {})[code];
    const total = allRoomsList().length;
    if (groups && groups.length) {
      const covered = groups.reduce((s, g) => s + g.rooms.length, 0);
      return groups.length === 1
        ? `${covered} rooms @ ${qf(groups[0].qty)} ${esc(S._byCode[code] ? S._byCode[code].unit : "")}`.trim()
        : `${covered} of ${total} rooms · ${groups.length} qty groups`;
    }
    if (!ids || !ids.length) return `all ${total} rooms`;
    return `${ids.length} of ${total} rooms`;
  }
  function openRoomsModal(code) {
    const it = S._byCode[code];
    const rooms = allRoomsList();
    if (!rooms.length) return toast("No rooms in the structure yet.");
    const current = (S.svc.item_rooms || {})[code];
    const existingGroups = (S.svc.item_room_qty || {})[code] || [];
    // missing/empty entry means "all rooms" (typical) — that is the default state
    const checked = new Set(current && current.length ? current : rooms.map((r) => r.id));
    // if this item already uses real per-room quantities, pre-check whichever
    // rooms are NOT yet in any group (the room set most likely being edited
    // next) rather than "all rooms" -- ticking "all" on a grouped item would
    // otherwise look like it re-applies to every room uniformly, which isn't
    // what grouped items mean anymore.
    if (existingGroups.length) {
      const grouped = new Set(existingGroups.flatMap((g) => g.rooms));
      checked.clear();
      rooms.forEach((r) => { if (!grouped.has(r.id)) checked.add(r.id); });
    }

    const groups = {};
    rooms.forEach((r) => { (groups[r.path] = groups[r.path] || []).push(r); });
    const groupEntries = Object.entries(groups);

    const groupsSummary = existingGroups.length
      ? `<div class="sp-qtygroups">
          <p class="lbl">Current quantity groups</p>
          ${existingGroups.map((g, gi) => `<div class="row"><span>${g.rooms.length} room${g.rooms.length === 1 ? "" : "s"}</span><b>${qf(g.qty)} ${esc(it.unit)}</b><button type="button" class="sp-qtygroup-rm" data-rmgroup="${gi}" title="Remove this group">Remove</button></div>`).join("")}
          <p class="hint">Ticking rooms below and saving with a quantity moves them into a new group (out of whichever group they're currently in).</p>
        </div>`
      : "";

    const body = `${groupsSummary}
      <div class="sp-qtyrow">
        <label for="sp-roomqty">Quantity for the rooms ticked below</label>
        <input class="ctl" id="sp-roomqty" type="number" min="0" step="any" placeholder="e.g. 52 (optional — leave blank for plain applicability)">
        <span class="u">${esc(it.unit)}</span>
      </div>
      <div style="margin:12px 0"><button class="btn" id="sp-rooms-all">Select all (typical)</button>
      <button class="btn" id="sp-rooms-none" style="margin-left:8px">Clear all</button></div>
      ${groupEntries.map(([path, rs], gi) => `
        <div class="sp-roomgroup" data-g="${gi}" style="margin-bottom:14px">
          <div style="display:flex;align-items:center;gap:10px;margin-bottom:6px">
            <b style="font-size:12.5px">${esc(path)}</b>
            <button type="button" class="linkbtn" data-gall="${gi}" style="font-size:11px">select all</button>
            <button type="button" class="linkbtn" data-gnone="${gi}" style="font-size:11px">clear</button>
          </div>
          <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(110px,1fr));gap:5px">
          ${rs.map((r) => `<label style="display:flex;align-items:center;gap:5px;font-size:12.5px;cursor:pointer"><input type="checkbox" data-room="${esc(r.id)}" ${checked.has(r.id) ? "checked" : ""}>${esc(r.name)}</label>`).join("")}
          </div>
        </div>`).join("")}`;

    modal(`Rooms → ${esc(code)}`,
      `${esc(short(it.desc, 70))} — pick which rooms this item applies to. All rooms are ticked by default (typical); untick the exceptions (e.g. Suites where the item mix differs). To record a REAL quantity for a set of rooms (e.g. corner rooms that genuinely need more material), tick just those rooms and enter their quantity above instead.`,
      body, async () => {
        const boxes = [...document.querySelectorAll("#sp-modal input[data-room]")];
        const ids = boxes.filter((c) => c.checked).map((c) => c.dataset.room);
        const qtyVal = $("sp-roomqty").value.trim();
        if (qtyVal !== "") {
          const qty = Number(qtyVal);
          if (isNaN(qty)) return toast("Enter a number for quantity, or leave it blank.");
          if (!ids.length) return toast("Tick at least one room to assign this quantity to.");
          S.svc = await jpost(api("/" + S.slug + "/item-room-qty" + roomQ()), { service: S.service, item_code: code, rooms: ids, qty });
        } else {
          // every room checked = typical -> send [] so it keeps auto-covering
          // rooms added later too, rather than pinning today's exact id list.
          const roomsPayload = ids.length === rooms.length ? [] : ids;
          S.svc = await jpost(api("/" + S.slug + "/item-rooms" + roomQ()), { service: S.service, item_code: code, rooms: roomsPayload });
        }
        try { S.pnl = await jget(pnlUrl()); } catch (e) {}
        closeModal(); renderMain();
      }, "Save rooms", "min(600px,94vw)");

    $("sp-rooms-all").onclick = () => document.querySelectorAll("#sp-modal input[data-room]").forEach((c) => { c.checked = true; });
    $("sp-rooms-none").onclick = () => document.querySelectorAll("#sp-modal input[data-room]").forEach((c) => { c.checked = false; });
    document.querySelectorAll("[data-gall]").forEach((b) => b.addEventListener("click", () => {
      document.querySelector(`.sp-roomgroup[data-g="${cssA(b.dataset.gall)}"]`).querySelectorAll("input[data-room]").forEach((c) => { c.checked = true; });
    }));
    document.querySelectorAll("[data-gnone]").forEach((b) => b.addEventListener("click", () => {
      document.querySelector(`.sp-roomgroup[data-g="${cssA(b.dataset.gnone)}"]`).querySelectorAll("input[data-room]").forEach((c) => { c.checked = false; });
    }));
    // remove one quantity group entirely -- reuses the same backend move/
    // clear mechanism a room-tick-then-blank-qty already uses (qty:null
    // strips the given rooms out of every group), just pre-filled with
    // this WHOLE group's own room list so one click clears the group, not
    // a per-room chore.
    document.querySelectorAll("[data-rmgroup]").forEach((b) => b.addEventListener("click", async () => {
      const g = existingGroups[Number(b.dataset.rmgroup)];
      if (!g) return;
      if (!confirm(`Remove this group (${g.rooms.length} room${g.rooms.length === 1 ? "" : "s"} @ ${qf(g.qty)} ${it.unit})? Those rooms fall back to the item's normal quantity — progress already recorded for them is not affected.`)) return;
      S.svc = await jpost(api("/" + S.slug + "/item-room-qty" + roomQ()), { service: S.service, item_code: code, rooms: g.rooms, qty: null });
      try { S.pnl = await jget(pnlUrl()); } catch (e) {}
      closeModal(); renderMain();
      openRoomsModal(code);   // reopen fresh so the summary reflects the removal immediately
    }));
  }

  // activity CRUD (engineer-driven) + planned edit
  async function newActivity() {
    const name = prompt("New activity name (e.g. Wall Piping):", ""); if (!name || !name.trim()) return;
    try { S.svc = await jpost(api("/" + S.slug + "/activity" + roomQ()), { service: S.service, op: "create", name: name.trim() }); afterSvc(); }
    catch (e) { toast("Failed: " + briefErr(e)); }
  }
  async function renameActivity(a) {
    const name = prompt("Rename activity:", a); if (!name || !name.trim() || name === a) return;
    try { S.svc = await jpost(api("/" + S.slug + "/activity" + roomQ()), { service: S.service, op: "rename", name: a, new_name: name.trim() }); afterSvc(); }
    catch (e) { toast("Failed: " + briefErr(e)); }
  }
  async function deleteActivity(a) {
    if (!confirm(`Delete activity “${a}”? Its progress is removed; BOQ items stay in the BOQ.`)) return;
    try { S.svc = await jpost(api("/" + S.slug + "/activity" + roomQ()), { service: S.service, op: "delete", name: a }); afterSvc(); }
    catch (e) { toast("Failed: " + briefErr(e)); }
  }
  // remove one BOQ item from one activity's mapping (item stays in the BOQ
  // and keeps its own recorded progress -- only the activity link is cut,
  // exactly like unchecking it in "+ Add BOQ items" would, just one tap away
  // on the row itself instead of hunting through the checkbox modal)
  async function removeItemFromActivity(code, activity) {
    const it = S._byCode[code];
    if (!confirm(`Remove ${code}${it ? " (" + short(it.desc, 40) + ")" : ""} from “${activity}”? The item stays in the BOQ — add it back anytime via “+ Add BOQ items”.`)) return;
    const codes = (S.svc.mapping[activity] || []).filter((c) => c !== code);
    try {
      S.svc = await jpost(api("/" + S.slug + "/mapping" + roomQ()), { service: S.service, activity, codes });
      afterSvc();
    } catch (e) { toast("Failed: " + briefErr(e)); }
  }
  async function editPlannedFromRoom(code) {
    // Planned qty has no per-room meaning -- switch to the whole-project
    // view first (clearing the room selection, same as the tree's own
    // "clear" action) so editPlanned() below always edits the one real
    // number, never a room's share mistaken for the whole thing.
    toast("Planned qty is whole-project — switching to the full view to edit it.");
    S.room = null; S.roomName = null;
    document.querySelectorAll("#sp-tree .row.on").forEach((r) => r.classList.remove("on"));
    await loadService();
    await editPlanned(code);
  }
  async function editPlanned(code) {
    const it = S._byCode[code];
    const v = prompt(`Whole-project planned quantity for ${code} (${it.unit}) — auto = BOQ qty × every room this item covers. Edit to override, or clear the field and press OK to reset to auto:`,
      it.planned != null ? it.planned : "");
    if (v == null) return;                          // Cancel — no change
    let p;
    if (v === "") { p = null; }                      // cleared deliberately -> reset to auto
    else { p = Number(v); if (isNaN(p)) return toast("Enter a number"); }
    try { S.svc = await jpost(api("/" + S.slug + "/planned"), { service: S.service, item_code: code, planned: p }); afterSvc(); }
    catch (e) { toast("Failed: " + briefErr(e)); }
  }
  async function afterSvc() {
    try { S.pnl = await jget(pnlUrl()); } catch (e) {}
    try { S.real = await jget(api("/" + S.slug + "/realistic/" + encodeURIComponent(S.service))); } catch (e) {}
    renderMain();
  }

  function short(d, m) { d = String(d || "").replace(/^(Supply,?\s*(Installation|Insallation)[^,]*,?\s*(Testing and Commissioning|Commissioing)?[^,]*of\s*)/i, ""); m = m || 58; return d.length > m ? d.slice(0, m) + "…" : d; }

  // shared local-preview + save plumbing for BOTH controls on a row (the
  // typed qty input, tap-to-edit -- the real/primary one -- and the slider,
  // kept as a secondary rough-drag shortcut). Whichever one the engineer
  // touches, the other stays in sync and the same save call fires on commit.
  function applyLocalQty(code, qty, pct) {
    const it = S._byCode[code]; if (!it) return;
    it.pct = pct; it.used = qty; it.remaining = Math.max((it.planned || 0) - qty, 0);
    if (it.rate != null) it.done_val = it.used * it.rate;
    const pe = sel(`[data-bpct="${cssA(code)}"]`); if (pe) pe.textContent = Math.round(pct) + "%";
    const sld = sel(`[data-slide="${cssA(code)}"]`); if (sld) sld.value = Math.round(pct);
    const mo = sel(`[data-money="${cssA(code)}"]`); if (mo && it.rate != null) mo.innerHTML = `<b>${inr(it.done_val)}</b>`;
    localRoll();
  }
  function flashSaved(code) {
    const el = sel(`[data-saved="${cssA(code)}"]`); if (!el) return;
    el.classList.add("show");
    clearTimeout(el._t); el._t = setTimeout(() => el.classList.remove("show"), 1300);
  }
  async function saveFrac(code, pct) {
    try {
      await jpost(api("/" + S.slug + "/progress/item"),
        { service: S.service, item_code: code, frac: pct / 100, room: S.room || undefined });
      flashSaved(code);
      await loadService();
    } catch (e) { toast("Save failed: " + briefErr(e)); }
  }
  // primary control: type the real installed quantity, in the item's own
  // unit. % is derived from this, never the other way round -- see the
  // manual-entry-UX decision (Option A: tap the number, type, done).
  function bindEntry(inp) {
    const code = inp.dataset.qty;
    const commit = () => {
      const it = S._byCode[code]; if (!it) return;
      let v = Number(inp.value);
      if (isNaN(v) || v < 0) v = 0;
      if (it.planned > 0 && v > it.planned) v = it.planned;   // can't install more than planned
      v = Math.round(v * 100) / 100;
      inp.value = v;
      const pct = it.planned > 0 ? (v / it.planned) * 100 : 0;
      applyLocalQty(code, v, pct);
      saveFrac(code, pct);
    };
    inp.addEventListener("change", commit);
    inp.addEventListener("keydown", (e) => { if (e.key === "Enter") inp.blur(); });
  }
  // secondary control: a quick rough drag. Kept in sync with the typed qty
  // above in both directions, so neither one goes stale.
  function bindSlider(s) {
    const code = s.dataset.slide;
    s.addEventListener("input", () => {
      const it = S._byCode[code]; if (!it) return;
      const pct = +s.value;
      const qty = it.planned > 0 ? Math.round((it.planned * pct / 100) * 100) / 100 : 0;
      applyLocalQty(code, qty, pct);
      const qi = sel(`[data-qty="${cssA(code)}"]`); if (qi) qi.value = qty;
    });
    s.addEventListener("change", () => { const it = S._byCode[code]; if (it) saveFrac(code, it.pct); });
  }
  function localRoll() {
    (S.svc.activities || []).forEach((a) => { const items = itemsFor(a); if (!items.length) return;
      const p = items.reduce((s, i) => s + (i.pct || 0), 0) / items.length;
      const f = sel(`[data-fill="${cssA(a)}"]`); if (f) f.style.width = p + "%";
      const pc = sel(`[data-apct="${cssA(a)}"]`); if (pc) pc.textContent = Math.round(p) + "%";
      const m = actMoney(a); const dn = sel(`[data-adone="${cssA(a)}"]`); if (dn) dn.textContent = m == null ? "—" : inr(m); });
    let sp = 0, np = 0, done = 0, rem = 0;
    // mirror the backend: only items currently mapped to an activity count
    // toward % and ₹ — an orphaned item (its activity was deleted) keeps its
    // own progress but must not keep inflating the hero numbers.
    S.svc.items.forEach((i) => { if ((i.qty > 0 || i.quick) && i.mapped) { sp += i.pct || 0; np++; } if (i.mapped && i.rate != null) { done += i.done_val || 0; rem += i.rem_val || 0; } });
    const hp = np ? sp / np : 0; $("sp-hpct").textContent = Math.round(hp) + "%"; $("sp-ring").style.setProperty("--p", hp.toFixed(1));
    $("sp-done").textContent = inr(done); $("sp-rem").textContent = inr(rem);
  }
  function renderHero() {
    const p = S.svc.overall_pct || 0; $("sp-hpct").textContent = Math.round(p) + "%"; $("sp-ring").style.setProperty("--p", p.toFixed(1));
    const t = (S.pnl && S.pnl.project) || {}; $("sp-done").textContent = inr(t.done_value); $("sp-rem").textContent = inr(t.remaining_value);
    $("sp-doneh").textContent = t.pct_value_done != null ? Math.round(t.pct_value_done) + "% of value" : "";
    const w = S.pnl && S.pnl.waste; $("sp-waste").textContent = (w && w.available) ? inr(w.wasted_value) : "—";
    // waste is never room-scoped (the stock register has no room column, see
    // pnl route docstring) -- when a room is selected, say so plainly rather
    // than let a whole-project number sit under a room heading looking like
    // it belongs to that room.
    $("sp-wasteh").textContent = (w && w.available)
      ? (w.caveat ? w.caveat : (S.room ? "whole project — not split by room" : "over-consumed vs work done"))
      : "link stock to measure";
    // items whose activity was deleted keep their own progress (nothing is
    // erased) but no longer count toward the numbers above, since they have
    // no home in the activity list. Say so, instead of the money vanishing
    // with no trace — click "+ New activity" to re-map them.
    const uv = (S.pnl && S.pnl.unmapped_value) || (S.svc && S.svc.pnl_unmapped_value);
    const un = $("sp-unmapped");
    if (un) {
      if (uv && uv.items > 0) {
        un.hidden = false;
        un.innerHTML = `<b>${uv.items} item${uv.items === 1 ? "" : "s"}</b> not in any activity — ${inr(uv.done_value)} done / ${inr(uv.remaining_value)} remaining not counted above. Add ${uv.items === 1 ? "it" : "them"} to an activity to bring it back in.`;
      } else {
        un.hidden = true;
      }
    }
  }

  // ---------- mapping modal (configure items) ----------
  function openMapModal(a) {
    // a persistent Set, not a DOM query at Save time -- once search/filter
    // hides an item, its checkbox leaves the DOM entirely, so reading
    // "input:checked" at save time would silently drop anything ticked
    // then scrolled/filtered out of view. This Set is the single source of
    // truth regardless of what's currently rendered.
    const checked = new Set(S.svc.mapping[a] || []);
    function renderRows(filter) {
      const f = (filter || "").trim().toLowerCase();
      const items = S.svc.items.filter((it) => !f
        || it.code.toLowerCase().includes(f)
        || (it.desc || "").toLowerCase().includes(f)
        || (it.sub || "").toLowerCase().includes(f));
      const host = document.getElementById("sp-maprows");
      if (!host) return;
      host.innerHTML = items.length
        ? items.map((it) => `<label class="sp-maprow"><input type="checkbox" data-c="${esc(it.code)}" ${checked.has(it.code) ? "checked" : ""}>
            <span class="c">${esc(it.code)}</span><span>${esc(short(it.desc))}</span>
            <span class="sub" style="margin-left:auto;white-space:nowrap">${qf(it.planned)} ${esc(it.unit)}</span>
            <span class="sp-tag">${esc(it.sub)}</span></label>`).join("")
        : `<p class="sp-empty" style="padding:24px;text-align:center">No items match “${esc(filter)}”.</p>`;
      host.querySelectorAll("input[data-c]").forEach((cb) => cb.addEventListener("change", () => {
        if (cb.checked) checked.add(cb.dataset.c); else checked.delete(cb.dataset.c);
      }));
    }
    const body = `<input class="ctl" id="sp-mapsearch" type="search"
        placeholder="Search by code, description, or type…" style="width:100%;margin-bottom:12px">
      <div id="sp-maprows"></div>`;
    modal(`Add BOQ items → ${esc(a)}`,
      `Tick the BOQ items this activity installs. Planned qty (BOQ × rooms) is shown; it fills in automatically and can be edited per item afterwards.`,
      body, async () => {
      S.svc = await jpost(api("/" + S.slug + "/mapping" + roomQ()), { service: S.service, activity: a, codes: [...checked] });
      try { S.pnl = await jget(pnlUrl()); } catch (e) {}
      try { S.real = await jget(api("/" + S.slug + "/realistic/" + encodeURIComponent(S.service))); } catch (e) {}
      closeModal(); renderMain();
    }, "Save items", "min(760px,96vw)");
    renderRows("");
    document.getElementById("sp-mapsearch").addEventListener("input", (e) => renderRows(e.target.value));
  }

  // ---------- quick add: a plannable line straight from stock, no BOQ needed ----------
  async function openQuickAddModal(activity) {
    let data, widen = false;
    const fetchData = async () => {
      try { data = await jget(api("/" + S.slug + "/quick-items/" + encodeURIComponent(S.service) + "/candidates" + (widen ? "?all_services=true" : ""))); }
      catch (e) { toast(briefErr(e)); data = { available: false, materials: [] }; }
    };
    await fetchData();
    if (!data.available) return toast(data.reason || "Upload the stock register on the Forecast tab first.");

    const renderList = (filter) => {
      const f = (filter || "").trim().toLowerCase();
      const list = data.materials.filter((m) => !f || m.name.toLowerCase().includes(f)).slice(0, 200);
      if (!list.length) {
        // distinguish "0 materials for this service at all" (a real signal
        // the forecast run's service labels don't line up, see backend
        // reason) from "0 matches for what you typed" -- either way, if
        // nothing showed and we haven't widened yet, offer to
        const msg = (!data.materials.length && data.reason) ? data.reason : "No match.";
        const hint = !widen ? `<button type="button" class="linkbtn" id="sp-quickwiden" style="display:block;margin-top:8px">Search other services too</button>` : "";
        return `<p class="sub" style="padding:10px 2px">${esc(msg)}</p>${hint}`;
      }
      return list.map((m) => `<div class="sp-maprow" data-mat="${esc(m.name)}">
        <span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(m.name)}${m.other_service ? ` <span class="sub">· from ${esc(m.other_service)}</span>` : ""}</span>
        <span class="sub" style="white-space:nowrap">${esc(m.unit || "")}</span>
        <button type="button" class="linkbtn" data-quickpick="${esc(m.name)}" style="white-space:nowrap">${m.already ? "✓ added" : "+ Add"}</button>
      </div>`).join("");
    };
    const wireList = () => {
      document.querySelectorAll("[data-quickpick]").forEach((b) => b.addEventListener("click", async () => {
        // NOTE: do NOT skip on "✓ added" -- that flag is service-wide (has
        // this material ever been quick-added anywhere in this service), not
        // "is it currently mapped to THIS activity". If it was removed from
        // an activity (the row's x button) it must be re-addable here; the
        // backend's add_quick_item already reuses the same item_code and
        // safely no-ops if it's genuinely already attached to this exact
        // activity, so it's always correct to just try.
        const material = b.dataset.quickpick;
        b.disabled = true; b.textContent = "…";
        try {
          S.svc = await jpost(api("/" + S.slug + "/quick-item" + roomQ()), { service: S.service, activity, material });
          b.textContent = "✓ added"; b.disabled = false;
          const row = b.closest("[data-mat]"); if (row) row.style.opacity = ".55";
          toast(`Added "${short(material, 34)}".`);
        } catch (e) { b.disabled = false; b.textContent = "+ Add"; toast("Failed: " + briefErr(e)); }
      }));
      const wb = $("sp-quickwiden");
      if (wb) wb.addEventListener("click", async () => { widen = true; await fetchData(); refresh(); });
    };
    const refresh = () => {
      $("sp-quicklist").innerHTML = renderList($("sp-quicksearch").value);
      $("sp-quickwidewrap").innerHTML = widen
        ? `<label class="sub" style="display:flex;align-items:center;gap:6px;margin-top:8px"><input type="checkbox" id="sp-quickwidecb" checked> Also showing other services (materials tagged with where they're really stocked)</label>`
        : `<label class="sub" style="display:flex;align-items:center;gap:6px;margin-top:8px"><input type="checkbox" id="sp-quickwidecb"> Also search other services — useful when a material (like PVC piping) is stocked under a different service's tab than the one you're planning</label>`;
      const cb = $("sp-quickwidecb");
      if (cb) cb.addEventListener("change", async (e) => { widen = e.target.checked; await fetchData(); refresh(); });
      wireList();
    };

    modal(`Add item from stock → ${esc(activity)}`,
      `Pick a material straight from the ${esc(S.service)} stock register — no BOQ line needed. It's added to this activity at 0 planned quantity (set that with ✎ afterwards, like any item) and linked to that exact stock material immediately.`,
      `<input class="ctl" id="sp-quicksearch" placeholder="Search stock materials…" style="width:100%;margin-bottom:10px">
       <div id="sp-quicklist" class="sp-maplist">${renderList("")}</div>
       <div id="sp-quickwidewrap"></div>`,
      async () => { closeModal(); await afterSvc(); }, "Done", "min(680px,94vw)");
    wireList();
    refresh();
    $("sp-quicksearch").addEventListener("input", (e) => { $("sp-quicklist").innerHTML = renderList(e.target.value); wireList(); });
  }

  // ---------- bulk rates ----------
  function openRatesModal() {
    if (!S.svc || S.service === "__overall__") return toast("Open a service first.");
    const rows = S.svc.items.filter((it) => it.qty > 0 || it.quick).map((it) =>
      `<div class="sp-maprow"><span class="c">${esc(it.code)}</span>
        <span style="flex:1">${esc(short(it.desc, 46))}</span>
        <input class="ctl sp-rateinput" data-code="${esc(it.code)}" type="number" min="0" placeholder="₹ / ${esc(it.unit)}" value="${it.rate != null ? it.rate : ""}" style="width:120px">
        <span class="sub" style="width:70px;text-align:right">${qf(it.planned)} ${esc(it.unit)}</span></div>`).join("");
    modal("Set install rates (₹ per unit)",
      "Enter the ₹/unit for each BOQ item — this is what turns progress into a work-done value. Leave blank to skip; you can come back anytime.",
      rows, async () => {
        const rates = {};
        document.querySelectorAll(".sp-rateinput").forEach((i) => { if (i.value !== "") rates[i.dataset.code] = Number(i.value); });
        S.svc = await jpost(api("/" + S.slug + "/rates" + roomQ()), { service: S.service, rates });
        try { S.pnl = await jget(pnlUrl()); } catch (e) {}
        closeModal(); renderMain();
      }, "Save rates", "min(680px,96vw)");
  }

  // ---------- item master link modal ----------
  // Multi-material chips: a BOQ line often genuinely consumes more than one
  // register material (e.g. a "point" = wire + conduit + box), so each item
  // gets a small chip list (already-linked materials, each removable) plus
  // one compact input + a small "+" button (or Enter) to add another. No
  // separate multi-add UI to switch into — the same row just grows.
  async function openLinkModal(focusCode) {
    let data; try { data = await jget(api("/" + S.slug + "/links/" + encodeURIComponent(S.service))); } catch (e) { return toast(briefErr(e)); }
    if (!data.has_run) return toast("Upload the stock register on the Forecast tab first, then link.");
    const opts = data.stock_names.map((n) => `<option value="${esc(n)}">`).join("");
    const list = focusCode ? data.items.filter((i) => i.code === focusCode) : data.items;
    const matUnits = data.material_units || {};
    // per-item chip state: [{material, factor}], seeded from what's already
    // linked. This is the single source of truth the Save button reads
    // from — not the text input, not the DOM.
    const chipState = {};
    list.forEach((it) => { chipState[it.code] = (it.linked || []).map((L) => ({ material: L.material, factor: L.factor })); });

    const rows = list.map((it) => {
      const sugg = it.suggestion.best;
      const showSugg = sugg && !chipState[it.code].some((c) => c.material === sugg);
      return `<div class="sp-linkcard">
        <div class="lft"><span class="c">${esc(it.code)}</span><div class="d">${esc(short(it.desc, 54))}</div><span class="sp-tag">${esc(it.sub)}</span></div>
        <div class="rgt">
          <div class="sp-chips" data-chips="${esc(it.code)}"></div>
          <div class="sp-addrow">
            <input class="ctl sp-linkinput" data-code="${esc(it.code)}" data-unit="${esc(it.unit)}" list="sp-stocklist" placeholder="type or pick a stock material…">
            <button type="button" class="sp-addchip" data-add="${esc(it.code)}" title="Add this material">+</button>
          </div>
          ${showSugg ? `<button class="sp-suggpick" data-fill="${esc(it.code)}" data-val="${esc(sugg)}">use “${esc(short(sugg, 24))}” ${it.suggestion.confident ? "✓" : "· review"}</button>` : ""}
        </div></div>`;
    }).join("");

    modal(focusCode ? `Link stock → ${esc(focusCode)}` : "Link BOQ items → stock",
      "A BOQ item can consume more than one register material (e.g. wire + conduit). Pick or type a material, then press + (or Enter) to add it. If a material's unit doesn't match this item's own unit, tell it how much of that material one unit of this item needs (e.g. Rmt of pipe per Nos of point) — otherwise the shortage forecast can't compare them.",
      `<datalist id="sp-stocklist">${opts}</datalist><div class="sp-linkgrid">${rows}</div>`,
      async () => {
        // Commit whatever's still sitting in each text box before saving --
        // this is the actual bug: typing a material then clicking "Save
        // links" directly (without pressing + or Enter first) left the
        // typed text uncommitted, so it never made it into chipState and
        // the link silently stayed empty even though nothing looked wrong
        // on screen. Same gap when picking a name from the <datalist>
        // dropdown by mouse -- that sets the input's value but fires
        // neither a click on +  nor an Enter keydown, so it was ALSO never
        // committed. Save is now the one place that's guaranteed to catch
        // both: whatever text is left in a box gets folded in right here,
        // right before the POSTs go out, so "type it and hit Save" just
        // works the way it visually promises to.
        document.querySelectorAll(".sp-linkinput").forEach((inp) => {
          const v = inp.value.trim();
          if (v) addChip(inp.dataset.code, v);
        });
        for (const code of Object.keys(chipState)) {
          await jpost(api("/" + S.slug + "/links"), { service: S.service, item_code: code, materials: chipState[code] });
        }
        closeModal(); await loadService(); toast("Links saved.");
      }, "Save links", "min(920px,96vw)");

    function mismatch(code, material) {
      const itemUnit = (document.querySelector(`.sp-linkinput[data-code="${cssA(code)}"]`) || {}).dataset?.unit || "";
      const matUnit = matUnits[material];
      return matUnit && itemUnit && matUnit.trim().toUpperCase() !== itemUnit.trim().toUpperCase();
    }
    function renderChips(code) {
      const host = document.querySelector(`[data-chips="${cssA(code)}"]`); if (!host) return;
      host.innerHTML = chipState[code].length
        ? chipState[code].map((c, i) => {
            const needsFactor = mismatch(code, c.material);
            const matUnit = matUnits[c.material] || "?";
            const itemUnit = (list.find((it) => it.code === code) || {}).unit || "?";
            return `<span class="sp-chip${needsFactor && c.factor == null ? " warn" : ""}">
              <span class="t">${esc(short(c.material, 30))}</span>
              ${needsFactor ? `<span class="sp-chipfactor">
                  <input type="number" min="0" step="any" class="ctl sp-factorinput" data-code="${esc(code)}" data-idx="${i}"
                    value="${c.factor != null ? c.factor : ""}" placeholder="?" style="width:52px">
                  <span class="u">${esc(matUnit)} / ${esc(itemUnit)}</span></span>` : ""}
              <button type="button" data-rmchip="${esc(code)}" data-idx="${i}" title="Remove">×</button></span>`;
          }).join("")
        : `<span class="sp-chipempty">not linked yet</span>`;
      host.querySelectorAll("[data-rmchip]").forEach((b) => b.addEventListener("click", () => {
        chipState[b.dataset.rmchip].splice(Number(b.dataset.idx), 1); renderChips(b.dataset.rmchip);
      }));
      host.querySelectorAll(".sp-factorinput").forEach((inp) => inp.addEventListener("change", () => {
        const v = inp.value.trim();
        chipState[inp.dataset.code][Number(inp.dataset.idx)].factor = v === "" ? null : Number(v);
        renderChips(inp.dataset.code);   // clears the "warn" state live once a factor is entered
      }));
    }
    function addChip(code, val) {
      val = (val || "").trim(); if (!val) return;
      if (!chipState[code].some((c) => c.material === val)) chipState[code].push({ material: val, factor: null });
      renderChips(code);
      const inp = document.querySelector(`.sp-linkinput[data-code="${cssA(code)}"]`);
      if (inp) { inp.value = ""; inp.focus(); }
    }
    list.forEach((it) => renderChips(it.code));
    document.querySelectorAll(".sp-addchip").forEach((b) => b.addEventListener("click", () => {
      const inp = document.querySelector(`.sp-linkinput[data-code="${cssA(b.dataset.add)}"]`);
      addChip(b.dataset.add, inp ? inp.value : "");
    }));
    document.querySelectorAll(".sp-linkinput").forEach((inp) => inp.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); addChip(inp.dataset.code, inp.value); }
    }));
    document.querySelectorAll(".sp-suggpick").forEach((b) => b.addEventListener("click", () => {
      addChip(b.dataset.fill, b.dataset.val); b.remove();
    }));
  }
  function editItemLink(code) { openLinkModal(code); }

  // ---------- rates ----------
  async function setRate(code) {
    const it = S._byCode[code]; const v = prompt(`Install rate for ${code} (₹ per ${it.unit}):`, it.rate || ""); if (v == null || v === "") return;
    const rate = Number(v); if (isNaN(rate)) return toast("Enter a number");
    try { S.svc = await jpost(api("/" + S.slug + "/rates" + roomQ()), { service: S.service, rates: { [code]: rate } });
      try { S.pnl = await jget(pnlUrl()); } catch (e) {} renderMain(); }
    catch (e) { toast("Save failed: " + briefErr(e)); }
  }

  // ---------- drawer (realistic forecast) ----------
  // "Rooms — this item" panel (Mockup 2): buckets come from the service view
  // (it.room_done/room_progress/room_pending/room_total, always computed
  // against every one of the item's applicable rooms, never room-scoped).
  // "need ~X more" reuses it.remaining as-is -- that figure is already
  // exactly the sum of the outstanding rooms' own shortfall (a done room
  // contributes zero), so this never invents a second, different number.
  function roomsBlockHTML(it) {
    const total = it.room_total || 0;
    if (!total) return "";
    const done = it.room_done || 0, prog = it.room_progress || 0, pend = it.room_pending || 0;
    const outstanding = prog + pend;
    const w = (n) => (total ? (100 * n / total) : 0);
    const needRow = outstanding > 0
      ? `<p class="sp-roomsneed"><span>To finish those ${outstanding} room${outstanding === 1 ? "" : "s"}</span><b>≈${qf(it.remaining)} ${esc(it.unit)} more</b></p>`
      : `<p class="sp-roomsneed"><span>All ${total} room${total === 1 ? "" : "s"} done for this item</span></p>`;
    return `<div class="sp-roomsblk">
      <p class="lbl">Rooms — this item</p>
      <div class="sp-roomsbar"><span class="done" style="width:${w(done)}%"></span><span class="prog" style="width:${w(prog)}%"></span><span class="pend" style="width:${w(pend)}%"></span></div>
      <p class="sp-roomscount"><b class="done">${done} done</b> · <b class="prog">${prog} in progress</b> · <b class="pend">${pend} not started</b></p>
      ${needRow}
    </div>`;
  }

  function openDrawer(code) {
    const it = S._byCode[code]; const rl = realOf(code);
    let stockHTML = "";
    if (rl && rl.links && rl.links.length) {
      stockHTML = rl.links.map((L) => {
        const recv = L.received == null ? "" : ` · <b class="sp-recv">${qf(L.received)}</b> received to date`;
        const cons = L.rate_per_day == null ? "" : ` · ≈${qf(L.rate_per_day)}/day`;
        // Cross-check THIS material's own issued-vs-used gap -- per material,
        // never pooled, because two materials linked to the same item can
        // have completely different factors (e.g. wire at 1 Rmt/Nos, conduit
        // at 1.2 Rmt/Nos): summing their total_consumed into one number and
        // comparing it to one `used` figure would silently mix two different
        // conversions into a meaningless total. This is a READ-ONLY cross-
        // check, not a new progress input -- it never touches `used`/
        // `planned`/room taps, exactly like waste_summary() reads
        // actual_consumed without ever becoming the source of truth for %
        // complete. A real gap is informative either way -- staged material,
        // a stale progress entry, or genuine over-consumption -- so it's
        // shown plainly, not diagnosed for them.
        let issuedNote = "";
        if (L.total_consumed != null) {
          if (L.units_match || L.factor != null) {
            const eff = L.factor != null ? L.factor : 1.0;
            const expected = (it.used || 0) * eff;
            const gap = L.total_consumed - expected;
            issuedNote = gap > 0 && expected > 0 && (gap / Math.max(expected, 1)) > 0.1
              ? `<div class="sp-drow sub"><span></span><span>${qf(L.total_consumed)} ${esc(L.unit || "")} issued vs ≈${qf(expected)} expected for work marked done — staged on site, or progress needs an update</span></div>`
              : `<div class="sp-drow sub"><span></span><span>${qf(L.total_consumed)} ${esc(L.unit || "")} issued to date</span></div>`;
          } else {
            issuedNote = `<div class="sp-drow sub"><span></span><span>${qf(L.total_consumed)} ${esc(L.unit || "")} issued — unit differs from ${esc(it.unit)}; set a conversion factor in Link stock to compare against work done</span></div>`;
          }
        }
        return `<div class="sp-drow"><span>${esc(String(L.material).slice(0, 30))}</span>
          <b>${L.on_hand == null ? "—" : qf(L.on_hand)} on hand · ${L.engine_days_left == null ? "?" : Math.round(L.engine_days_left) + "d"}${cons} ${L.verdict === "SHORTAGE" ? "⚠︎" : L.verdict === "UNKNOWN_FACTOR" ? "❓" : ""}</b></div>
          ${issuedNote}
          ${recv ? `<div class="sp-drow sub"><span></span><span>${esc(String(L.material).slice(0, 22))}${recv}</span></div>` : ""}`;
      }).join("");
    }
    const verdictCls = rl && rl.verdict === "SHORTAGE" ? "rev" : rl && rl.verdict === "UNKNOWN_FACTOR" ? "rev" : "ok";
    const msg = rl ? rl.message : "Link this item to stock (Link stock button) to forecast it.";
    // planned label: whichever room the engineer is actually looking at, not
    // always the whole project's room count -- the underlying number (it.planned)
    // already comes room-scoped from the service view when S.room is set;
    // this was previously mislabeled "(108 rooms)" even inside a room drill-down.
    const plannedLabel = S.room ? `Planned (${esc(S.roomName || "this room")})` : `Planned (${S.state.rooms} rooms)`;
    const html = `<div class="sheet" id="sp-draw"><div class="sheetin">
      <div class="sheethd"><div><h2>${esc(it.code)} · ${esc(it.sub)}</h2><p>${esc(short(it.desc, 120))}</p></div><button class="btn" id="sp-draw-x">Close</button></div>
      <div class="sp-drow"><span>${plannedLabel}</span><b>${qf(it.planned)} ${esc(it.unit)}</b></div>
      <div class="sp-drow"><span>Used so far</span><b>${qf(it.used)} ${esc(it.unit)}</b></div>
      <div class="sp-drow"><span>Remaining work</span><b>${qf(it.remaining)} ${esc(it.unit)}</b></div>
      ${roomsBlockHTML(it)}
      <div class="sp-drow"><span>Install rate</span><b>${it.rate != null ? inr(it.rate) + " /" + esc(it.unit) : "not set"}</b></div>
      <div class="sp-dbar"><i style="width:${Math.round(it.pct || 0)}%"></i></div>
      <div style="text-align:right;font-size:11.5px;color:var(--ink3)">${Math.round(it.pct || 0)}% complete</div>
      ${stockHTML ? `<p class="sub" style="margin:16px 0 4px">Linked stock</p>${stockHTML}` : ""}
      <div class="sp-dlink ${verdictCls}">${esc(msg)}${rl && rl.verdict === "SHORTAGE" ? ` <b>Order ~${qf(rl.order_qty)} ${esc(it.unit)}.</b>` : ""}</div>
      <p class="sp-demo">Rates are user-entered. Stock figures (on hand, received, consumption/day) come read-only from the Forecast run.</p></div></div>`;
    const d = document.createElement("div"); d.innerHTML = html; document.body.appendChild(d.firstChild);
    const close = () => { const x = $("sp-draw"); if (x) x.remove(); };
    $("sp-draw-x").onclick = close; $("sp-draw").addEventListener("click", (e) => { if (e.target.id === "sp-draw") close(); });
  }

  // ---------- generic modal ----------
  function modal(title, sub, bodyHTML, onSave, saveLabel, width) {
    closeModal();
    const w = width || "min(600px,94vw)";
    const html = `<div class="sheet mid" id="sp-modal"><div class="card wide" style="width:${w}">
      <div class="sheethd"><div><h2>${title}</h2><p>${sub}</p></div><button class="btn" id="sp-modal-x">Close</button></div>
      <div class="sp-maplist">${bodyHTML}</div>
      <div class="mact"><button class="btn" id="sp-modal-cancel">Cancel</button><button class="btn primary" id="sp-modal-save">${saveLabel || "Save"}</button></div></div></div>`;
    const d = document.createElement("div"); d.innerHTML = html; document.body.appendChild(d.firstChild);
    $("sp-modal-x").onclick = closeModal; $("sp-modal-cancel").onclick = closeModal;
    $("sp-modal").addEventListener("click", (e) => { if (e.target.id === "sp-modal") closeModal(); });
    $("sp-modal-save").onclick = async () => { try { await onSave(); } catch (e) { toast("Failed: " + briefErr(e)); } };
  }
  function closeModal() { const m = $("sp-modal"); if (m) m.remove(); }

  // ---------- BOQ-upload wizard: "per room" vs "already the total" ----------
  // Only shown for services the upload flagged as needs_qty_mode -- a
  // ProjectBase-sourced service never reaches here at all (its Design
  // Quantity convention is already known and auto-seeded on upload); this
  // is only for a raw/MEPF sheet (or a merge of raw + ProjectBase sheets)
  // where the qty column's convention is genuinely ambiguous from the data
  // alone. One choice per flagged service, asked once, right after upload.
  function showQtyModeWizard(services) {
    if (!services || !services.length) return;
    const leaf = leafLabel((S.state.structure || {}).kind);
    const body = services.map((svc, i) => `
      <div style="padding:14px 0;border-bottom:1px solid var(--line2)">
        <b style="font-size:13.5px">${esc(svc)}</b>
        <p style="font-size:12px;color:var(--ink3);margin:3px 0 10px">Does this sheet's quantity repeat for every ${leaf.toLowerCase()}, or is it already the total for the whole project?</p>
        <label style="display:flex;align-items:center;gap:8px;font-size:13px;margin-bottom:6px;cursor:pointer">
          <input type="radio" name="qm-${i}" value="per_room" checked> Repeats per ${leaf.toLowerCase()} — multiply by every ${leaf.toLowerCase()} it applies to
        </label>
        <label style="display:flex;align-items:center;gap:8px;font-size:13px;cursor:pointer">
          <input type="radio" name="qm-${i}" value="total"> Already the whole-project total — use it as-is
        </label>
      </div>`).join("");
    modal("One-time setup", "This decides how each item's planned quantity is calculated — get it right once and every ₹ figure downstream follows automatically.",
      body, async () => {
        for (let i = 0; i < services.length; i++) {
          const mode = document.querySelector(`input[name="qm-${i}"]:checked`).value;
          if (mode === "total") {
            await jpost(api("/" + S.slug + "/qty-mode"), { service: services[i], mode: "total" });
          }
        }
        closeModal(); toast("Saved"); await reloadStateQuiet(); refreshOpen();
      }, "Save", "min(520px,94vw)");
  }


  // ---------- utils ----------
  function cssA(s) { return String(s).replace(/"/g, '\\"'); }
  function toast(msg) {
    let t = $("sp-toast");
    if (!t) { t = document.createElement("div"); t.id = "sp-toast"; t.style.cssText = "position:fixed;bottom:20px;left:50%;transform:translateX(-50%);background:var(--ink);color:#fff;padding:10px 16px;border-radius:10px;font-size:13px;z-index:60;box-shadow:0 4px 16px rgba(0,0,0,.2);max-width:90vw"; document.body.appendChild(t); }
    t.textContent = msg; t.style.opacity = "1"; clearTimeout(t._h); t._h = setTimeout(() => { t.style.opacity = "0"; }, 3200);
  }

  function boot() { injectNav(); ensureSection(); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();
})();
