// Issue 4: labour-only activities are room-tickable now, no overall slider.
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

const R1 = "r1", R2 = "r2";
const STRUCTURE = {
  id: "p0", type: "project", name: "Fixture Hotel", kind: "hotel",
  children: [{ id: "f1", type: "floor", name: "Floor 1", children: [
    { id: R1, type: "room", name: "Room 1", children: [] },
    { id: R2, type: "room", name: "Room 2", children: [] },
  ] }],
};

let zariNode = {};   // activity_progress["Zari Work"], mutated by mark-done calls
const markCalls = [];

function svcState() {
  const total = 2;
  const done = [R1, R2].filter((r) => (zariNode[r] || zariNode["*"] || 0) >= 1).length;
  return {
    service: "Electrical", room: null,
    activities: ["Zari Work"],
    mapping: { "Zari Work": [] },
    act_pct: { "Zari Work": Math.round(100 * done / total * 10) / 10 },
    overall_pct: 0,
    items: [],
    pnl_by_activity: {}, pnl_totals: {}, pnl_unmapped_value: { items: 0 },
    item_rooms: {}, item_progress: {}, item_room_qty: {},
    activity_progress: { "Zari Work": zariNode },
    labour_buckets: { "Zari Work": { done, in_progress: 0, not_started: total - done, total } },
    unmapped: [],
    labour_only: { "Zari Work": true },
    labour_pct: { "Zari Work": Math.round(100 * done / total * 10) / 10 },
    labour_suggested: { "Zari Work": true },
  };
}

function route(url, opts) {
  const u = new URL(url, "http://localhost");
  const method = (opts && opts.method) || "GET";
  if (u.pathname === "/api/projects") return [{ slug: "fixture-hotel", project: "Fixture Hotel" }];
  if (u.pathname === "/api/siteprogress/fixture-hotel")
    return { slug: "fixture-hotel", structure: STRUCTURE, rooms: 2,
             services: ["Electrical"], activities: { Electrical: ["Zari Work"] },
             progress_summary: {}, has_boq: true };
  if (u.pathname === "/api/siteprogress/fixture-hotel/service/Electrical") return svcState();
  if (method === "POST" && u.pathname === "/api/siteprogress/fixture-hotel/mark-activity-rooms-done") {
    const body = JSON.parse(opts.body);
    markCalls.push(body);
    for (const r of body.rooms) zariNode[r] = body.done ? 1.0 : 0.0;
    return svcState();
  }
  throw new Error("unmocked route: " + method + " " + u.pathname);
}
global.fetch = window.fetch = async (url, opts) => ({ ok: true, json: async () => route(url, opts), text: async () => JSON.stringify(route(url, opts)) });
window.confirm = () => true; window.alert = () => {};

const src = fs.readFileSync(new URL("../frontend/siteprogress.js", import.meta.url), "utf8");
window.eval(src);
async function flush(n = 15) { for (let i = 0; i < n; i++) await new Promise((r) => setTimeout(r, 0)); }

async function main() {
  Object.defineProperty(document, "readyState", { value: "complete", configurable: true });
  document.dispatchEvent(new window.Event("DOMContentLoaded"));
  await flush();
  document.querySelector('.spnav button[data-v="siteprogress"]').click();
  await flush(20);

  // ---- test 1: no slider anywhere -- the whole point of this fix ----
  assert.ok(!document.querySelector(".sp-labourrange"),
    "FAIL: the old overall %-slider must be gone entirely for labour-only activities");
  const chip = document.querySelector('[data-actroomsedit="Zari Work"]');
  assert.ok(chip, "FAIL: expected a Rooms chip button for the labour-only activity");
  assert.strictEqual(chip.textContent.trim(), "🏠 0 of 2 rooms done");
  console.log("PASS: no slider rendered; a '0 of 2 rooms done' Rooms chip shows instead");

  // ---- test 2: opening the modal and ticking Room 1 + Mark done ----
  chip.click();
  await flush(10);
  const cb1 = document.querySelector('input[data-actroom="r1"]');
  assert.ok(cb1, "FAIL: Room 1 checkbox missing in the modal");
  cb1.checked = true;
  document.getElementById("sp-act-mark-done").click();
  await flush(15);
  assert.strictEqual(markCalls.length, 1);
  assert.deepStrictEqual(markCalls[0], { service: "Electrical", activity: "Zari Work", rooms: ["r1"], done: true });
  console.log("PASS: ticking Room 1 and 'Mark ticked as done' posts exactly {rooms:['r1'], done:true}");

  // ---- test 3: the chip now reflects 1 of 2 done ----
  const chip2 = document.querySelector('[data-actroomsedit="Zari Work"]');
  assert.strictEqual(chip2.textContent.trim(), "🏠 1 of 2 rooms done");
  console.log("PASS: Rooms chip updates to '1 of 2 rooms done' after marking Room 1");

  // ---- test 4: marking Room 2 done never wipes Room 1's own tick ----
  chip2.click();
  await flush(10);
  const cb2 = document.querySelector('input[data-actroom="r2"]');
  cb2.checked = true;
  document.getElementById("sp-act-mark-done").click();
  await flush(15);
  assert.strictEqual(zariNode["r1"], 1.0, "FAIL: Room 1's done-mark must survive marking Room 2 done too");
  assert.strictEqual(zariNode["r2"], 1.0);
  console.log("PASS: marking a second room done never wipes the first room's own tick");

  // ---- test 5: undo reverts just the ticked room ----
  const chip3 = document.querySelector('[data-actroomsedit="Zari Work"]');
  chip3.click();
  await flush(10);
  const cb1b = document.querySelector('input[data-actroom="r1"]');
  cb1b.checked = true;
  document.getElementById("sp-act-unmark-done").click();
  await flush(15);
  const last = markCalls[markCalls.length - 1];
  assert.strictEqual(last.done, false);
  assert.strictEqual(zariNode["r1"], 0.0);
  assert.strictEqual(zariNode["r2"], 1.0, "FAIL: undoing Room 1 must not touch Room 2");
  console.log("PASS: 'Undo done' reverts only the ticked room, leaves others untouched");
}

main().then(() => console.log("\nALL ISSUE 4 LABOUR ROOM-TICK TESTS PASSED"))
  .catch((e) => { console.error("TEST FAILURE:", e.message, e.stack); process.exit(1); });
