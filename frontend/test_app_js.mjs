import { JSDOM } from "jsdom";
import fs from "fs";

// Real index.html body, so every id app.js touches at top-level actually exists.
const html = fs.readFileSync(new URL("../index.html", import.meta.url), "utf8");

const dom = new JSDOM(html, { url: "http://localhost/", runScripts: "outside-only", pretendToBeVisual: true });
const { window } = dom;
global.window = window;
global.document = window.document;
global.HTMLElement = window.HTMLElement;

Object.defineProperty(window, "localStorage", {
  value: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
  writable: true,
});
window.fetch = global.fetch = async (url) => {
  const u = String(url);
  if (u.includes("/api/projects")) return { ok: true, json: async () => [] };
  return { ok: true, json: async () => ({}) };
};

const src = fs.readFileSync(new URL("../app.js", import.meta.url), "utf8");
window.eval(src);

async function flush(n = 5) { for (let i = 0; i < n; i++) await new Promise((r) => setTimeout(r, 0)); }

async function main() {
  await flush(10);

  if (typeof window.applyMode !== "function") throw new Error("FAIL: applyMode() not found on window (app.js has no IIFE, should be global)");

  // ---- test 1: forecast mode shows the KPI row ----
  window.applyMode("forecast");
  if (window.document.getElementById("kpis").hidden) throw new Error("FAIL: KPI row should be visible in forecast mode");
  console.log("PASS: KPI row visible in forecast mode");

  // ---- test 2: inventory mode (Safety/Tools tabs) hides the KPI row ----
  window.applyMode("inventory");
  if (!window.document.getElementById("kpis").hidden) throw new Error("FAIL: KPI row should be hidden on the Inventory tab -- these are Forecast-only shortage counts");
  console.log("PASS: KPI row hidden in inventory mode (Mahesh's screenshot bug)");

  // ---- test 3: ppe mode also hides it ----
  window.applyMode("ppe");
  if (!window.document.getElementById("kpis").hidden) throw new Error("FAIL: KPI row should be hidden on the PPE tab too");
  console.log("PASS: KPI row hidden in PPE mode");

  // ---- test 4: switching back to forecast un-hides it (not a one-way state) ----
  window.applyMode("forecast");
  if (window.document.getElementById("kpis").hidden) throw new Error("FAIL: KPI row should reappear when switching back to forecast mode");
  console.log("PASS: KPI row reappears on switching back to forecast");

  // ---- test 5: "Total received" includes opening stock, not just dated IN
  // (Mahesh's RG-6 CABLE bug: whole 4880-unit supply arrived as an opening
  // balance, zero dated IN transactions -- old code summed dated IN alone
  // and showed "0 received" despite the material clearly having stock).
  const rg6Rows = [
    { date: "2026-06-18", qty_in: 0, qty_out: 305, balance: 4575, opening: 4880 },
    { date: "2026-08-09", qty_in: 0, qty_out: 305, balance: 0, opening: 4880 },
  ];
  let html = window.sparkline(rg6Rows);
  if (!html.includes("4,880")) throw new Error("FAIL: Total received should be 4,880 (all opening stock, zero dated IN), got: " + html);
  console.log("PASS: Total received includes opening stock for a material with zero dated IN (RG-6 CABLE's real shape)");

  // a material with BOTH opening stock AND real dated IN must sum both
  const pvcRows = [
    { date: "2026-06-01", qty_in: 1000, qty_out: 0, balance: 3400, opening: 2400 },
    { date: "2026-07-01", qty_in: 3200, qty_out: 0, balance: 6600, opening: 2400 },
  ];
  html = window.sparkline(pvcRows);
  if (!html.includes("6,600")) throw new Error("FAIL: Total received should be 2400 opening + 4200 dated-in = 6,600, got: " + html);
  console.log("PASS: Total received correctly sums opening + dated IN when both are real");

  // a material with real dated IN and NO opening stock must be unaffected
  // (regression: must not double-count or break the existing normal case)
  const noOpeningRows = [
    { date: "2026-06-01", qty_in: 500, qty_out: 0, balance: 500, opening: null },
    { date: "2026-06-02", qty_in: 0, qty_out: 100, balance: 400, opening: null },
  ];
  html = window.sparkline(noOpeningRows);
  if (!html.includes("500")) throw new Error("FAIL: with no opening stock, received should just be the dated IN sum (500), got: " + html);
  console.log("PASS: materials with no opening stock are unaffected (regression check)");

  console.log("\nALL APP.JS TESTS PASSED");
}

main().catch((e) => { console.error("TEST FAILURE:", e.message); process.exit(1); });
