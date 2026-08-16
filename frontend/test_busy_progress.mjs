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
window.alert = () => {};
window.confirm = () => true;

// A controllable fetch: /api/upload and /api/sync-sheet don't resolve until
// the test explicitly releases them, so the animated (estimated) progress
// can be observed mid-flight before completion is driven by the real
// response -- exactly the real timing this feature depends on.
let releaseUpload, releaseSync;
const uploadPromise = new Promise((res) => { releaseUpload = res; });
const syncPromise = new Promise((res) => { releaseSync = res; });

function route(url) {
  const u = String(url);
  if (u.includes("/api/projects")) return { ok: true, json: async () => [] };
  if (u.includes("/api/subcategories")) return { ok: true, json: async () => ({ all: [], by_service: {} }) };
  if (u.includes("/api/forecast")) return { ok: true, json: async () => [] };
  if (u.includes("/api/run/")) return { ok: true, json: async () => ({ meta: {}, summary: {} }) };
  return { ok: true, json: async () => ({}) };
}
window.fetch = global.fetch = async (url, opts) => {
  const u = String(url);
  if (u.includes("/api/upload")) { await uploadPromise; return { ok: true, json: async () => ({ run_id: "run1", meta: bootMeta(), summary: bootSummary() }) }; }
  if (u.includes("/api/sync-sheet")) { await syncPromise; return { ok: true, json: async () => ({ run_id: "run1", meta: bootMeta(), summary: bootSummary() }) }; }
  return route(url);
};
function bootMeta() {
  return { project: "Fixture Hotel", filename: "google-sheet.xlsx", source: "site register",
           created: new Date().toISOString(), stats: { asof: "2026-08-14", materials: 385 },
           lead_time: 7, has_ppe: false, issues: [], mapping: [] };
}
function bootSummary() { return { services: [], counts: {}, overdue_orders: 0, idle_lines: 0 }; }

const src = fs.readFileSync(new URL("../frontend/app.js", import.meta.url), "utf8");
window.eval(src);
async function flush(n = 5) { for (let i = 0; i < n; i++) await new Promise((r) => setTimeout(r, 0)); }

async function main() {
  await flush(10);

  // ---- test 1: the old bare spinner element is gone; the new ring markup exists ----
  assert.ok(!document.querySelector(".spin"), "FAIL: old .spin element should be removed");
  assert.ok(document.getElementById("busy-arc"), "FAIL: #busy-arc (progress ring) missing");
  assert.ok(document.getElementById("busy-steps"), "FAIL: #busy-steps (stepper) missing");
  assert.ok(document.getElementById("busy-eta"), "FAIL: #busy-eta missing");
  console.log("PASS: old bare spinner replaced with the ring + stepper markup");

  // ---- test 2: starting an upload shows the 5-stage checklist with the
  // upload-specific label on stage 1, ring starts at 0% ----
  const file = { name: "stock.xlsx", size: 2 * 1024 * 1024 };   // 2MB, realistic
  window.send(file);
  await flush(5);
  const steps = [...document.querySelectorAll(".busy-steplabel")];
  assert.strictEqual(steps.length, 5, "FAIL: expected 5 stages, got " + steps.length);
  assert.strictEqual(steps[0].textContent, "Uploading your file", "FAIL: upload should relabel stage 1, got: " + steps[0].textContent);
  assert.strictEqual(steps[4].textContent, "Done");
  assert.strictEqual(document.getElementById("busy-pct").textContent, "0%");
  assert.ok(!document.getElementById("busy").hidden, "FAIL: busy section should be visible during upload");
  console.log("PASS: upload shows the 5-stage checklist ('Uploading your file' → ... → 'Done'), ring starts at 0%");

  // ---- test 3: while genuinely waiting (server hasn't responded yet), the
  // ring advances on its own and never reaches 100% -- it's an estimate,
  // capped below completion until the real response arrives ----
  await new Promise((r) => setTimeout(r, 400));
  const pctMidway = Number(document.getElementById("busy-pct").textContent.replace("%", ""));
  assert.ok(pctMidway > 0, "FAIL: ring should have advanced past 0% while waiting");
  assert.ok(pctMidway <= 92, "FAIL: animated progress must never exceed the 92% cap before real completion, got " + pctMidway);
  assert.ok(document.getElementById("busy-eta").textContent.length > 0, "FAIL: an ETA should be shown while waiting");
  console.log(`PASS: ring advances on its own while waiting (currently ${pctMidway}%), capped at 92% -- never claims done early`);

  // ---- test 4: releasing the real fetch response snaps straight to 100% /
  // "Done", regardless of where the estimate currently sat -- completion is
  // driven by the real result, never by the clock ----
  releaseUpload();
  await flush(15);
  assert.strictEqual(document.getElementById("busy-pct").textContent, "100%", "FAIL: should snap to 100% the moment the real response arrives");
  const doneIcon = document.querySelector('.busy-stepicon[data-i="4"]');
  assert.ok(doneIcon.classList.contains("current") || doneIcon.classList.contains("done"),
    "FAIL: the Done step should be marked reached");
  console.log("PASS: the real response arriving snaps the ring straight to 100% / Done, regardless of the estimate's position");

  // ---- test 5: a bigger file gets a bigger (but capped) time estimate than
  // a small one -- the estimate is informed by the real file, not a fixed
  // arbitrary number for every upload ----
  const smallEstimate = window.busyEstimateMs(50 * 1024);          // 50KB
  const bigEstimate = window.busyEstimateMs(20 * 1024 * 1024);     // 20MB
  const hugeEstimate = window.busyEstimateMs(500 * 1024 * 1024);   // 500MB (should hit the cap)
  assert.ok(bigEstimate > smallEstimate, "FAIL: a 20MB file should estimate longer than a 50KB file");
  assert.ok(hugeEstimate <= 25000, "FAIL: the estimate must stay capped (25s) even for an absurdly large file");
  assert.ok(smallEstimate >= 4000, "FAIL: the estimate must have a sane floor (4s) even for a tiny file");
  console.log(`PASS: estimate scales with real file size (50KB→${smallEstimate}ms, 20MB→${bigEstimate}ms), capped 4-25s`);

  // ---- test 6: sync (no file, no size signal) uses the default label,
  // unchanged copy, and a flat typical-duration estimate ----
  window.syncFromSheet("https://docs.google.com/x", "Fixture Hotel");
  await flush(5);
  const syncSteps = [...document.querySelectorAll(".busy-steplabel")];
  assert.strictEqual(syncSteps[0].textContent, "Fetching your Google Sheet",
    "FAIL: sync should keep the original stage-1 label, got: " + syncSteps[0].textContent);
  releaseSync();
  await flush(15);
  assert.strictEqual(document.getElementById("busy-pct").textContent, "100%");
  console.log("PASS: sync flow keeps its own stage-1 label ('Fetching your Google Sheet') and also completes on the real response");

  // ---- test 7: switching to an already-loaded project does NOT show the
  // staged checklist (nothing is actually being re-parsed) -- indeterminate
  // ring instead, no fabricated stages ----
  document.getElementById("report").hidden = false;
  window.switchProject("run1");
  await flush(10);
  assert.ok(document.getElementById("busy-ring").classList.contains("indeterminate"),
    "FAIL: switching projects should use the indeterminate ring, not the staged one");
  assert.strictEqual(document.getElementById("busy-steps").innerHTML, "", "FAIL: no stage checklist should render for a project switch");
  assert.strictEqual(document.getElementById("busytxt").textContent, "Loading project…");
  console.log("PASS: switching to an already-loaded project shows a plain indeterminate ring, not fabricated upload/sync stages");
}

main().then(() => console.log("\nALL BUSY-PROGRESS TESTS PASSED")).catch((e) => { console.error("TEST FAILURE:", e.message, e.stack); process.exit(1); });
