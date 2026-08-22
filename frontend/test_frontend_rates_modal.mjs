// jsdom harness: window.eval() the real siteprogress.js, mock window.fetch,
// assert on real DOM -- same pattern the previous session established
// (test_service_switch_parallel.mjs-style). Exercises the changed pieces:
// openRatesModal() (default % + per-item override) and openDrawer()'s new
// Value done/remaining + full-contract-value line.
import { JSDOM } from "jsdom";
import fs from "fs";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let failures = 0;
function assertEq(actual, expected, msg) {
  if (actual !== expected) {
    failures++;
    console.error(`FAIL: ${msg}\n   expected: ${JSON.stringify(expected)}\n   actual:   ${JSON.stringify(actual)}`);
  } else {
    console.log(`ok: ${msg}`);
  }
}
function assertTrue(cond, msg) {
  if (!cond) { failures++; console.error(`FAIL: ${msg}`); } else { console.log(`ok: ${msg}`); }
}

const html = `<!doctype html><html><body>
  <header class="top"><div class="topact"></div></header>
  <main></main>
</body></html>`;
const dom = new JSDOM(html, { url: "http://localhost/" });
const { window } = dom;
global.window = window; global.document = window.document;

// ---- fixture data, shaped exactly like the real backend responses ----
const STATE = {
  slug: "test-thoth", structure: { id: "p0", type: "project", name: "Thoth Mall", kind: "mall",
    children: [{ id: "lev1", type: "level", name: "B1", children: [{ id: "room1", type: "room", name: "Zone 1", children: [] }] }] },
  rooms: 1, services: ["Electrical"], activities: { Electrical: ["Cable Tray"] },
  progress_summary: {}, has_boq: true, settings: { default_install_pct: 15 },
};
const SVC = {
  service: "Electrical", room: null, activities: ["Cable Tray"],
  mapping: { "Cable Tray": ["Q12", "Q13"] }, act_pct: { "Cable Tray": 6 }, overall_pct: 6,
  items: [
    { code: "Q12", desc: "150X50X2MM CABLE TRAY", unit: "MTR", sub: "Cable Tray", acts: ["Cable Tray"],
      qty: 80, planned: 1700, used: 100, remaining: 1600, pct: 5.9, mapped: true, rooms: 1, in_room: true,
      rate: 97, quick: false, done_val: 1455, rem_val: 23280, full_val: 164900,
      install_pct: 15, install_rate: 14.55, install_pct_own: null,
      room_done: 0, room_progress: 1, room_pending: 0, room_total: 1, planned_override: false, has_room_groups: true },
    { code: "Q13", desc: "300X50X2MM CABLE TRAY", unit: "MTR", sub: "Cable Tray", acts: ["Cable Tray"],
      qty: 50, planned: 1050, used: 0, remaining: 1050, pct: 0, mapped: true, rooms: 1, in_room: true,
      rate: 110, quick: false, done_val: 0, rem_val: 23100, full_val: 115500,
      install_pct: 20, install_rate: 22, install_pct_own: 20,
      room_done: 0, room_progress: 0, room_pending: 1, room_total: 1, planned_override: false, has_room_groups: true },
  ],
  pnl_by_activity: {}, pnl_totals: { planned_value: 24735, done_value: 1455, remaining_value: 23280, full_value: 164900, items: 2, rated: 2 },
  pnl_unmapped_value: { items: 0, planned_value: 0, done_value: 0, remaining_value: 0 },
  item_rooms: {}, item_progress: {}, item_room_qty: {}, unmapped: [],
  labour_only: {}, labour_pct: {}, labour_suggested: {},
};
const PNL = { project: { planned_value: 24735, done_value: 1455, remaining_value: 23280, pct_value_done: 5.9 },
  by_activity: {}, waste: { available: false }, rated_items: 2, total_items: 2, unmapped_value: SVC.pnl_unmapped_value };

const calls = [];
window.fetch = async (url, opts) => {
  calls.push({ url, opts });
  const body = opts && opts.body ? JSON.parse(opts.body) : null;
  const ok = (data) => ({ ok: true, json: async () => data, text: async () => JSON.stringify(data) });
  if (url === "/api/projects") return ok([{ slug: "test-thoth", project: "Thoth Mall" }]);
  if (url === "/api/siteprogress/test-thoth") return ok(STATE);
  if (url.startsWith("/api/siteprogress/test-thoth/service/Electrical")) return ok(SVC);
  if (url.startsWith("/api/siteprogress/test-thoth/pnl/Electrical")) return ok(PNL);
  if (url.startsWith("/api/siteprogress/test-thoth/realistic/Electrical")) return ok({});
  if (url === "/api/siteprogress/test-thoth/settings" && opts.method === "POST") {
    STATE.settings = { default_install_pct: body.default_install_pct };
    return ok(STATE.settings);
  }
  if (url.startsWith("/api/siteprogress/test-thoth/rates") && opts.method === "POST") return ok(SVC);
  return { ok: false, status: 404, text: async () => "not found" };
};
// jsdom's window.eval() runs the file in the window's realm, but Node 22's
// OWN global `fetch` (undici) still shadows an unqualified `fetch(...)` call
// there in this jsdom version -- window.fetch alone is silently never hit.
// Point Node's global fetch at the same mock so the real file's plain
// `fetch(u)` calls land here too.
global.fetch = window.fetch;

window.eval(fs.readFileSync("/home/claude/frontend/siteprogress.js", "utf8"));
// jsdom's readyState stays "loading" without a real resource-loading run;
// siteprogress.js's own boot() is guarded on this event exactly like a real
// browser would fire it once initial parsing is done -- so fire it here.
document.dispatchEvent(new window.Event("DOMContentLoaded", { bubbles: true, cancelable: true }));

(async () => {
  // boot() already ran on eval (document was already 'complete'). Drive the
  // real nav button, exactly like a user clicking the "Site Progress" tab.
  const navBtn = document.querySelector('.spnav button[data-v="siteprogress"]');
  assertTrue(!!navBtn, "nav injected a Site Progress button");
  navBtn.click();

  let tries = 0;
  while (!document.getElementById("sp-rates") && tries < 50) { await sleep(5); tries++; }
  assertTrue(!!document.getElementById("sp-rates"), "service view rendered with a Set rates button");

  // ---------- openRatesModal() ----------
  document.getElementById("sp-rates").click();
  const modalEl = document.getElementById("sp-modal");
  assertTrue(!!modalEl, "Set rates modal opened");

  const defInput = document.getElementById("sp-default-install");
  assertEq(defInput.value, "15", "default install % prefilled from S.state.settings");

  const rateInputs = [...document.querySelectorAll(".sp-rateinput")];
  const q12Rate = rateInputs.find((i) => i.dataset.code === "Q12");
  assertEq(q12Rate.value, "97", "Q12 rate input prefilled");

  const installInputs = [...document.querySelectorAll(".sp-installinput")];
  const q12Install = installInputs.find((i) => i.dataset.code === "Q12");
  const q13Install = installInputs.find((i) => i.dataset.code === "Q13");
  assertEq(q12Install.value, "", "Q12 has no own override -> input left BLANK, not the inherited 15%");
  assertEq(q12Install.placeholder, "15%", "Q12's blank input shows the default as a placeholder");
  assertEq(q13Install.value, "20", "Q13 DOES have its own override -> input prefilled with 20");

  // edit: bump the project default, give Q12 its own override, CLEAR Q13's
  defInput.value = "18";
  q12Install.value = "25";
  q13Install.value = "";

  document.getElementById("sp-modal-save").click();
  tries = 0;
  while (!calls.find((c) => c.url.startsWith("/api/siteprogress/test-thoth/rates") && c.opts && c.opts.method === "POST") && tries < 100) { await sleep(5); tries++; }

  const settingsCall = calls.find((c) => c.url === "/api/siteprogress/test-thoth/settings");
  assertTrue(!!settingsCall, "settings POST fired");
  assertEq(JSON.parse(settingsCall.opts.body).default_install_pct, 18, "new project default sent");

  const ratesCall = calls.find((c) => c.url.startsWith("/api/siteprogress/test-thoth/rates"));
  assertTrue(!!ratesCall, "rates POST fired");
  const ratesBody = JSON.parse(ratesCall.opts.body);
  assertEq(ratesBody.install_pct.Q12, 25, "Q12's new override sent");
  assertEq(ratesBody.install_pct.Q13, null, "Q13's cleared override sent as explicit null, not omitted");

  // ---------- openDrawer(): Value done/remaining + full contract value ----------
  await sleep(10);
  const fx = document.querySelector('.sp-fx[data-fx="Q12"]');
  assertTrue(!!fx, "item row has an expand button");
  fx.click();
  const drawer = document.getElementById("sp-draw");
  assertTrue(!!drawer, "drawer opened");
  const drawerText = drawer.textContent;
  assertTrue(drawerText.includes("Value done"), "drawer shows Value done");
  assertTrue(drawerText.includes("Value remaining"), "drawer shows Value remaining");
  assertTrue(drawerText.includes("full contract value"), "drawer shows the full-contract-value reference line");
  assertTrue(!drawerText.includes("Install rate"), "old misleading 'Install rate' label is gone");

  console.log(failures === 0 ? `\nALL PASSED (${calls.length} fetch calls total)` : `\n${failures} FAILURE(S)`);
  process.exit(failures === 0 ? 0 : 1);
})();
