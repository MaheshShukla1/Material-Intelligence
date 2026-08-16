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

const STRUCTURE = { id: "p0", type: "project", name: "Fixture Hotel",
  children: [{ id: "flo1", type: "floor", name: "Floor 1", children: [
    { id: "roo1", type: "room", name: "Room 101", children: [] }] }] };

// item QI1: 12 done, 3 in progress, 93 not started (108 total) -- matches the
// confirmed screenshot's own numbers, so a mismatch here is easy to eyeball.
// item 2.1: fully done (0 outstanding) -- exercises the "all rooms done" path.
const SVC_STATE = {
  service: "Electrical", room: null,
  activities: ["Wall Piping"],
  mapping: { "Wall Piping": ["QI1", "2.1"] },
  act_pct: { "Wall Piping": 60.0 },
  overall_pct: 60.0,
  items: [
    { code: "QI1", desc: "25MM heavy C class pipe", unit: "MTR", sub: "Pipe",
      qty: 0, planned: 6.6, used: 0.5, remaining: 6.1, pct: 7.6, mapped: true,
      rooms: 108, in_room: true, rate: 120, quick: true,
      done_val: 60, rem_val: 732,
      room_done: 12, room_progress: 3, room_pending: 93, room_total: 108 },
    { code: "2.1", desc: "25mm PVC conduit", unit: "MTR", sub: "Pipe",
      qty: 10, planned: 100, used: 100, remaining: 0, pct: 100, mapped: true,
      rooms: 10, in_room: true, rate: 50, quick: false,
      done_val: 5000, rem_val: 0,
      room_done: 10, room_progress: 0, room_pending: 0, room_total: 10 },
  ],
  pnl_by_activity: {}, pnl_totals: {}, pnl_unmapped_value: { items: 0 },
  item_rooms: {}, unmapped: [],
};

const REAL_STATE = {
  service: "Electrical", has_run: true, linked_items: 1, shortages: 0,
  items: [{
    item_code: "QI1", unit: "MTR", planned_total: 6.6, used: 0.5, remaining: 6.1,
    progress_pct: 7.6, order_qty: 0, verdict: "ENOUGH",
    rooms: { done: 12, in_progress: 3, not_started: 93, total: 108 },
    links: [{ material: "25MM HEAVY C CLASS PIPE", unit: "MTR", on_hand: 1190,
              rate_per_day: 45, engine_days_left: 26, status: "GREEN",
              order_by: null, total_consumed: 1308, received: 2498, verdict: "ENOUGH",
              factor: null, units_match: true }],
    desc: "25MM heavy C class pipe",
    message: "6 MTR needed for the remaining 96 rooms — stock on hand is enough at the current rate.",
  }],
};

function route(url) {
  const u = new URL(url, "http://localhost");
  const p = u.pathname;
  if (p === "/api/projects") return [{ slug: "fixture-hotel", project: "Fixture Hotel" }];
  if (p === "/api/siteprogress/fixture-hotel")
    return { slug: "fixture-hotel", structure: STRUCTURE, rooms: 108,
             services: ["Electrical"], activities: { Electrical: ["Wall Piping"] },
             progress_summary: {}, has_boq: true };
  if (p === "/api/siteprogress/fixture-hotel/service/Electrical") return JSON.parse(JSON.stringify(SVC_STATE));
  if (p === "/api/siteprogress/fixture-hotel/pnl/Electrical")
    return { service: "Electrical", room: null,
             project: { done_value: 5060, remaining_value: 732, pct_value_done: 87.4 },
             by_activity: {}, waste: { available: false }, rated_items: 2, total_items: 2,
             unmapped_value: { items: 0 } };
  if (p === "/api/siteprogress/fixture-hotel/realistic/Electrical") return JSON.parse(JSON.stringify(REAL_STATE));
  throw new Error("unmocked route: " + p);
}
global.fetch = window.fetch = async (url) => ({ ok: true, json: async () => route(url), text: async () => JSON.stringify(route(url)) });
window.confirm = () => true; window.alert = () => {};

const src = fs.readFileSync(new URL("../frontend/siteprogress.js", import.meta.url), "utf8");
window.eval(src);
async function flush(n = 8) { for (let i = 0; i < n; i++) await new Promise((r) => setTimeout(r, 0)); }

async function main() {
  Object.defineProperty(document, "readyState", { value: "complete", configurable: true });
  document.dispatchEvent(new window.Event("DOMContentLoaded"));
  await flush();
  document.querySelector('.spnav button[data-v="siteprogress"]').click();
  await flush(20);

  // ---- test 1: drawer for an item with an outstanding-room mix ----
  const fx = document.querySelector('[data-fx="QI1"]');
  assert.ok(fx, "FAIL: drawer-open button missing for QI1");
  fx.click();
  await flush(5);
  const drawer = document.getElementById("sp-draw");
  assert.ok(drawer, "FAIL: drawer did not open");
  const roomsBlk = drawer.querySelector(".sp-roomsblk");
  assert.ok(roomsBlk, "FAIL: .sp-roomsblk panel missing from drawer");
  console.log("PASS: Rooms — this item panel renders in the drawer");

  const label = roomsBlk.querySelector(".lbl");
  assert.strictEqual(label.textContent, "Rooms — this item");
  console.log("PASS: panel label text matches");

  const countText = roomsBlk.querySelector(".sp-roomscount").textContent.replace(/\s+/g, " ").trim();
  assert.strictEqual(countText, "12 done · 3 in progress · 93 not started",
    "FAIL: count line mismatch, got: " + countText);
  console.log("PASS: room counts render as '12 done · 3 in progress · 93 not started'");

  // ---- test 2: counts are colour-coded per segment, not flattened to grey ----
  const doneB = roomsBlk.querySelector("b.done"), progB = roomsBlk.querySelector("b.prog"), pendB = roomsBlk.querySelector("b.pend");
  assert.ok(doneB && progB && pendB, "FAIL: expected b.done/b.prog/b.pend spans");
  assert.strictEqual(doneB.textContent, "12 done");
  assert.strictEqual(progB.textContent, "3 in progress");
  assert.strictEqual(pendB.textContent, "93 not started");
  // colours come from CSS classes (.sp-roomscount b.done{color:var(--green)} etc),
  // not inline grey styling -- confirm no inline color was hand-rolled onto them
  assert.ok(!doneB.getAttribute("style"), "FAIL: done count should not carry inline grey styling");
  console.log("PASS: done/in-progress/not-started counts keep their own CSS classes (not flattened to one grey span)");

  // ---- test 3: segmented bar widths match the bucket proportions ----
  const segs = roomsBlk.querySelectorAll(".sp-roomsbar span");
  assert.strictEqual(segs.length, 3);
  assert.ok(segs[0].style.width.startsWith("11.1") || segs[0].style.width.startsWith("11.11"),
    "FAIL: done segment width should be 12/108≈11.1%, got " + segs[0].style.width);
  console.log("PASS: segmented bar widths proportional to bucket counts");

  // ---- test 4: "need ~X more" row uses the item's own `remaining`, not a
  // second invented number, and mentions the outstanding room count ----
  const needRow = roomsBlk.querySelector(".sp-roomsneed");
  assert.ok(needRow.textContent.includes("96"), "FAIL: expected 96 outstanding rooms (3+93), got: " + needRow.textContent);
  assert.ok(needRow.textContent.includes("6.1 MTR"), "FAIL: expected the item's own remaining (6.1 MTR), got: " + needRow.textContent);
  console.log("PASS: 'need ~X more' row shows 96 outstanding rooms and reuses remaining=6.1 MTR verbatim");

  // ---- test 4b: the linked-material row shows a per-material issued-vs-
  // expected cross-check -- 1308 MTR issued vs ≈0.5 MTR expected (units
  // match here, so effective factor = 1.0) -- a huge gap, so it fires the
  // "staged or needs an update" note rather than the plain "issued to date" ----
  const drowsA = [...drawer.querySelectorAll(".sp-drow")];
  const issuedRow = drowsA.find((r) => (r.textContent || "").includes("1,308") && (r.textContent || "").includes("issued"));
  assert.ok(issuedRow, "FAIL: expected a per-material row mentioning 1,308 MTR issued");
  assert.ok(issuedRow.textContent.includes("expected"),
    "FAIL: expected wording comparing issued vs expected-for-work-done, got: " + issuedRow.textContent);
  console.log("PASS: linked-material row shows '1,308 MTR issued vs ≈0.5 expected' style cross-check");

  // ---- test 4c: a big gap gets an honest suggestion, and % complete stays
  // driven by used/planned -- never silently changed by the issued figure ----
  assert.ok(issuedRow.textContent.toLowerCase().includes("staged") || issuedRow.textContent.toLowerCase().includes("update"),
    "FAIL: caveat should suggest a reason, not just a bare number: " + issuedRow.textContent);
  const pctLineA = drawer.querySelector('div[style*="text-align:right"]');
  assert.ok(pctLineA && pctLineA.textContent.includes("8%"),
    "FAIL: % complete must stay driven by the real used/planned (0.5/6.6≈8%), never by the issued figure");
  console.log("PASS: a large issued-vs-expected gap shows an honest caveat, and never silently changes % complete");

  // ---- test 5: rooms panel sits between Remaining work and Install rate ----
  const rows = [...drawer.querySelectorAll(".sp-drow, .sp-roomsblk")];
  const labels = rows.map((r) => (r.querySelector("span") || {}).textContent || r.className);
  const remIdx = labels.findIndex((t) => t === "Remaining work");
  const rateIdx = labels.findIndex((t) => t === "Install rate");
  const blkIdx = rows.findIndex((r) => r.classList.contains("sp-roomsblk"));
  assert.ok(remIdx < blkIdx && blkIdx < rateIdx, "FAIL: rooms panel must sit between Remaining work and Install rate");
  console.log("PASS: rooms panel is positioned between Remaining work and Install rate");

  // ---- test 6: verdict banner reused unchanged, still shows the room-aware message ----
  const verdict = drawer.querySelector(".sp-dlink");
  assert.ok(verdict.textContent.includes("96 rooms"), "FAIL: verdict banner should carry the room-aware message from /realistic");
  console.log("PASS: verdict banner shows the room-aware sentence from realtime.sentence()");

  document.getElementById("sp-draw-x").click();
  await flush(5);

  // ---- test 7: fully-done item shows the "all rooms done" state, no "need ~0 more" ----
  const fx2 = document.querySelector('[data-fx="2.1"]');
  assert.ok(fx2, "FAIL: drawer-open button missing for 2.1");
  fx2.click();
  await flush(5);
  const drawer2 = document.getElementById("sp-draw");
  const blk2 = drawer2.querySelector(".sp-roomsblk");
  assert.ok(blk2, "FAIL: rooms panel missing for a fully-done item");
  const need2 = blk2.querySelector(".sp-roomsneed").textContent;
  assert.ok(need2.includes("All 10 rooms done"), "FAIL: expected 'All 10 rooms done', got: " + need2);
  assert.ok(!need2.includes("more"), "FAIL: a fully-done item must not show a 'need ~0 more' line");
  console.log("PASS: a fully-done item shows 'All 10 rooms done' instead of a nonsensical 'need ~0 more'");
}

main().then(() => console.log("\nALL DRAWER ROOMS-PANEL TESTS PASSED")).catch((e) => { console.error("TEST FAILURE:", e.message); process.exit(1); });
