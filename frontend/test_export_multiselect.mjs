// Issue 3: Export modal's Service/Category/Size are now real multi-select
// chips (the HTML was already converted to chip containers; this proves
// the JS -- previously still reading .value off a <select>, silently
// broken -- now actually drives them). Also proves the filename preview
// shown in the modal EXACTLY matches api.py's own _capped()/_slug() naming.
import { JSDOM } from "jsdom";
import fs from "fs";
import assert from "assert";

const html = `<!doctype html><html><body>
<header class="top" id="top"><div class="brand"><h1>Material intelligence</h1><p id="ctx"></p><p id="fresh" hidden></p></div>
  <div class="topact">
    <button id="home" hidden></button><select id="proj" hidden></select>
    <label id="leadwrap" hidden><input id="lead" type="number" value="7"></label>
    <button id="pick" hidden></button><button id="sync" hidden></button>
    <button id="dl" hidden>Export</button><button id="del" hidden></button>
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
  <div id="exSvc"></div>
  <div id="exType"></div>
  <div id="exSizeWrap" hidden><div id="exSize"></div></div>
  <div id="exStatusList"></div>
  <button id="exSelectAll"></button>
  <p id="exScopeLine"></p>
  <p id="exFname"></p>
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

let lastSizesUrl = null;
let lastExportHref = null;

function route(url) {
  const u = new URL(url, "http://localhost");
  const p = u.pathname;
  if (p === "/api/projects") return [];
  if (p === "/api/subcategories/run1")
    return { by_service: {
      Electrical: [{ name: "Wire", count: 37 }, { name: "Cable tray", count: 5 }],
      Plumbing: [{ name: "Pipe", count: 20 }],
    }, all: [] };
  if (p === "/api/sizes/run1") {
    lastSizesUrl = url;
    if (u.searchParams.get("subcategory") === "Wire,Pipe")
      return [{ name: "1.5 SQMM", count: 10 }, { name: "50MM", count: 8 }];
    return [];
  }
  if (p === "/api/forecast/run1") return [];
  throw new Error("unmocked route: " + p);
}
global.fetch = window.fetch = async (url) => ({ ok: true, json: async () => route(url), text: async () => JSON.stringify(route(url)) });
window.HTMLAnchorElement.prototype.click = function () { lastExportHref = this.href; };

const src = fs.readFileSync(new URL("../app.js", import.meta.url), "utf8");
window.eval(src);
async function flush(n = 15) { for (let i = 0; i < n; i++) await new Promise((r) => setTimeout(r, 0)); }

async function main() {
  await flush();
  await window.eval(`show("run1", { lead_time: 7, issues: [], mapping: {}, leadtime: {},
    project: "Hyatt Hotel", filename: "register.xlsx", source: "register",
    created: new Date().toISOString(), stats: { asof: "2026-09-27", materials: 413 } },
    { services: ["Electrical", "Plumbing"], counts: {}, materials: 413, act_today: 0, idle_lines: 0, overdue_orders: 0 })`);
  await flush();

  const dl = document.getElementById("dl");
  dl.click();
  await flush(10);
  assert.strictEqual(document.getElementById("exportModal").hidden, false);
  console.log("PASS: Export button opens the picker modal");

  // ---- test 1: Service chips render for both real services ----
  const svcChips = [...document.querySelectorAll("#exSvc [data-chipval]")].map((b) => b.dataset.chipval);
  assert.deepStrictEqual(svcChips.sort(), ["Electrical", "Plumbing"]);
  console.log("PASS: Service chips list every real project service");

  // ---- test 2: selecting BOTH services shows the UNION of their real categories ----
  document.querySelector('#exSvc [data-chipval="Electrical"]').click();
  await flush(10);
  document.querySelector('#exSvc [data-chipval="Plumbing"]').click();
  await flush(10);
  const catChips = [...document.querySelectorAll("#exType [data-chipval]")].map((b) => b.dataset.chipval);
  assert.deepStrictEqual(catChips.sort(), ["Cable tray", "Pipe", "Wire"],
    "FAIL: expected the union of Electrical's and Plumbing's real categories");
  console.log("PASS: selecting 2 services shows the union of their real categories (Wire, Cable tray, Pipe)");

  // ---- test 3: picking Wire (Electrical) AND Pipe (Plumbing) sends ONE
  // comma-joined /api/sizes call, not two separate calls ----
  document.querySelector('#exType [data-chipval="Wire"]').click();
  await flush(10);
  document.querySelector('#exType [data-chipval="Pipe"]').click();
  await flush(10);
  const sizesUrl = new URL(lastSizesUrl, "http://localhost");
  assert.strictEqual(sizesUrl.searchParams.get("subcategory"), "Wire,Pipe");
  const sizeChips = [...document.querySelectorAll("#exSize [data-chipval]")].map((b) => b.dataset.chipval);
  assert.deepStrictEqual(sizeChips.sort(), ["1.5 SQMM", "50MM"]);
  console.log("PASS: multi-category selection sends ONE comma-joined /api/sizes call and shows both real size sets");

  // ---- test 4: exportModalParams() joins multi-selections with commas,
  // matching exactly what _scoped_forecast expects server-side ----
  document.querySelector('#exSize [data-chipval="1.5 SQMM"]').click();
  await flush(10);
  const params = window.eval("exportModalParams()");
  assert.strictEqual(params.service, "Electrical,Plumbing");
  assert.strictEqual(params.subcategory, "Wire,Pipe");
  assert.strictEqual(params.size, "1.5 SQMM");
  console.log("PASS: exportModalParams() sends comma-joined multi-values for service/category/size");

  // ---- test 5: filename preview matches api.py's own naming exactly --
  // real project name first, real date, then CAPPED scope (<=2 names shown
  // in full, joined with '+'; 3+ would collapse to a count) ----
  const fname = document.getElementById("exFname").textContent;
  const today = new Date().toISOString().slice(0, 10);
  assert.strictEqual(fname, `Hyatt-Hotel_${today}_Electrical+Plumbing_Wire+Pipe_1-5-SQMM.xlsx`,
    `FAIL: filename preview doesn't match expected naming, got "${fname}"`);
  console.log("PASS: filename preview exactly matches the backend's real project-name-first, date, capped-scope naming");

  // ---- test 6: deselecting a service prunes now-invalid category picks
  // (Pipe belongs only to Plumbing) ----
  document.querySelector('#exSvc [data-chipval="Plumbing"]').click();   // untick Plumbing
  await flush(10);
  const catChips2 = [...document.querySelectorAll("#exType [data-chipval]")].map((b) => b.dataset.chipval);
  assert.deepStrictEqual(catChips2.sort(), ["Cable tray", "Wire"],
    "FAIL: Pipe (Plumbing-only) must disappear once Plumbing is deselected");
  console.log("PASS: deselecting a service prunes categories that only belonged to it");

  // ---- test 7: clicking Export actually triggers a download with the
  // real, correctly-scoped query string ----
  document.getElementById("exGo").click();
  await flush(10);
  const exportUrl = new URL(lastExportHref, "http://localhost");
  assert.strictEqual(exportUrl.pathname, "/api/export/run1");
  assert.strictEqual(exportUrl.searchParams.get("service"), "Electrical");
  console.log("PASS: clicking Export triggers the real download with the current (pruned) scope");

  // ---- test 8: "Select everything" clears every selection ----
  dl.click();
  await flush(10);
  document.querySelector('#exSvc [data-chipval="Electrical"]').click();
  await flush(10);
  document.getElementById("exSelectAll").click();
  await flush(10);
  const paramsAfter = window.eval("exportModalParams()");
  assert.strictEqual(Object.keys(paramsAfter).filter((k) => k !== "status").length, 0,
    "FAIL: 'Select everything' must clear every service/category/size selection");
  console.log("PASS: 'Select everything' clears all selections back to unscoped");
}

main().then(() => console.log("\nALL EXPORT MULTISELECT TESTS PASSED"))
  .catch((e) => { console.error("TEST FAILURE:", e.message, e.stack); process.exit(1); });
