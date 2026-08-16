import { JSDOM } from "jsdom";
import fs from "fs";

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

// ---- fixture data mirroring the Python fixture (planned 120, room split) ----
let saved = [];   // record every POST /progress/item call
let mappingCalls = [];
let quickAddCalls = [];
const ROOM101 = "roo2", ROOM102 = "roo3";
const STRUCTURE = {
  id: "p0", type: "project", name: "Fixture Hotel",
  children: [{ id: "flo1", type: "floor", name: "Floor 1", children: [
    { id: ROOM101, type: "room", name: "Room 101", children: [] },
    { id: ROOM102, type: "room", name: "Room 102", children: [] },
  ] }],
};
function svcStateFor(room) {
  // whole-service: planned 120, used 62 (51.7%). room101 is 100% done on its
  // own 10-unit share; room102 is 0% done -- deliberately different from the
  // whole-service numbers so a test can tell "room-scoped" from "still whole
  // project" apart.
  const byRoom = {
    [ROOM101]: { planned: 10, used: 10, remaining: 0, pct: 100 },
    [ROOM102]: { planned: 10, used: 0, remaining: 10, pct: 0 },
  };
  const base = room && byRoom[room] ? byRoom[room] : { planned: 120, used: 62, remaining: 58, pct: 51.7 };
  return {
    service: "Electrical", room: room || null,
    activities: ["Wall Piping", "Ceiling Piping"],
    mapping: { "Wall Piping": ["2.1"], "Ceiling Piping": ["9.1"] },
    act_pct: { "Wall Piping": 51.7, "Ceiling Piping": 20.0 },
    overall_pct: 51.7,
    items: [{ code: "2.1", desc: "25mm PVC conduit", unit: "Mtr", sub: "Pipe",
              qty: 10, ...base, mapped: true, rooms: 12, in_room: true, rate: 50,
              quick: false, done_val: base.used * 50, rem_val: base.remaining * 50 },
            { code: "9.1", desc: "Ceiling conduit 20mm", unit: "Mtr", sub: "Pipe",
              qty: 5, planned: 50, used: 10, remaining: 40, pct: 20.0, mapped: true,
              rooms: 12, in_room: true, rate: 30, quick: false, done_val: 300, rem_val: 1200 }],
    pnl_by_activity: {}, pnl_totals: {}, pnl_unmapped_value: { items: 0 },
    item_rooms: {}, unmapped: [],
  };
}
// mutable whole-service (room=null) state -- POST handlers below mutate this
// one, mirroring what tests 1-6 (typing qty, removing item, etc) exercise.
// The two fixed per-room states above are read-only fixtures for the
// room-drawer test and are NOT affected by these mutations (each test path
// stays independent, same as the earlier version of this file).
let wholeState = svcStateFor(null);

function route(url, opts) {
  const u = new URL(url, "http://localhost");
  const method = (opts && opts.method) || "GET";
  if (u.pathname === "/api/projects") return [{ slug: "fixture-hotel", project: "Fixture Hotel" }];
  if (u.pathname === "/api/siteprogress/fixture-hotel") {
    return { slug: "fixture-hotel", structure: STRUCTURE,
             rooms: 2, services: ["Electrical"], activities: { Electrical: ["Wall Piping"] },
             progress_summary: { by_service: {}, overall: 0, rooms: 2 }, has_boq: true };
  }
  if (u.pathname === "/api/siteprogress/fixture-hotel/service/Electrical") {
    const room = u.searchParams.get("room");
    if (room === ROOM101 || room === ROOM102) return svcStateFor(room);
    return JSON.parse(JSON.stringify(wholeState));
  }
  if (u.pathname === "/api/siteprogress/fixture-hotel/pnl/Electrical") {
    return { service: "Electrical", room: u.searchParams.get("room"),
             project: { done_value: 3100, remaining_value: 2900, pct_value_done: 51.7 },
             by_activity: {}, waste: { available: false }, rated_items: 1, total_items: 1,
             unmapped_value: { items: 0 } };
  }
  if (u.pathname === "/api/siteprogress/fixture-hotel/realistic/Electrical") {
    return { service: "Electrical", has_run: true, run: "run1", linked_items: 1, shortages: 0,
             items: [{ item_code: "2.1", unit: "Mtr", planned_total: 120, used: 62, remaining: 58,
                       progress_pct: 51.7, need: 58, order_qty: 0, verdict: "ENOUGH",
                       desc: "25mm PVC conduit",
                       message: "58 Mtr of work remains; stock on hand is enough to finish at the current rate.",
                       links: [{ material: "MS CONDUIT 25MM", unit: "Mtr", on_hand: 9062,
                                 rate_per_day: 58, engine_days_left: 155, status: "GREEN",
                                 order_by: null, total_consumed: 4200, received: 13262,
                                 verdict: "ENOUGH" }] }] };
  }
  if (u.pathname === "/api/siteprogress/fixture-hotel/overall") {
    return {
      overall_pct: 39.0, done_value: 400, remaining_value: 640, waste_value: 239900,
      pct_value_done: 39.0,
      by_service: {
        "Electrical": { items: 43, pct: 12.0, done_value: 400, remaining_value: 640 },
        "Plumbing": { items: 30, pct: 5.0, done_value: 100, remaining_value: 900 },
      },
    };
  }
  if (method === "POST" && u.pathname === "/api/siteprogress/fixture-hotel/progress/item") {
    const body = JSON.parse(opts.body);
    saved.push(body);
    const it = wholeState.items.find((x) => x.code === body.item_code) || wholeState.items[0];
    it.used = body.frac * it.planned;
    it.pct = body.frac * 100;
    return JSON.parse(JSON.stringify(wholeState));
  }
  if (method === "POST" && u.pathname === "/api/siteprogress/fixture-hotel/mapping") {
    const body = JSON.parse(opts.body);
    mappingCalls.push(body);
    wholeState.mapping[body.activity] = body.codes;
    wholeState.items = wholeState.items.filter((it) => body.codes.includes(it.code) || true); // item stays in BOQ
    return JSON.parse(JSON.stringify(wholeState));
  }
  if (u.pathname === "/api/siteprogress/fixture-hotel/quick-items/Electrical/candidates") {
    const widened = u.searchParams.get("all_services") === "true";
    const narrow = [
      { name: "MS CONDUIT 25MM", unit: "Mtr", already: false, other_service: null },
    ];
    const wide = widened ? [
      { name: "25MM PVC PIPE", unit: "Mtr", already: false, other_service: "Plumbing" },
    ] : [];
    return { available: true, run: "run1", materials: [...narrow, ...wide] };
  }
  if (method === "POST" && u.pathname === "/api/siteprogress/fixture-hotel/quick-item") {
    const body = JSON.parse(opts.body);
    quickAddCalls.push(body);
    return JSON.parse(JSON.stringify(wholeState));
  }
  throw new Error("unmocked route: " + method + " " + u.pathname);
}

global.fetch = window.fetch = async (url, opts) => {
  const data = route(url, opts);
  return { ok: true, json: async () => data, text: async () => JSON.stringify(data) };
};
window.confirm = () => true;   // auto-confirm the remove dialog
window.alert = () => {};

const src = fs.readFileSync(new URL("../frontend/siteprogress.js", import.meta.url), "utf8");
window.eval(src);

async function flush(n = 8) { for (let i = 0; i < n; i++) await new Promise((r) => setTimeout(r, 0)); }

async function main() {
  Object.defineProperty(document, "readyState", { value: "complete", configurable: true });
  document.dispatchEvent(new window.Event("DOMContentLoaded"));
  await flush();

  // switch to Site Progress tab
  document.querySelector('.spnav button[data-v="siteprogress"]').click();
  await flush(20);

  const row = document.querySelector('.sp-brow[data-code="2.1"]');
  if (!row) throw new Error("FAIL: item row did not render");
  console.log("PASS: row rendered for item 2.1");

  // ---- test 1: tap-to-edit qty input exists with correct initial value ----
  const qtyInput = row.querySelector(".sp-qty");
  if (!qtyInput) throw new Error("FAIL: .sp-qty entry input missing");
  if (Number(qtyInput.value) !== 62) throw new Error("FAIL: qty input should start at 62, got " + qtyInput.value);
  console.log("PASS: qty input starts at 62 (matches server-reported `used`)");

  // ---- test 2: typing a new qty + change event saves the right frac ----
  qtyInput.value = "90";
  qtyInput.dispatchEvent(new window.Event("change", { bubbles: true }));
  await flush(10);
  const lastSave = saved[saved.length - 1];
  if (!lastSave || Math.abs(lastSave.frac - 90 / 120) > 1e-6)
    throw new Error("FAIL: expected frac " + (90/120) + ", got " + JSON.stringify(lastSave));
  console.log("PASS: typing 90 saved frac = 90/120 =", lastSave.frac.toFixed(4));

  // ---- test 3: over-planned value clamps to planned, not silently accepted ----
  const row2 = document.querySelector('.sp-brow[data-code="2.1"]');
  const qtyInput2 = row2.querySelector(".sp-qty");
  qtyInput2.value = "999";
  qtyInput2.dispatchEvent(new window.Event("change", { bubbles: true }));
  await flush(10);
  if (Number(qtyInput2.value) !== 120) throw new Error("FAIL: qty should clamp to planned=120, got " + qtyInput2.value);
  const lastSave2 = saved[saved.length - 1];
  if (Math.abs(lastSave2.frac - 1.0) > 1e-6) throw new Error("FAIL: clamped frac should be 1.0, got " + lastSave2.frac);
  console.log("PASS: typing 999 (> planned) clamps to 120 and saves frac=1.0");

  // ---- test 4: slider drag stays in sync with the qty input (secondary control) ----
  const row3 = document.querySelector('.sp-brow[data-code="2.1"]');
  const slider = row3.querySelector(".sp-bslide");
  slider.value = "25";
  slider.dispatchEvent(new window.Event("input", { bubbles: true }));
  const qtyAfterDrag = row3.querySelector(".sp-qty").value;
  if (Number(qtyAfterDrag) !== 30) throw new Error("FAIL: dragging slider to 25% of planned=120 should set qty=30, got " + qtyAfterDrag);
  console.log("PASS: dragging slider to 25% live-updates qty input to 30 (120*0.25)");
  slider.dispatchEvent(new window.Event("change", { bubbles: true }));
  await flush(10);
  const lastSave3 = saved[saved.length - 1];
  if (Math.abs(lastSave3.frac - 0.25) > 1e-6) throw new Error("FAIL: slider release should save frac=0.25, got " + lastSave3.frac);
  console.log("PASS: releasing slider saves frac=0.25");

  // ---- test 5: remove-from-activity button exists and calls /mapping with item stripped ----
  const row4 = document.querySelector('.sp-brow[data-code="2.1"]');
  const removeBtn = row4.querySelector('[data-removeitem="2.1"]');
  if (!removeBtn) throw new Error("FAIL: remove-from-activity button missing");
  if (removeBtn.dataset.removeact !== "Wall Piping") throw new Error("FAIL: remove button missing correct activity");
  removeBtn.click();
  await flush(10);
  const lastMap = mappingCalls[mappingCalls.length - 1];
  if (!lastMap || lastMap.activity !== "Wall Piping" || lastMap.codes.includes("2.1"))
    throw new Error("FAIL: expected mapping POST removing 2.1 from Wall Piping, got " + JSON.stringify(lastMap));
  console.log("PASS: remove-from-activity button strips the code from the activity's mapping");

  // ---- test 6: drawer-open button (unrelated) still works, distinct from remove ----
  wholeState.mapping["Wall Piping"] = ["2.1"];  // restore for a clean re-render
  document.querySelector('.spnav button[data-v="siteprogress"]').click();
  await flush(15);
  const fxOpen = document.querySelector('[data-fx="2.1"]');
  if (!fxOpen) throw new Error("FAIL: drawer-open fx button missing after re-render");
  fxOpen.click();
  await flush(5);
  if (!document.getElementById("sp-draw")) throw new Error("FAIL: drawer did not open");
  console.log("PASS: drawer-open (arrow) button still works independently of the remove (x) button");
  document.getElementById("sp-draw-x").click();
  await flush(5);

  // ---- test 7: room-aware URLs -- the numeric correctness of room-scoping
  // is verified server-side by the pytest suite; this just confirms the JS
  // actually threads roomQ() through the pnl fetch and every mutation
  // endpoint (item-rooms, activity, mapping x2, planned, quick-item, rates x2)
  // rather than only the one route that happened to get fixed first.
  const roomQCount = (src.match(/roomQ\(\)/g) || []).length;
  if (!src.includes("function roomQ()") || roomQCount < 9)
    throw new Error(`FAIL: expected roomQ() defined + wired into 8 call sites, found ${roomQCount} uses`);
  console.log(`PASS: roomQ() defined and wired into ${roomQCount - 1} endpoint calls`);

  // ---- test 8: clicking a real room in the tree actually re-fetches
  // room-scoped numbers (not the whole project's), and the drawer reflects
  // that room -- not a hardcoded "(N rooms)" label regardless of selection.
  const roomRow = document.querySelector(`[data-toggle="${ROOM101}"]`);
  if (!roomRow) throw new Error("FAIL: Room 101 leaf did not render in the tree");
  roomRow.click();
  await flush(15);
  const roomRow2 = document.querySelector('.sp-brow[data-code="2.1"]');
  if (!roomRow2) throw new Error("FAIL: item row missing after selecting a room");
  const roomQty = roomRow2.querySelector(".sp-qty").value;
  if (Number(roomQty) !== 10) throw new Error("FAIL: Room 101 is 100% of its own 10-unit planned share, expected qty input = 10, got " + roomQty);
  console.log("PASS: selecting Room 101 in the tree re-fetches that room's own numbers (qty=10, not the whole project's 62)");

  document.querySelector('[data-fx="2.1"]').click();
  await flush(5);
  const drawText = document.getElementById("sp-draw").textContent;
  if (!drawText.includes("Room 101")) throw new Error("FAIL: drawer should label the planned row with the selected room's name, got: " + drawText.slice(0, 200));
  if (drawText.includes("108 rooms") || drawText.includes("2 rooms"))
    throw new Error("FAIL: drawer must not show a whole-project room count while a specific room is selected");
  console.log('PASS: drawer planned-label says "Room 101", not a whole-project room count');

  if (!drawText.includes("received to date"))
    throw new Error("FAIL: drawer should show received-to-date for linked stock, got: " + drawText.slice(0, 400));
  console.log("PASS: drawer shows received-to-date (derived from existing forecast data, no new upload needed)");

  // ---- test 9: item row itself carries an explicit per-room badge while a
  // room is selected (Mahesh's repeated ask: each room's own item numbers
  // must be visibly, individually distinguishable, not just inferred from
  // the breadcrumb above).
  const row5 = document.querySelector('.sp-brow[data-code="2.1"]');
  if (!row5.textContent.includes("Room 101"))
    throw new Error("FAIL: item row should carry a visible 'Room 101' badge while that room is selected");
  console.log("PASS: item row shows an explicit room badge, not just the breadcrumb, while drilled into a room");

  // ---- test 10: 'Re-upload BOQ' action exists in the main view (previously
  // the /boq upload endpoint was only reachable from first-time setup, so a
  // fixed boq.py could never actually take effect on an existing project).
  document.querySelector("#sp-allrooms")?.click();
  await flush(10);
  const reboqBtn = document.getElementById("sp-reboq");
  const reboqFile = document.getElementById("sp-reboq-file");
  if (!reboqBtn || !reboqFile)
    throw new Error("FAIL: 'Re-upload BOQ' control missing from the main view");
  console.log("PASS: 'Re-upload BOQ' is reachable from the main view, not just first-time setup");

  // ---- test 11: quick-item picker no longer permanently blocks "✓ added"
  // materials from being re-picked (Mahesh's stuck-forever bug: remove an
  // item from its activity, then try to re-add the same stock material).
  if (src.includes('if (b.textContent.indexOf("✓") === 0) return'))
    throw new Error("FAIL: the old permanent no-op guard on already-added quick-items is still present");
  console.log("PASS: quick-item picker no longer has the permanent 'already added' no-op guard");

  // ---- test 12: Overall tab's per-service rows must NOT reuse the item-row
  // classes (.sp-brow/.sp-bqty). Those now carry grid-area assignments for a
  // 6-column item row (round-2 fix); reusing them for the Overall table's
  // different 4-stat layout made pct/done/remaining all collapse onto the
  // same grid cell -- exactly the garbled overlapping text in Mahesh's
  // screenshot.
  document.querySelector('[data-s="__overall__"]')?.click();
  await flush(15);
  const ovRows = document.querySelectorAll(".sp-ovrow");
  if (ovRows.length !== 2) throw new Error("FAIL: expected 2 Overall rows (Electrical, Plumbing), got " + ovRows.length);
  const badRow = document.querySelector(".sp-ovrow .sp-bqty, .sp-brow.sp-ovrow");
  if (badRow) throw new Error("FAIL: Overall row is still reusing the item-row .sp-bqty/.sp-brow classes -- the overlap bug is back");
  const statCount = ovRows[0].querySelectorAll(".sp-ovstat").length;
  if (statCount !== 3) throw new Error("FAIL: expected 3 distinct stat cells (pct/done/remaining) per Overall row, got " + statCount);
  console.log("PASS: Overall tab's per-service rows use dedicated classes -- no grid-area collision with item rows");

  // static CSS check: .sp-ovstat must never be given the same grid-area as
  // .sp-bqty (that's precisely how three stats collapsed into one cell)
  const css = fs.readFileSync(new URL("../frontend/siteprogress.css", import.meta.url), "utf8");
  const bqtyArea = (css.match(/\.sp-bqty\{grid-area:(\w+)/) || [])[1];
  const ovstatArea = (css.match(/\.sp-ovstat\{grid-area:(\w+)/) || [])[1];
  if (bqtyArea && ovstatArea && bqtyArea === ovstatArea)
    throw new Error("FAIL: .sp-ovstat shares a grid-area with .sp-bqty in the stylesheet");
  console.log("PASS: stylesheet confirms .sp-ovstat has no grid-area collision with .sp-bqty");

  // ---- test 13: editing an item inside the SECOND activity must not close
  // that activity's card. Old bug: renderActs() always hard-opened the FIRST
  // activity with items on every re-render (typing a qty re-renders the
  // whole list), so editing anything in a non-first activity silently
  // collapsed it back shut every time.
  document.querySelector('[data-s="__overall__"]')?.click();
  await flush(5);
  document.querySelector('[data-s="Electrical"]')?.click();
  await flush(15);
  const cards = [...document.querySelectorAll(".sp-card")];
  if (cards.length !== 2) throw new Error("FAIL: expected 2 activity cards (Wall Piping, Ceiling Piping), got " + cards.length);
  const secondCard = cards.find((c) => c.dataset.a === "Ceiling Piping");
  if (!secondCard) throw new Error("FAIL: Ceiling Piping card not found");
  secondCard.querySelector(".sp-chd").click();   // open the SECOND activity
  await flush(5);
  if (!secondCard.classList.contains("open")) throw new Error("FAIL: clicking Ceiling Piping's header did not open it");
  const qtyInSecond = secondCard.querySelector(".sp-qty");
  qtyInSecond.value = "30";
  qtyInSecond.dispatchEvent(new window.Event("change", { bubbles: true }));
  await flush(15);
  const secondCardAfter = [...document.querySelectorAll(".sp-card")].find((c) => c.dataset.a === "Ceiling Piping");
  if (!secondCardAfter.classList.contains("open"))
    throw new Error("FAIL: editing an item inside Ceiling Piping (the 2nd activity) closed its card after re-render");
  console.log("PASS: editing an item inside a non-first activity keeps that activity open across the re-render");

  // ---- test 14: "+ Add item" cross-service widen (explicit opt-in only) ----
  const addItemBtn = [...document.querySelectorAll("[data-quickadd]")].find((b) => b.dataset.quickadd === "Wall Piping");
  if (!addItemBtn) throw new Error("FAIL: '+ Add item' button not found for Wall Piping");
  addItemBtn.click();
  await flush(15);
  let listText = document.getElementById("sp-quicklist").textContent;
  if (!listText.includes("MS CONDUIT 25MM")) throw new Error("FAIL: narrow candidate list should show the in-service material");
  if (listText.includes("25MM PVC PIPE")) throw new Error("FAIL: cross-service material must NOT appear before widening -- this must be opt-in, not automatic");
  console.log("PASS: narrow '+ Add item' list shows only in-service materials by default");

  const widenCb = document.getElementById("sp-quickwidecb");
  if (!widenCb) throw new Error("FAIL: 'search other services' checkbox missing");
  widenCb.checked = true;
  widenCb.dispatchEvent(new window.Event("change", { bubbles: true }));
  await flush(15);
  listText = document.getElementById("sp-quicklist").textContent;
  if (!listText.includes("25MM PVC PIPE")) throw new Error("FAIL: checking 'search other services' should surface the cross-service material");
  if (!listText.includes("Plumbing")) throw new Error("FAIL: the cross-service material must be tagged with its real service (Plumbing), never disguised");
  console.log("PASS: explicit 'search other services' checkbox surfaces the cross-service material, tagged with its real service");

  const pvcPick = [...document.querySelectorAll("[data-quickpick]")].find((b) => b.dataset.quickpick === "25MM PVC PIPE");
  if (!pvcPick) throw new Error("FAIL: cross-service material has no pick button");
  pvcPick.click();
  await flush(10);
  if (!quickAddCalls.some((c) => c.material === "25MM PVC PIPE"))
    throw new Error("FAIL: clicking + Add on the cross-service material did not call the quick-item endpoint");
  console.log("PASS: cross-service material can actually be added (used to 404 server-side before this round's fix)");

  console.log("\nALL UI TESTS PASSED");
}

main().catch((e) => { console.error("TEST FAILURE:", e.message); process.exit(1); });
