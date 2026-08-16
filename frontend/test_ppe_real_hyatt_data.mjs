import { JSDOM } from "jsdom";
import fs from "fs";
import assert from "assert";

const html = fs.readFileSync(new URL("../frontend/index.html", import.meta.url), "utf8");
const dom = new JSDOM(html, { url: "http://localhost/", runScripts: "outside-only", pretendToBeVisual: true });
const { window } = dom;
global.window = window; global.document = window.document; global.HTMLElement = window.HTMLElement;
Object.defineProperty(window, "localStorage", { value: { getItem: () => null, setItem: () => {}, removeItem: () => {} }, writable: true });

const REAL_RECORDS = JSON.parse(fs.readFileSync(new URL("./hyatt_ppe_records.json", import.meta.url), "utf8"));

let fetchLog = [];
function route(url) {
  const u = new URL(url, "http://localhost"); const p = u.pathname; fetchLog.push(p);
  if (p === "/api/projects") return [{ slug: "hyatt", project: "Hyatt Hotel", latest_run: "run1", runs: 1 }];
  if (p === "/api/ppe/run1") return { records: REAL_RECORDS };
  if (p === "/api/subcategories/run1") return { all: [], by_service: {} };
  if (p.startsWith("/api/forecast/run1")) return [];
  return {};
}
global.fetch = window.fetch = async (url) => ({ ok: true, json: async () => route(url), text: async () => JSON.stringify(route(url)) });
window.confirm = () => true; window.alert = () => {};
const src = fs.readFileSync(new URL("../frontend/app.js", import.meta.url), "utf8");
window.eval(src);
async function flush(n = 10) { for (let i = 0; i < n; i++) await new Promise((r) => setTimeout(r, 0)); }

async function main() {
  await flush(15);
  await window.show("run1", { project: "Hyatt Hotel", filename: "google-sheet.xlsx", source: "site register",
    created: new Date().toISOString(), stats: { asof: "2026-08-14", materials: 385 },
    lead_time: 7, has_ppe: true, issues: [], mapping: [] },
    { services: ["Electrical"], counts: {}, overdue_orders: 0, idle_lines: 0 });
  await flush(15);
  document.querySelector('[data-s="PPE"]').click();
  await flush(20);
  assert.ok(fetchLog.includes("/api/ppe/run1"));
  assert.strictEqual(window.PPE_ROWS === undefined, true); // sanity: still can't peek closures, confirms real flow was used

  const typeSel = document.getElementById("type");
  const contractorSel = document.getElementById("contractor");
  const sizeSel = document.getElementById("size");

  function readSummary() {
    const s = document.getElementById("ppesummary");
    return s.hidden ? null : Number(s.querySelector("b").textContent);
  }

  // ---- whole-project jackets ----
  typeSel.value = "Jacket"; typeSel.dispatchEvent(new window.Event("change")); await flush(10);
  assert.strictEqual(readSummary(), 232, "FAIL: whole-project jackets should be 232 (pandas ground truth), got " + readSummary());
  console.log("PASS: real Hyatt data — whole-project Jackets = 232 (matches pandas ground truth exactly)");

  // ---- SHADAB TEAM jackets ----
  contractorSel.value = "SHADAB TEAM"; contractorSel.dispatchEvent(new window.Event("change")); await flush(10);
  assert.strictEqual(readSummary(), 110, "FAIL: SHADAB TEAM jackets should be 110, got " + readSummary());
  console.log("PASS: real Hyatt data — SHADAB TEAM Jackets = 110 (matches pandas ground truth exactly)");

  // ---- SHADAB TEAM helmets ----
  typeSel.value = "Helmet"; typeSel.dispatchEvent(new window.Event("change")); await flush(10);
  assert.strictEqual(readSummary(), 33, "FAIL: SHADAB TEAM helmets should be 33, got " + readSummary());
  console.log("PASS: real Hyatt data — SHADAB TEAM Helmets = 33 (matches pandas ground truth exactly)");

  // ---- SHADAB TEAM shoes (all sizes) ----
  typeSel.value = "Shoes"; typeSel.dispatchEvent(new window.Event("change")); await flush(10);
  assert.strictEqual(readSummary(), 59, "FAIL: SHADAB TEAM shoes should be 59, got " + readSummary());
  console.log("PASS: real Hyatt data — SHADAB TEAM Shoes (all sizes) = 59 (matches pandas ground truth exactly)");

  // ---- SHADAB TEAM size-7 shoes ----
  sizeSel.value = "7"; sizeSel.dispatchEvent(new window.Event("change")); await flush(10);
  assert.strictEqual(readSummary(), 18, "FAIL: SHADAB TEAM size-7 shoes should be 18, got " + readSummary());
  console.log("PASS: real Hyatt data — SHADAB TEAM size-7 Shoes = 18 (matches pandas ground truth exactly)");
}
main().then(() => console.log("\nALL REAL-DATA PPE VERIFICATION PASSED")).catch((e) => { console.error("FAILURE:", e.message, e.stack); process.exit(1); });
