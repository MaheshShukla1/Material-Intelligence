// Issue 3 (v2): Category/Size only appear once a Service is picked, and each
// selected service gets its OWN group -- so selecting Electrical -> Wire,
// then ALSO selecting Plumbing, must never lose Electrical's picks, and
// must never let Plumbing's rows leak in in step to a category that's
// actually only real for Electrical (the cross-product bug).
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
  <p id="exNoSvcHint"></p>
  <div id="exGroups"></div>
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

let lastSizeCalls = [];
let lastExportHref = null;

function route(url) {
  const u = new URL(url, "http://localhost");
  const p = u.pathname;
  if (p === "/api/projects") return [];
  if (p === "/api/subcategories/run1")
    return { by_service: {
      Electrical: [{ name: "Wire", count: 37 }, { name: "Box/JB", count: 12 }],
      Plumbing: [{ name: "Pipe", count: 20 }, { name: "Valve", count: 5 }],
    }, all: [] };
  if (p === "/api/sizes/run1") {
    lastSizeCalls.push(url);
    const svc = u.searchParams.get("service"), cat = u.searchParams.get("subcategory");
    if (svc === "Electrical" && cat === "Wire") return [{ name: "1.5 SQMM", count: 10 }];
    if (svc === "Plumbing" && cat === "Pipe") return [{ name: "50MM", count: 8 }];
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

  document.getElementById("dl").click();
  await flush(10);

  // ---- test 1: no service picked -> no category groups at all, hint shown ----
  assert.strictEqual(document.getElementById("exGroups").children.length, 0,
    "FAIL: no groups should render before any service is picked");
  assert.strictEqual(document.getElementById("exNoSvcHint").hidden, false);
  console.log("PASS: no service picked -> zero category groups, hint visible");

  // ---- test 2: picking Electrical adds ONE group, showing Electrical's own categories ----
  document.querySelector('#exSvc [data-chipval="Electrical"]').click();
  await flush(10);
  assert.strictEqual(document.getElementById("exGroups").children.length, 1);
  assert.strictEqual(document.getElementById("exNoSvcHint").hidden, true);
  const elecCats = [...document.querySelectorAll("#exGroups > div:nth-child(1) [data-chipval]")].map((b) => b.dataset.chipval);
  assert.deepStrictEqual(elecCats.sort(), ["Box/JB", "Wire"]);
  console.log("PASS: picking Electrical shows exactly one group with Electrical's own categories");

  // ---- test 3: ticking Wire inside Electrical's group fetches sizes
  // scoped to Electrical+Wire specifically ----
  document.querySelector('#exGroups > div:nth-child(1) [data-chipval="Wire"]').click();
  await flush(10);
  const sizeChips1 = [...document.querySelectorAll("#exGroups > div:nth-child(1) .ex-sizebox [data-chipval]")].map((b) => b.dataset.chipval);
  assert.deepStrictEqual(sizeChips1, ["1.5 SQMM"]);
  console.log("PASS: Electrical's own Wire selection shows Electrical's own real sizes");

  // ---- test 4: THE KEY BEHAVIOUR -- adding Plumbing as a second service
  // must NOT lose Electrical's Wire pick, and must add Plumbing as its
  // OWN separate group ----
  document.querySelector('#exSvc [data-chipval="Plumbing"]').click();
  await flush(10);
  assert.strictEqual(document.getElementById("exGroups").children.length, 2,
    "FAIL: expected 2 groups (Electrical AND Plumbing) after adding Plumbing");
  const elecWireStillTicked = document.querySelector('#exGroups > div:nth-child(1) [data-chipval="Wire"]').style.background;
  assert.ok(elecWireStillTicked.includes("234, 96") || elecWireStillTicked === "rgb(234, 243, 222)",
    "FAIL: Electrical's Wire pick must still show as selected after adding Plumbing");
  const plumbCats = [...document.querySelectorAll("#exGroups > div:nth-child(2) [data-chipval]")].map((b) => b.dataset.chipval);
  assert.deepStrictEqual(plumbCats.sort(), ["Pipe", "Valve"]);
  console.log("PASS: adding Plumbing keeps Electrical's group + selection intact, and adds Plumbing as its own group");

  // ---- test 5: exportModalParams() sends a per-service GROUPS structure,
  // not a flat cross-producing list -- this is the actual correctness fix ----
  document.querySelector('#exGroups > div:nth-child(2) [data-chipval="Pipe"]').click();
  await flush(10);
  // also actually tick a size, not just confirm it's offered
  document.querySelector('#exGroups > div:nth-child(1) .ex-sizebox [data-chipval="1.5 SQMM"]').click();
  await flush(10);
  const params = window.eval("exportModalParams()");
  const groups = JSON.parse(params.groups);
  assert.strictEqual(groups.length, 2);
  const elecGroup = groups.find((g) => g.service === "Electrical");
  const plumbGroup = groups.find((g) => g.service === "Plumbing");
  assert.deepStrictEqual(elecGroup.subcategory, ["Wire"]);
  assert.deepStrictEqual(elecGroup.size, ["1.5 SQMM"]);
  assert.deepStrictEqual(plumbGroup.subcategory, ["Pipe"]);
  console.log("PASS: exportModalParams() sends the correct per-service groups structure (Electrical->Wire/1.5 SQMM, Plumbing->Pipe, never cross-mixed)");

  // ---- test 6: filename preview aggregates across both groups with the
  // same capping convention, matching api.py's own naming exactly (checked
  // now, before the reselect-reordering test below changes Set order) ----
  const fname = document.getElementById("exFname").textContent;
  const today = new Date().toISOString().slice(0, 10);
  assert.strictEqual(fname, `Hyatt-Hotel_${today}_Electrical+Plumbing_Wire+Pipe_1-5-SQMM.xlsx`,
    `FAIL: got "${fname}"`);
  console.log("PASS: filename preview matches the real backend naming exactly");

  // ---- test 7: deselecting Electrical removes its group from view, but
  // its picks are NOT thrown away -- re-selecting it restores them ----
  document.querySelector('#exSvc [data-chipval="Electrical"]').click();
  await flush(10);
  assert.strictEqual(document.getElementById("exGroups").children.length, 1,
    "FAIL: only Plumbing's group should remain visible");
  document.querySelector('#exSvc [data-chipval="Electrical"]').click();   // re-tick
  await flush(10);
  const wireBoxAfterRestore = [...document.querySelectorAll("#exGroups [data-chipval]")]
    .find((b) => b.dataset.chipval === "Wire");
  assert.ok(wireBoxAfterRestore.style.background.includes("234, 243, 222") || wireBoxAfterRestore.style.background.includes("eaf3de"),
    "FAIL: Electrical's Wire pick must be restored after re-selecting Electrical, not reset to empty");
  const sizeAfterRestore = [...document.querySelectorAll("#exGroups .ex-sizebox [data-chipval]")]
    .find((b) => b.dataset.chipval === "1.5 SQMM");
  assert.ok(sizeAfterRestore && (sizeAfterRestore.style.background.includes("234, 243, 222") || sizeAfterRestore.style.background.includes("eaf3de")),
    "FAIL: Electrical's 1.5 SQMM size pick must also be restored, not just the category");
  console.log("PASS: deselecting then re-selecting a service restores its exact prior picks (category AND size), nothing lost");

  // ---- test 8: "Select everything" clears every service and every group ----
  document.getElementById("exSelectAll").click();
  await flush(10);
  assert.strictEqual(document.getElementById("exGroups").children.length, 0);
  const paramsAfter = window.eval("exportModalParams()");
  assert.ok(!paramsAfter.groups, "FAIL: 'Select everything' must send no groups param at all");
  console.log("PASS: 'Select everything' clears every service and every group");
}

main().then(() => console.log("\nALL PER-SERVICE EXPORT GROUP TESTS PASSED"))
  .catch((e) => { console.error("TEST FAILURE:", e.message, e.stack); process.exit(1); });
