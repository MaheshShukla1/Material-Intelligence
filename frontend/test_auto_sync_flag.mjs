// The silent 10-minute refresh must tell the server it is the timer (auto=1) so
// several open tabs share one sheet download; the Sync button must NOT send it
// (pressing it always means "pull fresh data now").
import { JSDOM } from "jsdom";
import fs from "fs";
import assert from "assert";

const html = fs.readFileSync(new URL("../frontend/index.html", import.meta.url), "utf8");
const dom = new JSDOM(html, { url: "http://localhost/", runScripts: "outside-only", pretendToBeVisual: true });
const { window } = dom;
global.window = window;
global.document = window.document;
global.HTMLElement = window.HTMLElement;
const stored = {};
Object.defineProperty(window, "localStorage", {
  value: { getItem: (k) => stored[k] ?? null, setItem: (k, v) => { stored[k] = v; }, removeItem: (k) => { delete stored[k]; } },
  writable: true,
});
window.alert = () => {};
window.confirm = () => true;

const LINK = "https://docs.google.com/spreadsheets/d/e/FAKE/pub?output=xlsx";
const syncCalls = [];
let reused = false;

function meta() {
  return { project: "Fixture Hotel", filename: "google-sheet.xlsx", source: "site register", source_link: LINK,
           created: new Date().toISOString(), stats: { asof: "2026-08-14", materials: 3 },
           lead_time: 7, has_ppe: false, issues: [], mapping: [] };
}
const summary = { services: [], counts: {}, overdue_orders: 0, idle_lines: 0 };

window.fetch = global.fetch = async (url, opts) => {
  const u = String(url);
  if (u.includes("/api/sync-sheet")) {
    syncCalls.push(Object.fromEntries(opts.body.entries()));
    return { ok: true, json: async () => ({ run_id: "run1", meta: meta(), summary, ...(reused ? { reused: true } : {}) }) };
  }
  if (u.includes("/api/projects")) return { ok: true, json: async () => [] };
  if (u.includes("/api/runs")) return { ok: true, json: async () => [] };
  if (u.includes("/api/subcategories")) return { ok: true, json: async () => ({ all: [], by_service: {} }) };
  if (u.includes("/api/forecast")) return { ok: true, json: async () => [] };
  if (u.includes("/api/run/")) return { ok: true, json: async () => ({ meta: meta(), summary }) };
  return { ok: true, json: async () => ({}) };
};

window.eval(fs.readFileSync(new URL("../frontend/app.js", import.meta.url), "utf8"));
const flush = async (n = 8) => { for (let i = 0; i < n; i++) await new Promise((r) => setTimeout(r, 0)); };

await flush(10);
// a sheet-backed run is open and the report is visible - the state the live users were in
await window.show("run0", meta(), summary);        // the page's own path for opening a run

// 1) the silent timer sends auto=1 together with the same fields it always sent
await window.eval("autoSync()");
await flush();
assert.strictEqual(syncCalls.length, 1, "autoSync should call /api/sync-sheet once");
assert.strictEqual(syncCalls[0].auto, "1", "FAIL: silent auto-sync must send auto=1, got " + JSON.stringify(syncCalls[0]));
assert.strictEqual(syncCalls[0].link, LINK);
assert.strictEqual(syncCalls[0].project, "Fixture Hotel");
assert.ok("lead_time" in syncCalls[0]);
console.log("PASS: silent auto-sync sends auto=1 with link, project and lead_time");

// 2) a reused run from the server is painted exactly like a fresh one (no special case in the page)
reused = true;
stored.currentRun = "run0";
await window.eval("autoSync()");
await flush();
assert.strictEqual(syncCalls.length, 2);
assert.strictEqual(stored.currentRun, "run1", "FAIL: a reused run must become the current run");
console.log("PASS: a reused run is adopted like a fresh one");

// 3) the Sync button never sends auto
document.getElementById("sync").click();
await flush(12);
const manual = syncCalls[syncCalls.length - 1];
assert.strictEqual(syncCalls.length, 3, "Sync button should have called /api/sync-sheet");
assert.ok(!("auto" in manual), "FAIL: the Sync button must not send auto, got " + JSON.stringify(manual));
assert.strictEqual(manual.link, LINK);
console.log("PASS: the Sync button sends no auto flag (always fresh)");

// 4) the timer does nothing in a background tab or before a report is open (unchanged behaviour)
document.getElementById("report").hidden = true;
await window.eval("autoSync()");
await flush();
assert.strictEqual(syncCalls.length, 3, "FAIL: no sync while the report is hidden");
console.log("PASS: still idle while no report is open");
process.exit(0);
