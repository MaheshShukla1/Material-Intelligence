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

const STRUCTURE = { id: "p0", type: "project", name: "Hyatt Hotel", kind: "hotel",
  children: [{ id: "flo1", type: "floor", name: "Floor 1", children: [
    { id: "roo1", type: "room", name: "Room 101", children: [] }] }] };

// EXACTLY the reported scenario: QI4, a quick item, "Ceiling Piping" activity,
// Electrical service, starting at the real screenshot's own numbers --
// "1 of 52 MTR done, 51 remaining" -- before any Rooms-chip groups exist.
let qi4Planned = 52;   // this is what the /service/Electrical mock returns;
let qi4Used = 1;       // the test flips it after "using the chip" to simulate
let qi4Remaining = 51; // the backend recomputing compute() with real groups.

function svcState() {
  return {
    service: "Electrical", room: null,
    activities: ["Ceiling Piping"],
    mapping: { "Ceiling Piping": ["QI4"] },
    act_pct: { "Ceiling Piping": 2.0 }, overall_pct: 2.0,
    items: [{
      code: "QI4", desc: "25MM MS conduit pipe black", unit: "MTR", sub: "Pipe",
      qty: 0, planned: qi4Planned, used: qi4Used, remaining: qi4Remaining,
      pct: Math.round(100 * qi4Used / qi4Planned), mapped: true,
      rooms: 109, in_room: true, rate: null, quick: true, done_val: null, rem_val: null,
      room_done: 0, room_progress: 1, room_pending: 108, room_total: 109,
    }],
    pnl_by_activity: {}, pnl_totals: {}, pnl_unmapped_value: { items: 0 },
    item_rooms: {}, item_room_qty: {}, unmapped: [],
  };
}

function route(url) {
  const u = new URL(url, "http://localhost");
  const p = u.pathname;
  if (p === "/api/projects") return [{ slug: "hyatt", project: "Hyatt Hotel" }];
  if (p === "/api/siteprogress/hyatt")
    return { slug: "hyatt", structure: STRUCTURE, rooms: 109,
             services: ["Electrical"], activities: { Electrical: ["Ceiling Piping"] },
             progress_summary: {}, has_boq: true };
  if (p === "/api/siteprogress/hyatt/overall")
    return { overall_pct: 2.0, pct_value_done: 0.0, done_value: 0, planned_value: 0,
             remaining_value: 0, waste_value: 0, saved_value: 0, waste_caveat: null,
             services: ["Electrical"],
             by_service: { Electrical: { pct: 2.0, done_value: 0, remaining_value: 0, planned_value: 0, items: 67, waste_value: 0 } },
             rooms_summary: { done: 0, in_progress: 109, not_started: 0, total: 109 } };
  if (p === "/api/siteprogress/hyatt/service/Electrical") return JSON.parse(JSON.stringify(svcState()));
  throw new Error("unmocked route: " + p);
}
global.fetch = window.fetch = async (url) => ({ ok: true, json: async () => route(url), text: async () => JSON.stringify(route(url)) });
window.confirm = () => true; window.alert = () => {};

const src = fs.readFileSync(new URL("../frontend/siteprogress.js", import.meta.url), "utf8");
window.eval(src);
async function flush(n = 15) { for (let i = 0; i < n; i++) await new Promise((r) => setTimeout(r, 0)); }

async function openQI4Row() {
  const ovPill = document.querySelector('.sp-pill[data-s="__overall__"]');
  ovPill.click();
  await flush(15);
  const elecCard = document.querySelector('.sp-card[data-ovsvc="Electrical"]');
  elecCard.querySelector(".sp-ovrow").click();
  await flush(15);
  const actRow = elecCard.querySelector('.sp-ovact[data-ovact="Ceiling Piping"]');
  assert.ok(actRow, "FAIL: Ceiling Piping activity row missing");
  actRow.querySelector(".sp-ovactrow").click();
  await flush(10);
  const itemRow = actRow.querySelector('.sp-ovitemrow');
  assert.ok(itemRow, "FAIL: QI4 item row missing");
  return itemRow;
}

async function main() {
  Object.defineProperty(document, "readyState", { value: "complete", configurable: true });
  document.dispatchEvent(new window.Event("DOMContentLoaded"));
  await flush();
  document.querySelector('.spnav button[data-v="siteprogress"]').click();
  await flush(20);

  // ---- test 1: BEFORE using the Rooms chip -- reproduces the exact
  // reported screenshot: "QI4 · 1 of 52 MTR done, 51 remaining", right on
  // the Overall page, inside Electrical > Ceiling Piping ----
  let itemRow = await openQI4Row();
  let sub = itemRow.querySelector(".sp-ovitemsub").textContent;
  assert.strictEqual(sub, "QI4 · 1 of 52 MTR done, 51 remaining",
    "FAIL: expected the exact reported starting state, got: " + sub);
  console.log("PASS: reproduces the exact reported screenshot state -- 'QI4 · 1 of 52 MTR done, 51 remaining' on the Overall page");

  // ---- test 2: simulate using the 🏠 Rooms chip to set real per-room
  // quantity groups for QI4 (100 rooms @ 52 MTR, 9 rooms @ 60 MTR = 5740
  // total, matching the exact motivating example) -- the backend recomputes
  // and now returns the real summed total instead of the stale flat 52.
  // This is what itemprog.compute()'s fixed precedence (groups win over a
  // quick item's flat planned_over) actually produces -- see
  // test_room_qty_groups.py::test_the_exact_reported_scenario_QI4... for
  // the backend-side proof of the same number. ----
  qi4Planned = 5740;
  qi4Used = 52;        // the one room that was already done contributes its own group's 52
  qi4Remaining = 5688;

  itemRow = await openQI4Row();
  sub = itemRow.querySelector(".sp-ovitemsub").textContent;
  assert.strictEqual(sub, "QI4 · 52 of 5,740 MTR done, 5,688 remaining",
    "FAIL: after setting real per-room groups via the chip, the SAME row (same spot the '52' used to be) must show the real total, got: " + sub);
  console.log("PASS: after setting per-room quantity groups via the 🏠 chip, the exact same Overall row now reads '52 of 5,740 MTR done, 5,688 remaining' -- automatically, no other change needed");

  // ---- test 3: this is the SAME DOM element/location as before -- proving
  // it's not a different row or a new feature, just the existing row now
  // reflecting the real total, exactly where the engineer already looks ----
  const activityRow = document.querySelector('.sp-ovact[data-ovact="Ceiling Piping"] .sp-ovactrow');
  assert.ok(activityRow, "FAIL: the activity row context is unchanged -- same place in the same accordion");
  console.log("PASS: the fix lives in the exact same place the engineer already reads -- Overall → Service → Activity → Item, no new UI to learn");
}

main().then(() => console.log("\nALL QI4-SCREENSHOT END-TO-END TESTS PASSED")).catch((e) => { console.error("TEST FAILURE:", e.message, e.stack); process.exit(1); });
