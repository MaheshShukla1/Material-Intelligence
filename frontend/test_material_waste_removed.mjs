// Issue 2 (part 1): "Material waste" removed from both hero cards
// (Overall and per-service) -- the figure was flagged as unreliable/
// confusing early in a project (its own caveat text says as much) and
// causing a trust issue. Confirms it's gone from the DOM in both places,
// and that removing the backing sp-waste/sp-wasteh elements doesn't
// crash rendering (a real risk since renderHero() used to populate them
// unconditionally).
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
    service: "Electrical", room: null, activities: [], mapping: {},
    act_pct: {}, overall_pct: 55, items: [], pnl_by_activity: {}, pnl_totals: {},
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
    return { slug: "fixture-hotel", structure: STRUCTURE, rooms: 1,
             services: ["Electrical"], activities: { Electrical: [] },
             progress_summary: {}, has_boq: true };
  if (p === "/api/siteprogress/fixture-hotel/service/Electrical") return svcState();
  if (p === "/api/siteprogress/fixture-hotel/pnl/Electrical")
    return { service: "Electrical", room: null,
             project: { done_value: 54000, remaining_value: 216000, pct_value_done: 20 },
             by_activity: {}, waste: { available: true, wasted_value: 47250, caveat: null },
             rated_items: 1, total_items: 1, unmapped_value: { items: 0 } };
  if (p === "/api/siteprogress/fixture-hotel/realistic/Electrical") return { service: "Electrical", has_run: false, items: [] };
  if (p === "/api/siteprogress/fixture-hotel/overall")
    return { pct_value_done: 47, done_value: 1970000, remaining_value: 2267000, planned_value: 4237000, full_value: 4237000,
             waste_value: 636000, waste_caveat: null, saved_value: 0, overall_pct: 47,
             rooms_summary: { done: 0, in_progress: 212, total: 217, not_started: 5 },
             services: ["Electrical"],
             by_service: { Electrical: { done_value: 167000, remaining_value: 1290000, planned_value: 1457000, items: 63,
                                         waste_value: 0, waste_recorded_pct: null } } };
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

  // ---- test 1: per-service hero has no Material waste stat, no crash ----
  const heroText = document.querySelector(".sp-hero").textContent;
  assert.ok(!heroText.includes("Material waste"),
    "FAIL: per-service hero must not show Material waste anymore");
  assert.ok(!document.getElementById("sp-waste"), "FAIL: sp-waste element must be gone");
  console.log("PASS: Material waste removed from the per-service hero, no crash");

  // ---- test 2: Overall hero also has no Material waste stat ----
  document.querySelector('.sp-pill[data-s="__overall__"]').click();
  await flush(20);
  const overallHeroText = document.querySelector(".sp-hero").textContent;
  assert.ok(!overallHeroText.includes("Material waste"),
    "FAIL: Overall hero must not show Material waste anymore");
  assert.ok(!overallHeroText.includes("whole site"),
    "FAIL: 'Rooms — whole site' must also be removed from the Overall hero");
  // the remaining stats (Work done, Remaining) must still be intact --
  // "Rooms — whole site" was removed separately (its all-services-100%
  // definition was too strict to be useful; no replacement decided yet)
  assert.ok(overallHeroText.includes("Work done"));
  assert.ok(overallHeroText.includes("Remaining"));
  console.log("PASS: Material waste removed from the Overall hero too, other stats untouched");
}

main().then(() => console.log("\nALL MATERIAL-WASTE-REMOVED TESTS PASSED"))
  .catch((e) => { console.error("TEST FAILURE:", e.message, e.stack); process.exit(1); });
