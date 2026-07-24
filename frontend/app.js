/* Material Intelligence — frontend
 * Talks to the FastAPI backend in backend/api.py. No build step, no deps.
 */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const el = {
    pick: $("pick"), file: $("file"), dl: $("dl"), lead: $("lead"), ctx: $("ctx"),
    drop: $("drop"), busy: $("busy"), report: $("report"),
    health: $("health"), kpis: $("kpis"), svc: $("svc"),
    q: $("q"), status: $("status"), rows: $("rows"), empty: $("empty"),
    sheet: $("sheet"), sname: $("sname"), smeta: $("smeta"),
    spark: $("spark"), srows: $("srows"),
  };

  let state = { runId: null, meta: null, service: "", rows: [] };

  // ---------------------------------------------------------------- utils
  const asDate = (v) => (!v || v === "NaT") ? null : new Date(v + "T00:00:00");
  const fmtShort = (v) => {
    const d = asDate(v);
    if (!d) return null;
    return d.toLocaleDateString("en-GB", { day: "numeric", month: "short" });
  };
  const fmtNum = (n) => (n === null || n === undefined)
    ? "—" : Number(n).toLocaleString("en-IN", { maximumFractionDigits: 1 });

  function relativeLabel(target, from) {
    const d = asDate(target);
    if (!d) return null;
    const days = Math.round((d - from) / 86400000);
    if (days < 0) return `${-days} day${-days === 1 ? "" : "s"} late`;
    if (days === 0) return "today";
    if (days === 1) return "tomorrow";
    return `in ${days} days`;
  }

  // -------------------------------------------------- "what to do" mapping
  // Turns a forecast row (status/confidence/dates) into one action pill +
  // one short reason line, so the person doesn't have to cross-reference
  // five separate numbers to know what to do next.
  function actionFor(row, today) {
    switch (row.status) {
      case "STOCKED_OUT":
        return { cls: "a-out", label: "Already out", sub: "site is dry" };

      case "RED": {
        const sub = relativeLabel(row.order_by, today) || "today";
        return { cls: "a-now", label: "Order now", sub };
      }

      case "AMBER": {
        const date = fmtShort(row.order_by);
        const sub = relativeLabel(row.order_by, today) || "";
        return { cls: "a-soon", label: date ? `Order by ${date}` : "Order soon", sub };
      }

      case "GREEN": {
        const sub = row.days_left != null ? `${fmtNum(row.days_left)}d in stock` : "stocked";
        return { cls: "a-ok", label: "On track", sub };
      }

      case "OVERSTOCK": {
        let sub = "excess stock";
        if (row.days_left != null) {
          sub = row.days_left >= 365
            ? `${(row.days_left / 365).toFixed(1)} years of cover`
            : `${Math.round(row.days_left)} days of cover`;
        }
        return { cls: "a-stop", label: "Stop ordering", sub };
      }

      case "DEAD_STOCK":
      case "NO_RECENT_USE": {
        const sub = row.days_idle != null ? `idle ${row.days_idle}d` : "not moving";
        return { cls: "a-watch", label: "Review stock", sub };
      }

      case "INSUFFICIENT_DATA":
      default:
        return { cls: "a-watch", label: "Watching", sub: "not enough data yet" };
    }
  }

  // Runs-out is only meaningful for rows with a live burn-rate projection.
  // Already-out, idle, and data-starved rows show a dash instead of a guess.
  function runsOutFor(row) {
    if (!["RED", "AMBER", "GREEN"].includes(row.status) || row.days_left == null) {
      return { bold: "—", sub: "", dash: true };
    }
    const bold = `in ${Math.round(row.days_left)} day${Math.round(row.days_left) === 1 ? "" : "s"}`;
    const lo = fmtShort(row.exhaust_earliest), hi = fmtShort(row.exhaust_latest);
    const single = fmtShort(row.exhaust_date);
    const sub = (lo && hi && lo !== hi) ? `${lo} – ${hi}` : (single || "");
    return { bold, sub, dash: false };
  }

  const TRUST = { HIGH: 3, MEDIUM: 2, LOW: 1, NONE: 0 };
  function trustDots(confidence) {
    const n = TRUST[confidence] ?? 0;
    return `<div class="trust" data-level="${confidence}">` +
      [0, 1, 2].map(i => `<span class="dot ${i < n ? "on" : ""}"></span>`).join("") +
      `</div>`;
  }

  // -------------------------------------------------------------- upload
  el.pick.addEventListener("click", () => el.file.click());
  el.file.addEventListener("change", () => el.file.files[0] && upload(el.file.files[0]));

  ["dragover", "dragleave", "drop"].forEach(evt =>
    el.drop.addEventListener(evt, (e) => {
      e.preventDefault();
      el.drop.classList.toggle("over", evt === "dragover");
      if (evt === "drop" && e.dataTransfer.files[0]) upload(e.dataTransfer.files[0]);
    }));
  el.drop.addEventListener("dragenter", (e) => e.preventDefault());

  async function upload(file) {
    el.drop.hidden = true; el.report.hidden = true; el.busy.hidden = false;
    const fd = new FormData();
    fd.append("file", file);
    fd.append("lead_time", el.lead.value || "7");
    try {
      const res = await fetch("/api/upload", { method: "POST", body: fd });
      const body = await res.json();
      if (!res.ok) throw new Error(body.detail || "upload failed");
      state.runId = body.run_id;
      state.meta = body.meta;
      state.service = "";
      el.ctx.textContent = `${body.meta.filename} · ${body.meta.stats.materials} materials`;
      el.dl.href = `/api/export/${state.runId}`;
      el.dl.hidden = false;
      renderHealth(body.meta);
      renderKpis(body.summary);
      renderServiceTabs(body.summary.services);
      await loadRows();
      el.busy.hidden = true; el.report.hidden = false;
    } catch (err) {
      el.busy.hidden = true; el.drop.hidden = false;
      alert(`Could not process this file: ${err.message}`);
    } finally {
      el.file.value = "";
    }
  }

  function renderHealth(meta) {
    const blocks = meta.issues.filter(i => i.level === "block");
    const warns = meta.issues.filter(i => i.level === "warn");
    let html = "";
    if (blocks.length) {
      html += `<div class="note block"><b>Forecast on hold</b>${blocks.map(b => `<div>${b.text}</div>`).join("")}</div>`;
    }
    if (warns.length) {
      html += `<div class="note warn"><b>Worth checking</b><ul>${warns.map(w => `<li>${w.text}</li>`).join("")}</ul></div>`;
    }
    el.health.innerHTML = html;
  }

  function renderKpis(s) {
    const cards = [
      { l: "Materials tracked", v: s.materials, h: "" },
      { l: "Act today", v: s.act_today, h: "stocked out or ordering now", hot: s.act_today > 0 },
      { l: "Idle capital", v: s.idle_lines, h: "overstock / dead stock" },
      { l: "Overdue orders", v: s.overdue_orders, h: "past their order-by date" },
    ];
    el.kpis.innerHTML = cards.map(c =>
      `<div class="kpi${c.hot ? " hot" : ""}"><p class="l">${c.l}</p><p class="v">${fmtNum(c.v)}</p><p class="h">${c.h}</p></div>`
    ).join("");
  }

  function renderServiceTabs(services) {
    const tabs = ["All", ...services];
    el.svc.innerHTML = tabs.map(t =>
      `<button class="tab${(t === "All" && !state.service) ? " on" : ""}" data-svc="${t === "All" ? "" : t}">${t}</button>`
    ).join("");
    el.svc.querySelectorAll(".tab").forEach(btn =>
      btn.addEventListener("click", () => {
        state.service = btn.dataset.svc;
        el.svc.querySelectorAll(".tab").forEach(b => b.classList.toggle("on", b === btn));
        loadRows();
      }));
  }

  // ------------------------------------------------------------- table
  el.q.addEventListener("input", debounce(loadRows, 250));
  el.status.addEventListener("change", loadRows);

  function debounce(fn, ms) {
    let t;
    return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
  }

  async function loadRows() {
    if (!state.runId) return;
    const p = new URLSearchParams({
      status: el.status.value, service: state.service, q: el.q.value, limit: "1000",
    });
    const res = await fetch(`/api/forecast/${state.runId}?${p}`);
    state.rows = await res.json();
    renderRows();
  }

  function renderRows() {
    const today = asDate(state.meta.stats.asof) || new Date();
    if (!state.rows.length) {
      el.rows.innerHTML = ""; el.empty.hidden = false; return;
    }
    el.empty.hidden = true;
    el.rows.innerHTML = state.rows.map(r => {
      const act = actionFor(r, today);
      const run = runsOutFor(r);
      return `
      <tr data-mat="${r.material}">
        <td>
          <div class="mat">${title(r.material)}</div>
          <div class="sub">${r.service || "—"} · ${r.unit || ""}</div>
        </td>
        <td>
          <div class="act">
            <span class="pill ${act.cls}">${act.label}</span>
            <span class="sub">${act.sub}</span>
          </div>
        </td>
        <td class="n">${fmtNum(r.stock)}</td>
        <td class="n">${fmtNum(r.rate_per_day)}/day</td>
        <td>
          <div class="${run.dash ? "runs dash" : "runs"}">${run.bold}</div>
          ${run.sub ? `<div class="sub">${run.sub}</div>` : ""}
        </td>
        <td>${trustDots(r.confidence)}</td>
      </tr>`;
    }).join("");

    el.rows.querySelectorAll("tr").forEach(tr =>
      tr.addEventListener("click", () => openSheet(tr.dataset.mat)));
  }

  function title(s) {
    return s.replace(/\w\S*/g, t => t[0] + t.slice(1).toLowerCase());
  }

  // ------------------------------------------------------------- drawer
  async function openSheet(material) {
    el.sheet.hidden = false;
    el.sname.textContent = title(material);
    el.smeta.textContent = "Loading history…";
    el.spark.innerHTML = ""; el.srows.innerHTML = "";
    const res = await fetch(`/api/material/${state.runId}?name=${encodeURIComponent(material)}`);
    const hist = await res.json();
    el.smeta.textContent = `${hist.length} day${hist.length === 1 ? "" : "s"} of recorded movement`;
    el.srows.innerHTML = hist.slice().reverse().map(h => `
      <tr>
        <td>${fmtShort(h.date) || h.date}</td>
        <td class="n">${fmtNum(h.qty_in)}</td>
        <td class="n">${fmtNum(h.qty_out)}</td>
        <td class="n">${h.balance == null ? "—" : fmtNum(h.balance)}</td>
      </tr>`).join("");
  }
  window.closeSheet = () => { el.sheet.hidden = true; };
  el.sheet.addEventListener("click", (e) => { if (e.target === el.sheet) closeSheet(); });
})();
