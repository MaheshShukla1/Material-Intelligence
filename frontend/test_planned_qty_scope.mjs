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

const ROOM101 = "roo2";
const STRUCTURE = {
  id: "p0", type: "project", name: "Fixture Hotel",
  children: [{ id: "flo1", type: "floor", name: "Floor 1", children: [
    { id: ROOM101, type: "room", name: "Room 101", children: [] },
  ] }],
};

// whole-project: item 2.1 planned=120 (12 rooms x 10), manually overridden
// (planned_override:true) so the "manual" tag has something real to show.
// room101: planned=10 -- this item's own share of that one room.
function svcStateFor(room) {
  const wholeItem = { code: "2.1", desc: "25mm PVC conduit", unit: "Mtr", sub: "Pipe",
    qty: 10, planned: 120, used: 62, remaining: 58, pct: 51.7, mapped: true,
    rooms: 12, in_room: true, rate: 50, quick: false, done_val: 3100, rem_val: 2900,
    planned_override: true };
  const roomItem = { ...wholeItem, planned: 10, used: 10, remaining: 0, pct: 100, in_room: true };
  return {
    service: "Electrical", room: room || null,
    activities: ["Wall Piping"],
    mapping: { "Wall Piping": ["2.1"] },
    act_pct: { "Wall Piping": 51.7 }, overall_pct: 51.7,
    items: [room === ROOM101 ? roomItem : wholeItem],
    pnl_by_activity: {}, pnl_totals: {}, pnl_unmapped_value: { items: 0 },
    item_rooms: {}, unmapped: [],
  };
}

const plannedCalls = [];   // record every POST /planned call, url + body
function route(url, opts) {
  const u = new URL(url, "http://localhost");
  const method = (opts && opts.method) || "GET";
  if (u.pathname === "/api/projects") return [{ slug: "fixture-hotel", project: "Fixture Hotel" }];
  if (u.pathname === "/api/siteprogress/fixture-hotel")
    return { slug: "fixture-hotel", structure: STRUCTURE, rooms: 12,
             services: ["Electrical"], activities: { Electrical: ["Wall Piping"] },
             progress_summary: {}, has_boq: true };
  if (u.pathname === "/api/siteprogress/fixture-hotel/service/Electrical")
    return svcStateFor(u.searchParams.get("room"));
  if (u.pathname === "/api/siteprogress/fixture-hotel/pnl/Electrical")
    return { service: "Electrical", room: u.searchParams.get("room"),
             project: { done_value: 3100, remaining_value: 2900, pct_value_done: 51.7 },
             by_activity: {}, waste: { available: false }, rated_items: 1, total_items: 1,
             unmapped_value: { items: 0 } };
  if (u.pathname === "/api/siteprogress/fixture-hotel/realistic/Electrical")
    return { service: "Electrical", has_run: false, items: [] };
  if (method === "POST" && u.pathname === "/api/siteprogress/fixture-hotel/planned") {
    plannedCalls.push({ url: u.pathname + u.search, body: JSON.parse(opts.body) });
    return svcStateFor(null);
  }
  throw new Error("unmocked route: " + method + " " + u.pathname);
}
global.fetch = window.fetch = async (url, opts) => ({ ok: true, json: async () => route(url, opts), text: async () => JSON.stringify(route(url, opts)) });
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

  // ---- test 1: whole-project view -- planned is editable, plain original
  // markup ("{qty} ✎" + "{unit} planned"), no extra tag/text ----
  let row = document.querySelector('.sp-brow[data-code="2.1"]');
  assert.ok(row, "FAIL: item row missing in whole-project view");
  let btn = row.querySelector(".sp-planned");
  assert.ok(btn, "FAIL: whole-project view should show an editable planned button");
  assert.strictEqual(btn.textContent.trim(), "120 ✎");
  assert.strictEqual(row.querySelector(".sp-bqty").textContent.trim(), "120 ✎Mtr planned",
    "FAIL: no extra tag/text should be appended to the planned control");
  console.log("PASS: whole-project view shows the plain original planned control (120 ✎, 'Mtr planned') -- no extra tag or text");

  // ---- test 2: inside a room drill-down, the ✎ button is still visible and
  // clickable (visual parity restored) -- it shows THIS ROOM's own share
  // (10, not the whole-project 120), same label as before ("planned here"). ----
  document.querySelector(`[data-toggle="${ROOM101}"]`).click();
  await flush(15);
  row = document.querySelector('.sp-brow[data-code="2.1"]');
  assert.ok(row, "FAIL: item row missing in room-drilled view");
  const roomBtn = row.querySelector(".sp-planned");
  assert.ok(roomBtn, "FAIL: room-drilled view should still show the ✎ button (visual parity)");
  assert.strictEqual(roomBtn.textContent.trim(), "10 ✎", "FAIL: room-drilled button should show this room's own share (10)");
  assert.ok(row.querySelector(".sp-bqty").textContent.includes("planned here"), "FAIL: room-drilled label should still read 'planned here'");
  console.log("PASS: room-drilled view keeps the ✎ button, showing this room's own share (10 ✎, 'planned here')");

  // ---- test 3: clicking it while room-drilled never posts a room-scoped
  // save -- it safely switches to the whole-project view first (same as
  // clearing the room in the tree), THEN opens the edit prompt there, so
  // whatever gets typed is unambiguously the real whole-project number.
  // This is the actual fix: the silent-overwrite path is gone even though
  // the button looks and behaves like it always did. ----
  window.prompt = () => "300";
  roomBtn.click();
  await flush(20);
  const toastEl = document.getElementById("sp-toast");
  assert.ok(toastEl && toastEl.textContent.toLowerCase().includes("whole-project"),
    "FAIL: expected a toast explaining the switch to the whole-project view, got: " + (toastEl && toastEl.textContent));
  assert.ok(!document.querySelector("#sp-tree .row.on"), "FAIL: the room selection should be cleared after the redirect");
  const call0 = plannedCalls[plannedCalls.length - 1];
  assert.strictEqual(call0.url, "/api/siteprogress/fixture-hotel/planned", "FAIL: still must never carry a room query param, got: " + call0.url);
  assert.strictEqual(call0.body.planned, 300, "FAIL: the number typed should save as the whole-project override");
  console.log("PASS: clicking ✎ from a room view safely redirects to the whole-project view before saving — no silent room-scoped overwrite");

  // back to whole-project view for the editPlanned() behaviour tests
  document.querySelectorAll("#sp-tree .row.on").forEach((r) => r.classList.remove("on"));
  const svcPill = [...document.querySelectorAll(".sp-pill")].find((b) => b.textContent.trim() === "Electrical");
  assert.ok(svcPill, "FAIL: Electrical pill missing (needed to get back to whole-project view)");
  svcPill.click();
  await flush(15);
  row = document.querySelector('.sp-brow[data-code="2.1"]');
  btn = row.querySelector(".sp-planned");
  assert.ok(btn, "FAIL: expected to be back in whole-project (editable) view");

  // ---- test 4: entering a number saves a whole-project override, with NO
  // room param on the URL (matches the backend's hard rejection of one) ----
  window.prompt = () => "300";
  btn.click();
  await flush(10);
  const call1 = plannedCalls[plannedCalls.length - 1];
  assert.strictEqual(call1.url, "/api/siteprogress/fixture-hotel/planned", "FAIL: /planned must never be called with a room query param, got: " + call1.url);
  assert.strictEqual(call1.body.planned, 300);
  console.log("PASS: editing planned from the whole-project view saves 300 with no ?room= on the URL");

  // ---- test 5: clearing the field (blank + OK) sends planned:null to reset
  // to auto -- the actual fix for "the room chip doesn't seem to do anything"
  // (a stale override silently overrides the auto qty x rooms forever until
  // cleared) ----
  window.prompt = () => "";
  btn.click();
  await flush(10);
  const call2 = plannedCalls[plannedCalls.length - 1];
  assert.strictEqual(call2.body.planned, null, "FAIL: clearing the field should send planned:null to reset to auto, got: " + JSON.stringify(call2.body));
  console.log("PASS: clearing the prompt field sends planned:null -- resets to auto (qty × rooms), un-pinning the stale override");

  // ---- test 6: pressing Cancel (prompt returns null) makes no call at all ----
  const before = plannedCalls.length;
  window.prompt = () => null;
  btn.click();
  await flush(10);
  assert.strictEqual(plannedCalls.length, before, "FAIL: Cancel must not trigger any save");
  console.log("PASS: pressing Cancel on the prompt makes no API call");

  // ---- test 7: the prompt wording is unambiguous about scope ----
  let promptText = "";
  window.prompt = (msg) => { promptText = msg; return null; };
  btn.click();
  await flush(5);
  assert.ok(promptText.toLowerCase().includes("whole-project"), "FAIL: prompt should say 'whole-project', got: " + promptText);
  assert.ok(promptText.toLowerCase().includes("reset to auto"), "FAIL: prompt should explain how to reset to auto, got: " + promptText);
  console.log("PASS: the edit-planned prompt explicitly says 'whole-project' and explains how to reset to auto");
}

main().then(() => console.log("\nALL PLANNED-QTY SCOPE TESTS PASSED")).catch((e) => { console.error("TEST FAILURE:", e.message); process.exit(1); });
