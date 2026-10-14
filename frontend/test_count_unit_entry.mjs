// Typing a quantity for a COUNTED unit (Nos) saves a whole number; a measured unit (Rmt) keeps its decimals.
// Reported with the DPR: 5.25 / 6.75 / 4.286 Nos -- fractions of a fan that nobody installed.
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
const posts = [];
global.fetch = window.fetch = async (url, opts) => {
  if (opts && opts.method === "POST") {
    posts.push({ url: String(url), body: JSON.parse(opts.body) });
    const r = svcState();
    return { ok: true, json: async () => r, text: async () => JSON.stringify(r) };
  }
  return { ok: true, json: async () => route(url), text: async () => JSON.stringify(route(url)) };
};
window.confirm = () => true; window.alert = () => {};

const src = fs.readFileSync(new URL("../frontend/siteprogress.js", import.meta.url), "utf8");
window.eval(src);
async function flush(n = 15) { for (let i = 0; i < n; i++) await new Promise((r) => setTimeout(r, 0)); }
function type(inp, v) { inp.value = String(v); inp.dispatchEvent(new window.Event("change", { bubbles: true })); }

async function main() {
  Object.defineProperty(document, "readyState", { value: "complete", configurable: true });
  document.dispatchEvent(new window.Event("DOMContentLoaded"));
  await flush();
  document.querySelector('.spnav button[data-v="siteprogress"]').click();
  await flush(20);

  const nos = document.querySelector('input.sp-qty[data-qty="QI12"]');   // 204 NOS
  const rmt = document.querySelector('input.sp-qty[data-qty="QI99"]');   // 1000 RMT
  assert.ok(nos && rmt, "FAIL: both quantity boxes should render");

  // the box itself asks for whole numbers for a counted unit, decimals for a length
  assert.strictEqual(nos.getAttribute("step"), "1", "FAIL: NOS box must step by 1");
  assert.strictEqual(nos.getAttribute("inputmode"), "numeric", "FAIL: NOS box should open the numeric keypad");
  assert.strictEqual(rmt.getAttribute("step"), "any", "FAIL: RMT box must still accept decimals");
  assert.strictEqual(rmt.getAttribute("inputmode"), "decimal");
  console.log("PASS: NOS box = whole numbers (step 1, numeric keypad); RMT box = decimals");

  // typing 5.5 Nos saves 6 (and shows 6); the saved fraction is 6 / 204, not 5.5 / 204
  type(nos, 5.5); await flush();
  assert.strictEqual(nos.value, "6", `FAIL: box shows "${nos.value}" after typing 5.5 Nos`);
  let p = posts.filter((x) => x.url.endsWith("/progress/item")).pop();
  assert.ok(p, "FAIL: nothing was saved");
  assert.ok(Math.abs(p.body.frac - 6 / 204) < 1e-9, `FAIL: saved fraction ${p.body.frac}, expected ${6 / 204}`);
  console.log("PASS: 5.5 Nos typed -> shows 6, saves 6 / 204");

  type(nos, 5.4); await flush();
  assert.strictEqual(nos.value, "5");
  p = posts.filter((x) => x.url.endsWith("/progress/item")).pop();
  assert.ok(Math.abs(p.body.frac - 5 / 204) < 1e-9);
  console.log("PASS: 5.4 Nos typed -> 5");

  // a length keeps its decimals: 12.34 Rmt of 1000
  type(rmt, 12.34); await flush();
  assert.strictEqual(rmt.value, "12.34", `FAIL: RMT box shows "${rmt.value}"`);
  p = posts.filter((x) => x.url.endsWith("/progress/item")).pop();
  assert.ok(Math.abs(p.body.frac - 12.34 / 1000) < 1e-9);
  console.log("PASS: 12.34 Rmt typed -> 12.34, saved as 12.34 / 1000");

  // a whole number typed for Nos is untouched; above the plan it is KEPT (raised), whole
  type(nos, 7); await flush();
  assert.strictEqual(nos.value, "7");
  console.log("PASS: a whole count is saved as it is");
}

main().then(() => console.log("\nALL COUNT-UNIT ENTRY TESTS PASSED"))
  .catch((e) => { console.error("TEST FAILURE:", e.message, e.stack); process.exit(1); });
