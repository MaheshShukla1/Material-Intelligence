// Rooms modal (Issue 1): when an item already has a room_qty_group (or a
// plain item_rooms restriction), the tick-list must default to ONLY those
// configured areas -- not the whole building -- and show their REAL NAMES
// in "Current quantity groups", not just a count. An explicit "+ Add other
// rooms" link reveals everything, for the genuine expand-scope case.
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

// A small building: 2 floors, each with Room 1, Room 2, and a Corridor --
// enough leaf variety (numbered rooms + a differently-named area) to prove
// the modal genuinely restricts to configured areas, not the whole tree.
const F1_R1 = "r1", F1_R2 = "r2", F1_COR = "r3";
const F2_R1 = "r4", F2_R2 = "r5", F2_COR = "r6";
const STRUCTURE = {
  id: "p0", type: "project", name: "Fixture Hotel", kind: "hotel",
  children: [
    { id: "f1", type: "floor", name: "13th Floor", children: [
      { id: F1_R1, type: "room", name: "Room 1", children: [] },
      { id: F1_R2, type: "room", name: "Room 2", children: [] },
      { id: F1_COR, type: "room", name: "Corridor", children: [] },
    ] },
    { id: "f2", type: "floor", name: "14th Floor", children: [
      { id: F2_R1, type: "room", name: "Room 1", children: [] },
      { id: F2_R2, type: "room", name: "Room 2", children: [] },
      { id: F2_COR, type: "room", name: "Corridor", children: [] },
    ] },
  ],
};

// "Cable Tray" (QI1) is configured for just the two Corridors (a real
// quantity group, 143.3 MTR) -- exactly the reported scenario. "New Item"
// (QI2) has never been configured at all.
function svcState() {
  return {
    service: "Electrical", room: null,
    activities: ["Cable Tray Installation"],
    mapping: { "Cable Tray Installation": ["QI1", "QI2"] },
    act_pct: { "Cable Tray Installation": 20 }, overall_pct: 20,
    items: [
      { code: "QI1", desc: "450X50MM HDGI LADDER TRAY", unit: "MTR", sub: "Cable tray",
        qty: 1000, planned: 143.3, used: 0, remaining: 143.3, pct: 0,
        mapped: true, rooms: 2, in_room: true, rate: 378, quick: false,
        done_val: 0, rem_val: 54187 },
      { code: "QI2", desc: "Brand New Item", unit: "NOS", sub: "Other",
        qty: 5, planned: 30, used: 0, remaining: 30, pct: 0,
        mapped: true, rooms: 6, in_room: true, rate: null, quick: false,
        done_val: 0, rem_val: null },
    ],
    pnl_by_activity: {}, pnl_totals: {}, pnl_unmapped_value: { items: 0 },
    item_rooms: {}, item_progress: {},
    item_room_qty: { "QI1": [{ rooms: [F1_COR, F2_COR], qty: 143.3 }] },
    activity_progress: {}, labour_buckets: {}, unmapped: [],
    labour_only: {}, labour_pct: {}, labour_suggested: {},
  };
}

function route(url) {
  const u = new URL(url, "http://localhost");
  const p = u.pathname;
  if (p === "/api/projects") return [{ slug: "fixture-hotel", project: "Fixture Hotel" }];
  if (p === "/api/siteprogress/fixture-hotel")
    return { slug: "fixture-hotel", structure: STRUCTURE, rooms: 6,
             services: ["Electrical"], activities: { Electrical: ["Cable Tray Installation"] },
             progress_summary: {}, has_boq: true };
  if (p === "/api/siteprogress/fixture-hotel/service/Electrical") return svcState();
  if (p === "/api/siteprogress/fixture-hotel/pnl/Electrical")
    return { service: "Electrical", room: null, project: { done_value: 0, remaining_value: 54187, pct_value_done: 0 },
             by_activity: {}, waste: { available: false }, rated_items: 1, total_items: 2, unmapped_value: { items: 0 } };
  if (p === "/api/siteprogress/fixture-hotel/realistic/Electrical") return { service: "Electrical", has_run: false, items: [] };
  throw new Error("unmocked route: " + p);
}
global.fetch = window.fetch = async (url) => ({ ok: true, json: async () => route(url), text: async () => JSON.stringify(route(url)) });
window.confirm = () => true; window.alert = () => {};

const src = fs.readFileSync(new URL("../siteprogress.js", import.meta.url), "utf8");
window.eval(src);
async function flush(n = 15) { for (let i = 0; i < n; i++) await new Promise((r) => setTimeout(r, 0)); }

async function main() {
  Object.defineProperty(document, "readyState", { value: "complete", configurable: true });
  document.dispatchEvent(new window.Event("DOMContentLoaded"));
  await flush();
  document.querySelector('.spnav button[data-v="siteprogress"]').click();
  await flush(20);

  // ---- test 1: "Current quantity groups" shows REAL NAMES, not "2 rooms" ----
  document.querySelector('[data-roomsedit="QI1"]').click();
  await flush(10);
  const summaryText = document.querySelector(".sp-qtygroups .row span").textContent;
  assert.ok(summaryText.includes("Corridor"), `FAIL: expected real room names in the summary, got "${summaryText}"`);
  assert.ok(!/^\d+\s/.test(summaryText.trim()), `FAIL: summary still looks like a bare count ("${summaryText}"), not real names`);
  console.log("PASS: quantity-group summary shows real area names, not just a count");

  // ---- test 2: default tick-list shows ONLY the 2 configured Corridors,
  // not all 6 rooms in the building ----
  const boxes = [...document.querySelectorAll('#sp-modal input[data-room]')];
  const shownIds = boxes.map((b) => b.dataset.room).sort();
  assert.deepStrictEqual(shownIds, [F1_COR, F2_COR].sort(),
    `FAIL: expected only the 2 configured corridors, got ${JSON.stringify(shownIds)}`);
  console.log("PASS: tick-list defaults to only the 2 already-configured corridors, not all 6 rooms");

  // ---- test 3: an "+ Add other rooms" link is offered, and expands to
  // the full building when clicked ----
  const expandLink = document.getElementById("sp-rooms-expand");
  assert.ok(expandLink, "FAIL: expected a way to expand to the rest of the building");
  expandLink.click();
  await flush(15);
  const boxes2 = [...document.querySelectorAll('#sp-modal input[data-room]')];
  assert.strictEqual(boxes2.length, 6, "FAIL: expanding must reveal all 6 real rooms in the building");
  console.log("PASS: '+ Add other rooms' expands to the full real building (all 6 rooms)");

  // ---- test 4: a brand-new item with NO configuration yet shows the
  // whole tree by default (nothing to restrict to) and offers no expand
  // link, since everything is already shown ----
  document.querySelector('button[onclick="closeModal()"]')?.click();
  await flush(5);
  document.querySelector('[data-roomsedit="QI2"]').click();
  await flush(10);
  const boxes3 = [...document.querySelectorAll('#sp-modal input[data-room]')];
  assert.strictEqual(boxes3.length, 6, "FAIL: a never-configured item must show the whole building (nothing to restrict to yet)");
  assert.ok(!document.getElementById("sp-rooms-expand"),
    "FAIL: no expand link needed when everything is already shown");
  console.log("PASS: a brand-new, never-configured item still shows the whole building by default");
}

main().then(() => console.log("\nALL ROOMS-MODAL SCOPED-BY-DEFAULT TESTS PASSED"))
  .catch((e) => { console.error("TEST FAILURE:", e.message, e.stack); process.exit(1); });
