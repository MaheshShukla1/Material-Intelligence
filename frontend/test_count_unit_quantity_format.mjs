// Discrete (count) units display as whole numbers; continuous (measured)
// units keep decimal precision -- the exact reported symptom was "74.12
// NOS" for a discrete item.
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

const R1 = "r1";
const STRUCTURE = {
  id: "p0", type: "project", name: "Fixture Hotel", kind: "hotel",
  children: [{ id: "f1", type: "floor", name: "13th Floor", children: [
    { id: R1, type: "room", name: "Room 5", children: [] },
  ] }],
};

function svcState() {
  return {
    service: "Electrical", room: null,
    activities: ["DB Installation"],
    mapping: { "DB Installation": ["QI12", "QI99"] },
    act_pct: { "DB Installation": 36 }, overall_pct: 36,
    items: [
      { code: "QI12", desc: "16WAY SPN DB DD", unit: "NOS", sub: "Box/JB",
        qty: 1, planned: 204, used: 74.12, remaining: 129.88, pct: 36.3,
        mapped: true, rooms: 204, in_room: true, rate: 836.65, quick: false,
        done_val: 62011, rem_val: 108626 },
      { code: "QI99", desc: "25mm PVC Pipe", unit: "RMT", sub: "Pipe",
        qty: 1, planned: 1000, used: 535.7, remaining: 464.3, pct: 53.6,
        mapped: true, rooms: 100, in_room: true, rate: 100, quick: false,
        done_val: 53570, rem_val: 46430 },
    ],
    pnl_by_activity: {}, pnl_totals: {}, pnl_unmapped_value: { items: 0 },
    item_rooms: {}, item_progress: {}, item_room_qty: {},
    activity_progress: {}, labour_buckets: {}, unmapped: [],
    labour_only: {}, labour_pct: {}, labour_suggested: {},
  };
}

function route(url) {
  const u = new URL(url, "http://localhost");
  const p = u.pathname;
  if (p === "/api/projects") return [{ slug: "fixture-hotel", project: "Fixture Hotel" }];
  if (p === "/api/siteprogress/fixture-hotel")
    return { slug: "fixture-hotel", structure: STRUCTURE, rooms: 1,
             services: ["Electrical"], activities: { Electrical: ["DB Installation"] },
             progress_summary: {}, has_boq: true };
  if (p === "/api/siteprogress/fixture-hotel/service/Electrical") return svcState();
  if (p === "/api/siteprogress/fixture-hotel/pnl/Electrical")
    return { service: "Electrical", room: null, project: { done_value: 0, remaining_value: 0, pct_value_done: 0 },
             by_activity: {}, waste: { available: false }, rated_items: 2, total_items: 2, unmapped_value: { items: 0 } };
  if (p === "/api/siteprogress/fixture-hotel/realistic/Electrical") return { service: "Electrical", has_run: false, items: [] };
  throw new Error("unmocked route: " + p);
}
global.fetch = window.fetch = async (url) => ({ ok: true, json: async () => route(url), text: async () => JSON.stringify(route(url)) });
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

  // ---- the exact reported bug: a discrete (NOS) item's editable quantity
  // input must show a whole number, not "74.12" ----
  const nosInput = document.querySelector('input.sp-qty[data-qty="QI12"]');
  assert.ok(nosInput, "FAIL: expected QI12's quantity input to render");
  assert.strictEqual(nosInput.value, "74", `FAIL: NOS (discrete) item showed "${nosInput.value}", expected "74"`);
  console.log('PASS: a discrete-unit (NOS) item with used=74.12 shows "74", not "74.12"');

  // ---- a continuous (RMT) item must still show real decimal precision ----
  const rmtInput = document.querySelector('input.sp-qty[data-qty="QI99"]');
  assert.ok(rmtInput, "FAIL: expected QI99's quantity input to render");
  assert.strictEqual(rmtInput.value, "535.7", `FAIL: RMT (continuous) item showed "${rmtInput.value}", expected "535.7"`);
  console.log('PASS: a continuous-unit (RMT) item with used=535.7 keeps its real decimal, unchanged');
}

main().then(() => console.log("\nALL COUNT-UNIT QUANTITY FORMATTING TESTS PASSED"))
  .catch((e) => { console.error("TEST FAILURE:", e.message, e.stack); process.exit(1); });
