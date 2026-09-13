// Issue 2: the real "Export daily update" modal now has a Service dropdown
// and an Area tree picker, and builds the /export-dpr URL with the right
// service + rooms query params.
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
  children: [{ id: "f1", type: "floor", name: "13th Floor", children: [
    { id: R1, type: "room", name: "Room 5", children: [] },
    { id: R2, type: "room", name: "Room 6", children: [] },
  ] }],
};

let lastExportHref = null;

function svcState() {
  return {
    service: "Electrical", room: null, activities: [], mapping: {},
    act_pct: {}, overall_pct: 0, items: [], pnl_by_activity: {}, pnl_totals: {},
    pnl_unmapped_value: { items: 0 }, item_rooms: {}, item_progress: {},
    item_room_qty: {}, activity_progress: {}, labour_buckets: {}, unmapped: [],
    labour_only: {}, labour_pct: {}, labour_suggested: {},
  };
}

function route(url) {
  const u = new URL(url, "http://localhost");
  const p = u.pathname;
  if (p === "/api/projects") return [{ slug: "fixture-hotel", project: "Fixture Hotel" }];
  if (p === "/api/siteprogress/fixture-hotel")
    return { slug: "fixture-hotel", structure: STRUCTURE, rooms: 2,
             services: ["Electrical", "Fire"], activities: { Electrical: [], Fire: [] },
             progress_summary: {}, has_boq: true };
  if (p === "/api/siteprogress/fixture-hotel/service/Electrical") return svcState();
  if (p === "/api/siteprogress/fixture-hotel/service/Fire") return svcState();
  if (p === "/api/siteprogress/fixture-hotel/dpr/today") return { count: 0 };
  if (p === "/api/siteprogress/fixture-hotel/pnl/Electrical")
    return { service: "Electrical", room: null, project: { done_value: 0, remaining_value: 0, pct_value_done: 0 },
             by_activity: {}, waste: { available: false }, rated_items: 0, total_items: 0, unmapped_value: { items: 0 } };
  if (p === "/api/siteprogress/fixture-hotel/realistic/Electrical") return { service: "Electrical", has_run: false, items: [] };
  throw new Error("unmocked route: " + p);
}
global.fetch = window.fetch = async (url) => ({ ok: true, json: async () => route(url), text: async () => JSON.stringify(route(url)) });
window.confirm = () => true; window.alert = () => {};

// intercept the export <a>.click() -- jsdom doesn't navigate, but we can
// capture the href the moment click() fires
const origClick = window.HTMLAnchorElement.prototype.click;
window.HTMLAnchorElement.prototype.click = function () { lastExportHref = this.href; };

const src = fs.readFileSync(new URL("../frontend/siteprogress.js", import.meta.url), "utf8");
window.eval(src);
async function flush(n = 15) { for (let i = 0; i < n; i++) await new Promise((r) => setTimeout(r, 0)); }

async function main() {
  Object.defineProperty(document, "readyState", { value: "complete", configurable: true });
  document.dispatchEvent(new window.Event("DOMContentLoaded"));
  await flush();
  document.querySelector('.spnav button[data-v="siteprogress"]').click();
  await flush(20);

  const trigger = document.getElementById("sp-where");
  assert.ok(trigger, "FAIL: expected the #sp-where export trigger to exist");
  trigger.click();
  await flush(15);

  // ---- test 1: service dropdown lists real project services ----
  const svcSelect = document.getElementById("sp-dpr-service");
  assert.ok(svcSelect, "FAIL: expected a Service dropdown in the export modal");
  const options = [...svcSelect.querySelectorAll("option")].map((o) => o.value);
  assert.deepStrictEqual(options, ["", "Electrical", "Fire"]);
  console.log("PASS: Service dropdown lists 'All services' plus every real project service");

  // ---- test 2: area tree shows real rooms ----
  const cb1 = document.querySelector('input[data-dprroom="r1"]');
  assert.ok(cb1, "FAIL: expected Room 5's checkbox in the area tree");
  console.log("PASS: Area tree renders the real structure's rooms");

  // ---- test 3: picking a service + ticking one room builds the right URL ----
  svcSelect.value = "Electrical";
  cb1.checked = true;
  document.getElementById("sp-modal-save").click();
  await flush(10);
  assert.ok(lastExportHref, "FAIL: export link was never clicked");
  const url = new URL(lastExportHref);
  assert.strictEqual(url.searchParams.get("service"), "Electrical");
  assert.deepStrictEqual(url.searchParams.getAll("rooms"), ["r1"]);
  console.log("PASS: exporting with Electrical + Room 5 ticked builds ?service=Electrical&rooms=r1");

  // ---- test 4: no service, no rooms ticked -> plain whole-project export (backward compatible URL shape) ----
  lastExportHref = null;
  trigger.click();
  await flush(15);
  document.getElementById("sp-modal-save").click();
  await flush(10);
  const url2 = new URL(lastExportHref);
  assert.strictEqual(url2.searchParams.get("service"), null);
  assert.deepStrictEqual(url2.searchParams.getAll("rooms"), []);
  console.log("PASS: leaving Service/Area blank builds a plain whole-project export URL, unchanged");
}

main().then(() => console.log("\nALL ISSUE 2 EXPORT MODAL TESTS PASSED"))
  .catch((e) => { console.error("TEST FAILURE:", e.message, e.stack); process.exit(1); });
