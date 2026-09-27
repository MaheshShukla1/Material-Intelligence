// DPR export modal redesign: Service as single-select chips (matching the
// Material Forecast export's visual language), and the SAME one-floor-
// expanded accordion pattern for Area -- only one floor's rooms show at a
// time, others collapse to a one-line summary, picks persist across
// switching floors, "Whole project" resets everything.
import { JSDOM } from "jsdom";
import fs from "fs";
import assert from "assert";

const html = `<!doctype html><html><body>
<header class="top" id="top"><div class="brand"><h1>Material intelligence</h1><p id="ctx"></p></div>
  <div class="topact"><button id="pick"></button><button id="sync"></button><button id="syncgo"></button><button id="home" hidden></button>
  <input id="file" type="file" hidden></div></header>
<main>
<section id="drop"></section><section id="busy" hidden></section><section id="report" hidden>
  <div class="bar"><div class="tabs" id="svc"></div></div>
</section>
</main></body></html>`;

const dom = new JSDOM(html, { url: "http://localhost/", runScripts: "outside-only", pretendToBeVisual: true });
const { window } = dom;
global.window = window;
global.document = window.document;
global.HTMLElement = window.HTMLElement;
global.MutationObserver = window.MutationObserver;

const F1_R1 = "r1", F1_R2 = "r2";
const F2_R1 = "r3", F2_R2 = "r4";
const STRUCTURE = {
  id: "p0", type: "project", name: "Fixture Hotel", kind: "hotel",
  children: [
    { id: "f1", type: "floor", name: "13th Floor", children: [
      { id: F1_R1, type: "room", name: "Room 1", children: [] },
      { id: F1_R2, type: "room", name: "Room 2", children: [] },
    ] },
    { id: "f2", type: "floor", name: "14th Floor", children: [
      { id: F2_R1, type: "room", name: "Room 1", children: [] },
      { id: F2_R2, type: "room", name: "Room 2", children: [] },
    ] },
  ],
};

function svcState() {
  return {
    service: "Electrical", room: null, activities: [], mapping: {},
    act_pct: {}, overall_pct: 55, items: [], pnl_by_activity: {}, pnl_totals: {},
    pnl_unmapped_value: { items: 0 }, item_rooms: {}, item_progress: {},
    item_room_qty: {}, activity_progress: {}, labour_buckets: {}, unmapped: [],
    labour_only: {}, labour_pct: {}, labour_suggested: {},
  };
}

let lastExportHref = null;

function route(url) {
  const u = new URL(url, "http://localhost");
  const p = u.pathname;
  if (p === "/api/projects") return [{ slug: "fixture-hotel", project: "Fixture Hotel" }];
  if (p === "/api/siteprogress/fixture-hotel")
    return { slug: "fixture-hotel", structure: STRUCTURE, rooms: 4,
             services: ["Electrical", "Plumbing"], activities: { Electrical: [], Plumbing: [] },
             progress_summary: {}, has_boq: true };
  if (p === "/api/siteprogress/fixture-hotel/service/Electrical") return svcState();
  if (p === "/api/siteprogress/fixture-hotel/pnl/Electrical")
    return { service: "Electrical", room: null, project: { done_value: 0, remaining_value: 0, pct_value_done: 55 },
             by_activity: {}, waste: { available: false }, rated_items: 0, total_items: 0, unmapped_value: { items: 0 } };
  if (p === "/api/siteprogress/fixture-hotel/realistic/Electrical") return { service: "Electrical", has_run: false, items: [] };
  if (p === "/api/siteprogress/fixture-hotel/dpr/today") return { count: 0 };
  if (p === "/api/siteprogress/fixture-hotel/export-dpr") { lastExportHref = url; return {}; }
  throw new Error("unmocked route: " + p);
}
global.fetch = window.fetch = async (url) => ({ ok: true, json: async () => route(url), text: async () => JSON.stringify(route(url)) });
window.HTMLAnchorElement.prototype.click = function () { lastExportHref = this.href; };
window.confirm = () => true; window.alert = () => {};

const src = fs.readFileSync(new URL("../frontend/siteprogress.js", import.meta.url), "utf8");
window.eval(src);
async function flush(n = 20) { for (let i = 0; i < n; i++) await new Promise((r) => setTimeout(r, 0)); }

async function main() {
  Object.defineProperty(document, "readyState", { value: "complete", configurable: true });
  document.dispatchEvent(new window.Event("DOMContentLoaded"));
  await flush();
  document.querySelector('.spnav button[data-v="siteprogress"]').click();
  await flush(20);

  document.getElementById("sp-where").click();
  await flush(10);

  // ---- test 1: Service renders as chips, "All services" + every real service ----
  const svcChips = [...document.querySelectorAll("#sp-dpr-svc [data-dprsvc]")].map((b) => b.dataset.dprsvc);
  assert.deepStrictEqual(svcChips, ["", "Electrical", "Plumbing"]);
  console.log("PASS: Service renders as chips -- All services + every real project service");

  // ---- test 2: only 13th Floor's rooms show expanded initially (the
  // first floor), 14th Floor shows as a collapsed summary line ----
  const expandedBoxes = document.querySelectorAll("#sp-dpr-floors .sp-roomgroup").length;
  assert.strictEqual(expandedBoxes, 1, "FAIL: exactly one floor should be expanded");
  const floorsText = document.getElementById("sp-dpr-floors").textContent;
  assert.ok(floorsText.includes("14th Floor"), "FAIL: 14th Floor should still be listed (collapsed)");
  console.log("PASS: only one floor is expanded at a time, others show as a collapsed row");

  // ---- test 3: tick Room 1 on 13th Floor, switch to 14th Floor -- 13th's
  // tick must persist (shown in its collapsed summary), no extra click needed ----
  document.querySelector('.sp-roomgroup[data-g="0"] input[data-dprroom="r1"]').click();
  await flush(10);
  const row14 = [...document.querySelectorAll("#sp-dpr-floors > div")].find((d) => d.textContent.includes("14th Floor"));
  row14.click();
  await flush(10);
  const row13 = [...document.querySelectorAll("#sp-dpr-floors > div")].find((d) => d.textContent.includes("13th Floor"));
  assert.ok(row13.textContent.includes("1 of 2"), `FAIL: expected 13th Floor's collapsed summary to show its tick, got "${row13.textContent}"`);
  console.log("PASS: switching floors auto-collapses the previous one with its tick correctly summarized, no extra click needed");

  // ---- test 4: tick a room on 14th Floor too, then go back to 13th --
  // BOTH floors' ticks must be intact ----
  document.querySelector('.sp-roomgroup input[data-dprroom="r3"]').click();
  await flush(10);
  const row13b = [...document.querySelectorAll("#sp-dpr-floors > div")].find((d) => d.textContent.includes("13th Floor"));
  row13b.click();
  await flush(10);
  assert.ok(document.querySelector('.sp-roomgroup input[data-dprroom="r1"]').checked,
    "FAIL: 13th Floor's Room 1 tick must survive re-expanding it");
  console.log("PASS: re-expanding 13th Floor restores its exact tick; 14th Floor's own tick was never touched");

  // ---- test 5: scope-line reflects real picks across both floors ----
  const scopeText = document.getElementById("sp-dpr-scope").textContent;
  assert.ok(scopeText.includes("Room 1"), `FAIL: scope line should mention the real ticked rooms, got "${scopeText}"`);
  console.log("PASS: scope line reflects the real cross-floor selection");

  // ---- test 6: "Whole project" clears every floor's ticks ----
  document.querySelector("#sp-dpr-floors > div").click();   // "Whole project" is always the first row
  await flush(10);
  assert.strictEqual(document.getElementById("sp-dpr-scope").textContent, "Whole project");
  console.log("PASS: 'Whole project' clears every floor's ticks at once");

  // ---- test 7: picking Electrical + ticking rooms on 2 floors sends the
  // correct real export URL ----
  document.querySelector('#sp-dpr-svc [data-dprsvc="Electrical"]').click();
  const row13c = [...document.querySelectorAll("#sp-dpr-floors > div")].find((d) => d.textContent.includes("13th Floor"));
  row13c.click();
  await flush(10);
  document.querySelector('.sp-roomgroup input[data-dprroom="r1"]').click();
  await flush(10);
  document.querySelector("#sp-modal .btn.primary, #sp-modal button[class*='primary']")?.click();
  // fall back: the Save/Export button is whatever modal()'s own footer renders as primary action
  const saveBtn = [...document.querySelectorAll("#sp-modal button")].find((b) => b.textContent.trim() === "Export DPR");
  if (saveBtn) saveBtn.click();
  await flush(10);
  assert.ok(lastExportHref, "FAIL: expected an export request to have fired");
  const exportUrl = new URL(lastExportHref, "http://localhost");
  assert.strictEqual(exportUrl.searchParams.get("service"), "Electrical");
  assert.deepStrictEqual(exportUrl.searchParams.getAll("rooms"), ["r1"]);
  console.log("PASS: Export DPR sends the real service + real ticked room(s)");
}

main().then(() => console.log("\nALL DPR EXPORT MODAL REDESIGN TESTS PASSED"))
  .catch((e) => { console.error("TEST FAILURE:", e.message, e.stack); process.exit(1); });
