// Material Forecast export: the actual confirmed mockup -- clicking Export
// opens a modal with Service -> Category -> Size cascading dropdowns and
// Status checkboxes, matching the mockups discussed and finalized earlier.
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
    <button id="dl" hidden>Export</button>
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
<div id="exportModal" hidden>
  <button onclick="closeExportModal()">Close</button>
  <select id="exSvc"></select>
  <select id="exType" disabled><option value="">All categories</option></select>
  <label id="exSizeWrap" hidden><select id="exSize"><option value="">All sizes</option></select></label>
  <div id="exStatusList"></div>
  <button id="exSelectAll"></button>
  <p id="exScopeLine"></p>
  <button onclick="closeExportModal()">Cancel</button>
  <button id="exGo"></button>
</div>
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
  if (p === "/api/sizes/run1") {
    if (u.searchParams.get("subcategory") === "Wire")
      return [{ name: "1.5SQMM", count: 10 }, { name: "2.5SQMM", count: 10 }, { name: "4.0SQMM", count: 16 }];
    return [];   // Cable tray has no extractable sizes -- Size stays hidden
  }
  if (p === "/api/forecast/run1") return [];
  throw new Error("unmocked route: " + p);
}
global.fetch = window.fetch = async (url) => ({ ok: true, json: async () => route(url), text: async () => JSON.stringify(route(url)) });

// jsdom cannot actually perform navigation (window.location.href writes are
// silently no-ops there, "Not implemented: navigation to another Document")
// -- app.js's own export click now uses a transient <a>.click() instead
// (the same pattern the DPR export already used), which we can intercept
// cleanly here the same way test_issue2_export_modal_ui.mjs already does.
dom.virtualConsole.on("jsdomError", () => {});   // swallow any other unrelated jsdom noise
let lastExportHref = null;
window.HTMLAnchorElement.prototype.click = function () { lastExportHref = this.href; };

const src = fs.readFileSync(new URL("../app.js", import.meta.url), "utf8");
window.eval(src);
async function flush(n = 10) { for (let i = 0; i < n; i++) await new Promise((r) => setTimeout(r, 0)); }

async function main() {
  await flush();
  await window.eval(`show("run1", { lead_time: 7, issues: [], mapping: {}, leadtime: {},
    project: "Fixture Hotel", filename: "register.xlsx", source: "register",
    created: new Date().toISOString(), stats: { asof: "2026-09-18", materials: 413 } },
    { services: ["Electrical", "Fire & HVAC", "Plumbing"], counts: { STOCKED_OUT: 1, RED: 1 },
      materials: 413, act_today: 71, idle_lines: 111, overdue_orders: 79 })`);
  await flush();

  // ---- test 1: clicking Export opens the modal (not a direct download) ----
  const dl = document.getElementById("dl");
  assert.ok(!dl.hidden, "FAIL: Export button should be visible once a run is loaded");
  dl.click();
  await flush(10);
  assert.strictEqual(document.getElementById("exportModal").hidden, false,
    "FAIL: clicking Export must open the picker modal, not download directly");
  assert.strictEqual(lastExportHref, null, "FAIL: nothing should download yet -- only opening the modal");
  console.log("PASS: clicking Export opens the picker modal (no direct download)");

  // ---- test 2: Service dropdown lists the real project services ----
  const svcOpts = [...document.getElementById("exSvc").options].map((o) => o.value);
  assert.deepStrictEqual(svcOpts, ["", "Electrical", "Fire & HVAC", "Plumbing"]);
  console.log("PASS: Service dropdown lists 'All services' plus every real project service");

  // ---- test 3: picking a Service populates real Categories (cascading) ----
  const exSvc = document.getElementById("exSvc");
  exSvc.value = "Electrical";
  exSvc.dispatchEvent(new window.Event("change"));
  await flush(10);
  const exType = document.getElementById("exType");
  assert.strictEqual(exType.disabled, false);
  const catOpts = [...exType.options].map((o) => o.value);
  assert.deepStrictEqual(catOpts, ["", "Wire", "Cable tray"]);
  console.log("PASS: Service=Electrical cascades real categories (Wire, Cable tray) into the Category dropdown");

  // ---- test 4: picking a Category with real sizes reveals the Size dropdown (cascading) ----
  exType.value = "Wire";
  exType.dispatchEvent(new window.Event("change"));
  await flush(10);
  assert.strictEqual(document.getElementById("exSizeWrap").hidden, false,
    "FAIL: Wire has real extractable sizes -- Size dropdown should now be visible");
  const sizeOpts = [...document.getElementById("exSize").options].map((o) => o.value);
  assert.deepStrictEqual(sizeOpts, ["", "1.5SQMM", "2.5SQMM", "4.0SQMM"]);
  console.log("PASS: Category=Wire cascades real sizes (1.5/2.5/4.0 SQMM) into the Size dropdown");

  // ---- test 4b: a category with NO real sizes keeps the Size field hidden ----
  exType.value = "Cable tray";
  exType.dispatchEvent(new window.Event("change"));
  await flush(10);
  assert.strictEqual(document.getElementById("exSizeWrap").hidden, true,
    "FAIL: Cable tray has no extractable sizes -- Size must stay hidden, never a fake dropdown");
  console.log("PASS: a category with no real sizes keeps the Size field hidden (never guesses)");

  // back to Wire + a real size for the export test below
  exType.value = "Wire";
  exType.dispatchEvent(new window.Event("change"));
  await flush(10);
  document.getElementById("exSize").value = "2.5SQMM";
  document.getElementById("exSize").dispatchEvent(new window.Event("change"));

  // ---- test 5: checking two status buckets unions their real codes ----
  const boxes = [...document.querySelectorAll("#exStatusList input[data-exstatus]")];
  boxes.forEach((b) => { b.checked = false; });
  boxes.find((b) => b.dataset.exstatus === "STOCKED_OUT").checked = true;   // "Already out"
  boxes.find((b) => b.dataset.exstatus === "AMBER").checked = true;         // "Order this week"
  document.getElementById("exGo").click();
  await flush(10);
  const navUrl = new URL(lastExportHref, "http://localhost");
  assert.strictEqual(navUrl.pathname, "/api/export/run1");
  assert.strictEqual(navUrl.searchParams.get("service"), "Electrical");
  assert.strictEqual(navUrl.searchParams.get("subcategory"), "Wire");
  assert.strictEqual(navUrl.searchParams.get("size"), "2.5SQMM");
  const gotCodes = new Set((navUrl.searchParams.get("status") || "").split(","));
  assert.deepStrictEqual(gotCodes, new Set(["STOCKED_OUT", "AMBER"]));
  assert.strictEqual(navUrl.searchParams.get("overdue"), null);
  console.log("PASS: Electrical -> Wire -> 2.5SQMM + [Already out, Order this week] exports exactly that scope");

  // ---- test 6: "Order date passed" sends overdue=1 alongside any other
  // checked status buckets, never folded into the status code list ----
  lastExportHref = null;
  dl.click();
  await flush(10);
  const boxes2 = [...document.querySelectorAll("#exStatusList input[data-exstatus]")];
  boxes2.forEach((b) => { b.checked = false; });
  boxes2.find((b) => b.dataset.exstatus === "__overdue__").checked = true;
  document.getElementById("exGo").click();
  await flush(10);
  const navUrl2 = new URL(lastExportHref, "http://localhost");
  assert.strictEqual(navUrl2.searchParams.get("overdue"), "1");
  assert.strictEqual(navUrl2.searchParams.get("status"), null);
  console.log("PASS: 'Order date passed' alone sends overdue=1, never mixed into the status codes");

  // ---- test 7: "Select everything" clears service and checks every box ----
  lastExportHref = null;
  dl.click();
  await flush(10);
  document.getElementById("exSvc").value = "Electrical";
  document.getElementById("exSvc").dispatchEvent(new window.Event("change"));
  await flush(10);
  document.getElementById("exSelectAll").click();
  await flush(10);
  assert.strictEqual(document.getElementById("exSvc").value, "");
  const allChecked = [...document.querySelectorAll("#exStatusList input")].every((c) => c.checked);
  assert.ok(allChecked, "FAIL: 'Select everything' must tick every status box");
  document.getElementById("exGo").click();
  await flush(10);
  const navUrl3 = new URL(lastExportHref, "http://localhost");
  assert.strictEqual(navUrl3.search, "", "FAIL: 'Select everything' must produce a plain unscoped export URL");
  console.log("PASS: 'Select everything' clears all scoping -> plain unscoped export");

  // ---- test 8: Cancel closes the modal without downloading anything ----
  lastExportHref = null;
  dl.click();
  await flush(10);
  document.querySelector('#exportModal button').click();   // the Cancel/Close button
  await window.eval("closeExportModal()");   // inline onclick= doesn't fire under runScripts:'outside-only'; call it directly -- same effect a real click would have
  await flush(10);
  assert.strictEqual(document.getElementById("exportModal").hidden, true);
  assert.strictEqual(lastExportHref, null, "FAIL: Cancel must never trigger a download");
  console.log("PASS: Cancel closes the modal, nothing downloads");
}

main().then(() => console.log("\nALL EXPORT MODAL (FINAL MOCKUP) TESTS PASSED"))
  .catch((e) => { console.error("TEST FAILURE:", e.message, e.stack); process.exit(1); });
