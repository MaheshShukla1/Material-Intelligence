// jsdom test: the Zones dialog of a counted item with only a few pieces (the reported case: 5 Nos over 24 zones is handed out 1,1,1,1,1,0,0...).
// Nothing about the dialog's logic changes: still no quantity box and no Whole switch; it now SAYS why, and where the total is typed.
// Payloads: real backend responses in activity_delete_fixture.json (rooms roo2, roo3, roo5, roo6; item 2 = Nos, item 3 = Rmt, both in Pumps).
import { JSDOM } from "jsdom";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
let pass = 0, fail = 0;
function ok(cond, msg) { if (cond) pass++; else { fail++; console.error("FAIL:", msg); } }
const FX = JSON.parse(fs.readFileSync(path.join(__dirname, "activity_delete_fixture.json"), "utf8"));
const clone = (x) => JSON.parse(JSON.stringify(x));
const flush = async (n = 8) => { for (let i = 0; i < n; i++) await new Promise((r) => setTimeout(r, 0)); };
const SL = FX.slug, SP = "/api/siteprogress/" + SL;
const T = (el) => (el ? el.textContent : "").replace(/\s+/g, " ").trim();
const click = (w, el, init) => el.dispatchEvent(new w.MouseEvent("click", { bubbles: true, cancelable: true, ...(init || {}) }));

async function boot(PLAN) {
  const html = `<!doctype html><html><body>
  <header class="top" id="top"><div class="brand"><h1>Material intelligence</h1><p id="ctx"></p></div>
    <div class="topact"><button id="pick"></button><button id="sync"></button><button id="syncgo"></button><button id="home" hidden></button><input id="file" type="file" hidden></div></header>
  <main><section id="drop"></section><section id="busy" hidden></section><section id="report" hidden><div class="bar"><div class="tabs" id="svc"></div></div></section></main></body></html>`;
  const dom = new JSDOM(html, { url: "http://localhost/", runScripts: "outside-only", pretendToBeVisual: true });
  const { window } = dom; const posts = [], quick = []; const st = { confirmAns: true, confirms: 0 };
  const view = clone(FX.view_with_empty);
  window.confirm = () => { st.confirms++; return st.confirmAns; };
  window.alert = () => {}; window.prompt = () => null;
  window.fetch = async (url, o) => {
    const u = new URL(url, "http://localhost"), method = (o && o.method) || "GET";
    let data = {};
    if (u.pathname === "/api/projects") data = FX.projects;
    else if (u.pathname === SP) data = FX.project;
    else if (u.pathname === SP + "/service/Fire") data = clone(view);
    else if (u.pathname === SP + "/pnl/Fire") data = clone(FX.pnl);
    else if (u.pathname === SP + "/item-room-plan") data = PLAN(u.searchParams.get("item_code"));
    else if (u.pathname.endsWith("/candidates")) data = { available: true, materials: [
      { name: "1.0SQMM FRLS WIRE", unit: "MTR", already: false, batch_siblings: [] },
      { name: "1.5SQMM FRLS WIRE BLACK (90 MTR/COIL)", unit: "MTR", already: false, batch_siblings: ["1.5SQMM FRLS WIRE BLACK (270 MTR/COIL)"] },
      { name: "1.5SQMM FRLS WIRE BLACK (270 MTR/COIL)", unit: "MTR", already: false, batch_siblings: ["1.5SQMM FRLS WIRE BLACK (90 MTR/COIL)"] },
      { name: "10A PVC CONNECTOR", unit: "NOS", already: false, batch_siblings: [] }] };
    else if (method === "POST" && u.pathname === SP + "/quick-item") { quick.push(JSON.parse(o.body)); data = clone(view); }
    else if (method === "POST" && u.pathname === SP + "/mapping") { const b = JSON.parse(o.body); posts.push(b); view.mapping[b.activity] = b.codes; data = clone(view); }
    return { ok: true, status: 200, json: async () => data, text: async () => JSON.stringify(data) };
  };
  window.eval(fs.readFileSync(path.join(__dirname, "siteprogress.js"), "utf8"));
  Object.defineProperty(window.document, "readyState", { value: "complete", configurable: true });
  window.document.dispatchEvent(new window.Event("DOMContentLoaded"));
  await flush();
  window.document.querySelector('.spnav button[data-v="siteprogress"]').click();
  await flush(25);
  return { window, d: window.document, posts, st, quick };
}

const ROOMS = ["roo2", "roo3", "roo5", "roo6"];
const planOf = (shares, unit) => () => ({ unit, pools: [], free: false, rooms: Object.fromEntries(ROOMS.map((r, i) => [r, { planned: shares[i], frac: 0 }])) });
async function openZones(window, d, code) { click(window, d.querySelector(`[data-roomsedit="${code}"]`)); await flush(15); }
const hint = (d) => T(d.getElementById("sp-zhelp"));

async function main() {
  // the reported shape: a few Nos handed out 1 / 1 / none / none
  let { window, d } = await boot(planOf([1, 1, null, null], "Nos"));
  await openZones(window, d, "2");
  ok(d.querySelectorAll("#sp-modal input[data-roomqty]").length === 0 && d.querySelectorAll("#sp-modal .sp-areaseg").length === 0, "few pieces: still no quantity box and no Whole switch (logic unchanged)");
  ok(d.querySelectorAll("#sp-modal input[data-room]").length === 4, "all 4 zones can still be ticked");
  ok(/2 Nos over 4 \w+s: each \w+ gets 1 or none, so there is no quantity box here/.test(hint(d)), "the dialog says why: " + hint(d));
  ok(/type it in “Installed” on the item row/.test(hint(d)) && /A tick means 1 done/.test(hint(d)), "and where the total goes: " + hint(d));
  ok(d.querySelectorAll("#sp-zhelp").length === 1 && !!d.querySelector(".sp-planbox") && d.getElementById("sp-zhelp").nextElementSibling === d.querySelector(".sp-planbox"), "one line, placed above the planned-quantity box");
  // every zone exactly 1 (4 Nos over 4 zones): same, still tick-only
  ({ window, d } = await boot(planOf([1, 1, 1, 1], "Nos"))); await openZones(window, d, "2");
  ok(/4 Nos over 4/.test(hint(d)), "1 per zone: " + hint(d));
  // a bigger share per zone: boxes AND the Whole switch appear, and there is NO hint (nothing to explain)
  ({ window, d } = await boot(planOf([25, 25, 25, 25], "Nos"))); await openZones(window, d, "2");
  ok(d.querySelectorAll("#sp-modal input[data-roomqty]").length === 4 && d.querySelectorAll("#sp-modal .sp-areaseg").length >= 1, "25 per zone: quantity boxes and the Whole switch are there");
  ok(!/there is no quantity box here/.test(hint(d)) && /Tick = the full planned quantity/.test(hint(d)), "25 per zone: the usual help line, not the few-pieces one: " + hint(d));
  // a length item (Rmt) is never a few-pieces case
  ({ window, d } = await boot(planOf([1, 1, null, null], "Rmt"))); await openZones(window, d, "3");
  ok(!d.getElementById("sp-zhelp") || !/there is no quantity box here/.test(hint(d)), "Rmt item: no few-pieces line");
  // the plan is removed (free): unchanged, no few-pieces line
  ({ window, d } = await boot(() => ({ unit: "Nos", pools: [], free: true, total: 2, used: 0, rooms: Object.fromEntries(ROOMS.map((r, i) => [r, { planned: i < 2 ? 1 : null, frac: 0 }])) }))); await openZones(window, d, "2");
  ok(!/there is no quantity box here/.test(hint(d)), "plan removed: no few-pieces line");

  console.log(fail ? `FAIL: ${pass} passed, ${fail} failed` : `PASS ${pass} checks`);
  process.exit(fail ? 1 : 0);
}
main().catch((e) => { console.error("TEST FAILURE:", e); process.exit(1); });
