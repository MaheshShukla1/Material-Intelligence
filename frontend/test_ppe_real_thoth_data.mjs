import { JSDOM } from "jsdom";
import fs from "fs";
import assert from "assert";

const html = fs.readFileSync(new URL("../frontend/index.html", import.meta.url), "utf8");
const dom = new JSDOM(html, { url: "http://localhost/", runScripts: "outside-only", pretendToBeVisual: true });
const { window } = dom;
global.window = window; global.document = window.document; global.HTMLElement = window.HTMLElement;
Object.defineProperty(window, "localStorage", { value: { getItem: () => null, setItem: () => {}, removeItem: () => {} }, writable: true });

const REAL_RECORDS = JSON.parse(fs.readFileSync(new URL("./thoth_ppe_records.json", import.meta.url), "utf8"));

function route(url) {
  const u = new URL(url, "http://localhost"); const p = u.pathname;
  if (p === "/api/projects") return [{ slug: "thoth", project: "Thoth Mall", latest_run: "run1", runs: 1 }];
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
  await window.show("run1", { project: "Thoth Mall", filename: "google-sheet.xlsx", source: "site register",
    created: new Date().toISOString(), stats: { asof: "2026-08-14", materials: 200 },
    lead_time: 7, has_ppe: true, issues: [], mapping: [] },
    { services: ["Electrical"], counts: {}, overdue_orders: 0, idle_lines: 0 });
  await flush(15);
  document.querySelector('[data-s="PPE"]').click();
  await flush(20);

  const typeSel = document.getElementById("type");
  const contractorSel = document.getElementById("contractor");
  function readSummary() {
    const s = document.getElementById("ppesummary");
    return s.hidden ? null : Number(s.querySelector("b").textContent);
  }

  // ---- a real 4th PPE type this app has never hardcoded around: Blanket,
  // only present in Thoth Mall's sheet (Hyatt's has no blanket column at
  // all) -- proves the summary generalises to whatever types the file has ----
  typeSel.value = "Blanket"; typeSel.dispatchEvent(new window.Event("change")); await flush(10);
  assert.strictEqual(readSummary(), 13, "FAIL: whole-project blankets should be 13, got " + readSummary());
  console.log("PASS: real Thoth Mall data — whole-project Blankets = 13 (a type Hyatt's file doesn't even have, still works)");

  contractorSel.value = "SHIVDUTT"; contractorSel.dispatchEvent(new window.Event("change")); await flush(10);
  assert.strictEqual(readSummary(), 8, "FAIL: SHIVDUTT blankets should be 8, got " + readSummary());
  console.log("PASS: real Thoth Mall data — SHIVDUTT Blankets = 8 (matches pandas ground truth exactly)");

  typeSel.value = "Jacket"; typeSel.dispatchEvent(new window.Event("change")); await flush(10);
  assert.strictEqual(readSummary(), 49, "FAIL: SHIVDUTT jackets should be 49, got " + readSummary());
  console.log("PASS: real Thoth Mall data — SHIVDUTT Jackets = 49 (matches pandas ground truth exactly)");
}
main().then(() => console.log("\nALL REAL THOTH MALL PPE VERIFICATION PASSED")).catch((e) => { console.error("FAILURE:", e.message, e.stack); process.exit(1); });
