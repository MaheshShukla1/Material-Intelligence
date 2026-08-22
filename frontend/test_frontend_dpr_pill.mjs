// jsdom harness for the DPR projBar pill + export modal, same pattern
// established earlier this session (window.eval the real file, mock
// window.fetch + global.fetch, drive real DOM clicks).
import { JSDOM } from "jsdom";
import fs from "fs";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let failures = 0;
function assertEq(actual, expected, msg) {
  if (actual !== expected) { failures++; console.error(`FAIL: ${msg}\n   expected: ${JSON.stringify(expected)}\n   actual:   ${JSON.stringify(actual)}`); }
  else console.log(`ok: ${msg}`);
}
function assertTrue(cond, msg) {
  if (!cond) { failures++; console.error(`FAIL: ${msg}`); } else { console.log(`ok: ${msg}`); }
}

const html = `<!doctype html><html><body><header class="top"><div class="topact"></div></header><main></main></body></html>`;
const dom = new JSDOM(html, { url: "http://localhost/" });
const { window } = dom;
global.window = window; global.document = window.document;
let navAttempt = null;
dom.virtualConsole.on("jsdomError", (e) => { if (e.message) navAttempt = e.message; });

const STATE = { slug: "test-hyatt", structure: { id: "p0", type: "project", name: "Hyatt Hotel", kind: "hotel",
  children: [{ id: "f1", type: "floor", name: "13TH", children: [{ id: "r1", type: "room", name: "5", children: [] }] }] },
  rooms: 1, services: ["Electrical"], activities: { Electrical: ["Point Wiring"] },
  progress_summary: {}, has_boq: true, settings: {} };
const SVC = { service: "Electrical", room: null, activities: ["Point Wiring"], mapping: { "Point Wiring": ["E1"] },
  act_pct: { "Point Wiring": 100 }, overall_pct: 100,
  items: [{ code: "E1", desc: "POINT WIRING", unit: "Nos", sub: "Wiring", acts: ["Point Wiring"], qty: 1, planned: 1,
           used: 1, remaining: 0, pct: 100, mapped: true, rooms: 1, in_room: true, rate: 500, quick: false,
           done_val: 500, rem_val: 0, full_val: 500, install_pct: null, install_rate: 500, install_pct_own: null,
           room_done: 1, room_progress: 0, room_pending: 0, room_total: 1, planned_override: false, has_room_groups: false }],
  pnl_by_activity: {}, pnl_totals: { planned_value: 500, done_value: 500, remaining_value: 0, full_value: 500, items: 1, rated: 1 },
  pnl_unmapped_value: { items: 0, planned_value: 0, done_value: 0, remaining_value: 0 },
  item_rooms: {}, item_progress: {}, item_room_qty: {}, unmapped: [], labour_only: {}, labour_pct: {}, labour_suggested: {} };
const PNL = { project: { planned_value: 500, done_value: 500, remaining_value: 0, pct_value_done: 100 },
  by_activity: {}, waste: { available: false }, unmapped_value: SVC.pnl_unmapped_value };
const DPR_TODAY = { date: "2026-08-21", count: 6 };

const calls = [];
window.fetch = async (url, opts) => {
  calls.push({ url, opts });
  const ok = (data) => ({ ok: true, json: async () => data, text: async () => JSON.stringify(data) });
  if (url === "/api/projects") return ok([{ slug: "test-hyatt", project: "Hyatt Hotel" }]);
  if (url === "/api/siteprogress/test-hyatt") return ok(STATE);
  if (url.startsWith("/api/siteprogress/test-hyatt/service/Electrical")) return ok(SVC);
  if (url.startsWith("/api/siteprogress/test-hyatt/pnl/Electrical")) return ok(PNL);
  if (url.startsWith("/api/siteprogress/test-hyatt/realistic/Electrical")) return ok({});
  if (url.startsWith("/api/siteprogress/test-hyatt/dpr/today")) return ok(DPR_TODAY);
  return { ok: false, status: 404, text: async () => "not found" };
};
global.fetch = window.fetch;

window.eval(fs.readFileSync("/home/claude/frontend/siteprogress.js", "utf8"));
document.dispatchEvent(new window.Event("DOMContentLoaded", { bubbles: true, cancelable: true }));

(async () => {
  const navBtn = document.querySelector('.spnav button[data-v="siteprogress"]');
  navBtn.click();
  let tries = 0;
  while (!document.getElementById("sp-rates") && tries < 50) { await sleep(5); tries++; }
  assertTrue(!!document.getElementById("sp-rates"), "service view rendered");

  tries = 0;
  const where = () => document.getElementById("sp-where");
  while (where() && where().textContent === "" && tries < 50) { await sleep(5); tries++; }
  assertEq(where().textContent, "6 updates today", "sp-where populated from the real /dpr/today count");

  where().click();
  const modalEl = document.getElementById("sp-modal");
  assertTrue(!!modalEl, "DPR export modal opened");
  assertTrue(!!document.getElementById("sp-dpr-start"), "start date input present");
  assertTrue(!!document.getElementById("sp-dpr-end"), "end date input present");
  assertEq(document.getElementById("sp-dpr-start").value, document.getElementById("sp-dpr-end").value,
          "start and end both default to today");

  // change the range, then Save should navigate to the export URL with both params
  document.getElementById("sp-dpr-start").value = "2026-08-19";
  let navigatedTo = null;
  window.HTMLAnchorElement.prototype.click = function () { navigatedTo = this.href; };
  document.getElementById("sp-modal-save").click();
  await sleep(10);
  assertTrue(!!navigatedTo, "clicking Export DPR navigated to a download URL");
  assertTrue(navigatedTo.includes("start=2026-08-19"), "start param present");
  assertTrue(navigatedTo.includes("end=2026-08-21"), "end param present when different from start");

  console.log(failures === 0 ? `\nALL PASSED (${calls.length} fetch calls)` : `\n${failures} FAILURE(S)`);
  process.exit(failures === 0 ? 0 : 1);
})();
