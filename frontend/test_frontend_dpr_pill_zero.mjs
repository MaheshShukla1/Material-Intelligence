// Covers the exact real-world case from the Hyatt Hotel screenshot:
// dpr/today returns count 0 (nothing captured via the room-specific
// routes yet) -- the link must still show and still be clickable, not
// disappear the way it did before this fix.
import { JSDOM } from "jsdom";
import fs from "fs";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let failures = 0;
function assertEq(a, e, m) { if (a !== e) { failures++; console.error(`FAIL: ${m}\n  expected ${JSON.stringify(e)}\n  actual   ${JSON.stringify(a)}`); } else console.log(`ok: ${m}`); }
function assertTrue(c, m) { if (!c) { failures++; console.error(`FAIL: ${m}`); } else console.log(`ok: ${m}`); }

const html = `<!doctype html><html><body><header class="top"><div class="topact"></div></header><main></main></body></html>`;
const dom = new JSDOM(html, { url: "http://localhost/" });
const { window } = dom;
global.window = window; global.document = window.document;

const STATE = { slug: "test-hyatt", structure: { id: "p0", type: "project", name: "Hyatt Hotel", kind: "hotel", children: [] },
  rooms: 109, services: ["Electrical"], activities: { Electrical: ["Wall Piping"] },
  progress_summary: {}, has_boq: true, settings: {} };
const SVC = { service: "Electrical", room: null, activities: ["Wall Piping"], mapping: { "Wall Piping": ["Q11"] },
  act_pct: { "Wall Piping": 20 }, overall_pct: 20,
  items: [{ code: "Q11", desc: "25MM PVC PIPE", unit: "MTR", sub: "Piping", acts: ["Wall Piping"], qty: 46, planned: 5014,
           used: 2958.2, remaining: 2055.8, pct: 59, mapped: true, rooms: 109, in_room: true, rate: null, quick: false,
           done_val: null, rem_val: null, full_val: null, install_pct: null, install_rate: null, install_pct_own: null,
           room_done: 0, room_progress: 0, room_pending: 0, room_total: 109, planned_override: false, has_room_groups: false }],
  pnl_by_activity: {}, pnl_totals: { planned_value: 0, done_value: 0, remaining_value: 0, full_value: 0, items: 1, rated: 0 },
  pnl_unmapped_value: { items: 0, planned_value: 0, done_value: 0, remaining_value: 0 },
  item_rooms: {}, item_progress: {}, item_room_qty: {}, unmapped: [], labour_only: {}, labour_pct: {}, labour_suggested: {} };
const PNL = { project: { planned_value: 0, done_value: 0, remaining_value: 0, pct_value_done: 0 }, by_activity: {}, waste: { available: false }, unmapped_value: SVC.pnl_unmapped_value };
const DPR_TODAY = { date: "2026-08-21", count: 0 };   // the exact real case

window.fetch = async (url, opts) => {
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
  document.querySelector('.spnav button[data-v="siteprogress"]').click();
  let tries = 0;
  while (!document.getElementById("sp-rates") && tries < 50) { await sleep(5); tries++; }
  tries = 0;
  const where = () => document.getElementById("sp-where");
  while (where() && where().textContent === "" && tries < 50) { await sleep(5); tries++; }

  assertEq(where().textContent, "Export DPR", "with 0 updates today, link still shows (not blank)");
  assertTrue(!!where().onclick, "still clickable with 0 updates");
  where().click();
  assertTrue(!!document.getElementById("sp-modal"), "modal still opens with 0 updates today");

  console.log(failures === 0 ? "\nALL PASSED" : `\n${failures} FAILURE(S)`);
  process.exit(failures === 0 ? 0 : 1);
})();
