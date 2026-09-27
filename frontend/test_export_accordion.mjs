// Issue 3 (v3, accordion): only ONE service's category/size chips are ever
// expanded at a time. Picking a second service must auto-collapse the
// first to a one-line summary -- NOT require an extra click on it -- while
// its underlying selection (exGroupState) stays fully intact and gets sent
// to the backend regardless of which group is currently visible.
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
    const svc = u.searchParams.get("service"), cat = u.searchParams.get("subcategory");
    if (svc === "Electrical" && cat === "Wire") return [{ name: "1.5 SQMM", count: 10 }];
    return [];
  }
  if (p === "/api/forecast/run1") return [];
  throw new Error("unmocked route: " + p);
}
global.fetch = window.fetch = async (url) => ({ ok: true, json: async () => route(url), text: async () => JSON.stringify(route(url)) });
window.HTMLAnchorElement.prototype.click = function () {};

const src = fs.readFileSync(new URL("../app.js", import.meta.url), "utf8");
window.eval(src);
async function flush(n = 15) { for (let i = 0; i < n; i++) await new Promise((r) => setTimeout(r, 0)); }

function fullGroupsCount() { return document.querySelectorAll("#exGroups .ex-catbox").length; }
function collapsedCount() { return document.getElementById("exGroups").children.length - fullGroupsCount(); }

async function main() {
  await flush();
  await window.eval(`show("run1", { lead_time: 7, issues: [], mapping: {}, leadtime: {},
    project: "Hyatt Hotel", filename: "register.xlsx", source: "register",
    created: new Date().toISOString(), stats: { asof: "2026-09-27", materials: 413 } },
    { services: ["Electrical", "Plumbing"], counts: {}, materials: 413, act_today: 0, idle_lines: 0, overdue_orders: 0 })`);
  await flush();

  document.getElementById("dl").click();
  await flush(10);

  // ---- test 1: picking Electrical shows it FULLY EXPANDED (one group, real categories) ----
  document.querySelector('#exSvc [data-chipval="Electrical"]').click();
  await flush(10);
  assert.strictEqual(fullGroupsCount(), 1, "FAIL: Electrical should be expanded after picking it");
  assert.strictEqual(collapsedCount(), 0);
  console.log("PASS: picking Electrical expands it immediately, no collapsed rows yet");

  // ---- test 2: tick Wire, size shows, then actually tick the size too ----
  document.querySelector('#exGroups .ex-catbox [data-chipval="Wire"]').click();
  await flush(10);
  assert.ok(document.querySelector('#exGroups .ex-sizebox [data-chipval="1.5 SQMM"]'));
  document.querySelector('#exGroups .ex-sizebox [data-chipval="1.5 SQMM"]').click();
  await flush(10);

  // ---- test 3 (THE ACTUAL FIX): picking Plumbing auto-collapses
  // Electrical to a one-line summary -- with NO extra click on Electrical ----
  document.querySelector('#exSvc [data-chipval="Plumbing"]').click();
  await flush(10);
  assert.strictEqual(fullGroupsCount(), 1, "FAIL: exactly one group (Plumbing) should be expanded now");
  assert.strictEqual(collapsedCount(), 1, "FAIL: Electrical should now show as ONE collapsed summary line");
  const collapsedText = document.getElementById("exGroups").textContent;
  assert.ok(collapsedText.includes("Electrical") && collapsedText.includes("Wire"),
    "FAIL: the collapsed line must show Electrical's real picks (Wire), proving nothing was lost");
  const plumbCats = [...document.querySelectorAll("#exGroups .ex-catbox [data-chipval]")].map((b) => b.dataset.chipval);
  assert.deepStrictEqual(plumbCats.sort(), ["Pipe", "Valve"]);
  document.querySelector('#exGroups .ex-catbox [data-chipval="Pipe"]').click();   // actually select it, while Plumbing is still the expanded group
  await flush(10);
  console.log("PASS: picking Plumbing auto-collapses Electrical to a summary line showing its real picks -- no extra click needed");

  // ---- test 4: clicking the collapsed Electrical summary re-expands it,
  // collapsing Plumbing in turn ----
  document.querySelector('#exGroups > div:not(.ex-catbox-parent)').click();   // the collapsed row itself
  // more robust: find the row whose text mentions Electrical and click it
  const rows = [...document.getElementById("exGroups").children];
  const elecRow = rows.find((r) => r.textContent.includes("Electrical") && !r.querySelector(".ex-catbox"));
  if (elecRow) elecRow.click();
  await flush(10);
  assert.strictEqual(fullGroupsCount(), 1);
  const nowExpandedCats = [...document.querySelectorAll("#exGroups .ex-catbox [data-chipval]")].map((b) => b.dataset.chipval);
  assert.deepStrictEqual(nowExpandedCats.sort(), ["Box/JB", "Wire"], "FAIL: Electrical should be expanded again");
  const wireStillTicked = document.querySelector('#exGroups .ex-catbox [data-chipval="Wire"]').style.background;
  assert.ok(wireStillTicked.includes("234, 243, 222") || wireStillTicked.includes("eaf3de"),
    "FAIL: Electrical's Wire pick must still be ticked after re-expanding");
  console.log("PASS: tapping the collapsed summary re-expands that service with its picks intact, collapsing the other one");

  // ---- test 5: exportModalParams() sends BOTH services' real picks
  // regardless of which one is currently visually expanded ----
  const params = window.eval("exportModalParams()");
  const groups = JSON.parse(params.groups);
  const elecGroup = groups.find((g) => g.service === "Electrical");
  const plumbGroup = groups.find((g) => g.service === "Plumbing");
  assert.deepStrictEqual(elecGroup.subcategory, ["Wire"]);
  assert.deepStrictEqual(elecGroup.size, ["1.5 SQMM"]);
  assert.deepStrictEqual(plumbGroup.subcategory, ["Pipe"]);
  console.log("PASS: the real export query includes both services' full picks, independent of which is expanded in the UI");

  // ---- test 6: clicking a service's SERVICE CHIP (top row, not the
  // summary line) removes it entirely ----
  document.querySelector('#exSvc [data-chipval="Plumbing"]').click();
  await flush(10);
  const paramsAfterRemove = window.eval("exportModalParams()");
  const groupsAfterRemove = JSON.parse(paramsAfterRemove.groups);
  assert.strictEqual(groupsAfterRemove.length, 1);
  assert.strictEqual(groupsAfterRemove[0].service, "Electrical");
  console.log("PASS: clicking the Service chip (not the summary line) removes that service entirely");

  // ---- test 7: "Select everything" clears all services and collapses state ----
  document.getElementById("exSelectAll").click();
  await flush(10);
  assert.strictEqual(document.getElementById("exGroups").children.length, 0);
  const finalParams = window.eval("exportModalParams()");
  assert.ok(!finalParams.groups);
  console.log("PASS: 'Select everything' clears every service and every group");
}

main().then(() => console.log("\nALL ACCORDION EXPORT TESTS PASSED"))
  .catch((e) => { console.error("TEST FAILURE:", e.message, e.stack); process.exit(1); });
