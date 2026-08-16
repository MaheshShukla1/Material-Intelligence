import { JSDOM } from "jsdom";
import fs from "fs";
import assert from "assert";

const html = fs.readFileSync(new URL("../frontend/index.html", import.meta.url), "utf8");
const dom = new JSDOM(html, { url: "http://localhost/", runScripts: "outside-only", pretendToBeVisual: true });
const { window } = dom;
global.window = window;
global.document = window.document;
global.HTMLElement = window.HTMLElement;

Object.defineProperty(window, "localStorage", {
  value: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
  writable: true,
});

// realistic PPE issue-log records, shaped like the real Hyatt export: one
// row per person, per issue event -- Y/blank flags per item type, shoe size
// only meaningful alongside shoes:true.
const PPE_RECORDS = [
  { name: "AARIF", contractor: "SHADAB TEAM", date: "2026-06-16", shoes: "Y", shoes_size: "7", helmet: "Y", jacket: "Y" },
  { name: "AFROZ ALAM", contractor: "SHADAB TEAM", date: "2026-05-13", shoes: "Y", shoes_size: "10", helmet: "Y", jacket: "Y" },
  { name: "AFSAR", contractor: "SHADAB TEAM", date: "2026-06-06", shoes: "Y", shoes_size: "7", helmet: "Y", jacket: "Y" },
  { name: "AFSAR", contractor: "SHADAB TEAM", date: "2026-06-28", shoes: "-", jacket: "Y" },
  { name: "AFTAB KHAN", contractor: "SHADAB TEAM", date: "2026-04-05", shoes: "Y", shoes_size: "6", helmet: "Y", jacket: "Y" },
  { name: "RAHUL", contractor: "APEX ELECTRICALS", date: "2026-05-01", shoes: "Y", shoes_size: "7", helmet: "Y", jacket: "Y" },
  { name: "SURESH", contractor: "APEX ELECTRICALS", date: "2026-05-02", shoes: "Y", shoes_size: "9", jacket: "Y" },
  { name: "VIJAY", contractor: "APEX ELECTRICALS", date: "2026-05-03", helmet: "Y" },
];
// hand-counted expectations from the fixture above:
//   SHADAB TEAM:      shoes=4 (sizes 7,10,7,6), helmet=4, jacket=5
//   APEX ELECTRICALS: shoes=2 (sizes 7,9),       helmet=2, jacket=2
//   all contractors:  shoes=6, helmet=6, jacket=7
//   size 7 shoes: SHADAB=2 (AARIF, AFSAR), APEX=1 (RAHUL), all=3

let fetchLog = [];
function route(url) {
  const u = new URL(url, "http://localhost");
  const p = u.pathname;
  fetchLog.push(p);
  if (p === "/api/projects") return [{ slug: "fixture-hotel", project: "Fixture Hotel", latest_run: "run1", runs: 1 }];
  if (p === "/api/runs/run1" || p === "/api/run/run1") return {};
  if (p === "/api/ppe/run1") return { records: JSON.parse(JSON.stringify(PPE_RECORDS)) };
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

  // Bootstrap straight through the real show() -> paintTabs() -> click the
  // PPE tab -> load() -> loadPPE() -> renderPPE() chain, exactly like a
  // person opening the app and clicking the PPE tab would.
  await window.show("run1", { project: "Fixture Hotel", filename: "google-sheet.xlsx", source: "site register",
    created: new Date().toISOString(), stats: { asof: "2026-08-14", materials: 385 },
    lead_time: 7, has_ppe: true, issues: [], mapping: [] },
    { services: ["Electrical", "Plumbing"], counts: {}, overdue_orders: 0, idle_lines: 0 });
  await flush(15);

  const ppeTab = [...document.querySelectorAll("#svc .tab")].find((b) => b.dataset.s === "PPE");
  assert.ok(ppeTab, "FAIL: PPE tab not rendered (has_ppe should surface it)");
  ppeTab.click();
  await flush(20);
  assert.ok(fetchLog.includes("/api/ppe/run1"), "FAIL: clicking the PPE tab should fetch /api/ppe/run1");

  // ---- test 1: no type selected -> summary banner hidden ----
  let summary = document.getElementById("ppesummary");
  assert.ok(summary, "FAIL: #ppesummary element should exist once PPE has rendered");
  assert.ok(summary.hidden, "FAIL: summary should be hidden until a type is selected");
  console.log("PASS: summary banner stays hidden with 'All types' selected");

  // ---- test 2: selecting Jacket shows the total count across everyone ----
  const typeSel = document.getElementById("type");
  typeSel.value = "Jacket";
  typeSel.dispatchEvent(new window.Event("change"));
  await flush(10);
  summary = document.getElementById("ppesummary");
  assert.ok(!summary.hidden, "FAIL: summary should show once a type is selected");
  assert.strictEqual(summary.querySelector("b").textContent, "7", "FAIL: expected 7 total jackets issued");
  assert.strictEqual(summary.querySelector("span").textContent, "Jackets issued",
    "FAIL: expected 'Jackets issued' with no contractor, got: " + summary.querySelector("span").textContent);
  console.log("PASS: selecting Jacket shows '7 / Jackets issued' (whole-project total)");

  // ---- test 3: also picking a contractor narrows both the table AND the
  // summary count together -- the actual ask (per-contractor, per-type count
  // without manually counting rows) ----
  const contractorSel = document.getElementById("contractor");
  contractorSel.value = "SHADAB TEAM";
  contractorSel.dispatchEvent(new window.Event("change"));
  await flush(10);
  summary = document.getElementById("ppesummary");
  assert.strictEqual(summary.querySelector("b").textContent, "5", "FAIL: expected 5 jackets for SHADAB TEAM");
  assert.strictEqual(summary.querySelector("span").textContent, "Jackets issued to SHADAB TEAM");
  console.log("PASS: adding a contractor filter narrows to '5 / Jackets issued to SHADAB TEAM'");

  // ---- test 4: this is generic across every PPE type, not hardcoded to
  // Jacket -- Helmet works exactly the same way ----
  typeSel.value = "Helmet";
  typeSel.dispatchEvent(new window.Event("change"));
  await flush(10);
  summary = document.getElementById("ppesummary");
  assert.strictEqual(summary.querySelector("b").textContent, "4", "FAIL: expected 4 helmets for SHADAB TEAM");
  assert.strictEqual(summary.querySelector("span").textContent, "Helmets issued to SHADAB TEAM");
  console.log("PASS: same mechanism works for Helmet (generic across every PPE type) — '4 / Helmets issued to SHADAB TEAM'");

  // ---- test 5: Shoes gets its own wording ("pairs of Shoes"), and a size
  // filter narrows the count further, exactly as asked ----
  typeSel.value = "Shoes";
  typeSel.dispatchEvent(new window.Event("change"));
  await flush(10);
  const sizeSel = document.getElementById("size");
  assert.ok(!sizeSel.hidden, "FAIL: size filter should appear once Shoes is selected");
  summary = document.getElementById("ppesummary");
  assert.strictEqual(summary.querySelector("b").textContent, "4", "FAIL: expected 4 pairs of shoes for SHADAB TEAM (any size)");
  assert.strictEqual(summary.querySelector("span").textContent, "pairs of Shoes issued to SHADAB TEAM");
  console.log("PASS: Shoes reads 'pairs of Shoes' — '4 / pairs of Shoes issued to SHADAB TEAM' (all sizes)");

  sizeSel.value = "7";
  sizeSel.dispatchEvent(new window.Event("change"));
  await flush(10);
  summary = document.getElementById("ppesummary");
  assert.strictEqual(summary.querySelector("b").textContent, "2", "FAIL: expected 2 pairs of size 7 shoes for SHADAB TEAM (AARIF, AFSAR)");
  assert.strictEqual(summary.querySelector("span").textContent, "pairs of size 7 Shoes issued to SHADAB TEAM");
  console.log("PASS: adding a shoe-size filter narrows further — '2 / pairs of size 7 Shoes issued to SHADAB TEAM'");

  // ---- test 6: clearing the contractor filter (back to all contractors)
  // widens the count back out, still scoped to size 7 shoes ----
  contractorSel.value = "";
  contractorSel.dispatchEvent(new window.Event("change"));
  await flush(10);
  summary = document.getElementById("ppesummary");
  assert.strictEqual(summary.querySelector("b").textContent, "3", "FAIL: expected 3 pairs of size 7 shoes across all contractors (2 SHADAB + 1 APEX)");
  assert.strictEqual(summary.querySelector("span").textContent, "pairs of size 7 Shoes issued");
  console.log("PASS: clearing the contractor filter widens back to '3 / pairs of size 7 Shoes issued' (all contractors)");

  // ---- test 7: switching back to "All types" hides the banner again ----
  typeSel.value = "";
  typeSel.dispatchEvent(new window.Event("change"));
  await flush(10);
  summary = document.getElementById("ppesummary");
  assert.ok(summary.hidden, "FAIL: summary should hide again once type is cleared");
  console.log("PASS: clearing the type filter hides the summary again");

  // ---- test 8: switching to another tab (e.g. Forecast) hides the PPE
  // summary so it never lingers on an unrelated view ----
  typeSel.value = "Jacket";
  typeSel.dispatchEvent(new window.Event("change"));
  await flush(10);
  assert.ok(!document.getElementById("ppesummary").hidden, "sanity: summary visible before switching away");
  const allServicesTab = [...document.querySelectorAll("#svc .tab")].find((b) => b.dataset.s === "");
  assert.ok(allServicesTab, "FAIL: 'All services' tab missing");
  allServicesTab.click();
  await flush(15);
  assert.ok(document.getElementById("ppesummary").hidden, "FAIL: summary must not linger visible after leaving the PPE tab");
  console.log("PASS: switching away from PPE hides the summary banner");
}

main().then(() => console.log("\nALL PPE SUMMARY TESTS PASSED")).catch((e) => { console.error("TEST FAILURE:", e.message, e.stack); process.exit(1); });
