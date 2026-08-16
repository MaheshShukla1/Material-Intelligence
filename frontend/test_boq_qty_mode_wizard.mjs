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

function hotelStructure() {
  return { id: "p0", type: "project", name: "Fixture Hotel", kind: "hotel",
    children: [{ id: "flo1", type: "floor", name: "Floor 1", children: [
      { id: "roo1", type: "room", name: "Room 101", children: [] }] }] };
}
function mallStructure() {
  return { id: "p0", type: "project", name: "Fixture Mall", kind: "mall",
    children: [{ id: "lev1", type: "level", name: "Ground", children: [
      { id: "zon1", type: "room", name: "Zone A", children: [] }] }] };
}

let qtyModeCalls = [];
let boqResponse = null;   // set per-test before triggering upload
let structureKind = "hotel";

function svcState() {
  return {
    service: "Electrical", room: null, activities: [], mapping: {},
    act_pct: {}, overall_pct: 0, items: [], pnl_by_activity: {}, pnl_totals: {},
    pnl_unmapped_value: { items: 0 }, item_rooms: {}, unmapped: [],
  };
}

function route(url, opts) {
  const u = new URL(url, "http://localhost");
  const method = (opts && opts.method) || "GET";
  const p = u.pathname;
  if (p === "/api/projects") return [{ slug: "fixture", project: "Fixture" }];
  if (p === "/api/siteprogress/fixture") {
    // has_boq:false keeps loadState() on the setup screen (renderSetup),
    // which is the only place #sp-boq-file exists -- exactly where every
    // scenario below needs to trigger from.
    return { slug: "fixture", structure: structureKind === "mall" ? mallStructure() : hotelStructure(),
             rooms: 1, services: [], activities: {},
             progress_summary: { by_service: {}, overall: 0, rooms: 1 }, has_boq: false };
  }
  if (p === "/api/siteprogress/fixture/service/Electrical") return svcState();
  if (p === "/api/siteprogress/fixture/pnl/Electrical")
    return { service: "Electrical", room: null, project: { done_value: 0, remaining_value: 0, pct_value_done: 0 },
             by_activity: {}, waste: { available: false }, rated_items: 0, total_items: 0, unmapped_value: { items: 0 } };
  if (p === "/api/siteprogress/fixture/realistic/Electrical") return { service: "Electrical", has_run: false, items: [] };
  if (method === "POST" && p === "/api/siteprogress/fixture/boq") return boqResponse;
  if (method === "POST" && p === "/api/siteprogress/fixture/qty-mode") {
    const body = JSON.parse(opts.body);
    qtyModeCalls.push(body);
    return { service: body.service, mode: body.mode, seeded: body.mode === "total" ? 3 : 0 };
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
  await flush(15);
  assert.ok(document.getElementById("sp-boq-file"), "FAIL: setup screen (with the BOQ file input) did not render -- check the has_boq:false fixture");
}

function triggerUpload() {
  const input = document.getElementById("sp-boq-file");
  assert.ok(input, "FAIL: BOQ file input missing -- is the service page rendered?");
  const fakeFile = new window.File(["dummy"], "boq.xlsx");
  Object.defineProperty(input, "files", { value: [fakeFile], configurable: true });
  input.dispatchEvent(new window.Event("change"));
}

async function main() {
  await boot();

  // ============ test 1: a raw (needs_qty_mode) service opens the wizard,
  // with hotel-appropriate wording ("room") ============
  structureKind = "hotel";
  boqResponse = { slug: "fixture", services: { Electrical: 66 }, skipped: [],
    seeded_mapping: {}, auto_rated: {}, needs_qty_mode: ["Electrical"] };
  triggerUpload();
  await flush(15);
  const modal1 = document.getElementById("sp-modal");
  assert.ok(modal1, "FAIL: qty-mode wizard modal should open when needs_qty_mode is non-empty");
  assert.ok(modal1.textContent.includes("Electrical"), "FAIL: modal should name the flagged service");
  assert.ok(modal1.textContent.toLowerCase().includes("room"), "FAIL: hotel project should say 'room', got: " + modal1.textContent);
  assert.ok(!modal1.textContent.toLowerCase().includes("zone"), "FAIL: hotel project should not say 'zone'");
  console.log("PASS: uploading a raw BOQ with needs_qty_mode opens the wizard, hotel wording says 'room'");

  // default selection is "per_room" (the existing, unchanged default)
  const perRoomRadio = modal1.querySelector('input[value="per_room"]');
  assert.ok(perRoomRadio && perRoomRadio.checked, "FAIL: 'per room' should be the pre-selected default, matching current behaviour");
  console.log("PASS: 'repeats per room' is pre-selected by default -- matches existing behaviour, nothing changes unless the engineer picks otherwise");

  // ============ test 2: saving with the default (per_room) selected makes
  // NO call to /qty-mode at all -- a true no-op ============
  qtyModeCalls = [];
  document.getElementById("sp-modal-save").click();
  await flush(10);
  assert.strictEqual(qtyModeCalls.length, 0, "FAIL: choosing the default 'per room' must not call /qty-mode at all");
  assert.ok(!document.getElementById("sp-modal"), "FAIL: modal should close after saving");
  console.log("PASS: saving with the default 'per room' choice makes zero API calls (true no-op)");

  // ============ test 3: choosing "total" and saving calls /qty-mode with
  // the right service + mode ============
  boqResponse = { slug: "fixture", services: { Electrical: 66 }, skipped: [],
    seeded_mapping: {}, auto_rated: {}, needs_qty_mode: ["Electrical"] };
  triggerUpload();
  await flush(15);
  const totalRadio = document.querySelector('input[value="total"]');
  totalRadio.click();
  qtyModeCalls = [];
  document.getElementById("sp-modal-save").click();
  await flush(10);
  assert.strictEqual(qtyModeCalls.length, 1);
  assert.deepStrictEqual(qtyModeCalls[0], { service: "Electrical", mode: "total" });
  console.log("PASS: choosing 'already the total' calls /qty-mode with {service:'Electrical', mode:'total'}");

  // ============ test 4: a mall project uses "zone" wording, never "room" ============
  structureKind = "mall";
  boqResponse = { slug: "fixture", services: { Electrical: 40 }, skipped: [],
    seeded_mapping: {}, auto_rated: {}, needs_qty_mode: ["Electrical"] };
  triggerUpload();
  await flush(15);
  const modal2 = document.getElementById("sp-modal");
  assert.ok(modal2.textContent.toLowerCase().includes("zone"), "FAIL: mall project should say 'zone', got: " + modal2.textContent);
  assert.ok(!modal2.textContent.toLowerCase().includes("per room"), "FAIL: the literal phrase 'per room' must never appear for a mall project");
  document.getElementById("sp-modal-cancel").click();
  console.log("PASS: a mall project's wizard says 'zone', never the literal word 'room'");

  // ============ test 5: a fully ProjectBase upload (needs_qty_mode empty)
  // never opens the wizard at all -- zero clicks needed ============
  structureKind = "hotel";
  boqResponse = { slug: "fixture", services: { Electrical: 339 }, skipped: [],
    seeded_mapping: {}, auto_rated: { Electrical: 339 }, needs_qty_mode: [] };
  triggerUpload();
  await flush(15);
  assert.ok(!document.getElementById("sp-modal"), "FAIL: a ProjectBase-only upload (needs_qty_mode=[]) must never show the wizard");
  console.log("PASS: a fully ProjectBase-sourced upload never opens the wizard -- fully automatic, zero clicks");

  // ============ test 6: the auto_rated confirmation reaches the toast ============
  await flush(5);
  const toastEl = document.getElementById("sp-toast");
  assert.ok(toastEl && toastEl.textContent.includes("339") && toastEl.textContent.toLowerCase().includes("automatically"),
    "FAIL: expected an auto-rated confirmation toast mentioning 339 items, got: " + (toastEl && toastEl.textContent));
  console.log("PASS: auto-rated ProjectBase items surface a confirmation toast (339 items, read automatically)");
}

main().then(() => console.log("\nALL BOQ QTY-MODE WIZARD TESTS PASSED")).catch((e) => { console.error("TEST FAILURE:", e.message, e.stack); process.exit(1); });
