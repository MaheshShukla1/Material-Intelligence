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

// 12 rooms on one floor -- enough to exercise real group math without a huge fixture
const ROOM_IDS = Array.from({ length: 12 }, (_, i) => `roo${i + 1}`);
const STRUCTURE = {
  id: "p0", type: "project", name: "Fixture Hotel", kind: "hotel",
  children: [{ id: "flo1", type: "floor", name: "Floor 1",
    children: ROOM_IDS.map((id, i) => ({ id, type: "room", name: `Room ${i + 1}`, children: [] })) }],
};

let itemRoomQty = {};   // service-level item_room_qty, mutated by POSTs to simulate persistence
let itemRoomsPlain = {};
let lastRoomQtyPost = null;
let lastItemRoomsPost = null;

function svcState() {
  return {
    service: "Electrical", room: null, activities: ["Wall Piping"],
    mapping: { "Wall Piping": ["2.1"] }, act_pct: { "Wall Piping": 0 }, overall_pct: 0,
    items: [{
      code: "2.1", desc: "25MM MS conduit pipe black", unit: "MTR", sub: "Pipe",
      qty: 0, planned: 5740, used: 0, remaining: 5740, pct: 0, mapped: true,
      rooms: 12, in_room: true, rate: null, quick: true, done_val: null, rem_val: null,
      room_done: 0, room_progress: 0, room_pending: 12, room_total: 12,
    }],
    pnl_by_activity: {}, pnl_totals: {}, pnl_unmapped_value: { items: 0 },
    item_rooms: itemRoomsPlain, item_room_qty: itemRoomQty, unmapped: [],
  };
}

function route(url, opts) {
  const u = new URL(url, "http://localhost");
  const method = (opts && opts.method) || "GET";
  const p = u.pathname;
  if (p === "/api/projects") return [{ slug: "fixture-hotel", project: "Fixture Hotel" }];
  if (p === "/api/siteprogress/fixture-hotel")
    return { slug: "fixture-hotel", structure: STRUCTURE, rooms: 12,
             services: ["Electrical"], activities: { Electrical: ["Wall Piping"] },
             progress_summary: {}, has_boq: true };
  if (p === "/api/siteprogress/fixture-hotel/service/Electrical") return svcState();
  if (p === "/api/siteprogress/fixture-hotel/pnl/Electrical")
    return { service: "Electrical", room: null, project: { done_value: 0, remaining_value: 0, pct_value_done: 0 },
             by_activity: {}, waste: { available: false }, rated_items: 0, total_items: 1, unmapped_value: { items: 0 } };
  if (p === "/api/siteprogress/fixture-hotel/realistic/Electrical") return { service: "Electrical", has_run: false, items: [] };
  if (method === "POST" && p === "/api/siteprogress/fixture-hotel/item-room-qty") {
    const body = JSON.parse(opts.body);
    lastRoomQtyPost = body;
    // mirror itemprog.set_room_qty_group()'s move-not-duplicate semantics so
    // the mock actually behaves like the real backend across repeated saves
    const code = body.item_code;
    const newRooms = new Set(body.rooms || []);
    const existing = itemRoomQty[code] || [];
    const kept = [];
    for (const g of existing) {
      const remaining = g.rooms.filter((r) => !newRooms.has(r));
      if (remaining.length) kept.push({ rooms: remaining, qty: g.qty });
    }
    if (newRooms.size && body.qty != null) kept.push({ rooms: [...newRooms], qty: body.qty });
    if (kept.length) itemRoomQty[code] = kept; else delete itemRoomQty[code];
    return svcState();
  }
  if (method === "POST" && p === "/api/siteprogress/fixture-hotel/item-rooms") {
    const body = JSON.parse(opts.body);
    lastItemRoomsPost = body;
    return svcState();
  }
  throw new Error("unmocked route: " + method + " " + p);
}
global.fetch = window.fetch = async (url, opts) => ({ ok: true, json: async () => route(url, opts), text: async () => JSON.stringify(route(url, opts)) });
window.confirm = () => true; window.alert = () => {};

const src = fs.readFileSync(new URL("../frontend/siteprogress.js", import.meta.url), "utf8");
window.eval(src);
async function flush(n = 12) { for (let i = 0; i < n; i++) await new Promise((r) => setTimeout(r, 0)); }

async function boot() {
  Object.defineProperty(document, "readyState", { value: "complete", configurable: true });
  document.dispatchEvent(new window.Event("DOMContentLoaded"));
  await flush();
  document.querySelector('.spnav button[data-v="siteprogress"]').click();
  await flush(20);
}

function openModal() {
  const btn = document.querySelector('[data-roomsedit="2.1"]');
  assert.ok(btn, "FAIL: the 🏠 rooms chip for item 2.1 is missing");
  btn.click();
}

async function main() {
  await boot();

  // ---- test 1: modal shows the new optional quantity input ----
  openModal();
  await flush(10);
  let modalEl = document.getElementById("sp-modal");
  assert.ok(modalEl, "FAIL: Rooms modal did not open");
  const qtyInput = document.getElementById("sp-roomqty");
  assert.ok(qtyInput, "FAIL: expected a quantity input in the Rooms modal");
  assert.strictEqual(qtyInput.value, "", "FAIL: quantity should start blank when no groups exist yet");
  console.log("PASS: Rooms modal shows an optional quantity input, blank by default");

  // ---- test 2: leaving qty blank and saving still uses the OLD plain
  // applicability endpoint -- zero behaviour change for anyone who never
  // touches the new field ----
  lastItemRoomsPost = null; lastRoomQtyPost = null;
  document.querySelectorAll('#sp-modal input[data-room]').forEach((c, i) => { c.checked = i < 6; });
  document.getElementById("sp-modal-save").click();
  await flush(10);
  assert.ok(lastItemRoomsPost, "FAIL: expected a plain /item-rooms POST when qty is left blank");
  assert.strictEqual(lastRoomQtyPost, null, "FAIL: /item-room-qty must NOT be called when qty is blank");
  assert.strictEqual(lastItemRoomsPost.rooms.length, 6);
  console.log("PASS: leaving quantity blank still saves via the original /item-rooms endpoint (no regression)");

  // ---- test 3: THE motivating workflow -- tick 6 rooms, enter a real
  // quantity, save -> calls /item-room-qty with those rooms + qty ----
  itemRoomQty = {};   // reset as if nothing saved yet
  openModal();
  await flush(10);
  modalEl = document.getElementById("sp-modal");
  document.querySelectorAll('#sp-modal input[data-room]').forEach((c, i) => { c.checked = i < 6; });   // first 6 rooms
  document.getElementById("sp-roomqty").value = "52";
  lastRoomQtyPost = null; lastItemRoomsPost = null;
  document.getElementById("sp-modal-save").click();
  await flush(10);
  assert.ok(lastRoomQtyPost, "FAIL: expected a POST to /item-room-qty when a quantity is entered");
  assert.strictEqual(lastItemRoomsPost, null, "FAIL: /item-rooms must not also be called when a quantity is given");
  assert.strictEqual(lastRoomQtyPost.qty, 52);
  assert.strictEqual(lastRoomQtyPost.rooms.length, 6);
  console.log("PASS: ticking rooms + entering a real quantity saves via /item-room-qty with the right rooms and qty");

  // ---- test 4: re-opening the modal shows the existing group in a summary ----
  openModal();
  await flush(10);
  modalEl = document.getElementById("sp-modal");
  const summary = modalEl.querySelector(".sp-qtygroups");
  assert.ok(summary, "FAIL: expected a 'Current quantity groups' summary once a group exists");
  assert.ok(summary.textContent.includes("6 rooms") && summary.textContent.includes("52"),
    "FAIL: summary should show '6 rooms' and '52', got: " + summary.textContent);
  console.log("PASS: re-opening the modal shows the existing group (6 rooms @ 52 MTR) in a summary");

  // ---- test 5: the SECOND group -- tick the remaining 6 rooms, a
  // different quantity, save -- this is (52 x 6) + (60 x 6) in the real
  // motivating example scaled down, proving groups compose ----
  document.querySelectorAll('#sp-modal input[data-room]').forEach((c) => {
    // rooms 7-12 (the ones NOT in the first group) should already be pre-checked
    // by openRoomsModal's own "pre-check ungrouped rooms" logic -- verify that,
    // then just add the new quantity and save
  });
  const checkedNow = [...document.querySelectorAll('#sp-modal input[data-room]:checked')].map((c) => c.dataset.room);
  assert.strictEqual(checkedNow.length, 6, "FAIL: the 6 rooms NOT yet in any group should be pre-checked automatically");
  document.getElementById("sp-roomqty").value = "60";
  lastRoomQtyPost = null;
  document.getElementById("sp-modal-save").click();
  await flush(10);
  assert.strictEqual(lastRoomQtyPost.qty, 60);
  assert.strictEqual(lastRoomQtyPost.rooms.length, 6);
  console.log("PASS: the un-grouped rooms are pre-checked automatically for the second save; second group (60) saved correctly");

  // ---- test 6: roomsChipLabel() reflects TWO groups (both saved above --
  // 6 rooms @ 52, 6 rooms @ 60 -- covering all 12 rooms) ----
  openModal();
  await flush(10);
  document.getElementById("sp-modal-cancel").click();
  await flush(5);
  const chipLabel = document.querySelector('[data-roomsedit="2.1"]').textContent;
  assert.ok(chipLabel.includes("12 of 12 rooms") && chipLabel.includes("2 qty groups"),
    "FAIL: chip label should reflect 2 qty groups covering all 12 rooms, got: " + chipLabel);
  console.log("PASS: the 🏠 rooms chip label reflects multiple quantity groups (2 groups, 12 of 12 rooms)");

  // ---- test 7: a single group covering everything shows the plain
  // "N rooms @ X unit" label, not the "N of M · groups" form ----
  itemRoomQty = { "2.1": [{ rooms: ROOM_IDS, qty: 52 }] };
  document.querySelector('.sp-pill[data-s="Electrical"]').click();   // real re-fetch, no exposed loadService() to call directly
  await flush(15);
  const chipLabel2 = document.querySelector('[data-roomsedit="2.1"]').textContent;
  assert.ok(chipLabel2.includes("12 rooms @ 52"), "FAIL: single-group label should read '12 rooms @ 52 MTR', got: " + chipLabel2);
  console.log("PASS: a single group covering all rooms shows the plain '12 rooms @ 52 MTR' label");

  // ---- test 8: each group in the "Current quantity groups" summary has a
  // "Remove" action, and clicking it deletes that whole group in one go ----
  openModal();
  await flush(10);
  modalEl = document.getElementById("sp-modal");
  let removeBtn = modalEl.querySelector(".sp-qtygroup-rm");
  assert.ok(removeBtn, "FAIL: expected a 'Remove' action on the existing quantity group");
  lastRoomQtyPost = null;
  removeBtn.click();
  await flush(15);
  assert.ok(lastRoomQtyPost, "FAIL: clicking Remove should POST to /item-room-qty");
  assert.strictEqual(lastRoomQtyPost.qty, null, "FAIL: removing a group should send qty:null (the existing 'clear rooms from every group' mechanism)");
  assert.strictEqual(lastRoomQtyPost.rooms.length, 12, "FAIL: Remove should submit the WHOLE group's own room list, not a partial selection");
  assert.deepStrictEqual(itemRoomQty["2.1"], undefined, "FAIL: the item's group entry should be gone entirely once its only group is removed");
  console.log("PASS: 'Remove' on a quantity group deletes it entirely (qty:null over that group's own rooms), item drops out of item_room_qty");

  // ---- test 9: after removing the only group, re-opening the modal shows
  // no groups summary at all -- back to the plain applicability state ----
  openModal();
  await flush(10);
  modalEl = document.getElementById("sp-modal");
  assert.ok(!modalEl.querySelector(".sp-qtygroups"), "FAIL: the groups summary should be gone once every group has been removed");
  console.log("PASS: once every group is removed, the modal shows no leftover groups summary");
}

main().then(() => console.log("\nALL ROOM-QUANTITY-GROUPS UI TESTS PASSED")).catch((e) => { console.error("TEST FAILURE:", e.message, e.stack); process.exit(1); });
