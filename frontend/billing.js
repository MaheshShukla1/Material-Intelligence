/* Billing (RA bills) — additive front-end module.
 *
 * Loaded AFTER siteprogress.js. Self-contained IIFE, owns its own #billing
 * section, talks only to /api/billing/*. Every class is bl- prefixed so the
 * existing tabs are never restyled. siteprogress.js only adds the "Billing"
 * nav button and shows/hides this section (setView).
 *
 * Screens: overview (RA list) · RA bill (draft / locked) · Billing setup · Overall annexure (all Work Orders of a service).
 * The bill itself is computed on the server; this file only renders it and
 * sends the engineer's actions (new RA, typed qty, lock, reopen).
 */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const api = (p) => "/api/billing" + p;

  async function jget(u) {
    const r = await fetch(u);
    if (!r.ok) throw Object.assign(new Error("http"), { status: r.status, body: await r.text() });
    return r.json();
  }
  async function jsend(u, b, method) {
    const r = await fetch(u, { method: method || "POST", headers: { "Content-Type": "application/json" },
      body: b === undefined ? undefined : JSON.stringify(b) });
    if (!r.ok) throw Object.assign(new Error("http"), { status: r.status, body: await r.text() });
    return r.json();
  }
  const briefErr = (e) => { try { return JSON.parse(e.body).detail || e.body; } catch (_) { return e.body || e.message || "error"; } };

  // Indian digit grouping, always the full number (a bill is read to the rupee).
  const inr = (v, dec) => {
    if (v == null) return "—";
    const n = Number(v);
    return (n < 0 ? "−" : "") + "₹ " + Math.abs(n).toLocaleString("en-IN",
      { minimumFractionDigits: dec ? 2 : 0, maximumFractionDigits: dec ? 2 : 0 });
  };
  const qn = (v) => v == null ? "—" : (Math.round(v * 1000) / 1000).toLocaleString("en-IN");
  const SERIES = { supply: "Supply", install: "Installation", together: "Supply + Installation" };
  const STAGE = { supply: "Supply", install: "Installation" };
  const SOURCE = { progress: ["Site Progress", ""], stock: ["Stock register", ""], challan: ["Challans", ""],
    manual: ["Type qty", "warn"], "manual-entered": ["Typed", "typed"] };
  const today = () => { const d = new Date(); d.setMinutes(d.getMinutes() - d.getTimezoneOffset()); return d.toISOString().slice(0, 10); };
  const raL = (n) => "RA-" + String(n).padStart(2, "0");
  // a long list of blank details, cut after n with "+N more" (the full list is in the form itself)
  const brief = (list, n) => list.length > (n || 4) + 1 ? list.slice(0, n || 4).join(", ") + ` … +${list.length - (n || 4)} more` : list.join(", ");

  const B = { slug: null, projects: [], view: "home", ov: null, bill: null, raId: null, setup: null, expanded: new Set(), seq: 0, live: null };
  const OV = { base: null, data: null, drafts: true, all: false };      // overall annexure screen
  // a Work Order is its own billing unit "Service › Name"; a plain service is its own unit
  const WOSEP = " \u203a ";
  const unitBase = (u) => String(u).split(WOSEP)[0];
  const unitName = (u) => (String(u).indexOf(WOSEP) >= 0 ? String(u).split(WOSEP)[1] : "");

  function section() {
    let s = $("billing");
    if (!s) {
      s = document.createElement("section");
      s.id = "billing"; s.className = "bl"; s.hidden = true;
      const m = document.querySelector("main"); if (m) m.appendChild(s);
    }
    return s;
  }
  function toast(msg) {
    let t = $("bl-toast");
    if (!t) { t = document.createElement("div"); t.id = "bl-toast"; t.className = "bl-toast"; document.body.appendChild(t); }
    t.textContent = msg; t.classList.add("on");
    clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.remove("on"), 4200);
  }

  // ----------------------------------------------------------------- modal
  function closeModal() { const m = $("bl-modal"); if (m) m.remove(); }
  function modal(title, sub, body, buttons, width) {
    closeModal();
    const d = document.createElement("div");
    d.innerHTML = `<div class="sheet mid" id="bl-modal"><div class="card wide" style="width:${width || "min(560px,94vw)"}">
      <div class="sheethd"><div><h2>${esc(title)}</h2>${sub ? `<p>${esc(sub)}</p>` : ""}</div><button class="btn" id="bl-mx">Close</button></div>
      <div class="bl-mbody">${body}</div>
      <div class="mact">${(buttons || []).map((b, i) => `<button class="btn ${b.cls || ""}" data-b="${i}"${b.id ? ` id="${b.id}"` : ""}>${esc(b.label)}</button>`).join("")}</div>
      </div></div>`;
    document.body.appendChild(d.firstChild);
    $("bl-mx").onclick = closeModal;
    $("bl-modal").addEventListener("click", (e) => { if (e.target.id === "bl-modal") closeModal(); });
    document.querySelectorAll("#bl-modal .mact [data-b]").forEach((el) => {
      el.onclick = async () => {
        try { await buttons[+el.dataset.b].run(); } catch (e) { toast(briefErr(e)); }
      };
    });
  }

  // ------------------------------------------------------------------ open
  async function open() {
    const s = section();
    let projects = [];
    try { projects = await jget("/api/projects"); } catch (e) {}
    B.projects = projects;
    if (!projects.length) {
      s.innerHTML = `<div class="bl-empty">No project yet. Upload a stock register on the <b>Forecast</b> tab, then set up Site Progress, then bill here.</div>`;
      return;
    }
    if (!B.slug || !projects.find((p) => p.slug === B.slug)) {
      const fromF = (typeof META !== "undefined" && META && META.project_slug);
      B.slug = (fromF && projects.find((p) => p.slug === fromF)) ? fromF : projects[0].slug;
    }
    B.view = "home";
    await showHome();
  }

  function head(title, sub, right) {
    return `<div class="bl-head"><div><div class="bl-crumb"><select id="bl-proj" class="ctl">${B.projects.map((p) =>
      `<option value="${esc(p.slug)}" ${p.slug === B.slug ? "selected" : ""}>${esc(p.project)}</option>`).join("")}</select></div>
      <h2>${esc(title)}</h2>${sub ? `<p class="bl-sub">${esc(sub)}</p>` : ""}</div>
      <div class="bl-headact">${right || ""}</div></div>`;
  }
  function wireProj() {
    const p = $("bl-proj");
    if (p) p.onchange = () => { B.slug = p.value; B.expanded = new Set(); showHome(); };
  }

  // ------------------------------------------------------------- overview
  // A draft is computed from Site Progress every time it is opened, so it is always current when opened. This keeps an
  // already-open Billing screen current too (an engineer logs work on a phone while the manager has Billing open on a
  // desktop): every minute, only when the tab is visible, no dialog is open and nobody is typing.
  const LIVE_MS = window.__BL_LIVE_MS || 60000;
  function startLive() {
    if (B.live) return;
    B.live = setInterval(async () => {
      const sec = $("billing");
      if (!sec || sec.hidden || document.hidden || $("bl-modal")) return;
      const a = document.activeElement;
      if (a && sec.contains(a) && /^(INPUT|SELECT|TEXTAREA)$/.test(a.tagName)) return;
      const y = window.scrollY;
      if (B.view === "home") await showHome();
      else if (B.view === "ra" && B.bill && B.bill.ra.status === "draft") await showRA(B.raId);
      else if (B.view === "overall") await showOverall(OV.base, true);
      else return;
      window.scrollTo(0, y);
    }, LIVE_MS);
  }

  async function showHome() {
    const s = section();
    B.view = "home";
    const my = ++B.seq;
    // installation logged in Site Progress opens the service's draft by itself (a service can switch this off in setup)
    try {
      const a = await jsend(api("/" + B.slug + "/auto"), {});
      if (a && a.created && a.created.length) toast(a.created.map((c) => c.label).join(", ") + (a.created.length > 1 ? " were" : " was") + " opened automatically from Site Progress.");
    } catch (e) { /* the list below still loads */ }
    let ov;
    try { ov = await jget(api("/" + B.slug)); if (my !== B.seq) return; }
    catch (e) {
      if (my !== B.seq) return;
      s.innerHTML = head("Billing") + `<div class="bl-empty">${e.status === 404
        ? "No Site Progress data for this project yet. Upload the BOQ in Site Progress first — billing reads rates and quantities from there."
        : esc(briefErr(e))}</div>`;
      wireProj(); return;
    }
    B.ov = ov;
    setBadge(ov);
    startLive();
    const anySet = ov.services.some((x) => x.set);
    const drafts = ov.ras.filter((r) => r.status === "draft");
    s.innerHTML = head("Billing",
      "The bill builds itself: installation from Site Progress, supply from delivery challans (or the stock register), rate from your BOQ sheet.",
      `<button class="btn" id="bl-setup">Billing setup</button>
       ${ov.ras.length ? `<button class="btn" id="bl-dl">Download</button>` : ""}
       ${ov.services.some((x) => x.supply_source === "challan") ? `<button class="btn" id="bl-imph">Import challans</button>` : ""}
       <button class="btn primary" id="bl-new" ${anySet ? "" : "disabled"}>+ New RA bill</button>`) + `
      <div class="bl-kpis">
        <div class="bl-kpi"><span>Contract value</span><b>${inr(ov.contract_value)}</b><i>${basesOf(ov).length} services${ov.services.length > basesOf(ov).length ? " · " + ov.services.length + " Work Orders" : ""}</i></div>
        <div class="bl-kpi good"><span>Invoiced so far</span><b>${inr(ov.billed)}</b><i>${ov.ras.filter((r) => r.status === "locked").length} RA invoiced</i></div>
        <div class="bl-kpi warn"><span>Ready to bill, no draft yet</span><b>${inr(ov.ready_total)}</b><i>Work done on site + material received</i></div>
      </div>
      ${anySet ? "" : `<div class="bl-callout">No service has a payment term yet. Open <b>Billing setup</b> and set Supply % / Installation % for each service. Until it is set, that service cannot be billed — no % is ever guessed.</div>`}
      <div class="bl-sech"><h3>Services</h3><span class="bl-live"><i></i>Live · refreshes every minute</span></div>
      <div class="bl-card bl-svcl" id="bl-svcl">${basesOf(ov).flatMap((b) => b.units.map((u) => ov.services.find((x) => x.service === u)).filter(Boolean).map((x) => svcRow(ov, b, x))).join("")}</div>
      <div class="bl-sech"><h3>RA bills</h3><span class="bl-live">Each row shows what is inside, stage by stage</span></div>
      <div class="bl-card bl-scroll"><div class="bl-ras">
        <div class="bl-rah"><div>RA</div><div>Cut-off</div><div>Status</div><div>What is inside</div><div class="n">This bill</div><div class="n">Up to date</div><div></div></div>
        ${ov.ras.length ? ov.ras.map((r) => `
        <div class="bl-rar" data-ra="${esc(r.id)}" tabindex="0" role="button">
          <div><b>${esc(r.service)} · RA ${r.no}</b><small>${esc(SERIES[r.series])} · ${r.lines} line${r.lines === 1 ? "" : "s"}</small></div>
          <div>${esc(r.cutoff || "")}</div>
          <div><span class="bl-chip ${r.status}">${r.status === "draft" ? "Draft" : "Invoiced"}</span></div>
          <div class="ins">${stageChips(r)}</div>
          <div class="n"><b>${inr(r.this_total)}</b></div><div class="n">${inr(r.up_to_date)}</div>
          <div class="go">${r.status === "draft" ? "Open →" : "View →"}</div></div>`).join("")
          : `<div class="bl-none">No RA yet. Click “+ New RA bill”.</div>`}
      </div></div>
      ${ov.notes.length ? `<div class="bl-notes">${ov.notes.map((n) => `<div class="bl-note ${n.kind}" ${n.ra ? `data-ra="${esc(n.ra)}"` : ""}><b>${n.kind === "over" ? "Check" : "Needs your input"}</b>${esc(n.msg)}</div>`).join("")}</div>` : ""}`;
    wireProj();
    $("bl-setup").onclick = showSetup;
    $("bl-new").onclick = () => openNewRA();
    if ($("bl-dl")) $("bl-dl").onclick = () => openDownload();
    if ($("bl-imph")) $("bl-imph").onclick = importFromHome;
    s.querySelectorAll("[data-ra]").forEach((el) => {
      el.onclick = () => showRA(el.dataset.ra);
      el.onkeydown = (e) => { if (e.key === "Enter") showRA(el.dataset.ra); };
    });
    s.querySelectorAll("[data-ch]").forEach((el) => el.onclick = () => showChallans(el.dataset.ch));
    s.querySelectorAll("[data-wire]").forEach((el) => el.onclick = () => showWire(el.dataset.wire));
    s.querySelectorAll("[data-overall]").forEach((el) => el.onclick = () => showOverall(el.dataset.overall));
    s.querySelectorAll("[data-setup]").forEach((el) => el.onclick = showSetup);
    s.querySelectorAll("[data-mk]").forEach((el) => el.onclick = async () => {
      try { await createFromRow(el.dataset.mk, el.dataset.mkseries.split(",")); } catch (e) { toast(briefErr(e)); }
    });
    s.querySelectorAll("[data-menu]").forEach((el) => el.onclick = (e) => { e.stopPropagation(); openRowMenu(el); });
    void drafts;
  }

  // The services on the home screen. A service split into Work Orders shows one tile per Work Order (each has its
  // own payment term, RA numbers and challans); a plain service is a single tile. Both link to the overall annexure.
  const basesOf = (ov) => ov.bases && ov.bases.length ? ov.bases
    : [...new Set(ov.services.map((x) => x.base || unitBase(x.service)))].map((b) => ({ base: b, split: false, units: [b] }));
  // The number on the Billing tab: services that need a look right now (an open draft, or something ready to bill).
  // Kept current whenever the overview loads; also read once shortly after the page opens so it is there from the first tab.
  function setBadge(ov) {
    const nav = document.querySelector('.spnav button[data-v="billing"]');
    if (!nav) return;
    const n = ov ? ov.services.filter((x) => { const k = unitState(ov, x).k; return k === "draft" || k === "ready"; }).length : 0;
    let b = nav.querySelector(".bl-badge");
    if (!n) { if (b) b.remove(); return; }
    if (!b) { b = document.createElement("span"); b.className = "bl-badge"; nav.appendChild(b); }
    b.textContent = n; b.title = n + (n === 1 ? " service" : " services") + " to look at in Billing";
  }
  async function loadBadge() {
    try {
      const ps = await jget("/api/projects");
      if (!ps.length) return;
      const fromF = (typeof META !== "undefined" && META && META.project_slug);
      const slug = B.slug && ps.find((p) => p.slug === B.slug) ? B.slug : (fromF && ps.find((p) => p.slug === fromF)) ? fromF : ps[0].slug;
      if (B.view === "home" && B.ov) return;             // Billing is open: showHome keeps it current
      setBadge(await jget(api("/" + slug)));
    } catch (e) { /* the badge is only a hint */ }
  }

  // ------------------------------------------------ services on the home screen
  // One row per billing unit (a Work Order, or a plain service). Every row has the same slots, in the same places:
  // name + payment term | status | ONE main button | links | "···". Nothing about a service is hidden behind another click
  // except Work Orders… / Billing setup (the "···" menu), which are rare.
  const pctS = (v) => (v == null ? "—" : v + "%");
  const termShort = (x) => `Supply ${pctS(x.supply_pct)} · Install ${pctS(x.install_pct)}`;
  // what this unit needs from the person right now: an open draft, something ready to bill, nothing, or no payment term yet
  function unitState(ov, x) {
    if (!x.set) return { k: "unset" };
    const drafts = ov.ras.filter((r) => r.service === x.service && r.status === "draft").sort((p, q) => q.no - p.no);
    const rd = Object.entries(x.ready || {}).filter(([, v]) => v && v.ready > 0 && !v.has_draft);
    const readyAmt = rd.reduce((a, [, v]) => a + v.ready, 0);
    if (drafts.length) return { k: "draft", drafts, top: drafts[0], total: drafts.reduce((a, r) => a + (r.this_total || 0), 0), readyAmt, readySeries: rd.map((e) => e[0]) };
    if (rd.length) return { k: "ready", series: rd.map((e) => e[0]), amt: readyAmt };
    return { k: "none" };
  }
  function svcRow(ov, b, x) {
    const st = unitState(ov, x), split = !!b.split, enc = esc(x.service);
    const name = esc(x.service);
    let status = "", act = "";
    if (st.k === "draft") {
      const n = st.drafts.length;
      status = `<span class="bl-chip draft">${n === 1 ? "RA " + st.top.no + " · Draft" : n + " drafts open"}</span><b class="am">${inr(st.total)}</b>`
        + (st.readyAmt ? `<small class="also" title="${esc(st.readySeries.map((k) => SERIES[k]).join(" + "))}: ${st.readySeries.length === 1 && st.readySeries[0] === "supply" ? "delivery challans / stock are in" : "the work is done"}, but no ${esc(st.readySeries.map((k) => SERIES[k]).join(" + "))} RA is drafted yet. Open + New RA bill to create it.">+ ${inr(st.readyAmt)} ${esc(st.readySeries.map((k) => SERIES[k]).join(" + "))} ready</small>` : "");
      act = `<button class="btn primary" data-ra="${esc(st.top.id)}">Open draft →</button>`;
    } else if (st.k === "ready") {
      status = `<span class="bl-chip locked">Ready</span><b class="am good">${inr(st.amt)}</b>`;
      act = `<button class="btn primary" data-mk="${enc}" data-mkseries="${esc(st.series.join(","))}">Create draft →</button>`;
    } else if (st.k === "unset") {
      status = `<span class="bl-chip draft">Payment term not set</span>`;
      act = `<button class="btn" data-setup="1">Set payment term</button>`;
    } else status = `<span class="bl-quiet">Nothing to bill yet</span>`;
    const ch = x.set && x.supply_source === "challan"
      ? `<button class="bl-link" data-ch="${enc}">Challans · ${x.challans}${x.held ? " · " + x.held + " on hold" : ""}</button>` : `<span></span>`;
    const wire = x.set && x.supply_source === "challan" && x.wire ? `<button class="bl-link" data-wire="${enc}">Wire → points</button>` : `<span></span>`;
    return `<div class="bl-hr ${st.k}" data-unit="${enc}">
      <div class="nm"><b>${name}</b><small>${x.set ? esc(termShort(x)) : "Supply — · Install —"}</small></div>
      <div class="stt">${status}</div>
      <div class="act">${act}</div>
      <div class="lks">${ch}${wire}<button class="bl-link" data-overall="${esc(b.base)}">Annexure →</button></div>
      <div class="mn"><button class="bl-dots" data-menu="${esc(b.base)}" data-split="${split ? 1 : 0}" aria-label="More for ${name}" aria-haspopup="true">···</button></div></div>`;
  }
  // "What is inside" of an RA: both stages, a stage that carries nothing is greyed (never hidden, never a made-up 0)
  function stageChips(r) {
    const t = r.stage_this || {};
    return ["supply", "install"].map((k) => {
      const v = r.stages.includes(k) ? t[k] : null;
      const nm = k === "install" ? "Install" : "Supply";
      return v ? `<span class="bl-sg on">${nm} ${inr(v)}</span>` : `<span class="bl-sg">${nm} —</span>`;
    }).join("");
  }
  // the "···" menu of a service row
  function openRowMenu(btn) {
    document.querySelectorAll(".bl-rowmenu").forEach((m) => m.remove());
    const base = btn.dataset.menu, split = btn.dataset.split === "1";
    const m = document.createElement("div");
    m.className = "bl-menu bl-rowmenu";
    m.innerHTML = (split ? `<button data-mwo="1">Work Orders…<small>Split ${esc(base)} into Work Orders</small></button>` : "")
      + `<button data-msetup="1">Billing setup<small>Payment term, supply source, annexure groups</small></button>`;
    btn.parentNode.appendChild(m);
    m.querySelectorAll("button").forEach((el) => el.onclick = (e) => {
      e.stopPropagation(); m.remove();
      if (el.dataset.mwo) editPackages(base); else showSetup();
    });
    setTimeout(() => document.addEventListener("click", function off(e) { if (!m.contains(e.target)) { m.remove(); document.removeEventListener("click", off); } }), 0);
  }
  // Create draft from a "Ready" row: when exactly one bill type is ready it is created straight away; if several are, the
  // New RA dialog opens with the service chosen so the person picks the type.
  async function createFromRow(service, series) {
    if (series.length === 1) {
      const r = await jsend(api("/" + B.slug + "/ra"), { service, series: series[0] });
      return showRA(r.id);
    }
    openNewRA(service);
  }

  // ------------------------------------------------------ overall annexure
  // One service, every Work Order, outside any single RA: what each annexure group is worth, what is already invoiced,
  // what still sits in an open draft. Nothing is typed here, it only adds up the RAs (server: /overall/<service>).
  const pctText = (v) => (v == null ? "—" : (v > 0 && v < 0.1 ? (Math.round(v * 100) / 100) : (Math.round(v * 10) / 10)) + "%");
  async function showOverall(base, keep) {
    const s = section();
    B.view = "overall";
    if (!keep || OV.base !== base) { OV.drafts = true; OV.all = false; }
    OV.base = base;
    const my = ++B.seq;
    let o;
    try { o = await jget(api("/" + B.slug + "/overall/" + encodeURIComponent(base))); }
    catch (e) { if (my !== B.seq) return; toast(briefErr(e)); return showHome(); }
    if (my !== B.seq) return;
    OV.data = o;
    startLive();
    drawOverall();
  }

  function drawOverall() {
    const s = section(), o = OV.data, dr = OV.drafts;
    const val = (r) => (dr ? r.todate : r.invoiced);
    const T = o.totals, billed = dr ? T.todate : T.invoiced;
    const share = (v) => (T.wo_value ? Math.max(0, Math.min(100, v / T.wo_value * 100)) : 0);
    let hidden = 0;
    const cols = `46px minmax(170px,2.4fr) repeat(${dr ? 5 : 4},minmax(100px,1fr))`;
    const rowHtml = (r, sub) => `<div class="bl-or${sub ? " tot" : ""}" style="grid-template-columns:${cols}">
      <div class="sr">${esc(r.sr || "")}</div><div class="gn">${esc(r.name)}</div>
      <div class="n" data-l="Work Order">${inr(r.wo)}</div><div class="n inv" data-l="Invoiced">${inr(r.invoiced)}</div>
      ${dr ? `<div class="n drf" data-l="In draft">${inr(r.draft)}</div>` : ""}
      <div class="n" data-l="To date"><b>${inr(val(r))}</b></div><div class="n" data-l="Done">${pctText(r.wo ? val(r) / r.wo * 100 : null)}</div></div>`;
    const head2 = `<div class="bl-oh" style="grid-template-columns:${cols}"><div>Sr</div><div>Description</div><div class="n">Work Order</div><div class="n">Invoiced</div>${dr ? `<div class="n">In draft</div>` : ""}<div class="n">To date</div><div class="n">Done</div></div>`;
    const block = (u) => {
      const shown = u.rows.filter((r) => OV.all || Math.abs(val(r)) > 1e-9);
      hidden += u.rows.length - shown.length;
      const l = u.latest;
      return `<div class="bl-card bl-wob" data-unit="${esc(u.unit)}">
        <div class="bl-wohd"><div><b>${esc(u.package || o.base)}</b>
          <small>${u.wo_no ? "W/O " + esc(u.wo_no) + " · " : ""}Work Order ${inr(u.wo_value)}${u.set ? "" : " · payment term not set"}</small></div>
          <div class="bl-woact">${l ? `<span class="bl-chip ${l.status}">RA ${l.no} · ${l.status === "draft" ? "Draft" : "Invoiced"}</span>
            <button class="bl-link" data-ra="${esc(l.id)}">${l.status === "draft" ? "Open draft →" : "View →"}</button>` : `<span class="bl-chip none">No RA yet</span>`}
            ${u.source === "challan" && u.set ? `<button class="bl-link" data-ch="${esc(u.unit)}">Challans</button>` : ""}</div></div>
        <div class="bl-scroll"><div class="bl-ovg">${head2}
          ${shown.length ? shown.map((r) => rowHtml(r)).join("") : `<div class="bl-none sm">${u.rows.length ? "Nothing billed in this Work Order yet." : "No annexure group."}</div>`}
          ${rowHtml({ sr: "", name: "Subtotal", wo: u.wo_value, invoiced: u.invoiced, draft: u.draft, todate: u.todate }, true)}</div></div></div>`;
    };
    const blocks = o.units.map(block).join("");
    const firstSet = (o.units.find((u) => u.set) || {}).unit;
    s.innerHTML = head(o.base + ", overall annexure",
      "Every Work Order of this service in one place: what the contract is, what is already invoiced, what is sitting in a draft. Nothing is typed here, it reads the RAs.",
      `<button class="btn" id="bl-back">← Billing</button>
       <button class="btn" id="bl-ovdl">Download overall annexure (.xlsx)</button>
       <button class="btn primary" id="bl-ovnew" ${firstSet ? "" : "disabled"}>+ New RA bill</button>`) + `
      <div class="bl-kpis">
        <div class="bl-kpi"><span>Work Order value</span><b>${inr(T.wo_value)}</b><i>${o.units.length} Work Order${o.units.length === 1 ? "" : "s"}</i></div>
        <div class="bl-kpi good"><span>Invoiced</span><b>${inr(T.invoiced)}</b><i>${o.units.reduce((a, u) => a + u.ras.filter((r) => r.status === "locked").length, 0)} RA invoiced</i></div>
        ${dr ? `<div class="bl-kpi warn"><span>In draft</span><b>${inr(T.draft)}</b><i>${o.units.reduce((a, u) => a + u.ras.filter((r) => r.status === "draft").length, 0)} open draft(s)</i></div>` : ""}
        <div class="bl-kpi"><span>Billed to date</span><b>${inr(billed)} <small>${pctText(T.wo_value ? billed / T.wo_value * 100 : null)}</small></b>
          <div class="bl-bar" aria-hidden="true"><i class="inv" style="width:${share(T.invoiced)}%"></i>${dr ? `<i class="drf" style="width:${share(T.draft)}%"></i>` : ""}</div></div>
      </div>
      <div class="bl-ovbar"><div class="bl-tgl" role="radiogroup" aria-label="What to add up">
          <button type="button" class="${dr ? "" : "on"}" data-ovm="inv" role="radio" aria-checked="${!dr}">Invoiced only</button>
          <button type="button" class="${dr ? "on" : ""}" data-ovm="all" role="radio" aria-checked="${dr}">Invoiced + drafts</button></div>
        <span class="bl-ovh" id="bl-ovh"></span></div>
      ${blocks}
      <div class="bl-ovtot"><b>${esc(o.base)}${o.units.length > 1 ? ", all Work Orders" : ""}</b>
        <div><span>Work Order</span><b>${inr(T.wo_value)}</b></div><div><span>Invoiced</span><b class="inv">${inr(T.invoiced)}</b></div>
        ${dr ? `<div><span>In draft</span><b class="drf">${inr(T.draft)}</b></div>` : ""}
        <div><span>Billed to date</span><b>${inr(billed)}</b></div><div><span>Done</span><b>${pctText(T.wo_value ? billed / T.wo_value * 100 : null)}</b></div></div>
      <p class="bl-ovnote">Work Order values are the contract values of each Work Order. Billed amounts are the supply and installation percentages of the certified quantities, exactly as in each RA. “Invoiced” is what is marked invoiced; “In draft” is what an open draft would add.</p>`;
    const h = $("bl-ovh");
    if (h) {
      h.innerHTML = hidden || OV.all ? (OV.all ? `Showing every group. <button type="button" class="bl-link" id="bl-ovhide">Hide groups with nothing billed</button>`
        : `Groups with nothing billed are hidden. <button type="button" class="bl-link" id="bl-ovshow">Show ${hidden} hidden group${hidden === 1 ? "" : "s"}</button>`) : "";
    }
    wireProj();
    $("bl-back").onclick = showHome;
    $("bl-ovnew").onclick = async () => { try { B.ov = await jget(api("/" + B.slug)); } catch (e) { toast(briefErr(e)); return; } openNewRA(firstSet); };
    $("bl-ovdl").onclick = async () => {
      try { const nm = await fetchDownload(api("/" + encodeURIComponent(B.slug) + "/overall/" + encodeURIComponent(o.base) + "/xlsx?drafts=" + (dr ? 1 : 0))); toast("Downloaded " + nm); }
      catch (e) { toast(briefErr(e)); }
    };
    s.querySelectorAll("[data-ovm]").forEach((el) => el.onclick = () => { OV.drafts = el.dataset.ovm === "all"; drawOverall(); });
    if ($("bl-ovshow")) $("bl-ovshow").onclick = () => { OV.all = true; drawOverall(); };
    if ($("bl-ovhide")) $("bl-ovhide").onclick = () => { OV.all = false; drawOverall(); };
    s.querySelectorAll("[data-ra]").forEach((el) => el.onclick = () => showRA(el.dataset.ra));
    s.querySelectorAll("[data-ch]").forEach((el) => el.onclick = () => showChallans(el.dataset.ch));
    s.querySelectorAll("[data-wire]").forEach((el) => el.onclick = () => showWire(el.dataset.wire));
  }

  // --------------------------------------------------------------- new RA
  // The bill type is chosen on EACH new RA: some months supply and installation go out as two invoices,
  // some months as one. The service's "usually billed as" (Billing setup) only decides which is pre-selected.
  const BILL_TYPES = [["supply", "Supply only", "a separate supply invoice"], ["install", "Installation only", "a separate installation invoice"],
    ["together", "Supply + Installation together", "one invoice carrying both"]];
  const usualType = (x) => (x.mode === "together" ? ["together"] : ["supply", "install"]);
  function openNewRA(pre) {
    const svcs = B.ov.services.filter((x) => x.set);
    let svc = (typeof pre === "string" && svcs.find((x) => x.service === pre)) ? pre : svcs[0].service, series = null;
    const okType = (r) => r && !r.blocked && !r.has_draft;
    const body = () => {
      const x = B.ov.services.find((y) => y.service === svc);
      const can = x.can || {};
      if (!series || !can[series]) series = usualType(x).find((k) => okType(can[k])) || BILL_TYPES.map((t) => t[0]).find((k) => okType(can[k])) || usualType(x)[0];
      return `<div class="bl-lbl">Service</div><div class="bl-pills">${B.ov.services.map((y) => `
        <button class="bl-pill ${y.service === svc ? "on" : ""} ${y.set ? "" : "off"}" data-svc="${esc(y.service)}" ${y.set ? "" : "disabled"}>${esc(y.service)}${y.set ? "" : " · not set"}</button>`).join("")}</div>
        <div class="bl-lbl">Bill type <small>pick what this invoice carries</small></div>
        ${BILL_TYPES.map(([k, title, what]) => { const r = can[k] || {};
          const src = x.supply_source === "challan" ? `Delivery challans (${x.challans} entered)` : "Material received (stock register)";
          const sub = k === "supply" ? `${src}, ${x.supply_pct}%`
            : k === "install" ? `Work done (Site Progress), ${x.install_pct}%`
            : `${src} ${x.supply_pct}% + Installation ${x.install_pct}%, ${what}`;
          const off = !okType(r);
          const why = r.blocked ? r.blocked : r.has_draft ? `A ${r.draft_label} draft is already open and carries the same stage — mark it invoiced or delete it first.` : "";
          return `<div class="bl-opt ${k === series ? "on" : ""} ${off ? "off" : ""}" data-series="${k}" role="radio" aria-checked="${k === series}" tabindex="0">
            <div><b>${esc(title)}${usualType(x).includes(k) ? ` <span class="bl-usual">usual for ${esc(x.service)}</span>` : ""}</b><small>${esc(why || sub)}</small></div>
            <div class="n">${off ? "" : `<small>RA ${r.next_no} · Ready</small><b>${inr(r.ready)}</b>`}</div></div>`; }).join("")}`;
    };
    const draw = () => {
      $("bl-modal").querySelector(".bl-mbody").innerHTML = body();
      document.querySelectorAll("#bl-modal [data-svc]").forEach((el) => el.onclick = () => { svc = el.dataset.svc; series = null; draw(); });
      document.querySelectorAll("#bl-modal [data-series]").forEach((el) => {
        el.onclick = () => { series = el.dataset.series; draw(); };
        el.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); series = el.dataset.series; draw(); } };
      });
    };
    modal("New RA bill", "Pick the service and what this invoice carries. The number is assigned automatically.", "", [
      { label: "Cancel", run: closeModal },
      { label: "Create draft", cls: "primary", run: async () => {
        const x = B.ov.services.find((y) => y.service === svc);
        if (!series || !okType((x.can || {})[series])) { toast("Pick a bill type that is ready."); return; }
        const r = await jsend(api("/" + B.slug + "/ra"), { service: svc, series });
        closeModal(); showRA(r.id);
      } }]);
    draw();
  }

  // ------------------------------------------------------------- RA bill
  async function showRA(id) {
    const s = section();
    B.view = "ra"; B.raId = id;
    const my = ++B.seq;
    let b;
    try { b = await jget(api("/" + B.slug + "/ra/" + id)); }
    catch (e) { if (my !== B.seq) return; toast(briefErr(e)); return showHome(); }
    if (my !== B.seq) return;
    B.bill = b;
    startLive();
    const ra = b.ra, locked = ra.status === "locked", stages = ra.stages;
    const cols = `minmax(230px,2.6fr) 84px 92px repeat(${stages.length * 4},minmax(86px,1fr))`;
    const hdr = `<div class="bl-lh" style="grid-template-columns:${cols}">
      <div>Line</div><div class="n">BOQ qty</div><div class="n">Rate</div>
      ${stages.map((st) => `<div class="n sg">${STAGE[st]} ${b.pct[st] == null ? "" : b.pct[st] + "%"}<br><small>Before</small></div><div class="n"><small>&nbsp;<br>To date</small></div><div class="n"><small>&nbsp;<br>This bill</small></div><div class="n"><small>&nbsp;<br>Amount</small></div>`).join("")}</div>`;
    const lineRow = (l) => `
      <div class="bl-lr ${B.expanded.has(l.item_code) ? "open" : ""}" data-code="${esc(l.item_code)}" style="grid-template-columns:${cols}">
        <div class="d" data-l="Line"><span class="t">${esc(l.desc)}</span><small>${l.internal_ref ? "Ref " + esc(l.internal_ref) + " · " : ""}${esc(l.sheet || "")}${l.src_row ? " row " + l.src_row : ""}</small></div>
        <div class="n" data-l="BOQ qty">${qn(l.boq_qty)} <small>${esc(l.unit)}</small></div>
        <div class="n" data-l="Rate">${inr(l.rate, true)}</div>
        ${stages.map((st) => { const c = l[st]; if (!c) return `<div></div><div></div><div></div><div></div>`;
          const src = SOURCE[c.source] || [c.source, ""];
          return `<div class="n" data-l="${STAGE[st]} before">${qn(c.prev)}</div>
            <div class="n" data-l="${STAGE[st]} to date">${c.utd == null ? "—" : qn(c.utd)}<small class="bl-src ${src[1]}">${esc(src[0])}</small></div>
            <div class="n" data-l="${STAGE[st]} this bill">${locked ? `<b class="${c.this < 0 ? "neg" : ""}">${qn(c.this)}</b>`
              : `<button class="bl-qtybtn ${c.this == null ? "need" : ""} ${c.this < 0 ? "neg" : ""}" data-edit="${esc(l.item_code)}" data-stage="${st}" title="Type a quantity">${c.this == null ? "Type qty" : qn(c.this)}</button>`}</div>
            <div class="n" data-l="${STAGE[st]} amount"><b class="${c.amount < 0 ? "neg" : ""}">${inr(c.amount)}</b></div>`; }).join("")}
        <div class="x" style="grid-column:1/-1">${stages.map((st) => { const c = l[st]; if (!c) return "";
          return `<div><b>${STAGE[st]}</b>: ${c.amount == null ? "Amount not computed — quantity missing." :
            `rate ${inr(l.rate, true)} × ${qn(c.this)} ${esc(l.unit)} × ${c.pct}% = <b>${inr(c.amount, true)}</b>`}<br>
            <span>${esc(c.note || "")}${c.overridden ? " · typed by engineer" + (c.reason ? ": “" + esc(c.reason) + "”" : "") : ""}</span>
            ${c.stock_qty != null ? `<br><span>Stock register shows ${qn(c.stock_qty)} ${esc(l.unit)} received${Math.abs(c.stock_qty - (c.utd == null ? 0 : c.utd)) > 1e-6 ? " — differs from the challans" : ""}.</span>` : ""}</div>`; }).join("")}</div>
      </div>`;
    const g = b.groups, oth = b.other;
    const gcols = `46px minmax(180px,2fr) 120px repeat(${stages.length * 2},minmax(110px,1fr))`;
    const gRow = (name, sr, wo, st) => `<div class="bl-gr" style="grid-template-columns:${gcols}"><div class="sr">${esc(sr || "")}</div><div class="gn">${esc(name)}</div><div class="n" data-l="WO amount">${inr(wo)}</div>
      ${stages.map((k) => `<div class="n" data-l="${STAGE[k]} previous">${inr(st[k].prev)}</div><div class="n" data-l="${STAGE[k]} this bill"><b>${inr(st[k].this)}</b></div>`).join("")}</div>`;
    s.innerHTML = head(ra.label, `${locked ? "Invoiced" : "Draft"} · cut-off ${ra.cutoff || ""} · numbers as of the latest Site Progress and ${b.run ? "stock run " + b.run : "stock run (none yet)"}`,
      `<button class="btn" id="bl-back">← Billing</button>
       <button class="btn" id="bl-toverall" title="${esc(ra.base || unitBase(ra.service))}: every annexure group in one view">Overall annexure</button>
       ${stages.includes("supply") && ((B.ov && B.ov.services || []).find((y) => y.service === ra.service) || {}).supply_source === "challan" ? `<button class="btn" id="bl-tochallans">Challans</button>` : ""}
       <button class="btn" id="bl-dl" title="Invoice workbook, All services Excel — tick what you need">Download</button>
       ${!locked && stages.includes("supply") && ((B.ov && B.ov.services || []).find((y) => y.service === ra.service) || {}).supply_source === "challan" ? `<button class="btn" id="bl-impra" title="Filled the CHALLAN sheet in the downloaded Excel? Bring it back here">Import challans</button>` : ""}
       ${locked ? `<button class="btn" id="bl-reopen">Reopen</button>`
        : `<button class="btn danger" id="bl-del">Delete draft</button><button class="btn primary" id="bl-lock">Mark as invoiced</button>`}`) + `
      <div class="bl-kpis">
        <div class="bl-kpi"><span>This bill</span><b>${inr(b.this_total)}</b><i>${stages.map((k) => STAGE[k] + " " + inr(b.totals[k].this)).join(" · ")}</i></div>
        <div class="bl-kpi"><span>Billed before</span><b>${inr(stages.reduce((a, k) => a + b.totals[k].prev, 0))}</b><i>from invoiced RAs</i></div>
        <div class="bl-kpi"><span>Contract value (service)</span><b>${inr(b.contract_value)}</b><i>${stages.map((k) => STAGE[k] + " " + (b.pct[k] == null ? "—" : b.pct[k] + "%")).join(" · ")}</i></div>
      </div>
      <div class="bl-card pad bl-inv">
        <div class="bl-invh"><b>Tax invoice</b><span class="bl-chip ${(ra.inv_missing || []).length ? "draft" : "locked"}" id="bl-invchip">${(ra.inv_missing || []).length ? (ra.inv_missing || []).length + " blank" : "Complete"}</span>
          <span class="bl-invs" id="bl-invsaved" aria-live="polite"></span></div>
        <div class="bl-invf">
          <label>Invoice no.<input class="ctl" id="bl-ino" value="${esc(ra.inv_no || "")}" autocomplete="off"></label>
          <label>Invoice date<input class="ctl" id="bl-ida" type="date" value="${esc(ra.inv_date || "")}"></label>
          <button class="btn" id="bl-idet">Company, client &amp; GST details</button></div>
        <p class="bl-invm" id="bl-invm">${(ra.inv_missing || []).length ? "Blank on the Tax Invoice sheet: " + esc(brief(ra.inv_missing)) + "." : "Everything the Tax Invoice sheet needs is filled in."}</p>
      </div>
      ${b.warnings.length ? `<div class="bl-notes">${b.warnings.map((w) => `<div class="bl-note over"><b>Check</b>${esc(w.msg)}</div>`).join("")}</div>` : ""}
      ${!locked && Object.values(b.unfilled).some((n) => n) ? `<div class="bl-callout">${stages.filter((k) => b.unfilled[k]).map((k) =>
        `${b.unfilled[k]} ${STAGE[k].toLowerCase()} lines have no quantity yet (not linked to stock, or not safe to compute). Use “+ Add line” to type them in. Nothing is guessed.`).join("<br>")}</div>` : ""}
      ${stages.includes("install") && b.totals.install.this === 0 && b.totals.install.prev === 0 ? `<div class="bl-callout soft" id="bl-noinst">${locked
        ? "Installation is ₹0 in this bill: no installation progress had been logged in Site Progress for this service when it was invoiced. Bill installation later with New RA bill → Installation."
        : "Installation is ₹0 for now: no installation progress is logged in Site Progress for this service yet. It fills in by itself as work is logged — nothing to type here."}</div>` : ""}
      ${b.groups_confirmed ? "" : `<div class="bl-callout soft">Annexure groups were read from your sheet’s own “Cost of… / Total…” rows. Check them once in Billing setup → Annexure groups.</div>`}
      <h3 class="bl-h3">Annexure</h3>
      <div class="bl-card bl-scroll"><div class="bl-grid" style="min-width:${520 + stages.length * 260}px">
        <div class="bl-gh" style="grid-template-columns:${gcols}"><div>Sr</div><div>Description</div><div class="n">WO amount</div>
          ${stages.map((k) => `<div class="n">${STAGE[k]} previous</div><div class="n">${STAGE[k]} this bill</div>`).join("")}</div>
        ${g.map((x) => gRow(x.name, x.sr, x.wo_amount, x.stages)).join("")}
        ${oth ? gRow("Other items (not in any group)", "", oth.wo_amount, oth.stages) : ""}
      </div></div>
      <div class="bl-lines-h"><h3 class="bl-h3">Lines <small>${b.lines.length}</small></h3>
        ${locked ? "" : `<button class="btn" id="bl-addline">+ Add line</button>`}</div>
      <div class="bl-card bl-scroll"><div class="bl-lines" style="min-width:${360 + stages.length * 380}px">${hdr}
        ${b.lines.length ? b.lines.map(lineRow).join("") : `<div class="bl-none">No line has anything to bill yet.</div>`}</div></div>`;
    wireProj();
    $("bl-back").onclick = showHome;
    if ($("bl-tochallans")) $("bl-tochallans").onclick = () => showChallans(ra.service);
    if ($("bl-toverall")) $("bl-toverall").onclick = () => showOverall(ra.base || unitBase(ra.service));
    $("bl-dl").onclick = async () => { if (B.invSaving) await B.invSaving(); openDownload([ra.id]); };
    if ($("bl-impra")) $("bl-impra").onclick = () => importChallans(ra.service, () => showRA(id));
    wireInvStrip(ra, id);
    s.querySelectorAll(".bl-lr").forEach((row) => row.addEventListener("click", (e) => {
      if (e.target.closest("[data-edit]")) return;
      const c = row.dataset.code; B.expanded.has(c) ? B.expanded.delete(c) : B.expanded.add(c); row.classList.toggle("open");
    }));
    s.querySelectorAll("[data-edit]").forEach((el) => el.onclick = () => editQty(el.dataset.edit, el.dataset.stage));
    if (!locked) {
      $("bl-addline").onclick = addLine;
      $("bl-del").onclick = () => modal("Delete this draft?", ra.label, "<p>Typed quantities in this draft are lost. Invoiced RAs are not affected.</p>", [
        { label: "Cancel", run: closeModal },
        { label: "Delete draft", cls: "danger", run: async () => { await jsend(api("/" + B.slug + "/ra/" + id), undefined, "DELETE"); closeModal(); showHome(); } }]);
      $("bl-lock").onclick = () => modal("Mark " + ra.label + " as invoiced?",
        "Quantities, rates and % freeze as of now. Later progress or rate changes never move an invoiced bill.",
        `<div class="bl-lockbox"><div><span>This bill</span><b>${inr(b.this_total)}</b></div>
          ${Object.values(b.unfilled).some((n) => n) ? `<p class="bl-warn">${Object.values(b.unfilled).reduce((a, n) => a + n, 0)} lines still have no quantity and will not be in this bill.</p>` : ""}
          ${b.warnings.length ? `<p class="bl-warn">${b.warnings.length} line(s) are negative (over-billed earlier).</p>` : ""}
          <p>You can reopen only the last invoiced RA of a series.</p></div>`, [
          { label: "Cancel", run: closeModal },
          { label: "Mark as invoiced", cls: "primary", run: async () => { await jsend(api("/" + B.slug + "/ra/" + id + "/lock"), {}); closeModal(); toast(ra.label + " marked as invoiced."); showRA(id); } }]);
    } else {
      $("bl-reopen").onclick = async () => { try { await jsend(api("/" + B.slug + "/ra/" + id + "/reopen"), {}); toast("Reopened as draft."); showRA(id); } catch (e) { toast(briefErr(e)); } };
    }
  }

  function editQty(code, stage) {
    const l = B.bill.lines.find((x) => x.item_code === code); const c = l[stage];
    const max = Math.max(0, l.boq_qty - c.prev);
    modal("Quantity for this bill", l.desc.slice(0, 120),
      `<div class="bl-edit"><div class="bl-erow"><span>${STAGE[stage]} · before</span><b>${qn(c.prev)} ${esc(l.unit)}</b></div>
        <div class="bl-erow"><span>${c.source === "manual" ? "No automatic quantity" : "Automatic, to date"}</span><b>${c.utd == null ? "—" : qn(c.utd) + " " + esc(l.unit)}</b></div>
        <div class="bl-erow"><span>Most you can bill now (BOQ ${qn(l.boq_qty)})</span><b>${qn(max)} ${esc(l.unit)}</b></div>
        <label>This bill (${esc(l.unit)})<input id="bl-q" class="ctl" type="number" step="any" value="${c.this == null ? "" : c.this}" inputmode="decimal"></label>
        <label>Reason (shown as a note in the Excel)<input id="bl-r" class="ctl" type="text" value="${esc(c.reason || "")}" placeholder="e.g. gate pass 44, received 12 Oct"></label></div>`, [
        { label: "Cancel", run: closeModal },
        ...(c.overridden ? [{ label: "Back to automatic", run: async () => { await jsend(api("/" + B.slug + "/ra/" + B.raId + "/line"), { item_code: code, stage, qty: null }); closeModal(); showRA(B.raId); } }] : []),
        { label: "Save", cls: "primary", run: async () => {
          const v = $("bl-q").value;
          if (v === "") { toast("Type a quantity, or use Back to automatic."); return; }
          await jsend(api("/" + B.slug + "/ra/" + B.raId + "/line"), { item_code: code, stage, qty: Number(v), reason: $("bl-r").value });
          closeModal(); showRA(B.raId);
        } }]);
  }

  async function addLine() {
    const ra = B.bill.ra;
    let all;
    try { all = (await jget(api("/" + B.slug + "/lines/" + encodeURIComponent(ra.service)))).lines; } catch (e) { toast(briefErr(e)); return; }
    const stageSel = ra.stages.length > 1 ? `<label>Stage<select id="bl-st" class="ctl">${ra.stages.map((k) => `<option value="${k}">${STAGE[k]}</option>`).join("")}</select></label>` : "";
    modal("Add a line to this bill", "Search the BOQ, pick the line, then type its quantity.",
      `<input id="bl-find" class="ctl" type="search" placeholder="Search description or Ref no" autocomplete="off">${stageSel}<div id="bl-found" class="bl-found"></div>`, [{ label: "Close", run: closeModal }]);
    const draw = () => {
      const q = $("bl-find").value.trim().toLowerCase();
      const hits = all.filter((l) => !q || l.desc.toLowerCase().includes(q) || String(l.internal_ref || "").toLowerCase() === q || String(l.item_code).toLowerCase() === q).slice(0, 40);
      $("bl-found").innerHTML = hits.map((l) => `<button class="bl-hit" data-c="${esc(l.item_code)}"><span>${esc(l.desc.slice(0, 110))}</span><small>${l.internal_ref ? "Ref " + esc(l.internal_ref) + " · " : ""}BOQ ${qn(l.boq_qty)} ${esc(l.unit)} · ${inr(l.rate, true)}</small></button>`).join("") || `<div class="bl-none">No match.</div>`;
      document.querySelectorAll("#bl-found .bl-hit").forEach((el) => el.onclick = () => {
        const l = all.find((x) => x.item_code === el.dataset.c); const st = $("bl-st") ? $("bl-st").value : ra.stages[0];
        let row = B.bill.lines.find((x) => x.item_code === l.item_code);
        if (!row) { row = { item_code: l.item_code, desc: l.desc, unit: l.unit, boq_qty: l.boq_qty, rate: l.rate }; B.bill.lines.push(row); }
        if (!row[st]) row[st] = { prev: 0, utd: null, this: null, amount: null, pct: B.bill.pct[st], source: "manual", overridden: false, reason: "", note: "" };
        editQty(l.item_code, st);
      });
    };
    $("bl-find").oninput = draw; draw(); $("bl-find").focus();
  }

  // invoice no. / date strip on the RA screen: saves as soon as a field is left
  function wireInvStrip(ra, id) {
    let chain = Promise.resolve();                       // saves go one after the other, in the order typed
    B.invSaving = () => chain;                           // Download waits for it, so it never reads a stale invoice no.
    const doSave = async () => {
      try {
        const r = await jsend(api("/" + B.slug + "/ra/" + ra.id + "/invoice"), { no: $("bl-ino").value, date: $("bl-ida").value });
        $("bl-invsaved").textContent = "Saved";
        clearTimeout(wireInvStrip._t); wireInvStrip._t = setTimeout(() => { const e = $("bl-invsaved"); if (e) e.textContent = ""; }, 2200);
        const n = r.missing.length;
        $("bl-invchip").textContent = n ? n + " blank" : "Complete";
        $("bl-invchip").className = "bl-chip " + (n ? "draft" : "locked");
        $("bl-invm").textContent = n ? "Blank on the Tax Invoice sheet: " + brief(r.missing) + "." : "Everything the Tax Invoice sheet needs is filled in.";
      } catch (e) { toast(briefErr(e)); }
    };
    const save = () => { chain = chain.then(doSave); return chain; };
    $("bl-ino").onchange = save; $("bl-ida").onchange = save;
    $("bl-idet").onclick = () => openInvoiceDetails({ done: () => showRA(id) });
  }

  // ----------------------------------------------------- invoice details
  // The Tax Invoice sheet of every RA is the user's own invoice format. What it needs is typed
  // ONCE here (company, client, GST, SAC per service); the invoice no. and date belong to each RA.
  // Whatever is left blank stays blank on the sheet, nothing is guessed.
  const F_CO = [["contractor", "Company name"], ["supplier_gstin", "GSTIN"], ["supplier_pan", "PAN"], ["supplier_state", "State"],
    ["supplier_state_code", "State code"], ["supplier_category", "Service tax category", "e.g. MEPF"]];
  const F_CL = [["client", "Company name"], ["client_address", "Registered address"], ["place_of_supply", "Place of supply"],
    ["client_gstin", "GSTIN"], ["client_state", "State"], ["client_state_code", "State code"], ["project_name", "Project"], ["wo_no", "W/O number"]];

  // a project's display name, or its slug made readable when it is no longer in the project list
  const projName = (slug) => ((B.projects.find((p) => p.slug === slug) || {}).project)
    || (slug ? String(slug).replace(/[-_]+/g, " ").replace(/\b\w/g, (ch) => ch.toUpperCase()) : "another project");

  async function openInvoiceDetails(opts) {
    opts = opts || {};
    let st;
    try { st = await jget(api("/" + B.slug + "/setup")); } catch (e) { toast(briefErr(e)); return; }
    const m = st.meta || {};
    const proj = (B.projects.find((p) => p.slug === B.slug) || {}).project || "";
    const ras = (opts.ras || []).map((id) => ((B.ov || {}).ras || []).find((r) => r.id === id)).filter(Boolean);
    // Your company is the same on every project: what was typed on another project is pre-filled here
    // (shown as a suggestion, kept only if you Save) and can be edited or cleared.
    const offer = st.profile && st.profile.fields ? st.profile.fields : null;
    const offerFrom = offer ? projName(st.profile.from) : "";
    const suggested = (k) => !!(offer && offer[k] && !String(m[k] || "").trim());
    const fld = (f) => {
      const v = suggested(f[0]) ? offer[f[0]] : (m[f[0]] == null ? (f[0] === "project_name" ? proj : "") : m[f[0]]);
      return `<label>${esc(f[1])}<input class="ctl${suggested(f[0]) ? " bl-sug" : ""}" data-m="${f[0]}" value="${esc(v)}"${f[2] ? ` placeholder="${esc(f[2])}"` : ""} autocomplete="off"></label>`;
    };
    const body = `<div class="bl-idet">
      <h4>Your company <small>printed at the bottom as “For …”</small><button type="button" class="bl-link" id="bl-iclrco">Clear company</button></h4>
      ${offer ? `<div class="bl-prenote" id="bl-inote">Filled from <b>${esc(offerFrom)}</b> — your company details are usually the same on every project. Check them, then Save; or clear them.</div>` : ""}
      <div class="bl-igrid">${F_CO.map(fld).join("")}</div>
      <h4>Client <small>billed to</small></h4><div class="bl-igrid">${F_CL.map(fld).join("")}</div>
      <h4>GST <small>leave blank if the invoice carries no GST</small></h4>
      <div class="bl-igrid"><label>SGST %<input class="ctl" data-m="sgst" type="number" step="any" min="0" max="50" value="${m.sgst == null ? "" : m.sgst}" inputmode="decimal"></label>
        <label>CGST %<input class="ctl" data-m="cgst" type="number" step="any" min="0" max="50" value="${m.cgst == null ? "" : m.cgst}" inputmode="decimal"></label></div>
      <h4>Services <small>SAC code, and the invoice line if you want your own words${st.services.some((x) => x.package) ? "; each Work Order has its own W/O number" : ""}</small></h4>
      ${st.services.map((x, i) => `<div class="bl-isvc"><b>${esc(x.service)}</b>
        <label>SAC code<input class="ctl" data-sac="${i}" value="${esc((m.sac || {})[x.service] || "")}" inputmode="numeric"></label>
        <label>Invoice line<input class="ctl" data-desc="${i}" value="${esc((m.inv_desc || {})[x.service] || "")}" placeholder="Supply and Installation of ${esc(x.package ? x.base + " (" + x.package + ")" : x.service)} Work"></label>
        ${x.package ? `<label>W/O number of this Work Order<input class="ctl" data-wono="${i}" value="${esc((m.wo_of || {})[x.service] || "")}" placeholder="${esc(m.wo_no || "")}" autocomplete="off"></label>` : ""}</div>`).join("")}
      ${ras.length ? `<h4>Invoice no. and date <small>of the RA${ras.length > 1 ? "s" : ""} you are downloading</small></h4>
        ${ras.map((r) => `<div class="bl-isvc"><b>${esc(r.service)} · ${esc(SERIES[r.series])} ${raL(r.no)}</b>
          <label>Invoice no.<input class="ctl" data-rno="${esc(r.id)}" value="${esc(r.inv_no || "")}"></label>
          <label>Date<input class="ctl" type="date" data-rdt="${esc(r.id)}" value="${esc(r.inv_date || "")}"></label></div>`).join("")}` : ""}
    </div>`;
    const clearInputs = (sel) => {
      document.querySelectorAll("#bl-modal " + sel).forEach((el) => { el.value = ""; el.classList.remove("bl-sug"); });
      const n = $("bl-inote"); if (n) n.remove();
    };
    modal("Invoice details", "Filled once, used on the Tax Invoice sheet of every RA. Anything left blank stays blank on the sheet.", body, [
      { label: "Cancel", run: () => { closeModal(); if (opts.back) opts.back(); } },
      { label: "Clear all", id: "bl-iclrall", run: () => { clearInputs("[data-m], [data-sac], [data-desc]"); toast("Cleared. Save to keep it empty."); } },
      { label: "Save", cls: "primary", id: "bl-isave", run: async () => {
        const root = $("bl-modal"), v = (k) => root.querySelector(`[data-m="${k}"]`).value;
        const payload = {};
        [...F_CO, ...F_CL].forEach((f) => { payload[f[0]] = v(f[0]); });
        payload.sgst = v("sgst"); payload.cgst = v("cgst");
        payload.sac = {}; payload.inv_desc = {}; payload.wo_of = {};
        st.services.forEach((x, i) => {
          payload.sac[x.service] = root.querySelector(`[data-sac="${i}"]`).value;
          payload.inv_desc[x.service] = root.querySelector(`[data-desc="${i}"]`).value;
          const w = root.querySelector(`[data-wono="${i}"]`); if (w) payload.wo_of[x.service] = w.value;
        });
        await jsend(api("/" + B.slug + "/meta"), payload);
        for (const r of ras) {
          const no = root.querySelector(`[data-rno="${r.id}"]`).value, dt = root.querySelector(`[data-rdt="${r.id}"]`).value;
          if (no !== (r.inv_no || "") || dt !== (r.inv_date || "")) await jsend(api("/" + B.slug + "/ra/" + r.id + "/invoice"), { no, date: dt });
        }
        closeModal(); toast("Invoice details saved.");
        if (opts.done) await opts.done();
      } }], "min(820px,96vw)");
    $("bl-modal").querySelector(".card").classList.add("bl-dlcard");        // header and Save stay in view, the form scrolls
    $("bl-iclrco").onclick = () => clearInputs(F_CO.map((f) => `[data-m="${f[0]}"]`).join(", "));
    document.querySelectorAll("#bl-modal .bl-sug").forEach((el) => el.addEventListener("input", () => el.classList.remove("bl-sug")));
  }

  // -------------------------------------------------------------- download
  // ONE place for everything that leaves the app as Excel: tick what, which sheets, which RAs.
  // The server builds it (GET .../download); this file only plans and previews it.
  const SHEETS = [["invoice", "Tax Invoice", "Your invoice format, filled in"], ["summary", "Annexure B-Summary BOQ", "Summary of the bill"],
    ["boq", "Annexure B-BOQ", "Line by line"], ["abstract", "Challan Abstract", "RA-wise qty · challan services"], ["challan", "Challan", "Challan-wise qty · challan services"]];
  const SER_ORD = { supply: 0, install: 1, together: 2 };
  const slugSafe = (t) => String(t).replace(/[^A-Za-z0-9._-]+/g, "_");
  const raFile = (r) => `${slugSafe(B.slug)}_${slugSafe(String(r.service).split(WOSEP).join("-"))}_${SERIES[r.series].replace(" + ", "+").replace(/ /g, "")}_RA-${String(r.no).padStart(2, "0")}${r.status === "draft" ? "_DRAFT" : ""}.xlsx`;
  const pbFile = (r) => raFile(r).replace(slugSafe(B.slug) + "_", slugSafe(B.slug) + "_ProjectBase_");
  const latestIds = (ov) => {
    const best = {};
    ov.ras.forEach((r) => { const k = r.service + "|" + r.series; if (!best[k] || r.no > best[k].no) best[k] = r; });
    // a Together RA that later Supply / Installation RAs have fully taken over (both stages billed again) is history, not "latest"
    const all = Object.values(best);
    return all.filter((r) => !(r.stages || []).every((st) => ov.ras.some((o) => o.service === r.service && o.no > r.no && (o.stages || []).includes(st)))).map((r) => r.id);
  };

  // What a set of ticks will produce. Pure: the same ticks always give the same plan.
  function planDownload(ov, DL) {
    const sheetKeys = SHEETS.map((x) => x[0]).filter((k) => DL.sheets[k]);
    const src = (name) => ((ov.services.find((s) => s.service === name) || {}).supply_source);
    const chosen = ov.ras.filter((r) => DL.sel.has(r.id)).sort((a, b) =>
      ov.services.findIndex((s) => s.service === a.service) - ov.services.findIndex((s) => s.service === b.service) || a.no - b.no || SER_ORD[a.series] - SER_ORD[b.series]);
    const sheetsOf = (r) => {
      const hasSup = r.stages.includes("supply"), ch = src(r.service) === "challan";
      let n = (DL.sheets.invoice ? 1 : 0) + (DL.sheets.summary ? 1 : 0) + (DL.sheets.boq ? 1 : 0);
      if (hasSup && ch) n += (DL.sheets.abstract ? 1 : 0) + (DL.sheets.challan ? 1 : 0);
      return { n, noChallan: hasSup && !ch && (DL.sheets.abstract || DL.sheets.challan) };
    };
    const files = [];
    const wantInv = DL.invoice && sheetKeys.length > 0 && chosen.length > 0;
    let counts = chosen.map(sheetsOf);
    if (wantInv) {
      if (DL.mode === "one") {
        files.push({ name: `${slugSafe(B.slug)}_RA_invoices_${today()}.xlsx`, note: `${chosen.length} RA · ${counts.reduce((a, c) => a + c.n, 0)} sheets, named by service` });
      } else {
        chosen.forEach((r, i) => files.push({ name: raFile(r), note: `${counts[i].n} sheets · ${r.status === "draft" ? "Draft" : "Invoiced"}` }));
      }
    }
    // the ProjectBase sheet: one file per ticked RA whose service is connected (the others are named in a warning, not guessed)
    const pbMeta = ov.pb || {};
    const hasPb = (r) => !!pbMeta[unitBase(r.service)];
    const pbIds = DL.pb ? chosen.filter(hasPb).map((r) => r.id) : [];
    chosen.filter((r) => pbIds.includes(r.id)).forEach((r) => files.push({ name: pbFile(r), pb: true, note: "ProjectBase sheet · only the Now column is filled" }));
    if (DL.combined && ov.ras.length) files.push({ name: `${slugSafe(B.slug)}_RA_bills_${today()}.xlsx`, note: "Every service and RA · BOQ with bill columns, invoices, challan sheets" });
    const q = new URLSearchParams();
    if (wantInv) { q.set("ras", chosen.map((r) => r.id).join(",")); q.set("mode", DL.mode); q.set("sheets", sheetKeys.join(",")); }
    if (pbIds.length) { q.set("pb", "1"); q.set("pbras", pbIds.join(",")); if (!wantInv) q.set("invoice", "0"); }
    if (DL.combined) q.set("combined", "1");
    // things the person should know BEFORE downloading
    const warnings = [];
    const drafts = wantInv ? chosen.filter((r) => r.status === "draft").length : 0;
    if (drafts) warnings.push({ k: "draft", text: `${drafts} draft RA ${drafts === 1 ? "is" : "are"} ticked. The file says “DRAFT — not invoiced yet” and its numbers are live formulas.` });
    if (wantInv) {
      const bad = [...new Set(chosen.filter((r, i) => counts[i].noChallan).map((r) => r.service))];
      bad.forEach((name) => {
        const sv = ov.services.find((s) => s.service === name);
        warnings.push({ k: "stock", go: sv && !sv.source_locked, text: sv && sv.source_locked
          ? `${name} is on the stock register and already has an invoiced Supply RA, so it cannot switch to challans: no Challan Abstract / Challan sheets.`
          : `${name} is on the stock register, so no Challan Abstract / Challan sheets. Switch it to Delivery challans in Billing setup.` });
      });
    }
    if (DL.pb) {
      const lack = [...new Set(chosen.filter((r) => !hasPb(r)).map((r) => unitBase(r.service)))];
      lack.forEach((base) => warnings.push({ k: "pbn", base, text: `${base} has no ProjectBase sheet connected, so its ticked RA gets no ProjectBase file.` }));
    }
    const scope = (DL.invoice && DL.sheets.invoice && wantInv) ? chosen : [];
    const missing = [...new Set([...scope, ...(DL.combined ? ov.ras : [])].flatMap((r) => r.inv_missing || []))];
    if (missing.length) warnings.push({ k: "details", text: `Blank on the Tax Invoice sheet: ${brief(missing)}.` });
    return { chosen, files, pbIds, url: files.length ? api("/" + encodeURIComponent(B.slug) + "/download?" + q.toString()) : null, warnings, sheetsOf, zip: files.length > 1 };
  }

  async function fetchDownload(url) {
    const r = await fetch(url);
    if (!r.ok) throw Object.assign(new Error("http"), { status: r.status, body: await r.text() });
    const m = /filename="?([^";]+)"?/.exec(r.headers.get("Content-Disposition") || "");
    const blob = await r.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = m ? m[1] : "billing.xlsx"; a.style.display = "none";
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
    return a.download;
  }

  async function openDownload(pre, keep) {
    let ov;
    try { ov = await jget(api("/" + B.slug)); B.ov = ov; } catch (e) { toast(briefErr(e)); return; }
    if (!ov.ras.length) { toast("No RA yet — create an RA bill first."); return; }
    const DL = keep || { invoice: true, mode: "files", combined: false, pb: Object.keys(ov.pb || {}).length > 0, sheets: { invoice: true, summary: true, boq: true, abstract: true, challan: true }, sel: new Set() };
    DL.pbrep = {};                                   // per-RA report of the ProjectBase sheet, fetched when it is ticked
    if (!keep) DL.sel = new Set(pre && pre.length ? pre : latestIds(ov));
    DL.sel = new Set([...DL.sel].filter((id) => ov.ras.some((r) => r.id === id)));
    const allGroups = ov.services.map((s) => ({ s, ras: ov.ras.filter((r) => r.service === s.service).sort((a, b) => a.no - b.no || SER_ORD[a.series] - SER_ORD[b.series]) }));
    const groups = () => allGroups.filter((g) => g.ras.length);           // services with no RA yet are only named, not listed
    const noRa = () => allGroups.filter((g) => !g.ras.length).map((g) => g.s.service);
    const pbCard = (ck) => {
      const meta = ov.pb || {}, bases = (ov.bases || []).map((x) => x.base);
      const on = Object.keys(meta).filter((k) => bases.includes(k)), off = bases.filter((k) => !meta[k]);
      return `<div class="bl-fmt ${DL.pb && on.length ? "on" : ""}${on.length ? "" : " off"}" id="bl-pbcard"><label class="bl-fmtt"><input type="checkbox" data-dl="pb"${ck(DL.pb && on.length)}${on.length ? "" : " disabled"}>
          <div><b>ProjectBase sheet</b> ${on.length ? `<span class="bl-tag ok">Connected</span>` : `<span class="bl-tag mute">Not connected</span>`}
          <small>Your ProjectBase Billing Request file with the Now column filled for each ticked RA. Nothing else in it is changed. One file per RA.</small></div></label>
        ${on.length ? `<div class="bl-pbl">${on.map((k) => `<div class="bl-pbi"><span><b>${esc(k)}</b> · ${esc(meta[k].project || "")}</span><span class="bl-pbact"><button type="button" class="bl-link" data-pbc="${esc(k)}">Replace file</button><button type="button" class="bl-link" data-pbx="${esc(k)}">Disconnect</button></span></div>`).join("")}</div>` : ""}
        ${off.length ? `<div class="bl-pbl nc"><span class="bl-pbt">${on.length ? "Not connected" : "Connect once per service"}: ${off.map(esc).join(", ")}${on.length ? "" : ". After that every download can include its ProjectBase sheet."}</span>
          <span class="bl-pbact">${off.map((k) => `<button type="button" class="btn" data-pbc="${esc(k)}">Connect ${esc(k)}…</button>`).join("")}</span></div>` : ""}</div>`;
    };
    const pbPanel = (P) => P.pbIds.length ? `<div class="bl-lbl">ProjectBase sheet check</div>${P.pbIds.map((id) => {
      const r = DL.pbrep[id], ra = ov.ras.find((x) => x.id === id);
      if (!r) return `<div class="bl-pbr"><div class="bl-pbh">${esc(ra ? raName(ra) : "")}</div><div class="bl-hint">Checking the sheet…</div></div>`;
      if (r.error) return `<div class="bl-wn"><span>${esc(r.error)}</span></div>`;
      const kv = (k, v, cls) => `<div class="bl-kv ${cls || ""}"><span>${k}</span><b>${v}</b></div>`;
      const notes = [];
      if (r.not_found.length) notes.push(["warn", `${r.not_found.length} line${r.not_found.length === 1 ? "" : "s"} of this RA ${r.not_found.length === 1 ? "is" : "are"} not in the sheet and will not be imported: ${brief(r.not_found.map((x) => x.item_code + " (" + qn(x.qty) + ")"), 4)}.`]);
      if (r.ask) notes.push(["warn", `${r.ask} line${r.ask === 1 ? "" : "s"} of ${esc(r.base)} could not be matched by Internal Ref No. Connect the file again to review them.`]);
      if (r.negative.length) notes.push(["warn", `Negative quantity on ${brief(r.negative, 4)}. ProjectBase may not accept it.`]);
      if (r.manual.length) notes.push(["warn", `${r.manual.length} line${r.manual.length === 1 ? " has" : "s have"} no quantity yet (type it in the RA) and ${r.manual.length === 1 ? "is" : "are"} not in the sheet.`]);
      if (r.odd.length) notes.push(["warn", `${r.odd.length} line${r.odd.length === 1 ? "" : "s"} have rows MI could not tell apart, so nothing was written for them: ${brief(r.odd, 4)}.`]);
      if (r.cleared.length) notes.push(["info", `${r.cleared.length} old quantit${r.cleared.length === 1 ? "y" : "ies"} in the Now column of the exported file ${r.cleared.length === 1 ? "is" : "are"} blanked, so ${r.cleared.length === 1 ? "it" : "they"} do not get imported into this bill.`]);
      if (r.left.length) notes.push(["info", `${r.left.length} Testing / Handover row${r.left.length === 1 ? " has" : "s have"} a quantity in the file. MI leaves ${r.left.length === 1 ? "it" : "them"} as they are.`]);
      return `<div class="bl-pbr"><div class="bl-pbh">${esc(r.label)}</div>
        ${kv("Lines filled", `${r.filled} of ${r.lines_in_ra}`)}
        ${kv("Not found in the sheet", r.not_found.length, r.not_found.length ? "bad" : "")}
        ${kv("Testing / Handover rows", r.left.length ? "left as they are" : "left empty")}
        ${kv("Total after import", inr(r.expect_total, true), "tot")}
        ${notes.map(([k, t]) => `<div class="bl-pbn ${k}">${t}</div>`).join("")}</div>`;
    }).join("")}<div class="bl-hint">After you import it, ProjectBase should show the “Total after import”. You can check it with MI on the next screen.</div>` : "";
    const raName = (r) => `${r.service.split(WOSEP).join(" › ")} · ${SERIES[r.series]} ${raL(r.no)}`;
    const ensurePb = () => {
      const need = planDownload(ov, DL).pbIds.filter((id) => !(id in DL.pbrep));
      if (!need.length || DL.pbBusy) return;
      DL.pbBusy = true;
      jget(api("/" + B.slug + "/projectbase/preview?ras=" + need.join(",")))
        .then((j) => { j.ras.forEach((x) => { DL.pbrep[x.id] = x; }); })
        .catch((e) => { need.forEach((id) => { DL.pbrep[id] = { error: briefErr(e) }; }); })
        .finally(() => { DL.pbBusy = false; if ($("bl-dl-go")) upd(); });
    };
    const body = () => {
      const P = planDownload(ov, DL);
      const ck = (on) => (on ? " checked" : "");
      return `<div class="bl-dl"><div class="bl-dlL">
        <div class="bl-lbl">1 · What to download</div>
        <div class="bl-fmt ${DL.invoice ? "on" : ""}"><label class="bl-fmtt"><input type="checkbox" data-dl="invoice"${ck(DL.invoice)}>
            <div><b>Invoice workbook</b> <span class="bl-tag">Recommended</span>
            <small>Your invoice layout: Tax Invoice, Annexure B-Summary BOQ, Annexure B-BOQ and, for services on delivery challans, Challan Abstract and Challan.</small></div></label>
          <div class="bl-seg ${DL.invoice ? "" : "off"}" role="radiogroup" aria-label="Invoice workbook files">
            <button type="button" class="${DL.mode === "files" ? "on" : ""}" data-mode="files" role="radio" aria-checked="${DL.mode === "files"}">One file per RA</button>
            <button type="button" class="${DL.mode === "one" ? "on" : ""}" data-mode="one" role="radio" aria-checked="${DL.mode === "one"}">All in one workbook</button></div></div>
        ${pbCard(ck)}
        <div class="bl-fmt ${DL.combined ? "on" : ""}"><label class="bl-fmtt"><input type="checkbox" data-dl="combined"${ck(DL.combined)}>
            <div><b>All services Excel</b>
            <small>Your BOQ with the bill columns of every RA side by side, plus each RA’s Tax Invoice and each service’s Challan Abstract and Challan. Always every service and every RA.</small></div></label></div>
        <div class="bl-lbl">2 · Sheets in the invoice workbook</div>
        <div class="bl-shs ${DL.invoice ? "" : "off"}">${SHEETS.map(([k, n, d]) => `<label class="bl-ck ${DL.sheets[k] ? "on" : ""}" title="${esc(d)}"><input type="checkbox" data-sh="${k}"${ck(DL.sheets[k])}${DL.invoice ? "" : " disabled"}><span>${esc(n)}</span></label>`).join("")}</div>
        <div class="bl-hint">Challan Abstract and Challan exist only for services whose supply quantity comes from delivery challans.</div>
        <div class="bl-dlq"><div class="bl-lbl">3 · Which RAs</div><div class="bl-qk">
          <button type="button" class="btn" data-q="latest">Latest of each service</button><button type="button" class="btn" data-q="locked">All invoiced</button>
          <button type="button" class="btn" data-q="all">All</button><button type="button" class="btn" data-q="none">Clear</button></div>
          <div class="bl-qk bl-qt"><span class="bl-qtl">By type</span>${["supply", "install", "together"].map((k) => { const ids = ov.ras.filter((r) => r.series === k).map((r) => r.id);
            const on = ids.length && ids.length === DL.sel.size && ids.every((id) => DL.sel.has(id));
            return `<button type="button" class="btn ${on ? "on" : ""}" data-qt="${k}" aria-pressed="${on ? "true" : "false"}"${ids.length ? "" : " disabled"}>${esc(SERIES[k])} · ${ids.length}</button>`; }).join("")}</div>
          <div class="bl-hint bl-qth">By type ticks every RA of that type, in every service. Each one stays its own invoice.</div></div>
        ${groups().map((g, gi) => { const picked = g.ras.filter((r) => DL.sel.has(r.id)).length; const ch = g.s.supply_source === "challan";
          return `<div class="bl-ds"><label class="bl-dsh"><input type="checkbox" data-svc="${gi}"${ck(picked === g.ras.length)}>
            <b>${esc(g.s.service)}</b><span class="bl-src2 ${ch ? "ch" : "st"}">${ch ? "Delivery challans" : "Stock register"}</span><span class="bl-cnt">${picked} / ${g.ras.length}</span></label>
            ${g.ras.map((r) => { const c = P.sheetsOf(r);
              const noInst = r.stages.includes("install") && r.stage_this && r.stage_this.install === 0 ? " · Installation ₹0" : "";
              const sub = (!DL.invoice ? "In the All services Excel" : c.noChallan ? `${c.n} sheets · no challan sheets (stock register)` : (r.stages.includes("supply") ? `${c.n} sheets` : `${c.n} sheets · no challan sheets in an installation RA`)) + noInst;
              return `<label class="bl-dr"><input type="checkbox" data-ra="${esc(r.id)}"${ck(DL.sel.has(r.id))}>
                <span class="t">${esc(SERIES[r.series])} ${raL(r.no)}<small>${esc(sub)}${r.inv_no ? " · Inv. " + esc(r.inv_no) : ""}</small></span>
                <span class="bl-chip ${r.status}">${r.status === "draft" ? "Draft" : "Invoiced"}</span></label>`; }).join("")}</div>`; }).join("")}
        ${noRa().length ? `<div class="bl-hint">No RA yet: ${noRa().map(esc).join(", ")}.</div>` : ""}
      </div><div class="bl-dlR">
        <div class="bl-lbl">In the download</div>
        ${P.files.length ? `<div class="bl-fl">${P.files.map((f) => `<div class="bl-fi"><svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" aria-hidden="true"><path d="M4 1.8h7l3.2 3.2v11.2H4z"/><path d="M11 1.8V5h3.2M6.5 9h5M6.5 12h5"/></svg><div><b>${esc(f.name).replace(/_/g, "_<wbr>")}</b><small>${esc(f.note)}</small></div></div>`).join("")}</div>`
          : `<div class="bl-none sm bl-fempty">Nothing ticked yet.</div>`}
        ${pbPanel(P)}
        ${P.warnings.map((w) => `<div class="bl-wn ${w.k}"><span>${esc(w.text)}</span>${w.k === "details" ? `<button type="button" class="btn" data-act="details">Fill details</button>` : w.k === "pbn" ? `<button type="button" class="btn" data-pbc="${esc(w.base)}">Connect…</button>` : w.go ? `<button type="button" class="btn" data-act="setup">Billing setup</button>` : ""}</div>`).join("")}
      </div></div>`;
    };
    const upd = () => {
      const m = $("bl-modal"); if (!m) return;
      const P = planDownload(ov, DL);
      const L = m.querySelector(".bl-dlL"), top = L ? L.scrollTop : 0, topR = m.querySelector(".bl-mbody").scrollTop;
      m.querySelector(".bl-mbody").innerHTML = body();
      const L2 = m.querySelector(".bl-dlL"); if (L2) L2.scrollTop = top;
      m.querySelector(".bl-mbody").scrollTop = topR;
      const go = $("bl-dl-go");
      go.disabled = !P.files.length;
      go.textContent = !P.files.length ? "Download" : P.files.length === 1 ? "Download .xlsx" : `Download ${P.files.length} files (.zip)`;
      $("bl-dlsum").textContent = P.files.length ? `${P.files.length} ${P.files.length === 1 ? "file" : "files"}${P.zip ? " · one zip" : ""} · ${P.chosen.length} RA` : "Tick what you need";
      wire();
      ensurePb();
    };
    const wire = () => {
      const m = $("bl-modal");
      m.querySelectorAll("[data-dl]").forEach((el) => el.onchange = () => { DL[el.dataset.dl] = el.checked; upd(); });
      m.querySelectorAll("[data-sh]").forEach((el) => el.onchange = () => { DL.sheets[el.dataset.sh] = el.checked; upd(); });
      m.querySelectorAll("[data-mode]").forEach((el) => el.onclick = () => { DL.mode = el.dataset.mode; DL.invoice = true; upd(); });
      m.querySelectorAll("[data-ra]").forEach((el) => el.onchange = () => { el.checked ? DL.sel.add(el.dataset.ra) : DL.sel.delete(el.dataset.ra); upd(); });
      m.querySelectorAll("[data-svc]").forEach((el) => el.onchange = () => {
        groups()[+el.dataset.svc].ras.forEach((r) => { el.checked ? DL.sel.add(r.id) : DL.sel.delete(r.id); }); upd(); });
      m.querySelectorAll("[data-q]").forEach((el) => el.onclick = () => {
        const q = el.dataset.q;
        DL.sel = new Set(q === "latest" ? latestIds(ov) : q === "locked" ? ov.ras.filter((r) => r.status === "locked").map((r) => r.id) : q === "all" ? ov.ras.map((r) => r.id) : []);
        upd(); });
      m.querySelectorAll("[data-qt]").forEach((el) => el.onclick = () => {
        DL.sel = new Set(ov.ras.filter((r) => r.series === el.dataset.qt).map((r) => r.id)); upd(); });
      m.querySelectorAll("[data-pbc]").forEach((el) => el.onclick = () => openConnect(el.dataset.pbc, () => { DL.pb = true; openDownload(null, DL); }));
      m.querySelectorAll("[data-pbx]").forEach((el) => el.onclick = async () => {
        const k = el.dataset.pbx;
        if (!confirm(`Disconnect the ProjectBase sheet of ${k}? Your RAs are not affected. You can connect it again any time.`)) return;
        try { await jsend(api("/" + B.slug + "/projectbase/" + encodeURIComponent(k)), undefined, "DELETE"); toast("ProjectBase sheet of " + k + " disconnected."); openDownload(null, DL); }
        catch (e) { toast(briefErr(e)); }
      });
      m.querySelectorAll("[data-act]").forEach((el) => el.onclick = () => {
        const P = planDownload(ov, DL);
        if (el.dataset.act === "setup") { closeModal(); showSetup(); return; }
        closeModal();
        openInvoiceDetails({ ras: P.chosen.map((r) => r.id), done: () => openDownload(null, DL), back: () => openDownload(null, DL) });
      });
    };
    modal("Download", "Tick what you need. Everything comes in one go.", `<div id="bl-dlbody"></div>`, [
      { label: "Cancel", run: closeModal },
      { label: "Download", cls: "primary", id: "bl-dl-go", run: async () => {
        const P = planDownload(ov, DL); if (!P.url) return;
        const go = $("bl-dl-go"); go.disabled = true; go.textContent = "Preparing…";
        try {
          const nm = await fetchDownload(P.url);
          if (P.pbIds.length) { const reps = P.pbIds.map((id) => DL.pbrep[id]).filter((r) => r && !r.error); closeModal(); openHandoff(P.pbIds, reps, nm); return; }
          closeModal(); toast("Downloaded " + nm);
        }
        catch (e) { go.disabled = false; upd(); throw e; }
      } }], "min(1040px,96vw)");
    const act = document.querySelector("#bl-modal .mact");
    const sum = document.createElement("span"); sum.id = "bl-dlsum"; sum.className = "bl-dlsum"; act.insertBefore(sum, act.firstChild);
    $("bl-modal").querySelector(".card").classList.add("bl-dlcard");
    $("bl-modal").querySelector(".bl-mbody").innerHTML = body();
    upd();
  }

  // ---------------------------------------------------------------- ProjectBase: connect (once per service)
  // The person exports a Billing Request from ProjectBase and drops it here. MI keeps it and later returns the same file with only
  // the Now column filled. Lines are matched by Internal Ref No; a line MI cannot match is asked about, never guessed.
  function openConnect(base, done) {
    let found = null, yes = {}, file = null, err = "";
    const step1 = () => `<div class="bl-pb1"><div class="bl-lbl">Step 1 of 2 · first time only</div>
        <p class="bl-pbp">MI needs to know your ProjectBase sheet once. After this, every download can include it.</p>
        <ol class="bl-pbo"><li><span>1</span><div>In ProjectBase, open any Billing Request of this project and press <b>Export</b>.</div></li><li><span>2</span><div>Drop that file here.</div></li></ol>
        <label class="bl-drop" id="bl-pbdrop" tabindex="0"><input type="file" id="bl-pbfile" accept=".xlsx,.xlsm" hidden>
          <b>Drop the ProjectBase export here</b><small>or choose a file</small></label>
        ${err ? `<div class="bl-wn" role="alert"><span>${esc(err)}</span></div>` : ""}
        <div class="bl-pbk"><b>What MI reads</b> Project code, each BOQ line’s Internal Ref No, and its Supply / Installation / Testing / Handover rows. It never writes to ProjectBase; you import the file yourself.</div></div>`;
    const step2 = () => {
      const j = found, nAsk = j.ask.length;
      const kv = (k, v) => `<div class="bl-kv"><span>${k}</span><b>${v}</b></div>`;
      return `<div class="bl-pb2"><div class="bl-lbl">Step 2 of 2 · check what MI found</div>
        <div class="bl-pbproj">${esc(j.project || "")} <small>${esc(file ? file.name : "")}</small></div>
        <div class="bl-card bl-pbsum">${kv("Layout", `<span class="bl-ok">Recognised</span>`)}${kv("BOQ lines in the file", j.lines)}${kv("Billing rows per line", ["supply", "install", "testing", "handover"].map((s) => (j.pct[s] == null ? "—" : j.pct[s] + "%")).join(" · "))}
          ${j.units.map((u) => kv(esc(u.unit), `${u.matched} of ${u.total} lines matched`)).join("")}</div>
        ${j.warnings.map((w) => `<div class="bl-wn"><span>${esc(w)}</span></div>`).join("")}
        ${nAsk ? `<div class="bl-lbl">${nAsk} line${nAsk === 1 ? "" : "s"} need${nAsk === 1 ? "s" : ""} your OK</div>
          ${j.ask.map((a, i) => `<div class="bl-ask" data-i="${i}"><p>MI has no matching Internal Ref No for <b>${esc(a.item_code)}</b>. One line in the file has the same Client Ref No, unit and quantity. MI will not guess.</p>
            <div class="bl-askc"><div><small>In MI</small>“${esc(a.mi.desc)}” · ${qn(a.mi.qty)} ${esc(a.mi.unit || "")}</div><div><small>In the file</small>“${esc(a.file.desc)}” · ${qn(a.file.qty)} ${esc(a.file.unit || "")} · Ref ${esc(a.file.ref)}</div></div>
            <div class="bl-seg"><button type="button" class="${yes[a.item_code] ? "on" : ""}" data-yes="${i}">Yes, same line</button><button type="button" class="${yes[a.item_code] ? "" : "on"}" data-no="${i}">Skip this line</button></div></div>`).join("")}` : ""}
        ${j.n_missing ? `<div class="bl-hint">${j.n_missing} line${j.n_missing === 1 ? "" : "s"} of ${esc(base)} in MI ${j.n_missing === 1 ? "is" : "are"} not in this file (${esc(brief(j.missing.slice(0, 3).map((x) => x.item_code), 3))}${j.n_missing > 3 ? "…" : ""}). If an RA bills ${j.n_missing === 1 ? "it" : "them"}, the download says so.</div>` : ""}</div>`;
    };
    const view = () => {
      const bd = $("bl-modal").querySelector(".bl-mbody");
      bd.innerHTML = found ? step2() : step1();
      const go = $("bl-pbgo"), back = $("bl-pbback");
      go.hidden = !found; back.hidden = !found;
      if (found) go.textContent = "Connect " + base;
      if (!found) {
        const dz = $("bl-pbdrop"), inp = $("bl-pbfile");
        dz.onclick = (e) => { if (e.target !== inp) inp.click(); };
        dz.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); inp.click(); } };
        dz.ondragover = (e) => { e.preventDefault(); dz.classList.add("over"); };
        dz.ondragleave = () => dz.classList.remove("over");
        dz.ondrop = (e) => { e.preventDefault(); dz.classList.remove("over"); const f = e.dataTransfer && e.dataTransfer.files[0]; if (f) pick(f); };
        inp.onchange = () => { if (inp.files[0]) pick(inp.files[0]); };
      } else {
        bd.querySelectorAll("[data-yes]").forEach((el) => el.onclick = () => { const a = found.ask[+el.dataset.yes]; yes[a.item_code] = a.file.ref; view(); });
        bd.querySelectorAll("[data-no]").forEach((el) => el.onclick = () => { delete yes[found.ask[+el.dataset.no].item_code]; view(); });
      }
    };
    const pick = async (f) => {
      file = f; err = "";
      const dz = $("bl-pbdrop"); if (dz) dz.classList.add("busy");
      const fd = new FormData(); fd.append("file", f);
      try {
        const r = await fetch(api("/" + B.slug + "/projectbase/" + encodeURIComponent(base) + "/inspect"), { method: "POST", body: fd });
        if (!r.ok) throw Object.assign(new Error("http"), { status: r.status, body: await r.text() });
        found = await r.json(); yes = {};
      } catch (e) { err = briefErr(e); found = null; }
      view();
    };
    modal("Connect ProjectBase for " + base, "", "", [
      { label: "Back", id: "bl-pbback", run: async () => { found = null; err = ""; view(); } },
      { label: "Cancel", run: closeModal },
      { label: "Connect", cls: "primary", id: "bl-pbgo", run: async () => {
        if (!found) return;
        const go = $("bl-pbgo"); go.disabled = true;
        try {
          const r = await jsend(api("/" + B.slug + "/projectbase/" + encodeURIComponent(base) + "/connect"), { confirm: yes, name: file ? file.name : null });
          closeModal(); if (done) done(); else toast("ProjectBase connected for " + base + (r.project ? " · " + r.project : "") + ".");
        } catch (e) { go.disabled = false; throw e; }
      } }], "min(720px,96vw)");
    $("bl-modal").querySelector(".card").classList.add("bl-pbcard");
    view();
  }

  // ---------------------------------------------------------------- ProjectBase: what happens after the download
  // MI cannot write into ProjectBase, so the last mile is the person's: create a request, import the file, then type the Subtotal
  // ProjectBase shows. MI compares it with the total it put in the file and, when they differ, names the line that explains it.
  function openHandoff(ids, reps, downloaded) {
    const ras = ids.map((id) => (B.ov ? B.ov.ras.find((r) => r.id === id) : null)).filter(Boolean);
    const res = {};                                             // id -> check result
    let imported = false;
    const nm = (id) => { const r = reps.find((x) => x.id === id); return r ? r.label : id; };
    const card = (id) => {
      const x = res[id], ra = ras.find((r) => r.id === id), r = reps.find((y) => y.id === id) || {};
      const locked = ra && ra.status === "locked";
      let out = "";
      if (x) {
        const c = (k, v) => `<div><small>${k}</small><b>${inr(v, true)}</b></div>`;
        out = x.match
          ? `<div class="bl-res ok" data-res="${esc(id)}"><div class="bl-resh"><span class="bl-rm">✓</span><b>Totals match</b></div><div class="bl-resv">${c("MI", x.mi)}${c("ProjectBase", x.pb)}</div>
              <div class="bl-resa">${locked ? `<span class="bl-hint">Already marked as invoiced.</span>` : `<button type="button" class="btn primary" data-lockra="${esc(id)}">Mark as invoiced</button>`}</div></div>`
          : `<div class="bl-res bad" data-res="${esc(id)}"><div class="bl-resh"><span class="bl-rm">!</span><b>${inr(Math.abs(x.diff), true)} apart</b></div><div class="bl-resv">${c("MI", x.mi)}${c("ProjectBase", x.pb)}</div>
              ${x.suspects.length ? `<p class="bl-resp">Usually one line. Compare ${x.suspects.length === 1 ? "this" : "these"} in ProjectBase:</p>${x.suspects.map((s) => `<div class="bl-sus"><b>${esc(s.item_code)}</b><span>${esc(s.desc)}<small>MI sent ${qn(s.sent)} ${esc(s.unit || "")}. If ProjectBase shows ${qn(s.would_be)}, that is the whole difference.</small></span></div>`).join("")}`
                : `<p class="bl-resp">No single line explains it. Check that the whole file was imported, and that no line was added or removed in ProjectBase.</p>`}
              ${x.not_found ? `<p class="bl-resp">${x.not_found} line${x.not_found === 1 ? "" : "s"} of this RA ${x.not_found === 1 ? "was" : "were"} not in the sheet and ${x.not_found === 1 ? "is" : "are"} not part of MI’s total.</p>` : ""}
              <div class="bl-resa"><button type="button" class="btn" data-again="${esc(id)}">Download again</button>${locked ? "" : `<button type="button" class="btn" data-lockra="${esc(id)}">Mark as invoiced anyway</button>`}</div></div>`;
      }
      return `<div class="bl-chk"><div class="bl-chkh">${esc(nm(id))}</div>
        <div class="bl-chkr"><input class="ctl" type="number" step="any" inputmode="decimal" data-sub="${esc(id)}" placeholder="Subtotal from ProjectBase" aria-label="Subtotal ProjectBase shows for ${esc(nm(id))}"><button type="button" class="btn" data-chk="${esc(id)}">Check</button></div>${out}</div>`;
    };
    const body = () => `<div class="bl-ho">
      <div class="bl-hs done"><span class="bl-n ok">✓</span><div><b>Downloaded</b><small>${esc(downloaded || "")}</small></div></div>
      <div class="bl-hs ${imported ? "done" : ""}"><span class="bl-n ${imported ? "ok" : ""}">${imported ? "✓" : 2}</span><div><b>Import it in ProjectBase</b>
        <p>ProjectBase needs a request to exist first, so this part is yours:</p>
        <ol class="bl-hl"><li>Billing Requests → <b>Create</b>. Pick the project and date.</li><li>Add <b>any one line</b> with any quantity (it gets replaced).</li><li><b>Import</b> → choose the downloaded file.</li></ol>
        ${imported ? "" : `<div class="bl-hrow"><button type="button" class="btn primary" id="bl-imp">I have imported it</button><small>Takes about a minute</small></div>`}</div></div>
      <div class="bl-hs ${imported ? "" : "off"}"><span class="bl-n">3</span><div><b>Check the total</b><p>Type the Subtotal ProjectBase shows. MI compares it with its own.</p>
        ${imported ? ids.map(card).join("") : ""}</div></div>
      <div class="bl-tip"><b>Tip:</b> progress saved after you download can change MI’s numbers (a draft is live). Import first, then press <b>Mark as invoiced</b> to freeze them.</div></div>`;
    const typed = {};                                           // what is in each Subtotal box survives a re-render
    const view = () => {
      const bd = $("bl-modal").querySelector(".bl-mbody"), top = bd.scrollTop;
      bd.querySelectorAll("[data-sub]").forEach((el) => { typed[el.dataset.sub] = el.value; });
      bd.innerHTML = body(); bd.scrollTop = top;
      bd.querySelectorAll("[data-sub]").forEach((el) => { if (typed[el.dataset.sub] != null) el.value = typed[el.dataset.sub]; });
      if ($("bl-imp")) $("bl-imp").onclick = () => { imported = true; view(); const i = bd.querySelector("[data-sub]"); if (i) i.focus(); };
      bd.querySelectorAll("[data-chk]").forEach((el) => el.onclick = async () => {
        const id = el.dataset.chk, inp = bd.querySelector(`[data-sub="${id}"]`), v = inp.value.trim();
        if (v === "" || isNaN(Number(v))) { toast("Type the Subtotal ProjectBase shows."); inp.focus(); return; }
        el.disabled = true;
        try { res[id] = await jsend(api("/" + B.slug + "/ra/" + id + "/pb-check"), { subtotal: Number(v) }); }
        catch (e) { toast(briefErr(e)); }
        view();
      });
      bd.querySelectorAll("[data-sub]").forEach((el) => el.onkeydown = (e) => { if (e.key === "Enter") { const b = bd.querySelector(`[data-chk="${el.dataset.sub}"]`); if (b) b.click(); } });
      bd.querySelectorAll("[data-lockra]").forEach((el) => el.onclick = async () => {
        const id = el.dataset.lockra; el.disabled = true;
        try { await jsend(api("/" + B.slug + "/ra/" + id + "/lock"), {}); const ra = ras.find((r) => r.id === id); if (ra) ra.status = "locked"; toast(nm(id) + " marked as invoiced."); }
        catch (e) { toast(briefErr(e)); }
        view();
      });
      bd.querySelectorAll("[data-again]").forEach((el) => el.onclick = async () => {
        el.disabled = true;
        try { const q = new URLSearchParams({ pb: "1", invoice: "0", pbras: el.dataset.again }); await fetchDownload(api("/" + encodeURIComponent(B.slug) + "/download?" + q.toString())); toast("Downloaded again with MI’s current numbers."); }
        catch (e) { toast(briefErr(e)); }
        el.disabled = false;
      });
    };
    modal("Finish in ProjectBase: 3 steps", ids.length === 1 ? nm(ids[0]) : ids.length + " RAs", "", [{ label: "Done", cls: "primary", run: () => { closeModal(); showHome(); } }], "min(640px,96vw)");
    $("bl-modal").querySelector(".card").classList.add("bl-hocard");
    view();
  }

  // ---------------------------------------------------------------- setup
  async function showSetup() {
    const s = section();
    B.view = "setup";
    let st;
    try { st = await jget(api("/" + B.slug + "/setup")); } catch (e) { toast(briefErr(e)); return; }
    B.setup = st;
    const m = st.meta || {};
    s.innerHTML = head("Billing setup",
      "Each service has its own payment term. Set it once from the contract. Until it is set that service cannot be billed, and no % is guessed. “Usually billed as” only pre-selects the bill type: on every new RA you still choose Supply only, Installation only or Together.",
      `<button class="btn" id="bl-back">← Billing</button>`) + `
      <div class="bl-card bl-scroll"><div class="bl-sets">
        <div class="bl-sh"><div>Service</div><div title="Only decides which bill type is pre-selected on a new RA. Every RA can still be Supply only, Installation only or Together.">Usually billed as</div><div>Supply quantity from</div><div class="n">Supply %</div><div class="n">Installation %</div><div class="n">Contract value</div><div></div></div>
        ${st.services.map((x, i) => `<div class="bl-sr ${x.mode ? "" : "unset"}" data-i="${i}">
          <div><b>${x.package ? esc(x.base) + ` <i class="bl-wotag">› ${esc(x.package)}</i>` : esc(x.service)}</b><small>${x.lines} billable lines</small>
            ${x.package && st.services.findIndex((y) => y.base === x.base) === i ? `<button type="button" class="bl-link" data-wo="${esc(x.base)}">Work Orders…</button>` : ""}</div>
          <div><select class="ctl" data-f="mode" title="Only the default for new RAs. You can change it any time; each RA still lets you pick Supply only, Installation only or Together.">
            <option value="" ${x.mode ? "" : "selected"} disabled>Not set</option>
            <option value="separate" ${x.mode === "separate" ? "selected" : ""}>Separate Supply / Installation RAs</option>
            <option value="together" ${x.mode === "together" ? "selected" : ""}>Supply + Installation in one RA</option></select>
            <label class="bl-auto" title="When installation is logged in Site Progress, this service's draft RA opens by itself and follows Site Progress. Delete it and it stays closed until more work is logged."><input type="checkbox" data-f="auto_draft"${x.auto_draft === false ? "" : " checked"}> Open draft automatically</label></div>
          <div><select class="ctl" data-f="supply_source" title="Where the supply quantity of a bill comes from">
            <option value="challan" ${!x.mode || x.supply_source === "challan" ? "selected" : ""}>Delivery challans</option>
            <option value="stock" ${x.mode && x.supply_source === "stock" ? "selected" : ""}>Stock register</option></select></div>
          <div class="n"><input class="ctl" data-f="supply_pct" type="number" step="any" min="0" max="100" value="${x.supply_pct == null ? "" : x.supply_pct}" placeholder="—"></div>
          <div class="n"><input class="ctl" data-f="install_pct" type="number" step="any" min="0" max="100" value="${x.install_pct == null ? "" : x.install_pct}" placeholder="—"></div>
          <div class="n">${inr(x.contract_value)}</div>
          <div class="act"><button class="btn primary" data-save="${i}">Save</button><button class="btn" data-lp="${i}">Line %${Object.keys(x.line_pct || {}).length ? " (" + Object.keys(x.line_pct).length + ")" : ""}</button><button class="btn" data-groups="${i}">Annexure groups${x.groups_confirmed ? "" : " ·check"}</button></div>
        </div>`).join("")}
      </div></div>
      <div class="bl-cards3">
        <div class="bl-card pad"><b>A service with more than one Work Order?</b><p>Some services are contracted under several Work Orders (for example Electrical: point wiring and the rest). Each Work Order then gets its own RA numbers and invoice.</p>
          <div class="bl-wosplit"><select class="ctl" id="bl-wosel" aria-label="Service to split">${[...new Set(st.services.filter((x) => !x.package).map((x) => x.service))].map((n) => `<option>${esc(n)}</option>`).join("")}</select>
            <button class="btn" id="bl-wosplit">Set up Work Orders</button></div></div>
        <div class="bl-card pad"><b>One line has a different %?</b><p>Use “Line %” on a service row. A line’s own % overrides the service’s %.</p></div>
        <div class="bl-card pad"><b>Testing / retention stages</b><p>If your contract bills Testing & Commissioning or retention as a separate %, tell us — a third stage is needed. Nothing is assumed.</p></div>
        <div class="bl-card pad"><b>Overhead lumpsums</b><p>The “Overhead” lines in the sheet (Lumpsum Amount column) are not part of any RA yet.</p></div>
      </div>
      <h3 class="bl-h3">Invoice &amp; annexure details</h3>
      <div class="bl-card pad bl-metac"><div><b>${(st.invoice_missing || []).length ? (st.invoice_missing || []).length + " details still blank" : "All filled in"}</b>
        <p>${(st.invoice_missing || []).length ? "Blank on every Tax Invoice sheet: " + esc(brief(st.invoice_missing)) + "." : "Company, client, GST and W/O are set. Client, contractor and W/O also head the annexure sheets."}
          SAC code and invoice line are set per service; invoice no. and date on each RA.</p>
        ${st.profile ? `<p class="bl-prenote" id="bl-prenote">Your company details from <b>${esc(projName(st.profile.from))}</b> are ready: ${esc(brief(Object.values(st.profile.fields)))}.
          <button type="button" class="bl-link" id="bl-iuse">Use them here</button></p>` : ""}</div>
        <button class="btn primary" id="bl-idet">Edit invoice details</button></div>`;
    wireProj();
    $("bl-back").onclick = showHome;
    $("bl-idet").onclick = () => openInvoiceDetails({ done: showSetup });
    if ($("bl-iuse")) $("bl-iuse").onclick = async () => {
      try { await jsend(api("/" + B.slug + "/meta"), st.profile.fields); toast("Company details filled in."); showSetup(); } catch (e) { toast(briefErr(e)); }
    };
    s.querySelectorAll("[data-save]").forEach((el) => el.onclick = async () => {
      const row = el.closest(".bl-sr"), x = st.services[+el.dataset.save];
      const f = (n) => row.querySelector(`[data-f="${n}"]`);
      if (!f("mode").value) { toast("Choose how this service is billed."); return; }
      try {
        await jsend(api("/" + B.slug + "/setup"), { service: x.service, mode: f("mode").value, supply_source: f("supply_source").value, supply_pct: f("supply_pct").value, install_pct: f("install_pct").value, auto_draft: f("auto_draft").checked });
        toast(x.service + " saved."); showSetup();
      } catch (e) { toast(briefErr(e)); }
    });
    s.querySelectorAll("[data-groups]").forEach((el) => el.onclick = () => editGroups(st.services[+el.dataset.groups]));
    s.querySelectorAll("[data-wo]").forEach((el) => el.onclick = () => editPackages(el.dataset.wo));
    if ($("bl-wosplit")) $("bl-wosplit").onclick = () => editPackages($("bl-wosel").value);
    s.querySelectorAll("[data-lp]").forEach((el) => el.onclick = () => editLinePct(st.services[+el.dataset.lp]));
  }

  async function editLinePct(x) {
    if (!x.mode) { toast("Save the service’s payment term first."); return; }
    let all;
    try { all = (await jget(api("/" + B.slug + "/lines/" + encodeURIComponent(x.service)))).lines; } catch (e) { toast(briefErr(e)); return; }
    const lp = JSON.parse(JSON.stringify(x.line_pct || {}));
    const byCode = Object.fromEntries(all.map((l) => [l.item_code, l]));
    const draw = () => {
      $("bl-lpbody").innerHTML = `<div class="bl-lpl">${Object.keys(lp).map((c) => `<div class="bl-lpr" data-c="${esc(c)}"><span>${esc(((byCode[c] || {}).desc || c).slice(0, 90))}</span>
        <label>Supply %<input class="ctl" data-k="supply" type="number" step="any" value="${lp[c].supply == null ? "" : lp[c].supply}" placeholder="${x.supply_pct == null ? "—" : x.supply_pct}"></label>
        <label>Install %<input class="ctl" data-k="install" type="number" step="any" value="${lp[c].install == null ? "" : lp[c].install}" placeholder="${x.install_pct == null ? "—" : x.install_pct}"></label>
        <button class="btn" data-rm="${esc(c)}">Remove</button></div>`).join("") || `<div class="bl-none">No line has its own % yet.</div>`}</div>
        <input id="bl-lpf" class="ctl" type="search" placeholder="Search a line to give it its own %" autocomplete="off"><div id="bl-lpfound" class="bl-found"></div>`;
      document.querySelectorAll("#bl-lpbody .bl-lpr input").forEach((el) => el.oninput = () => {
        const c = el.closest(".bl-lpr").dataset.c; lp[c][el.dataset.k] = el.value === "" ? null : Number(el.value);
      });
      document.querySelectorAll("#bl-lpbody [data-rm]").forEach((el) => el.onclick = () => { delete lp[el.dataset.rm]; draw(); });
      const find = () => {
        const q = $("bl-lpf").value.trim().toLowerCase();
        const hits = q ? all.filter((l) => !lp[l.item_code] && (l.desc.toLowerCase().includes(q) || String(l.internal_ref || "") === q)).slice(0, 25) : [];
        $("bl-lpfound").innerHTML = hits.map((l) => `<button class="bl-hit" data-c="${esc(l.item_code)}"><span>${esc(l.desc.slice(0, 110))}</span><small>${l.internal_ref ? "Ref " + esc(l.internal_ref) + " · " : ""}${inr(l.rate, true)}</small></button>`).join("");
        document.querySelectorAll("#bl-lpfound .bl-hit").forEach((el) => el.onclick = () => { lp[el.dataset.c] = { supply: null, install: null }; draw(); });
      };
      $("bl-lpf").oninput = find;
    };
    modal("Line % · " + x.service, "A line’s own % replaces the service’s % for that line only. Leave a box empty to use the service’s %.",
      `<div id="bl-lpbody"></div>`, [
        { label: "Cancel", run: closeModal },
        { label: "Save", cls: "primary", run: async () => {
          await jsend(api("/" + B.slug + "/setup"), { service: x.service, mode: x.mode, supply_source: x.supply_source, supply_pct: x.supply_pct, install_pct: x.install_pct, line_pct: lp });
          closeModal(); toast("Line % saved."); showSetup(); } }], "min(760px,96vw)");
    draw();
  }

  function editGroups(x) {
    const rows = x.groups.map((g) => ({ ...g }));
    const draw = () => {
      $("bl-gbody").innerHTML = `<table class="bl-gt"><thead><tr><th>Sr</th><th>Description</th><th>Sheet</th><th>From row</th><th>To row</th><th>WO amount (optional)</th><th></th></tr></thead><tbody>
        ${rows.map((g, i) => `<tr data-i="${i}"><td><input class="ctl" data-k="sr" value="${esc(g.sr)}" style="width:54px"></td>
          <td><input class="ctl" data-k="name" value="${esc(g.name)}"></td><td><input class="ctl" data-k="sheet" value="${esc(g.sheet || "")}" style="width:90px"></td>
          <td><input class="ctl" data-k="from" type="number" value="${g.from}" style="width:76px"></td><td><input class="ctl" data-k="to" type="number" value="${g.to}" style="width:76px"></td>
          <td><input class="ctl" data-k="wo" type="number" step="any" value="${g.wo == null ? "" : g.wo}" placeholder="from sheet" style="width:130px"></td>
          <td><button class="btn" data-rm="${i}">Remove</button></td></tr>`).join("")}</tbody></table>
        <button class="btn" id="bl-gadd">+ Add group</button>`;
      document.querySelectorAll("#bl-gbody tr[data-i] input").forEach((el) => el.oninput = () => {
        const g = rows[+el.closest("tr").dataset.i], k = el.dataset.k;
        g[k] = (k === "from" || k === "to") ? Number(el.value) : (k === "wo" ? (el.value === "" ? null : Number(el.value)) : el.value);
      });
      document.querySelectorAll("#bl-gbody [data-rm]").forEach((el) => el.onclick = () => { rows.splice(+el.dataset.rm, 1); draw(); });
      $("bl-gadd").onclick = () => { const last = rows[rows.length - 1]; rows.push({ sr: "", name: "", sheet: last ? last.sheet : "", from: last ? last.to + 1 : 2, to: last ? last.to + 1 : 2, wo: null }); draw(); };
    };
    modal("Annexure groups · " + x.service, "Each group is one row of the annexure: a block of sheet rows. Rows are the Excel row numbers of your BOQ sheet.",
      `<div id="bl-gbody" class="bl-gwrap"></div>`, [
        { label: "Cancel", run: closeModal },
        { label: "Use sheet’s own rows", run: async () => { await jsend(api("/" + B.slug + "/groups/" + encodeURIComponent(x.service)), { groups: [] }); closeModal(); toast("Back to the sheet’s own rows."); showSetup(); } },
        { label: "Save groups", cls: "primary", run: async () => { await jsend(api("/" + B.slug + "/groups/" + encodeURIComponent(x.service)), { groups: rows }); closeModal(); toast("Groups saved."); showSetup(); } }], "min(860px,96vw)");
    draw();
  }


  // ------------------------------------------------------- Work Orders
  // One service can be billed under several Work Orders (Electrical: "Excl. point wiring" and "Only point wiring").
  // Each Work Order is billed on its own: its own payment term, RA numbers, challans and invoice. Which BOQ lines
  // belong to which one is decided by the annexure GROUPS given to it; one Work Order takes every group not given
  // to another. A Work Order that already has RAs or challans keeps its groups.
  async function editPackages(base) {
    let j;
    try { j = await jget(api("/" + B.slug + "/packages/" + encodeURIComponent(base))); } catch (e) { toast(briefErr(e)); return; }
    const wos = j.split ? j.items.map((i) => ({ name: i.name, was: i.name, rest: i.rest, busy: (i.ras || 0) + (i.challans || 0) > 0, ras: i.ras, challans: i.challans }))
      : [{ name: "", was: null, rest: true, busy: false }, { name: "", was: null, rest: false, busy: false }];
    const plain = j.plain || { ras: 0, challans: 0 };
    const rest0 = Math.max(0, wos.findIndex((w) => w.rest));
    const asg = j.groups.map((g) => { const k = wos.findIndex((w) => w.was && w.was === g.pkg); return k >= 0 ? k : rest0; });
    const label = (w, i) => w.name.trim() || `Work Order ${i + 1}`;
    const sumHtml = () => wos.map((w, k) => { const gs = j.groups.filter((g, gi) => asg[gi] === k); return `<span><b>${esc(label(w, k))}</b> ${gs.length} group${gs.length === 1 ? "" : "s"} · ${inr(gs.reduce((a, g) => a + g.value, 0))}</span>`; }).join("");
    const relabel = () => {
      const restI = wos.findIndex((w) => w.rest);
      document.querySelectorAll("#bl-wobody [data-g]").forEach((sel) => [...sel.options].forEach((o, k) => { o.textContent = label(wos[k], k) + (k === restI ? " (rest)" : ""); }));
      const sm = $("bl-wosum"); if (sm) sm.innerHTML = sumHtml();
    };
    const draw = () => {
      const restI = wos.findIndex((w) => w.rest);
      $("bl-wobody").innerHTML = `
        <div class="bl-lbl">1 · Name each Work Order <small>as it is called on the contract</small></div>
        <div class="bl-wol">${wos.map((w, i) => `<div class="bl-wor2" data-i="${i}">
          <label class="bl-rest" title="This Work Order takes every group you do not give to another one"><input type="radio" name="bl-rest" data-rest="${i}"${w.rest ? " checked" : ""}> Takes the rest</label>
          <input class="ctl" data-name="${i}" value="${esc(w.name)}" placeholder="${i === 0 ? "e.g. Excl. point wiring" : "e.g. Only point wiring"}" maxlength="40" autocomplete="off">
          ${w.busy ? `<small class="bl-busy">${w.ras || 0} RA · ${w.challans || 0} challan${w.challans === 1 ? "" : "s"}: its groups are fixed</small>`
            : (wos.length > 2 ? `<button type="button" class="btn" data-rm="${i}" aria-label="Remove Work Order">×</button>` : "<span></span>")}</div>`).join("")}</div>
        <button type="button" class="btn" id="bl-woadd">+ Add a Work Order</button>
        <div class="bl-lbl">2 · Give each annexure group to a Work Order <small>${j.groups.length} groups</small></div>
        <div class="bl-wog">${j.groups.map((g, gi) => `<div class="bl-wogr"><div class="t"><b>${esc(g.sr ? g.sr + " · " : "")}${esc(g.name)}</b>
            <small>${g.lines} billable line${g.lines === 1 ? "" : "s"} · ${inr(g.value)}</small></div>
          <select class="ctl" data-g="${gi}" aria-label="Work Order of ${esc(g.name)}"${g.lines ? "" : " disabled"}>
            ${wos.map((w, k) => `<option value="${k}" ${asg[gi] === k ? "selected" : ""}${w.busy && g.pkg !== w.was ? " disabled" : ""}>${esc(label(w, k))}${k === restI ? " (rest)" : ""}</option>`).join("")}</select></div>`).join("")}</div>
        <div class="bl-wosum" id="bl-wosum">${sumHtml()}</div>
        ${(plain.ras || plain.challans) && !j.split ? `<div class="bl-callout soft">${base} already has ${plain.ras} RA and ${plain.challans} challan${plain.challans === 1 ? "" : "s"}. An invoiced RA moves into the Work Order its lines belong to (it must not have lines of two Work Orders); challans are split by line; an untouched draft is dropped and opens again by itself; the payment term is copied to each Work Order.</div>` : ""}`;
      // typing a name only relabels the dropdowns and the summary: the boxes are never rebuilt under the person's cursor
      document.querySelectorAll("#bl-wobody [data-name]").forEach((el) => el.oninput = () => { wos[+el.dataset.name].name = el.value; relabel(); });
      document.querySelectorAll("#bl-wobody [data-rest]").forEach((el) => el.onchange = () => {
        const prev = wos.findIndex((w) => w.rest); wos.forEach((w, k) => { w.rest = k === +el.dataset.rest; });
        asg.forEach((a, gi) => { if (a === prev) asg[gi] = +el.dataset.rest; }); draw(); });
      document.querySelectorAll("#bl-wobody [data-g]").forEach((el) => el.onchange = () => { asg[+el.dataset.g] = +el.value; draw(); });
      document.querySelectorAll("#bl-wobody [data-rm]").forEach((el) => el.onclick = () => {
        const k = +el.dataset.rm, r = wos.findIndex((w) => w.rest), to = k === r ? (k === 0 ? 1 : 0) : r;
        asg.forEach((a, gi) => { if (a === k) asg[gi] = to; });
        wos.splice(k, 1); for (let gi = 0; gi < asg.length; gi++) if (asg[gi] > k) asg[gi]--;
        if (!wos.some((w) => w.rest)) wos[0].rest = true; draw(); });
      $("bl-woadd").onclick = () => { wos.push({ name: "", was: null, rest: false, busy: false }); draw(); };
    };
    modal("Work Orders · " + base, "A Work Order is billed on its own: its own RA numbers, payment term, challans and invoice.",
      `<div id="bl-wobody" class="bl-wo-ed"></div>`, [
        { label: "Cancel", run: closeModal },
        ...(j.split ? [{ label: "Back to one Work Order", run: async () => {
          await jsend(api("/" + B.slug + "/packages/" + encodeURIComponent(base)), { items: [] });
          closeModal(); toast(base + " is one Work Order again."); showSetup(); } }] : []),
        { label: "Save Work Orders", cls: "primary", id: "bl-wosave", run: async () => {
          const items = wos.map((w, k) => w.rest ? { name: w.name, was: w.was, rest: true }
            : { name: w.name, was: w.was, groups: j.groups.filter((g, gi) => asg[gi] === k).map((g) => ({ sheet: g.sheet, from: g.from, to: g.to })) });
          const r = await jsend(api("/" + B.slug + "/packages/" + encodeURIComponent(base)), { items });
          closeModal();
          toast("Work Orders saved." + (r.removed_drafts ? ` ${r.removed_drafts} untouched draft${r.removed_drafts === 1 ? "" : "s"} opens again by itself.` : "") + " Set each Work Order’s payment term if it is not set.");
          showSetup(); } }], "min(820px,96vw)");
    $("bl-modal").querySelector(".card").classList.add("bl-dlcard");
    draw();
  }

  // ------------------------------------------------------------ challans
  // One screen per service. A challan is one delivery on site: number, date and
  // quantity per BOQ line, tagged with the RA it is billed in. Everything else
  // (certified qty per RA, previous / this / total, annexure, next RA) is the
  // server's job and is read back in the RA bill and its invoice workbook.
  async function showChallans(service) {
    const s = section();
    B.view = "challans"; B.svc = service;
    let j;
    try { j = await jget(api("/" + B.slug + "/challans/" + encodeURIComponent(service))); }
    catch (e) { toast(briefErr(e)); return showHome(); }
    B.ch = j;
    if (IB.unit !== service) Object.assign(IB, { unit: service, q: "", scope: "this", tab: "waiting", earlier: false, sel: new Set(), pick: {}, nos: {} });
    IB.data = null;
    if (j.source === "challan") {
      try { IB.data = await jget(api("/" + B.slug + "/stock-inbox/" + encodeURIComponent(service) + (IB.earlier ? "?earlier=1" : ""))); } catch (e) { IB.data = null; }
    }
    const hasIb = !!(IB.data && IB.data.ready === true && IB.data.counts && Array.isArray(IB.data.entries));
    const open = j.open_no;
    const groupsDef = [];
    const by = (f) => j.challans.filter(f);
    groupsDef.push({ key: "open", title: `${raL(open)} · open`, sub: "These go into the next bill.", list: by((c) => c.state === "open") });
    const later = [...new Set(by((c) => c.state === "later").map((c) => c.ra))].sort((a, b) => a - b);
    later.forEach((n) => groupsDef.push({ key: "later" + n, title: `${raL(n)} · later`, sub: "Tagged for a later bill.", list: by((c) => c.ra === n) }));
    groupsDef.push({ key: "held", title: "On hold", sub: "Not in any RA until you tag them.", list: by((c) => c.state === "held") });
    const locked = [...new Set(by((c) => c.state === "locked").map((c) => c.ra))].sort((a, b) => b - a);
    locked.forEach((n) => groupsDef.push({ key: "locked" + n, title: `${raL(n)} · invoiced`, sub: "Frozen with the bill.", list: by((c) => c.ra === n) }));
    const row = (c) => `
      <div class="bl-ch ${B.expanded.has("c" + c.id) ? "ex" : ""} st-${c.state}" data-id="${esc(c.id)}">
        <div class="no"><b>${c.virtual || c.src ? "" : "Challan "}${esc(c.no)}</b><small>${esc(c.date)}${c.note ? " · " + esc(c.note) : ""}</small>${c.src ? `<span class="bl-chip stock">Stock register</span>` : ""}${c.no_missing ? ` <span class="bl-chip draft">No. missing</span>` : ""}${c.register ? ` <span class="bl-chip draft">${c.register.missing ? "No longer in the register" : "Register now " + qn(c.register.now) + " " + esc(c.src.unit)}</span>` : ""}</div>
        <div class="n" data-l="Lines">${c.lines.length} line${c.lines.length === 1 ? "" : "s"}</div>
        <div data-l="Billed in">${c.frozen ? `<span class="bl-chip locked">${raL(c.ra)} invoiced</span>` : c.virtual ? `<span class="bl-chip draft">${raL(c.ra)}</span>` : `<select class="ctl bl-retag" data-id="${esc(c.id)}" aria-label="RA for challan ${esc(c.no)}">
          ${[open, open + 1, open + 2].concat(c.ra != null && c.ra > open + 2 ? [c.ra] : []).map((n) => `<option value="${n}" ${c.ra === n ? "selected" : ""}>${raL(n)}${n === open ? " (open)" : ""}</option>`).join("")}
          <option value="hold" ${c.ra == null ? "selected" : ""}>On hold</option></select>`}</div>
        <div class="go">${c.frozen || c.virtual ? "View" : "Edit"}</div>
        <div class="x">${c.lines.map((l) => `<div class="bl-cl"><span>${esc(l.desc)}</span><b>${qn(l.qty)} ${esc(l.unit)}</b></div>`).join("")}
          ${c.virtual ? `<div class="bl-cact"><span class="bl-ovh">Worked out from wire metres.</span><button class="btn" data-towire="1">Open Wire → points</button>${c.frozen ? "" : `<button class="btn danger" data-wdel="${esc(c.id)}">Delete</button>`}</div>` : c.frozen ? "" : `${c.src ? `<div class="bl-ovh">From the stock register: ${esc(c.src.material)}, ${qn(c.src.stock_qty)} ${esc(c.src.unit)}.${c.no_missing ? " Add the challan number from the paper challan; until then the invoice shows “" + esc(c.no) + "”." : ""}</div>` : ""}<div class="bl-cact">${c.register && !c.register.missing ? `<button class="btn" data-resync="${esc(c.id)}">Use register qty (${qn(c.register.now_counts)} ${esc(c.lines[0].unit)})</button>` : ""}<button class="btn" data-edit="${esc(c.id)}">Edit challan</button><button class="btn danger" data-del="${esc(c.id)}">Delete</button></div>`}</div>
      </div>`;
    s.innerHTML = head(`Challans · ${service}`,
      hasIb ? "Deliveries come from the stock register. Type a challan only for material that never went into stock. The qty, annexure, previous / this / total and the next RA follow from it."
        : "Type each delivery in once. The qty, annexure, previous / this / total and the next RA follow from it.",
      `<button class="btn" id="bl-back">← Billing</button>
       ${j.wire && j.wire.available ? `<button class="btn" id="bl-chwire">Wire → points</button>` : ""}
       <button class="btn" id="bl-chimp">Import from Excel</button>
       ${hasIb ? `<span class="bl-addm"><button class="btn primary" id="bl-chadd" aria-haspopup="true" aria-expanded="false">+ Add challan ▾</button>
         <div class="bl-menu" id="bl-chmenu" hidden>
           <button id="bl-chfrom"><b>From stock register</b> <span class="bl-chip stock">${IB.data.counts.waiting} waiting</span><small>Material that arrived on site. Recommended.</small></button>
           <button id="bl-chtype"><b>Type a challan</b><small>For material that never went into the stock register.</small></button></div></span>`
         : `<button class="btn primary" id="bl-chadd">+ Add challan</button>`}`) + `
      <div class="bl-kpis">
        <div class="bl-kpi"><span>Challans entered</span><b>${j.challans.length}</b><i>${by((c) => c.state === "locked").length} in invoiced RAs</i></div>
        <div class="bl-kpi"><span>Going into ${raL(open)}</span><b>${by((c) => c.state === "open").length}</b><i>challan(s) tagged to the open RA</i></div>
        <div class="bl-kpi ${by((c) => c.state === "held").length ? "warn" : ""}"><span>On hold</span><b>${by((c) => c.state === "held").length}</b><i>not in any RA</i></div>
      </div>
      ${j.source !== "challan" ? `<div class="bl-callout">This service takes its supply quantity from the stock register. Choose “Delivery challans” in Billing setup to bill from challans.</div>` : ""}
      <div id="bl-ib"></div>
      ${j.over.length ? `<div class="bl-notes">${j.over.map((o) => `<div class="bl-note over"><b>Check</b>${esc(o.desc.slice(0, 90))}: challans total ${qn(o.challan)} ${esc(o.unit)}, BOQ qty is ${qn(o.boq_qty)}. A bill is capped at the BOQ qty.</div>`).join("")}</div>` : ""}
      ${j.challans.length ? groupsDef.filter((g) => g.list.length).map((g) => `
        <h3 class="bl-h3">${esc(g.title)} <small>${esc(g.sub)}</small></h3>
        <div class="bl-card"><div class="bl-chs">${g.list.map(row).join("")}</div></div>`).join("")
        : `<div class="bl-card"><div class="bl-none">${hasIb ? "No challan in an RA yet. Add deliveries from the stock register above, or “Import from Excel” to bring in the challans you already have in your invoice workbook." : "No challan yet. Click “+ Add challan”, or “Import from Excel” to bring in the challans you already have in your invoice workbook."}</div></div>`}
      ${j.stock_check.length ? `<h3 class="bl-h3">Stock register check <small>${j.stock_check.length} line(s) where the stock register and the challans disagree — information only, the bill follows the challans</small></h3>
        <div class="bl-card bl-scroll"><div class="bl-sc">
          <div class="bl-sch"><div>Line</div><div class="n">Challans</div><div class="n">Stock register</div><div class="n">Difference</div></div>
          ${j.stock_check.slice(0, 60).map((x) => `<div class="bl-scr"><div>${esc(x.desc.slice(0, 100))}<small>${esc(x.unit)}</small></div><div class="n">${qn(x.challan)}</div><div class="n">${qn(x.stock)}</div><div class="n ${x.diff < 0 ? "neg" : ""}">${(x.diff > 0 ? "+" : "") + qn(x.diff)}</div></div>`).join("")}
        </div></div>` : ""}`;
    wireProj();
    $("bl-back").onclick = showHome;
    if (hasIb) {
      const addb = $("bl-chadd"), menu = $("bl-chmenu");
      addb.onclick = (ev) => {
        ev.stopPropagation(); menu.hidden = !menu.hidden; addb.setAttribute("aria-expanded", String(!menu.hidden));
        if (!menu.hidden) {                                                  // keep the menu inside the screen, whichever side the button is on
          menu.style.marginLeft = ""; const r = menu.getBoundingClientRect(), vw = document.documentElement.clientWidth;
          if (r.right > vw - 8) menu.style.marginLeft = -(r.right - (vw - 8)) + "px";
          else if (r.left < 8) menu.style.marginLeft = (8 - r.left) + "px";
        }
      };
      $("bl-chfrom").onclick = () => { menu.hidden = true; IB.tab = "waiting"; IB.scope = "this"; drawInbox(); const q = $("bl-ibq"); if (q) { q.scrollIntoView({ block: "center" }); q.focus(); } };
      $("bl-chtype").onclick = () => { menu.hidden = true; editChallan(null); };
      if (!B.menuClose) { B.menuClose = true; document.addEventListener("click", () => { const m = $("bl-chmenu"); if (m) m.hidden = true; }); }
      drawInbox();
    } else $("bl-chadd").onclick = () => editChallan(null);
    $("bl-chimp").onclick = () => importChallans();
    s.querySelectorAll("[data-resync]").forEach((el) => el.onclick = async () => {
      try { await jsend(api("/" + B.slug + "/challans/" + encodeURIComponent(service) + "/" + el.dataset.resync + "/resync")); toast("Quantity taken from the stock register."); showChallans(service); }
      catch (e) { toast(briefErr(e)); }
    });
    if ($("bl-chwire")) $("bl-chwire").onclick = () => showWire(service);
    s.querySelectorAll(".bl-ch").forEach((el) => el.addEventListener("click", (e) => {
      if (e.target.closest("select,button")) return;
      const k = "c" + el.dataset.id; B.expanded.has(k) ? B.expanded.delete(k) : B.expanded.add(k); el.classList.toggle("ex");
    }));
    s.querySelectorAll("[data-towire]").forEach((el) => el.onclick = () => showWire(service));
    // "Wire RA-nn" is the points given to point lines from the wire metres. Deleting it takes those points out of the bill;
    // the wire deliveries themselves stay, so the metres can be given again in Wire → points.
    s.querySelectorAll("[data-wdel]").forEach((el) => el.onclick = () => {
      const c = j.challans.find((x) => x.id === el.dataset.wdel);
      const pts = c.lines.reduce((a, l) => a + (Number(l.qty) || 0), 0);
      modal("Delete " + c.no + "?", `${qn(pts)} points on ${c.lines.length} line${c.lines.length === 1 ? "" : "s"}`,
        "<p>These points leave the bill. The wire deliveries stay as they are, so you can give the metres to the lines again in Wire → points.</p>", [
        { label: "Cancel", run: closeModal },
        { label: "Delete", cls: "danger", run: async () => {
          try { await jsend(api("/" + B.slug + "/wire/" + encodeURIComponent(service) + "/alloc"), { rows: [] }, "PUT"); }
          catch (e) { closeModal(); toast(briefErr(e)); return; }
          closeModal(); toast(c.no + " deleted."); showChallans(service); } }]);
    });
    s.querySelectorAll("[data-edit]").forEach((el) => el.onclick = () => editChallan(j.challans.find((c) => c.id === el.dataset.edit)));
    s.querySelectorAll("[data-del]").forEach((el) => el.onclick = () => {
      const c = j.challans.find((x) => x.id === el.dataset.del);
      modal("Delete challan " + c.no + "?", c.date, "<p>The quantities of this challan leave the bill they were in.</p>", [
        { label: "Cancel", run: closeModal },
        { label: "Delete", cls: "danger", run: async () => { await jsend(api("/" + B.slug + "/challans/" + encodeURIComponent(service) + "/" + c.id), undefined, "DELETE"); closeModal(); toast("Challan deleted."); showChallans(service); } }]);
    });
    s.querySelectorAll(".bl-retag").forEach((el) => el.onchange = async () => {
      const c = j.challans.find((x) => x.id === el.dataset.id);
      try {
        await jsend(api("/" + B.slug + "/challans/" + encodeURIComponent(service) + "/" + c.id),
          { no: c.no, date: c.date, note: c.note, ra: el.value === "hold" ? null : Number(el.value), lines: c.lines.map((l) => ({ item_code: l.item_code, qty: l.qty })) });
        toast("Challan " + c.no + " → " + (el.value === "hold" ? "on hold" : raL(Number(el.value))) + "."); showChallans(service);
      } catch (e) { toast(briefErr(e)); showChallans(service); }
    });
  }

  // ------------------------------------------- challans straight from the stock register
  // Material reaches site and is entered in the stock register, so a delivery challan is made from that, not retyped.
  // The register's dated IN rows are an inbox (server: /stock-inbox/<unit>). Nothing is added until the engineer adds
  // it to an RA; an added delivery is a frozen copy that locks with its RA like any typed challan.
  const IB = { unit: null, q: "", scope: "this", tab: "waiting", earlier: false, sel: new Set(), pick: {}, nos: {}, data: null };
  const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const fDate = (iso) => { const m = /^(\d{4})-(\d\d)-(\d\d)$/.exec(iso || ""); return m ? `${m[3]} ${MON[+m[2] - 1]} ${m[1].slice(2)}` : (iso || ""); };
  const ibApi = (p) => api("/" + B.slug + "/stock-inbox/" + encodeURIComponent(B.svc) + p);
  const ibCand = (e) => { const c = IB.pick[e.key] || e.item_code; return e.lines.find((l) => l.item_code === c) || null; };
  const ibText = (e) => [e.material, fDate(e.date), e.date, e.service, e.unit, ...e.lines.flatMap((l) => [l.item_code, l.desc])].join(" ").toLowerCase();
  function ibVisible() {
    const d = IB.data; if (!d || !d.entries) return [];
    const words = IB.q.trim().toLowerCase().split(/\s+/).filter(Boolean);
    const st = IB.tab === "skipped" ? "dismissed" : IB.tab;
    return d.entries.filter((e) => e.state === st && (IB.scope === "all" || e.scope === "this") && words.every((w) => ibText(e).includes(w)));
  }
  function ibRow(e, open) {
    const c = ibCand(e);
    const canSel = e.kind === "line" && e.state !== "dismissed" && c && !c.problem && !c.dup;
    let counts;
    if (e.kind === "other") counts = `<span class="bl-chip none">${esc(e.service)}</span>`;
    else if (e.kind === "unlinked") counts = `<span class="bl-chip draft">Not linked to a BOQ line</span>`;
    else if (e.lines.length > 1 && !c) counts = `<select class="ctl" data-ibline="${esc(e.key)}" aria-label="BOQ line for ${esc(e.material)}"><option value="">Pick the BOQ line…</option>${e.lines.map((l) => `<option value="${esc(l.item_code)}">${esc(l.item_code)} · ${esc(l.desc.slice(0, 50))}</option>`).join("")}</select>`;
    else if (c && c.problem) counts = `<span class="bl-chip draft">${esc(c.problem)}</span><small>line ${esc(c.item_code)}</small>`;
    else counts = `<b>${qn(c.counts_as)} ${esc(c.unit)}</b><small>line ${esc(c.item_code)}${c.factor && c.factor !== 1 ? ` · ${qn(e.qty)} ${esc(e.unit)} ÷ ${qn(c.factor)}` : ""}</small>${c.dup ? `<span class="bl-chip draft">Looks like challan ${esc(c.dup.no)}</span>` : ""}`;
    let act = "";
    if (e.state === "dismissed") act = `<button class="btn" data-ibmark="unskip:${esc(e.key)}">Bring back</button>`;
    else if (e.kind === "unlinked") act = `<button class="btn primary" data-iblink="${esc(e.key)}">Link it</button><button class="btn" data-ibmark="skip:${esc(e.key)}">Not billable</button>`;
    else if (e.kind === "other") act = `<small class="bl-ovh">Add it from ${esc(e.service)}</small>`;
    else if (c && c.problem) act = `<button class="btn primary" data-iblink="${esc(e.key)}" data-code="${esc(c.item_code)}">Fix link</button>`;
    else act = `<button class="btn primary" data-ibadd="${esc(e.key)}">Add to ${raL(open)}</button>
      <button class="btn" data-ibmark="${e.state === "held" ? "unhold" : "hold"}:${esc(e.key)}">${e.state === "held" ? "Put back" : "Hold"}</button>`;
    return `<div class="bl-ibr ${e.kind}" data-k="${esc(e.key)}">
      <label class="ck"><input type="checkbox" data-ibsel="${esc(e.key)}" aria-label="Select ${esc(e.material)}, ${esc(fDate(e.date))}" ${canSel ? "" : "disabled"} ${IB.sel.has(e.key) ? "checked" : ""}></label>
      <div class="dt" data-l="Date IN">${esc(fDate(e.date))}${e.opening ? `<small>opening stock</small>` : ""}</div>
      <div class="mt" data-l="Stock material"><b>${esc(e.material)}</b></div>
      <div class="n" data-l="Stock IN">${qn(e.qty)} ${esc(e.unit)}</div>
      <div class="ca" data-l="Counts as">${counts}</div>
      <div class="no" data-l="Challan no.">${e.kind === "line" && c && !c.problem && e.state !== "dismissed" ? `<input class="ctl" data-ibno="${esc(e.key)}" value="${esc(IB.nos[e.key] || "")}" placeholder="optional" autocomplete="off" aria-label="Challan number for ${esc(e.material)}, optional">` : ""}</div>
      <div class="act">${act}</div></div>`;
  }
  function drawIbList() {
    const d = IB.data, host = $("bl-iblist"); if (!host || !d) return;
    const rows = ibVisible(), open = d.open_no;
    const sel = [...IB.sel].filter((k) => rows.some((e) => e.key === k));
    host.innerHTML = rows.length ? rows.slice(0, 200).map((e) => ibRow(e, open)).join("") + (rows.length > 200 ? `<div class="bl-ovh pad">+${rows.length - 200} more. Narrow the list with the search box.</div>` : "")
      : `<div class="bl-none sm">${!d.run ? "No stock register has been uploaded for this project yet (Forecast tab)."
        : IB.q.trim() ? "Nothing matches your search."
        : IB.tab === "waiting" ? `Nothing new in the stock register for ${esc(d.service_label)}${d.since ? " after " + esc(fDate(d.since)) : ""}${d.last_in ? ". Last stock IN: " + esc(fDate(d.last_in)) : ""}.`
        : IB.tab === "held" ? "Nothing on hold." : "Nothing marked Not billable."}</div>`;
    const bar = $("bl-ibbar"); if (bar) { bar.hidden = !sel.length; bar.querySelector("span").textContent = sel.length + " selected"; bar.querySelector("button").textContent = "Add selected to " + raL(open); }
    host.querySelectorAll("[data-ibsel]").forEach((el) => el.onchange = () => { el.checked ? IB.sel.add(el.dataset.ibsel) : IB.sel.delete(el.dataset.ibsel); drawIbList(); });
    host.querySelectorAll("[data-ibno]").forEach((el) => el.oninput = () => { IB.nos[el.dataset.ibno] = el.value; });
    host.querySelectorAll("[data-ibline]").forEach((el) => el.onchange = () => { if (el.value) { IB.pick[el.dataset.ibline] = el.value; drawIbList(); } });
    host.querySelectorAll("[data-ibadd]").forEach((el) => el.onclick = () => ibAddOne(el.dataset.ibadd));
    host.querySelectorAll("[data-ibmark]").forEach((el) => el.onclick = () => { const [a, k] = el.dataset.ibmark.split(":"); ibMark(a, [k]); });
    host.querySelectorAll("[data-iblink]").forEach((el) => el.onclick = () => ibLink(IB.data.entries.find((x) => x.key === el.dataset.iblink), el.dataset.code));
  }
  function drawInbox() {
    const d = IB.data, host = $("bl-ib"); if (!host || !d || d.ready !== true || !d.counts) return;
    const c = d.counts, open = d.open_no;
    const units = ((B.ov && B.ov.services) || []).filter((x) => x.supply_source === "challan");
    host.innerHTML = `<div class="bl-card bl-ibcard">
      <div class="bl-ibhd"><div><b>New in stock register</b> <span class="bl-chip stock">${c.waiting} waiting</span>
        <p>Material that reached site is already in your stock register. Pick what belongs to ${raL(open)}. Nothing becomes a challan until you add it.</p></div>
        <div class="bl-ibtabs" role="group" aria-label="Show">
          <button class="bl-ibt" data-ibtab="waiting">Waiting ${c.waiting}</button><button class="bl-ibt" data-ibtab="held">Held ${c.held}</button><button class="bl-ibt" data-ibtab="skipped">Not billable ${c.dismissed}</button></div></div>
      <div class="bl-ibtool">
        <input id="bl-ibq" class="ctl" type="search" placeholder="Search material, date, BOQ line or challan no." value="${esc(IB.q)}" autocomplete="off" aria-label="Search stock deliveries">
        ${units.length > 1 ? `<select id="bl-ibsvc" class="ctl" aria-label="Service">${units.map((x) => `<option value="${esc(x.service)}" ${x.service === B.svc ? "selected" : ""}>Service: ${esc(x.service.replace(WOSEP, " › "))}</option>`).join("")}</select>` : ""}
        <button class="bl-ibt" data-ibscope="this">This service</button><button class="bl-ibt" data-ibscope="all">All services${c.other ? " +" + c.other : ""}</button>
        <small class="bl-ovh">Service comes from the register’s own service column.</small></div>
      ${d.wire && d.wire.available && d.wire.entries ? `<div class="bl-ibwire"><span><b>Wire</b> · ${d.wire.entries} deliveries in the register. Wire is counted in points, not as a challan.</span><button class="btn primary" id="bl-ibwire">Open Wire → points</button></div>` : ""}
      <div id="bl-iblist" class="bl-iblist"></div>
      ${d.earlier ? `<div class="bl-ibmore"><span>${d.earlier} earlier deliveries (up to ${esc(fDate(d.since))}, already in a submitted bill) are hidden.</span><button class="bl-link" id="bl-ibearlier">Show them</button></div>` : IB.earlier && d.since ? `<div class="bl-ibmore"><span>Showing deliveries up to ${esc(fDate(d.since))} too.</span><button class="bl-link" id="bl-ibearlier">Hide them</button></div>` : ""}
      <div id="bl-ibbar" class="bl-ibbar" hidden><span></span><button class="btn primary" id="bl-ibaddsel"></button><small class="bl-ovh">Date and quantity are copied from the register. The challan no. is the only thing you type.</small></div>
    </div>`;
    host.querySelectorAll("[data-ibtab]").forEach((el) => { el.classList.toggle("on", el.dataset.ibtab === IB.tab); el.onclick = () => { IB.tab = el.dataset.ibtab; IB.sel = new Set(); host.querySelectorAll("[data-ibtab]").forEach((x) => x.classList.toggle("on", x === el)); drawIbList(); }; });
    host.querySelectorAll("[data-ibscope]").forEach((el) => { el.classList.toggle("on", el.dataset.ibscope === IB.scope); el.onclick = () => { IB.scope = el.dataset.ibscope; host.querySelectorAll("[data-ibscope]").forEach((x) => x.classList.toggle("on", x === el)); drawIbList(); }; });
    $("bl-ibq").oninput = () => { IB.q = $("bl-ibq").value; drawIbList(); };
    if ($("bl-ibsvc")) $("bl-ibsvc").onchange = () => showChallans($("bl-ibsvc").value);
    if ($("bl-ibwire")) $("bl-ibwire").onclick = () => showWire(B.svc);
    if ($("bl-ibearlier")) $("bl-ibearlier").onclick = () => { IB.earlier = !IB.earlier; showChallans(B.svc); };
    $("bl-ibaddsel").onclick = () => ibAdd([...IB.sel].map((k) => ({ key: k })), false);
    drawIbList();
  }
  async function ibReload() { await showChallans(B.svc); }
  async function ibMark(action, keys) {
    try { await jsend(ibApi("/mark"), { action, keys }); IB.sel = new Set(); toast({ hold: "Kept on hold.", unhold: "Back in the waiting list.", skip: "Marked Not billable. It will not show again.", unskip: "Brought back." }[action]); ibReload(); }
    catch (e) { toast(briefErr(e)); ibReload(); }
  }
  async function ibAdd(items, force, ra) {
    const d = IB.data;
    const body = { items: items.map((it) => { const e = d.entries.find((x) => x.key === it.key); return { key: it.key, item_code: it.item_code || IB.pick[it.key] || (e && e.item_code) || undefined, no: it.no !== undefined ? it.no : (IB.nos[it.key] || "").trim(), force: !!it.force }; }), force: !!force };
    if (ra !== undefined) body.ra = ra;
    const r = await jsend(ibApi("/accept"), body);
    items.forEach((it) => { IB.sel.delete(it.key); delete IB.nos[it.key]; delete IB.pick[it.key]; });
    toast(r.added + " deliver" + (r.added === 1 ? "y" : "ies") + " added to " + (ra === null ? "On hold" : raL(ra === undefined ? d.open_no : ra)) + ".");
    closeModal(); ibReload();
  }
  async function ibAddOne(key) {
    const e = IB.data.entries.find((x) => x.key === key), c = ibCand(e);
    if (!c) return toast("Pick the BOQ line this delivery feeds first.");
    if (c.dup) return ibReview(e);
    try { await ibAdd([{ key }], false); } catch (er) { toast(briefErr(er)); }
  }
  function ibReview(e) {
    const d = IB.data, open = d.open_no;
    let force = false, code = (ibCand(e) || e.lines[0]).item_code;
    const cur = () => e.lines.find((l) => l.item_code === code);
    modal("Add delivery to " + raL(open), "Date and quantity come from the stock register. Only the challan number is yours to type.",
      `<div class="bl-ibrev"><div class="bl-ibro"><span>Date IN</span><b>${esc(fDate(e.date))}</b><span>Material</span><b>${esc(e.material)}</b><span>Stock IN</span><b>${qn(e.qty)} ${esc(e.unit)}</b></div>
        <div class="bl-chform"><label>BOQ line it feeds${e.lines.length > 1 ? `<select id="bl-irl" class="ctl">${e.lines.map((l) => `<option value="${esc(l.item_code)}" ${l.item_code === code ? "selected" : ""}>${esc(l.item_code)} · ${esc(l.desc.slice(0, 60))}</option>`).join("")}</select>` : `<input class="ctl" value="${esc(cur().item_code + " · " + cur().desc.slice(0, 60))}" disabled>`}</label>
          <label>Add to<select id="bl-ira" class="ctl"><option value="${open}">${raL(open)} (open)</option><option value="${open + 1}">${raL(open + 1)}</option><option value="hold">On hold</option></select></label></div>
        <div class="bl-ibro" id="bl-irc"></div>
        <label class="bl-irn">Challan no. <small>optional</small><input id="bl-irno" class="ctl" value="${esc(IB.nos[e.key] || "")}" placeholder="as printed on the paper challan" autocomplete="off"></label>
        <small class="bl-ovh">The register has no challan number. Blank is allowed: the invoice prints “Stock IN ${esc(fDate(e.date))}” and the row shows “No. missing” until you fill it.</small>
        <div class="bl-callout" id="bl-ird" hidden></div></div>`, [
        { label: "Cancel", run: closeModal },
        { label: "Hold for later", run: async () => { closeModal(); await ibMark("hold", [e.key]); } },
        { label: "Add to " + raL(open), cls: "primary", id: "bl-iradd", run: async () => {
          const ra = $("bl-ira").value === "hold" ? null : Number($("bl-ira").value);
          await ibAdd([{ key: e.key, item_code: code, no: $("bl-irno").value.trim(), force }], false, ra);
        } }], "min(640px,96vw)");
    const paint = () => {
      const c = cur();
      $("bl-irc").innerHTML = c.problem ? `<span>Counts as</span><b>${esc(c.problem)}</b>` : `<span>Counts as</span><b>${qn(c.counts_as)} ${esc(c.unit)}</b><span>How</span><b>${c.factor && c.factor !== 1 ? `${qn(e.qty)} ${esc(e.unit)} ÷ ${qn(c.factor)} (link factor)` : "same unit, no conversion"}</b>`;
      const dd = $("bl-ird");
      if (c.dup) {
        dd.hidden = false;
        dd.innerHTML = `<b>Already counted? Challan ${esc(c.dup.no)} has the same date, line and quantity.</b><br>A delivery counts once, from stock or typed, never both.
          <div class="bl-ibdb"><button class="btn" id="bl-irsame">Same delivery. Hold it</button><button class="btn ${force ? "primary" : ""}" id="bl-irdiff">Different delivery. ${force ? "Continue ✓" : "Continue"}</button></div>`;
        $("bl-irsame").onclick = async () => { closeModal(); await ibMark("hold", [e.key]); };
        $("bl-irdiff").onclick = () => { force = true; paint(); };
      } else dd.hidden = true;
      $("bl-iradd").disabled = !!c.problem || (!!c.dup && !force);
    };
    if ($("bl-irl")) $("bl-irl").onchange = () => { code = $("bl-irl").value; force = false; paint(); };
    paint();
  }
  async function ibLink(e, presetCode) {
    let lines;
    try { lines = (await jget(api("/" + B.slug + "/lines/" + encodeURIComponent(B.svc)))).lines; } catch (er) { return toast(briefErr(er)); }
    let code = presetCode || "";
    const same = (l) => l && l.unit && String(e.unit).trim().toUpperCase() === String(l.unit).trim().toUpperCase();
    modal("Link " + e.material, "Say which BOQ line this stock material supplies. This is the same link as Site Progress › Link stock.",
      `<div class="bl-ibro"><span>Stock material</span><b>${esc(e.material)}</b><span>Register unit</span><b>${esc(e.unit)}</b></div>
       <input id="bl-lkq" class="ctl" type="search" placeholder="Search the BOQ line by words or Ref no" autocomplete="off" value="">
       <div id="bl-lkf" class="bl-found"></div><div id="bl-lkc"></div>`, [
        { label: "Cancel", run: closeModal },
        { label: "Save link", cls: "primary", id: "bl-lksave", run: async () => {
          const l = lines.find((x) => x.item_code === code); if (!l) return toast("Pick a BOQ line.");
          const f = $("bl-lkfac") ? $("bl-lkfac").value : "";
          await jsend(ibApi("/link"), { key: e.key, item_code: code, factor: f === "" ? undefined : Number(f) });
          closeModal(); toast("Linked. It now counts as " + l.unit + " of line " + l.item_code + "."); ibReload();
        } }], "min(640px,96vw)");
    const paintC = () => {
      const l = lines.find((x) => x.item_code === code);
      $("bl-lkc").innerHTML = l ? `<div class="bl-callout soft"><b>${esc(l.item_code)}</b> · ${esc(l.desc.slice(0, 120))} <small>(${esc(l.unit)})</small></div>
        ${same(l) ? `<div class="bl-hint">Both count in ${esc(l.unit)}: no conversion needed.</div>`
          : `<label class="bl-irn">How many ${esc(e.unit)} make 1 ${esc(l.unit)}?<input id="bl-lkfac" class="ctl" type="number" step="any" min="0" inputmode="decimal" placeholder="required, e.g. 10"></label>`}` : "";
      $("bl-lksave").disabled = !l;
    };
    const draw = () => {
      const q = $("bl-lkq").value.trim().toLowerCase(), words = q.split(/\s+/).filter(Boolean);
      const hits = (q ? lines.filter((l) => words.every((w) => l.desc.toLowerCase().includes(w)) || String(l.internal_ref || "").toLowerCase() === q || String(l.item_code).toLowerCase() === q) : []).slice(0, 30);
      $("bl-lkf").innerHTML = hits.map((l) => `<button class="bl-hit" data-c="${esc(l.item_code)}"><span>${esc(l.desc.slice(0, 120))}</span><small>${l.internal_ref ? "Ref " + esc(l.internal_ref) + " · " : ""}BOQ ${qn(l.boq_qty)} ${esc(l.unit)}</small></button>`).join("") || (q ? `<div class="bl-none sm">No match.</div>` : "");
      $("bl-lkf").querySelectorAll(".bl-hit").forEach((b) => b.onclick = () => { code = b.dataset.c; paintC(); });
    };
    $("bl-lkq").oninput = draw; draw(); paintC();
    if (presetCode) $("bl-lkq").value = "";
  }

  async function editChallan(c) {
    const service = B.svc, j = B.ch;
    let all;
    try { all = (await jget(api("/" + B.slug + "/lines/" + encodeURIComponent(service)))).lines; } catch (e) { toast(briefErr(e)); return; }
    const byCode = Object.fromEntries(all.map((l) => [l.item_code, l]));
    const rows = c ? c.lines.map((l) => ({ item_code: l.item_code, qty: l.qty })) : [];
    const open = j.open_no;
    const raOpts = [open, open + 1, open + 2].concat(c && c.ra != null && c.ra > open + 2 ? [c.ra] : []);
    modal(c ? "Edit challan " + c.no : "Add a challan", "Number, date, then every BOQ line on the challan with its quantity.",
      `<div class="bl-chform">
        <label>Challan no.<input id="bl-cno" class="ctl" value="${esc(c ? c.no : "")}" autocomplete="off" inputmode="text"></label>
        <label>Date<input id="bl-cdate" class="ctl" type="date" value="${esc(c ? c.date : today())}"></label>
        <label>Billed in<select id="bl-cra" class="ctl">${raOpts.map((n) => `<option value="${n}" ${(c ? c.ra : open) === n ? "selected" : ""}>${raL(n)}${n === open ? " (open)" : ""}</option>`).join("")}
          <option value="hold" ${c && c.ra == null ? "selected" : ""}>On hold</option></select></label>
        <label>Note (optional)<input id="bl-cnote" class="ctl" value="${esc(c ? c.note : "")}" autocomplete="off"></label>
      </div>
      <div class="bl-lbl">Lines on this challan <b id="bl-ccount"></b></div>
      <div id="bl-clist" class="bl-clist"></div>
      <input id="bl-cfind" class="ctl" type="search" placeholder="Search a BOQ line by words or Ref no, then click it" autocomplete="off">
      <div id="bl-cfound" class="bl-found"></div>`, [
        { label: "Cancel", run: closeModal },
        { label: c ? "Save changes" : "Save challan", cls: "primary", run: async () => {
          const body = { no: $("bl-cno").value.trim(), date: $("bl-cdate").value, note: $("bl-cnote").value, ra: $("bl-cra").value === "hold" ? null : Number($("bl-cra").value),
            lines: rows.map((r) => ({ item_code: r.item_code, qty: r.qty === "" ? 0 : Number(r.qty) })) };
          const base = api("/" + B.slug + "/challans/" + encodeURIComponent(service));
          await jsend(c ? base + "/" + c.id : base, body);
          closeModal(); toast("Challan " + body.no + " saved."); showChallans(service);
        } }], "min(720px,96vw)");
    const drawList = (focusLast) => {
      $("bl-ccount").textContent = rows.length ? "· " + rows.length : "";
      $("bl-clist").innerHTML = rows.map((r, i) => { const l = byCode[r.item_code] || {};
        return `<div class="bl-crow" data-i="${i}"><span class="t">${esc((l.desc || r.item_code).slice(0, 140))}<small>${l.internal_ref ? "Ref " + esc(l.internal_ref) + " · " : ""}BOQ ${qn(l.boq_qty)} ${esc(l.unit || "")}</small></span>
          <input class="ctl" type="number" step="any" min="0" inputmode="decimal" value="${r.qty === "" ? "" : r.qty}" aria-label="Quantity" data-q="${i}"><small>${esc(l.unit || "")}</small>
          <button class="btn" data-rm="${i}" aria-label="Remove line">×</button></div>`; }).join("") || `<div class="bl-none sm">No line yet. Search below and click a line to add it.</div>`;
      document.querySelectorAll("#bl-clist [data-q]").forEach((el) => {
        el.oninput = () => { rows[+el.dataset.q].qty = el.value === "" ? "" : Number(el.value); };
        el.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); $("bl-cfind").focus(); $("bl-cfind").select(); } };
      });
      document.querySelectorAll("#bl-clist [data-rm]").forEach((el) => el.onclick = () => { rows.splice(+el.dataset.rm, 1); drawList(); drawFound(); });
      if (focusLast) { const q = document.querySelector(`#bl-clist [data-q="${rows.length - 1}"]`); if (q) { q.focus(); q.select(); } }
    };
    const drawFound = () => {
      const q = $("bl-cfind").value.trim().toLowerCase();
      const have = new Set(rows.map((r) => r.item_code));
      const words = q.split(/\s+/).filter(Boolean);
      const hits = (q ? all.filter((l) => !have.has(l.item_code) && (words.every((w) => l.desc.toLowerCase().includes(w)) ||
        String(l.internal_ref || "").toLowerCase() === q || String(l.item_code).toLowerCase() === q)) : []).slice(0, 30);
      $("bl-cfound").innerHTML = hits.map((l) => `<button class="bl-hit" data-c="${esc(l.item_code)}"><span>${esc(l.desc.slice(0, 120))}</span><small>${l.internal_ref ? "Ref " + esc(l.internal_ref) + " · " : ""}BOQ ${qn(l.boq_qty)} ${esc(l.unit)} · already on challans ${qn((j.received || {})[l.item_code] || 0)}</small></button>`).join("")
        || (q ? `<div class="bl-none sm">No match.</div>` : "");
      document.querySelectorAll("#bl-cfound .bl-hit").forEach((el) => el.onclick = () => {
        rows.push({ item_code: el.dataset.c, qty: "" }); $("bl-cfind").value = ""; drawList(true); drawFound();
      });
    };
    $("bl-cfind").oninput = drawFound;
    drawList(); drawFound();
    if (!c) $("bl-cno").focus();
  }

  // Opens from the Challans screen, the draft RA screen and Billing home (next to Download): the service is passed in,
  // the "already submitted?" info is loaded for it, and `done` brings the person back to the screen they started on.
  function importChallans(service = B.svc, done = null) {
    let file = null, lock = 0, last = null, info = B.ch && B.svc === service ? B.ch : null;
    const view = () => {
      const mb = $("bl-modal").querySelector(".bl-mbody");
      const r = last;
      if (!$("bl-file")) {
        mb.innerHTML = `<p class="bl-hint">Choose the invoice workbook (or its CHALLAN sheet). MI reads the challan columns — number, date, RA tag and the quantity against each line — and pairs every row with a BOQ line. Nothing is saved until you press Import.</p>
          <input id="bl-file" type="file" accept=".xlsx,.xlsm" class="ctl"><div id="bl-impres"></div>`;
        $("bl-file").onchange = async () => { file = $("bl-file").files[0] || null; lock = 0; if (file) await check(false); };
      }
      $("bl-impres").innerHTML = `${r ? `<div class="bl-imp">
          <div class="bl-impk"><div><span>Sheet read</span><b>${esc(r.sheet)}</b></div><div><span>Challans to import</span><b>${r.new}</b></div><div><span>Already in MI</span><b>${r.skipped_duplicate}</b></div><div><span>Rows not paired</span><b class="${r.unmatched.length ? "neg" : ""}">${r.unmatched.length}</b></div></div>
          ${r.by_ra.length ? `<div class="bl-lbl">What these challans come to${r.pct == null ? "" : " at " + r.pct + "%"} — compare with your submitted bill</div>
            ${r.by_ra.map((x) => `<div class="bl-erow"><span>${raL(x.ra)} · ${x.lines} lines</span><b>${x.value == null ? "set the Supply % first" : inr(x.value, true)}</b></div>`).join("")}` : ""}
          ${r.over_boq.length ? `<div class="bl-callout">${r.over_boq.length} line(s) have more challan qty than the BOQ qty (e.g. ${esc(r.over_boq[0].desc.slice(0, 60))}: ${qn(r.over_boq[0].challan)} vs ${qn(r.over_boq[0].boq_qty)}). A bill is capped at the BOQ qty.</div>` : ""}
          ${r.problems.length ? `<div class="bl-callout">${r.problems.map(esc).join("<br>")}</div>` : ""}
          ${r.unmatched.length ? `<div class="bl-lbl">Not paired with a BOQ line (not imported — add these by hand)</div><div class="bl-unm">${r.unmatched.slice(0, 40).map((u) => `<div><b>Challan ${esc(u.challan)}</b> · ${qn(u.qty)} · ${esc(u.desc.slice(0, 100))}</div>`).join("")}</div>` : ""}
          ${r.challans.length ? `<div class="bl-lbl">Challans found</div><div class="bl-unm">${r.challans.map((c) => `<div><b>${esc(c.no)}</b> · ${esc(c.date)} · ${c.ra == null ? "no RA tag (on hold)" : raL(c.ra)} · ${c.lines} lines${c.duplicate ? " · already in MI" : ""}${c.unmatched ? " · " + c.unmatched + " not paired" : ""}</div>`).join("")}</div>` : ""}
          ${(r.by_ra.length && info && info.last_locked === 0 && info.mode === "separate" && Math.max(...r.by_ra.map((x) => x.ra)) >= 1) ? `<div class="bl-lbl">Were any of these RAs already submitted to the client?</div>
            <select id="bl-lock" class="ctl"><option value="0">No — they are still open in MI</option>
              ${[...new Set(r.by_ra.map((x) => x.ra))].sort((a, b) => a - b).map((n) => `<option value="${n}" ${lock === n ? "selected" : ""}>Yes — RA-01 to ${raL(n)} are already submitted (bring them in as locked)</option>`).join("")}</select>
            ${r.opening_totals ? `<div class="bl-hint">${Object.entries(r.opening_totals).map(([n, v]) => `${raL(Number(n))}: ${inr(v, true)}`).join(" · ")}</div>` : ""}` : ""}
        </div>` : ""}`;
      if ($("bl-lock")) $("bl-lock").onchange = async () => { lock = Number($("bl-lock").value); await check(false); };
    };
    const check = async (commit) => {
      if (!file) { toast("Choose a file first."); return null; }
      const fd = new FormData(); fd.append("file", file); fd.append("commit", commit ? "true" : "false"); fd.append("lock_upto", String(lock));
      const res = await fetch(api("/" + B.slug + "/challans/" + encodeURIComponent(service) + "/import"), { method: "POST", body: fd });
      if (!res.ok) { const t = await res.text(); try { toast(JSON.parse(t).detail || t); } catch (_) { toast(t); } return null; }
      const r = await res.json();
      if (!commit) { last = r; view(); }
      return r;
    };
    modal("Import challans from Excel", "One-time or any time: re-importing the same file adds nothing twice.", "", [
      { label: "Cancel", run: closeModal },
      { label: "Import", cls: "primary", id: "bl-doimp", run: async () => {
        if (!last || !last.new) { toast("Check a file with new challans first."); return; }
        const r = await check(true); if (!r) return;
        closeModal(); toast(r.new + " challan" + (r.new === 1 ? "" : "s") + " imported."); done ? done() : showChallans(service);
      } }], "min(760px,96vw)");
    view();
    if (!info) jget(api("/" + B.slug + "/challans/" + encodeURIComponent(service))).then((j) => { info = j; if (last) view(); }).catch(() => {});
  }

  // Billing home: one button next to Download. One challan-billed unit -> straight to the file picker; several -> pick the unit first.
  function importFromHome() {
    const us = (B.ov ? B.ov.services : []).filter((x) => x.supply_source === "challan");
    if (!us.length) { toast("No service is billed from delivery challans."); return; }
    const go = (u) => importChallans(u, showHome);
    if (us.length === 1) return go(us[0].service);
    modal("Import challans", "Which service is this Excel for?", `<div class="bl-pick">${us.map((x) => `<button class="btn bl-pickb" data-u="${esc(x.service)}">${esc(x.service)}</button>`).join("")}</div>`,
      [{ label: "Cancel", run: closeModal }]);
    $("bl-modal").querySelectorAll("[data-u]").forEach((b) => { b.onclick = () => { closeModal(); go(b.dataset.u); }; });
  }

  // ------------------------------------------------------ wire metres -> points
  // Point wiring is bought as wire and billed as points. Step 1: the wire challans (metres per colour; only the phase
  // colours of the project's colour code are counted, neutral and earth run beside them). Step 2: the engineer gives
  // metres to BOQ point lines; points = metres / the average point length the BOQ states for that line (whole points
  // only, never above the BOQ). Step 3: those points are the supply quantity of the open RA (server: /wire/<unit>).
  const WCOL = { RED: "#d8402f", YELLOW: "#e8c42a", BLUE: "#2f5fc4", BLACK: "#222", GREEN: "#2f8a43", BROWN: "#8a5a35", GREY: "#8d8d8d",
    GREEN_YELLOW: "linear-gradient(90deg,#2f8a43 50%,#e8c42a 50%)" };
  const W = { unit: null, st: null, blocks: [], sig: "" };
  const mN = (v) => (Math.round(Number(v) * 100) / 100).toLocaleString("en-IN");
  const wFloor = (m, per) => Math.floor(m / per + 1e-9);
  const blocksFrom = (st) => st.lines.map((l) => ({ code: l.item_code, ref: l.ref, desc: l.desc, quote: l.quote, per: l.per, upto: l.upto, typed: l.typed,
    boq: l.boq_qty, rate: l.rate, pct: l.pct, rows: l.rows.map((r) => ({ size: r.size, earlier: r.earlier, now: r.this > 0 ? r.this : "" })) }));
  const bMetres = (b) => b.rows.reduce((a, r) => a + r.earlier + (Number(r.now) || 0), 0);
  const bBefore = (b) => b.rows.reduce((a, r) => a + r.earlier, 0);
  const bPts = (b) => (b.per > 0 ? wFloor(bMetres(b), b.per) : 0);
  const bPtsThis = (b) => bPts(b) - (b.per > 0 ? wFloor(bBefore(b), b.per) : 0);
  const bVal = (b) => (b.pct == null ? null : bPtsThis(b) * b.rate * b.pct / 100);
  const wPayload = () => W.blocks.flatMap((b) => b.rows.filter((r) => Number(r.now) > 0).map((r) => ({ item_code: b.code, size: r.size, metres: Number(r.now), ...(b.typed ? { per: b.per } : {}) })));

  async function showWire(unit, keep) {
    const s = section();
    B.view = "wire"; W.unit = unit;
    let st;
    try { st = await jget(api("/" + B.slug + "/wire/" + encodeURIComponent(unit))); }
    catch (e) { toast(briefErr(e)); return showChallans(unit); }
    if (!keep) {
      W.sync = null; W.syncErr = "";
      if (st.source === "stock" && st.stock && st.stock.confirmed > 0) {      // new deliveries in the register flow in; they only add metres to the pool
        try {
          W.sync = await jsend(api("/" + B.slug + "/wire/" + encodeURIComponent(unit) + "/stock-sync"));
          if (W.sync.added || W.sync.updated || W.sync.removed) st = await jget(api("/" + B.slug + "/wire/" + encodeURIComponent(unit)));
        } catch (e) { W.syncErr = briefErr(e); }
      }
    }
    W.st = st;
    if (!keep) { W.blocks = blocksFrom(st); W.sig = JSON.stringify(wPayload()); }
    drawWire();
  }

  // Step 1 source panel: wire arrives on site and goes into the stock register first, so by default the wire
  // deliveries come from there (confirm each stock material's size and colour once). "Type challans" stays for wire
  // that never went into stock. One source at a time, so a delivery is never counted twice.
  function wireSourceHtml(j) {
    const st = j.stock || { materials: [] }, stock = j.source === "stock";
    const mats = st.materials || [];
    const una = mats.filter((m) => !m.confirmed);
    const sync = W.sync, bits = [];
    if (sync) { if (sync.added) bits.push(sync.added + " new deliver" + (sync.added === 1 ? "y" : "ies") + " from the stock register"); if (sync.updated) bits.push(sync.updated + " updated"); if (sync.removed) bits.push(sync.removed + " no longer in the register"); }
    return `<div class="bl-wsrc"><div class="bl-seg" role="group" aria-label="Where do the wire deliveries come from?">
        <button class="bl-segb ${stock ? "on" : ""}" data-wsrc="stock">Stock register · recommended</button><button class="bl-segb ${stock ? "" : "on"}" data-wsrc="typed">Type challans</button></div>
      <p class="bl-ovnote">${stock ? "Wire reaches site and goes into the stock register first, so its deliveries are read from there: nothing to retype. Choose “Type challans” only for wire that never went into stock." : "You type each wire challan yourself. Choose “Stock register” to read the deliveries from the stock register instead."}</p></div>
      ${stock ? (mats.length ? `<div class="bl-wmap"><div class="bl-wmaph"><b>Confirm the stock materials once</b><small>Size and colour are read from the name; you only confirm.</small>
          ${una.length ? `<button class="btn primary" id="bl-wmconf">Confirm ${una.length} material${una.length === 1 ? "" : "s"}</button>` : `<span class="bl-chip locked">All confirmed</span><button class="btn" id="bl-wmref">Refresh from stock</button>`}</div>
        <div class="bl-scroll"><table class="bl-wtab bl-wmt"><thead><tr><th>Stock material</th><th>Size</th><th>Colour</th><th>Counts for points?</th><th></th></tr></thead><tbody>
        ${mats.map((m) => `<tr class="${m.counted ? "cnt" : "nc"}"><th>${esc(m.material)}</th><td>${esc(m.size)} sq mm</td><td><i class="bl-dot" style="background:${WCOL[m.colour] || "#999"}"></i>${esc(((j.scheme.colours.find((k) => k.key === m.colour)) || { label: m.colour }).label)}</td>
          <td><span class="bl-chip ${m.counted ? "locked" : "none"}">${m.counted ? "Yes · phase" : "No · not counted"}</span></td><td>${m.confirmed ? `<span class="bl-chip locked">Confirmed</span>` : `<span class="bl-chip draft">Needs confirming</span>`}</td></tr>`).join("")}</tbody></table></div></div>`
        : `<div class="bl-callout soft">No wire material (a size and a colour in its name, like “1.5SQMM FRLS WIRE RED”) was found in the stock register for this service. Choose “Type challans” to enter the wire yourself.</div>`) : ""}
      ${W.syncErr ? `<div class="bl-callout">${esc(W.syncErr)}</div>` : ""}
      ${bits.length ? `<div class="bl-callout soft">Stock register: ${esc(bits.join(", "))}.</div>` : ""}
      ${sync && sync.typed_kept ? `<div class="bl-callout">${sync.typed_kept} typed wire challan${sync.typed_kept === 1 ? "" : "s"} in an invoiced RA still count. If the same delivery is also in the stock register, it would be counted twice: check the dates below.</div>` : ""}
      ${sync && sync.locked_differs && sync.locked_differs.length ? `<div class="bl-callout">The register now shows different metres for ${esc(sync.locked_differs.join(", "))}, which are already in an invoiced RA. They are not changed.</div>` : ""}`;
  }
  function wireSourceWire(j, unit) {
    const s = section();
    s.querySelectorAll("[data-wsrc]").forEach((el) => el.onclick = () => {
      const to = el.dataset.wsrc;
      if (to === (j.source || "typed")) return;
      const live = j.challans.filter((c) => c.counts && !c.frozen);
      const go = async () => {
        try { await jsend(api("/" + B.slug + "/wire/" + encodeURIComponent(unit) + "/source"), { source: to }, "PUT"); }
        catch (e) { closeModal(); toast(briefErr(e)); return; }
        closeModal(); toast(to === "stock" ? "Wire deliveries now come from the stock register." : "You type the wire challans now."); showWire(unit);
      };
      if (!live.length) return go();
      modal(to === "stock" ? "Take wire from the stock register?" : "Type the wire challans yourself?", "A delivery is counted once, from one source.",
        `<p>${live.length} wire challan${live.length === 1 ? "" : "s"} in ${raL(j.open_no)} or later ${to === "stock" ? "(typed)" : "(from stock)"} stay saved but stop counting. Challans in an invoiced RA keep counting.</p>`,
        [{ label: "Cancel", run: closeModal }, { label: "Switch", cls: "primary", run: go }]);
    });
    const conf = $("bl-wmconf");
    if (conf) conf.onclick = async () => {
      const una = (j.stock.materials || []).filter((m) => !m.confirmed).map((m) => m.material);
      try {
        await jsend(api("/" + B.slug + "/wire/" + encodeURIComponent(unit) + "/stock-map"), { confirm: una }, "PUT");
        toast("Wire materials confirmed."); showWire(unit);
      } catch (e) { toast(briefErr(e)); }
    };
    const ref = $("bl-wmref"); if (ref) ref.onclick = () => showWire(unit);
  }

  function wireNeeds() {          // the live numbers under the table: size cards + supply summary
    const j = W.st;
    const given = {};
    W.blocks.forEach((b) => b.rows.forEach((r) => { given[r.size] = (given[r.size] || 0) + r.earlier + (Number(r.now) || 0); }));
    const sizes = [...new Set([...j.sizes, ...Object.keys(given)])].sort((a, b) => a - b);
    const cards = sizes.map((sz) => {
      const st = j.size_status.find((x) => x.size === sz) || { counted: 0 };
      const left = st.counted - (given[sz] || 0);
      const cls = left < -1e-6 ? "bad" : left < 1e-6 ? "ok" : "";
      return `<div class="bl-wsz ${cls}"><b>${esc(sz)} sq mm · ${left < -1e-6 ? mN(-left) + " m too many" : left < 1e-6 ? "all metres used" : mN(left) + " m left to give"}</b>
        <span>${mN(st.counted)} m counted · ${mN(given[sz] || 0)} m given</span>
        <div class="bl-bar"><i class="${cls === "bad" ? "drf" : "inv"}" style="width:${st.counted > 0 ? Math.min(100, (given[sz] || 0) / st.counted * 100) : 0}%"></i></div></div>`;
    }).join("");
    const pts = W.blocks.reduce((a, b) => a + bPtsThis(b), 0);
    const vals = W.blocks.map(bVal);
    const total = vals.some((v) => v == null) ? null : vals.reduce((a, v) => a + v, 0);
    return { cards, pts, total, over: sizes.some((sz) => ((j.size_status.find((x) => x.size === sz) || { counted: 0 }).counted - (given[sz] || 0)) < -1e-6) };
  }

  function wireLive() {
    const n = wireNeeds();
    const c = $("bl-wsizes"); if (c) c.innerHTML = n.cards || `<div class="bl-none sm">Enter a wire challan first; the metres you can give appear here.</div>`;
    W.blocks.forEach((b, i) => {
      const a = document.querySelector(`[data-pts="${i}"]`); if (a) a.textContent = qn(bPts(b)) + " of " + qn(b.boq);
      const v = document.querySelector(`[data-val="${i}"]`); if (v) v.textContent = bVal(b) == null ? "—" : inr(bVal(b));
    });
    if ($("bl-wtot")) $("bl-wtot").textContent = n.total == null ? "—" : inr(n.total);
    if ($("bl-wpts")) $("bl-wpts").textContent = qn(n.pts);
    const dirty = JSON.stringify(wPayload()) !== W.sig;
    if ($("bl-wdirty")) $("bl-wdirty").hidden = !dirty;
    if ($("bl-wsave")) $("bl-wsave").classList.toggle("primary", dirty);
  }

  function drawWire() {
    const s = section(), j = W.st, open = j.open_no, unit = W.unit;
    const live = B.ov && (B.ov.ras || []).find((r) => r.service === unit && r.status === "draft" && r.stages && r.stages.indexOf("supply") >= 0);
    const cols = j.scheme.colours, chs = j.challans;
    const counted = (ch) => ch.ra != null && ch.counts !== false;      // on hold = not in any RA yet; a challan of the other source does not count
    const matrix = j.sizes.length ? `<div class="bl-scroll"><table class="bl-wtab"><thead><tr><th></th>${chs.map((c) => `<th class="n"><button class="bl-link" data-wch="${esc(c.id)}">${c.src ? "" : "Challan "}${esc(c.no)}</button><small>${esc(c.date)}</small>
        <span class="bl-chip ${c.frozen ? "locked" : c.ra == null ? "none" : "draft"}">${c.frozen ? raL(c.ra) + " invoiced" : c.ra == null ? "On hold" : raL(c.ra)}</span>${c.src ? ` <span class="bl-chip stock">From stock</span>` : ""}${c.no_missing ? ` <span class="bl-chip draft">No. missing</span>` : ""}${c.counts === false ? ` <span class="bl-chip none">Not counted</span>` : ""}</th>`).join("")}<th class="n">Total m</th><th>Counted for points?</th></tr></thead>
      <tbody>${j.sizes.map((sz) => {
        const tot = (key) => chs.reduce((a, c) => a + (counted(c) ? ((c.sizes[sz] || {})[key] || 0) : 0), 0);
        return `<tr class="sz"><th colspan="${chs.length + 3}">Wire ${esc(sz)} sq mm</th></tr>` + cols.map((k) => `<tr class="${k.counted ? "cnt" : "nc"}">
          <th><i class="bl-dot" style="background:${WCOL[k.key] || "#999"}"></i>${esc(k.label)}</th>${chs.map((c) => `<td class="n">${(c.sizes[sz] || {})[k.key] ? mN(c.sizes[sz][k.key]) : ""}</td>`).join("")}
          <td class="n"><b>${tot(k.key) ? mN(tot(k.key)) : ""}</b></td><td><span class="bl-chip ${k.counted ? "locked" : "none"}">${k.counted ? "Counted" : "Not counted"}</span></td></tr>`).join("")
          + `<tr class="sum"><th>Counted metres</th>${chs.map((c) => `<td class="n">${mN(cols.filter((k) => k.counted).reduce((a, k) => a + ((c.sizes[sz] || {})[k.key] || 0), 0)) || ""}</td>`).join("")}<td class="n"><b>${mN(cols.filter((k) => k.counted).reduce((a, k) => a + tot(k.key), 0))}</b></td><td></td></tr>`;
      }).join("")}</tbody></table></div>`
      : `<div class="bl-none">${j.source === "stock" ? "No wire delivery yet. Confirm the stock materials above and the deliveries appear here." : "No wire challan yet. Click “+ Add wire challan” and type each challan once."}</div>`;
    const blockHtml = (b, i) => `<div class="bl-wb" data-i="${i}">
        <div class="bl-wbh">
          <div class="t"><b>${esc(b.ref)}</b> ${esc(b.desc.replace(/\s+/g, " ").length > 100 ? b.desc.replace(/\s+/g, " ").slice(0, 100).replace(/\s+\S*$/, "") + " …" : b.desc.replace(/\s+/g, " "))}<small>${b.typed ? "Metres per point typed by the engineer (the BOQ text states none)" : "“…" + esc(b.quote) + "”"}</small></div>
          <div class="pp" data-l="Metres per point">${b.typed && !bBefore(b) ? `<input class="ctl" type="number" step="any" min="0" inputmode="decimal" value="${b.per || ""}" data-per="${i}" aria-label="Metres per point">`
            : `<b>${mN(b.per)}</b> m<small>${b.typed ? "typed" : "from BOQ text" + (b.upto ? " (upto)" : "")}</small>`}</div>
          <div class="n" data-l="Points / BOQ points"><b data-pts="${i}">${qn(bPts(b))} of ${qn(b.boq)}</b></div>
          <div class="n" data-l="Supply ${j.supply_pct == null ? "" : esc(String(j.supply_pct)) + "%"} this bill"><b data-val="${i}">${bVal(b) == null ? "—" : inr(bVal(b))}</b></div>
        </div>
        ${b.rows.map((r, k) => `<div class="bl-wrow" data-k="${k}">
          <label>Wire<select class="ctl" data-sz="${i}:${k}">${[...new Set([...j.sizes, r.size])].sort((x, y) => x - y).map((z) => `<option value="${esc(z)}" ${z === r.size ? "selected" : ""}>${esc(z)} sq mm</option>`).join("")}</select></label>
          <label>Metres given in ${raL(open)}<input class="ctl" type="number" step="any" min="0" inputmode="decimal" value="${r.now === "" ? "" : r.now}" data-m="${i}:${k}" aria-label="Metres given"></label>
          <span class="bl-ovh">${r.earlier ? mN(r.earlier) + " m already in earlier RAs" : ""}</span>
          ${r.earlier ? "" : `<button class="btn" data-rmrow="${i}:${k}" aria-label="Remove wire size">×</button>`}</div>`).join("")}
        <div class="bl-wadd"><button class="bl-link" data-addsz="${i}">+ another wire size on this line</button>${bBefore(b) ? "" : `<button class="bl-link" data-rmblk="${i}">Remove this line</button>`}</div>
      </div>`;
    const n = wireNeeds();
    s.innerHTML = head("Wire metres to points", `${unit} · points go into ${raL(open)}. The BOQ’s own “average point length” turns metres into points; nothing is guessed.`,
      `<button class="btn" id="bl-wback">← Challans</button>${live ? `<button class="btn" id="bl-wra" data-ra="${esc(live.id)}">Open ${raL(live.no)} bill</button>` : ""}
       ${j.source !== "stock" ? `<button class="btn" id="bl-wadd">+ Add wire challan</button>` : ""}`) + `
      <div class="bl-card pad"><div class="bl-stp"><span>1</span><b>Wire received, by delivery</b><small>${j.source === "stock" ? "Metres per colour, read from the stock register." : "Metres per colour. Type each challan once."}</small>
        <label class="bl-wsch">Colour code <select class="ctl" id="bl-wscheme" ${j.scheme_locked ? "disabled title=\"Locked once wire challans are entered\"" : ""}>${j.schemes.map((x) => `<option value="${esc(x.id)}" ${x.id === j.scheme.id ? "selected" : ""}>${esc(x.label)}</option>`).join("")}</select></label></div>
        ${wireSourceHtml(j)}
        ${matrix}
        <p class="bl-ovnote">Only the phase wires (${esc(cols.filter((k) => k.counted).map((k) => k.label).join(" + "))}) are counted: every circuit has its own phase, neutral and earth laid together, so the phase wire is the length of circuit laid and neutral / earth are not counted again (${esc(j.scheme.note)}).</p></div>
      <div class="bl-card pad"><div class="bl-stp"><span>2</span><b>Give the metres to BOQ point lines</b><small>Points = metres ÷ metres per point. Whole points only; a line never goes above its BOQ points.</small>
        <i class="bl-wdirty" id="bl-wdirty" hidden>Unsaved changes</i><button class="btn" id="bl-wsave">Save metres</button></div>
        <div id="bl-wblocks">${W.blocks.length ? W.blocks.map(blockHtml).join("") : `<div class="bl-none sm">No point line has wire yet. Search a point line below.</div>`}</div>
        ${j.sizes.length ? `<input id="bl-wfind" class="ctl" type="search" placeholder="Search a point line by words or its Ref no, then click it" autocomplete="off"><div id="bl-wfound" class="bl-found"></div>` : `<div class="bl-callout soft">Enter a wire challan first, then give its metres to the point lines.</div>`}
        <div class="bl-wsizes" id="bl-wsizes">${n.cards}</div></div>
      <div class="bl-card pad bl-wsum"><div class="bl-stp"><span>3</span><b>Supply bill from points</b><small>${raL(open)}${j.supply_pct == null ? " · set the Supply % in Billing setup" : " · supply " + j.supply_pct + "% of the line rate"}</small></div>
        <div class="bl-wsumv"><div><span>Points in this bill</span><b id="bl-wpts">${qn(n.pts)}</b></div><div><span>Supply amount</span><b id="bl-wtot">${n.total == null ? "—" : inr(n.total)}</b></div></div>
        <p class="bl-ovnote">These points are the supply quantity of ${raL(open)} (shown in Challans as “Wire ${raL(open)}”). Installation, the balance %, is billed on points completed in Site Progress, never on wire. Metres not yet given stay visible above until a person gives them to a line.</p></div>`;
    wireProj();
    $("bl-wback").onclick = () => showChallans(unit);
    if ($("bl-wadd")) $("bl-wadd").onclick = () => editWireChallan(null);
    wireSourceWire(j, unit);
    if ($("bl-wra")) $("bl-wra").onclick = () => showRA($("bl-wra").dataset.ra);
    $("bl-wsave").onclick = saveWire;
    const sch = $("bl-wscheme");
    if (sch) sch.onchange = async () => {
      try { await jsend(api("/" + B.slug + "/wire-scheme"), { scheme: sch.value }, "PUT"); toast("Colour code saved."); showWire(unit); }
      catch (e) { toast(briefErr(e)); showWire(unit); }
    };
    s.querySelectorAll("[data-wch]").forEach((el) => el.onclick = () => editWireChallan(j.challans.find((c) => c.id === el.dataset.wch)));
    s.querySelectorAll("[data-m]").forEach((el) => el.oninput = () => { const [i, k] = el.dataset.m.split(":").map(Number); W.blocks[i].rows[k].now = el.value === "" ? "" : Number(el.value); wireLive(); });
    s.querySelectorAll("[data-per]").forEach((el) => el.oninput = () => { W.blocks[+el.dataset.per].per = Number(el.value) || 0; wireLive(); });
    s.querySelectorAll("[data-sz]").forEach((el) => el.onchange = () => { const [i, k] = el.dataset.sz.split(":").map(Number); W.blocks[i].rows[k].size = el.value; wireLive(); });
    s.querySelectorAll("[data-rmrow]").forEach((el) => el.onclick = () => { const [i, k] = el.dataset.rmrow.split(":").map(Number); W.blocks[i].rows.splice(k, 1); if (!W.blocks[i].rows.length) W.blocks.splice(i, 1); drawWire(); });
    s.querySelectorAll("[data-rmblk]").forEach((el) => el.onclick = () => { W.blocks.splice(+el.dataset.rmblk, 1); drawWire(); });
    s.querySelectorAll("[data-addsz]").forEach((el) => el.onclick = () => {
      const b = W.blocks[+el.dataset.addsz];
      const free = j.sizes.find((z) => !b.rows.some((r) => r.size === z)) || j.sizes[0];
      if (!free) return toast("Enter a wire challan first.");
      if (b.rows.some((r) => r.size === free)) return toast("Every wire size is already on this line.");
      b.rows.push({ size: free, earlier: 0, now: "" }); drawWire();
    });
    const find = $("bl-wfind");
    if (find) {
      const draw = () => {
        const q = find.value.trim().toLowerCase(), words = q.split(/\s+/).filter(Boolean);
        const have = new Set(W.blocks.map((b) => b.code));
        const pool = j.eligible.map((l) => ({ ...l, free: false })).concat(j.free_lines.map((l) => ({ ...l, free: true })));
        const hits = (q ? pool.filter((l) => !have.has(l.item_code) && (words.every((w) => l.desc.toLowerCase().includes(w)) || String(l.ref).toLowerCase() === q)) : pool.filter((l) => !have.has(l.item_code) && !l.free)).slice(0, q ? 30 : 6);
        const more = q ? 0 : pool.filter((l) => !have.has(l.item_code) && !l.free).length - hits.length;
        $("bl-wfound").innerHTML = hits.map((l) => `<button class="bl-hit" data-c="${esc(l.item_code)}"><span>${esc(l.desc.replace(/\s+/g, " ").slice(0, 120))}</span>
          <small>Ref ${esc(l.ref)} · BOQ ${qn(l.boq_qty)} points · ${l.free ? "the BOQ states no point length: you type it" : mN(l.per) + " m per point" + (l.upto ? " (upto)" : "")}</small></button>`).join("")
          || (q ? `<div class="bl-none sm">No match.</div>` : "");
        if (more > 0) $("bl-wfound").insertAdjacentHTML("beforeend", `<div class="bl-ovh">+${more} more point lines. Type words or a Ref no to find them.</div>`);
        $("bl-wfound").querySelectorAll(".bl-hit").forEach((el) => el.onclick = () => {
          const l = pool.find((x) => x.item_code === el.dataset.c);
          W.blocks.push({ code: l.item_code, ref: l.ref, desc: l.desc, quote: l.quote, per: l.free ? 0 : l.per, upto: l.upto, typed: l.free, boq: l.boq_qty,
            rate: l.rate, pct: l.pct, rows: [{ size: j.sizes[0], earlier: 0, now: "" }] });
          drawWire();
          const m = document.querySelector(`[data-m="${W.blocks.length - 1}:0"]`); if (m) m.focus();
        });
      };
      find.oninput = draw; draw();
    }
    wireLive();
  }

  async function saveWire() {
    const bad = W.blocks.find((b) => b.rows.some((r) => Number(r.now) > 0) && !(b.per > 0));
    if (bad) return toast("Type the metres per point for line " + bad.ref + ".");
    if (wireNeeds().over) return toast("More metres are given than the wire received. Reduce the metres, or add the wire challan.");
    try {
      await jsend(api("/" + B.slug + "/wire/" + encodeURIComponent(W.unit) + "/alloc"), { rows: wPayload() }, "PUT");
      toast("Metres saved. The points are in " + raL(W.st.open_no) + "."); showWire(W.unit);
    } catch (e) { toast(briefErr(e)); }
  }

  function editWireChallan(c) {
    const j = W.st, unit = W.unit, open = j.open_no;
    const cols = j.scheme.colours;
    const blocks = c ? Object.keys(c.sizes).sort((a, b) => a - b).map((sz) => ({ size: sz, v: { ...c.sizes[sz] } })) : [{ size: j.sizes[0] || "1.5", v: {} }];
    const raOpts = [open, open + 1, open + 2].concat(c && c.ra != null && c.ra > open + 2 ? [c.ra] : []);
    const body = () => `<div class="bl-chform">
        <label>Challan no.<input id="bl-wcno" class="ctl" value="${esc(c ? c.no : "")}" autocomplete="off" inputmode="text"></label>
        <label>Date<input id="bl-wcdate" class="ctl" type="date" value="${esc(c ? c.date : today())}"></label>
        <label>Billed in<select id="bl-wcra" class="ctl">${raOpts.map((n) => `<option value="${n}" ${(c ? c.ra : open) === n ? "selected" : ""}>${raL(n)}${n === open ? " (open)" : ""}</option>`).join("")}
          <option value="hold" ${c && c.ra == null ? "selected" : ""}>On hold</option></select></label>
        <label>Note (optional)<input id="bl-wcnote" class="ctl" value="${esc(c ? c.note : "")}" autocomplete="off"></label></div>
      ${c && c.src ? `<div class="bl-hint">From the stock register. The metres follow the register; fix a wrong number there. You can add the challan number from the paper challan${c.no_missing ? " (the invoice shows “" + esc(c.no) + "” until then)" : ""}.</div>` : ""}
      <div class="bl-lbl">Metres received, per wire size and colour</div><div id="bl-wcb"></div>
      ${c && c.src ? "" : `<button class="btn" id="bl-wcaddsz">+ Add another wire size</button>`}`;
    modal(c ? "Edit wire challan " + c.no : "Add a wire challan", "Metres of each colour on this challan. Counted colours are marked.", body(), [
      { label: "Cancel", run: closeModal },
      ...(c && !c.frozen && !c.src ? [{ label: "Delete", cls: "danger", run: async () => { await jsend(api("/" + B.slug + "/wire/" + encodeURIComponent(unit) + "/challans/" + c.id), undefined, "DELETE"); closeModal(); toast("Wire challan deleted."); showWire(unit); } }] : []),
      ...(c && c.frozen ? [] : [{ label: c ? "Save changes" : "Save challan", cls: "primary", run: async () => {
        const payload = { no: $("bl-wcno").value.trim(), date: $("bl-wcdate").value, note: $("bl-wcnote").value, ra: $("bl-wcra").value === "hold" ? null : Number($("bl-wcra").value),
          sizes: blocks.map((b) => ({ size: b.size, colours: Object.fromEntries(Object.entries(b.v).filter(([, m]) => Number(m) > 0).map(([k, m]) => [k, Number(m)])) })) };
        const base = api("/" + B.slug + "/wire/" + encodeURIComponent(unit) + "/challans");
        await jsend(c ? base + "/" + c.id : base, payload);
        closeModal(); toast("Wire challan " + payload.no + " saved."); showWire(unit);
      } }])], "min(720px,96vw)");
    const draw = () => {
      $("bl-wcb").innerHTML = blocks.map((b, i) => `<div class="bl-wcblk" data-i="${i}">
        <div class="bl-wchd"><label>Wire size, sq mm <input class="ctl" type="number" step="any" min="0" inputmode="decimal" value="${esc(b.size)}" data-bsz="${i}" ${c && c.src ? "disabled" : ""}></label>
          ${blocks.length > 1 && !(c && (c.frozen || c.src)) ? `<button class="btn" data-bdel="${i}" aria-label="Remove this size">×</button>` : ""}</div>
        <div class="bl-wcc">${cols.map((k) => `<label><span><i class="bl-dot" style="background:${WCOL[k.key] || "#999"}"></i>${esc(k.label)}${k.counted ? "" : " <small>(not counted)</small>"}</span>
          <input class="ctl" type="number" step="any" min="0" inputmode="decimal" value="${b.v[k.key] || ""}" data-bv="${i}:${k.key}" ${c && (c.frozen || c.src) ? "disabled" : ""}></label>`).join("")}</div></div>`).join("");
      document.querySelectorAll("#bl-wcb [data-bsz]").forEach((el) => el.oninput = () => { blocks[+el.dataset.bsz].size = el.value; });
      document.querySelectorAll("#bl-wcb [data-bv]").forEach((el) => el.oninput = () => { const [i, k] = el.dataset.bv.split(":"); blocks[+i].v[k] = el.value; });
      document.querySelectorAll("#bl-wcb [data-bdel]").forEach((el) => el.onclick = () => { blocks.splice(+el.dataset.bdel, 1); draw(); });
    };
    draw();
    if ($("bl-wcaddsz")) $("bl-wcaddsz").onclick = () => { blocks.push({ size: "", v: {} }); draw(); const l = document.querySelectorAll("#bl-wcb [data-bsz]"); l[l.length - 1].focus(); };
    if (!c) $("bl-wcno").focus();
  }

  setTimeout(loadBadge, window.__BL_BADGE_MS || 2500);
  window.MIBilling = { open, showChallans, showOverall, editPackages, showWire, _state: B, _inr: inr };
  section();
})();
