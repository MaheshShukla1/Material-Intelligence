// Material Forecast export: the Export link/button must always match
// whatever Service/Category/Size/Status/Search is currently on screen --
// no separate modal, the existing filter bar IS the "choose what to
// export" UI (see currentScopeParams()/updateExportLink() in app.js).
import { JSDOM } from "jsdom";
import fs from "fs";
import assert from "assert";

const html = `<!doctype html><html><body>
<header class="top" id="top">
  <div class="brand"><h1>Material intelligence</h1><p id="ctx"></p><p id="fresh" hidden></p></div>
  <div class="topact">
    <button id="home" hidden></button>
    <select id="proj" hidden></select>
    <label id="leadwrap" hidden><input id="lead" type="number" value="7"></label>
    <button id="pick" hidden></button>
    <button id="sync" hidden></button>
    <a id="dl" hidden>Export</a>
    <button id="del" hidden></button>
    <input id="file" type="file" hidden>
  </div>
</header>
<main>
<section id="drop"><div id="recent" hidden><div id="recentlist"></div></div>
  <input id="pname"><button id="pickHome"></button>
  <input id="sheetname"><input id="sheetlink"><button id="syncgo"></button>
  <button id="segUpload" class="on"></button><button id="segSheet"></button>
  <div id="paneUpload"></div><div id="paneSheet" hidden></div>
</section>
<section id="busy" hidden><div id="busy-ring"><svg><circle id="busy-arc"></circle></svg></div>
  <b id="busy-pct"></b><p id="busytxt"></p><p id="busy-eta"></p><div id="busy-steps"></div></section>
<section id="report" hidden>
  <div id="health"></div>
  <details id="mapbox"><summary>How this file was read <span id="mapsum"></span></summary><div id="mapbody"></div></details>
  <div id="kpis"></div>
  <div class="bar"><div id="svc"></div>
    <select id="type" hidden></select><select id="size" hidden></select><select id="contractor" hidden></select>
    <input id="q" type="search">
    <label id="statuswrap"><select id="status">
      <option value="STOCKED_OUT,RED,AMBER">Needs a decision</option>
      <option value="STOCKED_OUT,RED">Act today (order now)</option>
      <option value="STOCKED_OUT">Already out of stock</option>
      <option value="__overdue__">Order date passed</option>
      <option value="RED">Order now</option>
      <option value="AMBER">Order this week</option>
      <option value="GREEN">No action needed</option>
      <option value="NO_RECENT_USE">Paused (no recent use)</option>
      <option value="OVERSTOCK,DEAD_STOCK,NO_RECENT_USE">Stop ordering</option>
      <option value="">Everything</option>
      <option value="INSUFFICIENT_DATA">Not enough data</option>
    </select></label>
  </div>
  <p id="rule"></p>
  <div class="tablewrap">
    <table><colgroup></colgroup><thead><tr></tr></thead><tbody id="rows"></tbody></table>
    <p id="empty" hidden></p>
  </div>
</section>
</main>
<div id="sheet" hidden><div id="sname"></div><p id="smeta"></p><div id="spark"></div><table><tbody id="srows"></tbody></table></div>
<div id="modal" hidden><div id="mlist"></div><p id="merr" hidden></p></div>
<div id="confirm" hidden><h2 id="ctitle"></h2><p id="ctext"></p><button id="cgo"></button></div>
</body></html>`;

const dom = new JSDOM(html, { url: "http://localhost/", runScripts: "outside-only", pretendToBeVisual: true });
const { window } = dom;
global.window = window;
global.document = window.document;
global.localStorage = window.localStorage;
global.HTMLElement = window.HTMLElement;

function route(url) {
  const u = new URL(url, "http://localhost");
  const p = u.pathname;
  if (p === "/api/projects") return [];
  if (p === "/api/subcategories/run1")
    return { by_service: { Electrical: [{ name: "Wire", count: 37 }, { name: "Cable tray", count: 5 }] }, all: [] };
  if (p === "/api/sizes/run1") return [{ name: "1.5SQMM", count: 10 }, { name: "2.5SQMM", count: 10 }];
  if (p === "/api/forecast/run1") return [];
  throw new Error("unmocked route: " + p);
}
global.fetch = window.fetch = async (url) => ({ ok: true, json: async () => route(url), text: async () => JSON.stringify(route(url)) });

const src = fs.readFileSync(new URL("../app.js", import.meta.url), "utf8");
window.eval(src);
async function flush(n = 10) { for (let i = 0; i < n; i++) await new Promise((r) => setTimeout(r, 0)); }

async function main() {
  await flush();   // let the script's own loadProjects()/restoreLast() settle (no saved run -> no-op)

  // simulate a loaded run, same as a real upload/sync would trigger
  await window.eval(`show("run1", { lead_time: 7, issues: [], mapping: {}, leadtime: {},
    project: "Fixture Hotel", filename: "register.xlsx", source: "register",
    created: new Date().toISOString(), stats: { asof: "2026-09-18", materials: 413 } },
    { services: ["Electrical", "Fire & HVAC", "Plumbing", "Safety"], counts: { STOCKED_OUT: 1, RED: 1 },
      materials: 413, act_today: 71, idle_lines: 111, overdue_orders: 79 })`);
  await flush();

  const dl = document.getElementById("dl");
  assert.ok(!dl.hidden, "FAIL: Export link should be visible once a run is loaded");

  // ---- test 1: default state -- the status dropdown's own real default
  // (first option, "Needs a decision") is what a fresh page load actually
  // has selected, so Export must reflect exactly that, not a blank filter ----
  const href1 = new URL(dl.getAttribute("href"), "http://localhost");
  assert.strictEqual(href1.pathname, "/api/export/run1");
  assert.strictEqual(href1.searchParams.get("status"), "STOCKED_OUT,RED,AMBER");
  assert.strictEqual(href1.searchParams.get("service"), null);
  console.log("PASS: fresh load -> Export matches the status dropdown's real default (Needs a decision), nothing else scoped");

  // ---- test 1b: explicitly picking "Everything" drops the status param entirely ----
  document.getElementById("status").value = "";
  await window.eval("load()");
  await flush();
  const href1b = new URL(dl.getAttribute("href"), "http://localhost");
  assert.strictEqual(href1b.pathname, "/api/export/run1");
  assert.strictEqual(href1b.search, "", "FAIL: 'Everything' selected must produce a plain unscoped URL");
  console.log("PASS: explicitly picking 'Everything' -> plain unscoped /api/export/run1");

  // ---- test 2: picking Service + Category + Status on screen updates
  // Export's href to match, with NO separate modal -- real DOM interactions
  // (pill click, select value + change event), exactly as a person would ----
  document.querySelector('#svc .tab[data-s="Electrical"]').click();
  await flush(10);
  const typeSel = document.getElementById("type");
  typeSel.value = "Wire";
  typeSel.dispatchEvent(new window.Event("change"));
  await flush(10);
  document.getElementById("status").value = "STOCKED_OUT,RED";
  document.getElementById("status").dispatchEvent(new window.Event("change"));
  await window.eval("load()");
  await flush(10);
  const href2 = new URL(dl.getAttribute("href"), "http://localhost");
  assert.strictEqual(href2.pathname, "/api/export/run1");
  assert.strictEqual(href2.searchParams.get("service"), "Electrical");
  assert.strictEqual(href2.searchParams.get("subcategory"), "Wire");
  assert.strictEqual(href2.searchParams.get("status"), "STOCKED_OUT,RED");
  assert.ok(dl.title.includes("Electrical"), "FAIL: tooltip should reflect the current scope");
  console.log("PASS: Service=Electrical + Category=Wire + Status=Act today builds the matching export URL, live");

  // ---- test 3: "Order date passed" sends overdue=1, not a status list ----
  document.getElementById("status").value = "__overdue__";
  await window.eval("load()");
  await flush(10);
  const href3 = new URL(dl.getAttribute("href"), "http://localhost");
  assert.strictEqual(href3.searchParams.get("overdue"), "1");
  assert.strictEqual(href3.searchParams.get("status"), null);
  console.log("PASS: 'Order date passed' sends overdue=1, matching the KPI card's own definition");

  // ---- test 4: switching to a non-forecast mode (Inventory, via the real
  // Safety pill) falls back to the plain unscoped link rather than a stale
  // Forecast-mode scope ----
  const invPill = document.querySelector('#svc .tab[data-s="Safety"]');
  assert.ok(invPill, "FAIL: expected a Safety pill to exist for this test");
  invPill.click();
  await flush(15);
  assert.strictEqual(dl.getAttribute("href"), "/api/export/run1",
    "FAIL: Inventory mode must not leave a stale Electrical/Wire scope on the Export link");
  console.log("PASS: switching to Inventory resets Export to the plain unscoped link, no stale scope");
}

main().then(() => console.log("\nALL MATERIAL FORECAST EXPORT LINK TESTS PASSED"))
  .catch((e) => { console.error("TEST FAILURE:", e.message, e.stack); process.exit(1); });
