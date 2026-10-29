// jsdom test: typing MORE than a zone's equal share in the Zones dialog (the reported case: 15.1.2, 110 Rmt over 24 zones = 4.58 a zone;
// a zone that really got more was cut back to 4.58). The share is the item's total split equally -- nobody typed it -- so the dialog now
// offers the item's own "remove the plan" (typed quantities count as typed, the item's total stays the limit) at that moment.
// Rooms roo2, roo3 (floor 1), roo5, roo6 (floor 2); item 3 = Rmt, planned 20 here = 5 a zone. Payloads: activity_delete_fixture.json.
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

async function boot(PLAN, mut) {
  const html = `<!doctype html><html><body>
  <header class="top" id="top"><div class="brand"><h1>Material intelligence</h1><p id="ctx"></p></div>
    <div class="topact"><button id="pick"></button><button id="sync"></button><button id="syncgo"></button><button id="home" hidden></button><input id="file" type="file" hidden></div></header>
  <main><section id="drop"></section><section id="busy" hidden></section><section id="report" hidden><div class="bar"><div class="tabs" id="svc"></div></div></section></main></body></html>`;
  const dom = new JSDOM(html, { url: "http://localhost/", runScripts: "outside-only", pretendToBeVisual: true });
  const { window } = dom; const posts = [], quick = [], off = []; const st = { confirmAns: true, confirms: 0, msgs: [], free: false, toasts: [] };
  const view = clone(FX.view_with_empty); if (mut) mut(view);
  window.confirm = (m) => { st.confirms++; st.msgs.push(String(m)); return st.confirmAns; };
  window.alert = () => {}; window.prompt = () => null;
  window.fetch = async (url, o) => {
    const u = new URL(url, "http://localhost"), method = (o && o.method) || "GET";
    let data = {};
    if (u.pathname === "/api/projects") data = FX.projects;
    else if (u.pathname === SP) data = FX.project;
    else if (u.pathname === SP + "/service/Fire") data = clone(view);
    else if (u.pathname === SP + "/pnl/Fire") data = clone(FX.pnl);
    else if (u.pathname === SP + "/item-room-plan") data = PLAN(u.searchParams.get("item_code"), st);
    else if (method === "POST" && u.pathname === SP + "/item-plan-off") { off.push(JSON.parse(o.body)); st.free = JSON.parse(o.body).off; data = clone(view); }
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
  return { window, d: window.document, posts, st, quick, off, view };
}


const ROOMS = ["roo2", "roo3", "roo5", "roo6"];
const plan = (free) => (code, st) => ({ unit: "Rmt", pools: [], free: !!(free || st.free), total: 20, used: 0, rooms: Object.fromEntries(ROOMS.map((r) => [r, { planned: 5, frac: 0 }])) });
async function openZones(window, d, code) { click(window, d.querySelector(`[data-roomsedit="${code}"]`)); await flush(15); }
const box = (d, room) => d.querySelector(`#sp-modal input[data-roomqty="${room}"]`);
const type = async (window, el, v) => { el.value = String(v); el.dispatchEvent(new window.Event("input", { bubbles: true })); el.dispatchEvent(new window.Event("change", { bubbles: true })); await flush(15); };
const toastText = (d) => T(d.querySelector(".toast, #sp-toast, [role=status].toast")) ;

async function main() {
  // 1. the reported case: a zone gets more than its equal share
  let { window, d, st, off } = await boot(plan(false));
  await openZones(window, d, "3");
  ok(!!box(d, "roo2") && box(d, "roo2").max === "5", "zone box is there, capped at the equal share 5");
  await type(window, box(d, "roo2"), 12);
  ok(st.confirms === 1, "typing 12 (share 5) asks once: " + st.confirms);
  ok(/You typed 12 Rmt for .*equal share of this item is only 5 Rmt/.test(st.msgs[0]) && /20 Rmt split equally; nobody typed it/.test(st.msgs[0]), "says what happened: " + st.msgs[0]);
  ok(/Allow any quantity per \w+\? The item's total stays the limit/.test(st.msgs[0]) && /plain tick no longer counts/.test(st.msgs[0]), "and what changes: " + st.msgs[0]);
  ok(off.length === 1 && off[0].off === true && off[0].item_code === "3" && off[0].service === "Fire", "yes -> the item's own remove-plan route: " + JSON.stringify(off[0]));
  await flush(20);
  ok(!!box(d, "roo2") && box(d, "roo2").getAttribute("max") === null, "the dialog reopened with the plan removed: a box takes any quantity now");

  // 2. said no: the number is cut to the share, exactly as before, with the same message
  ({ window, d, st, off } = await boot(plan(false))); st.confirmAns = false;
  await openZones(window, d, "3"); await type(window, box(d, "roo3"), 12);
  ok(st.confirms === 1 && off.length === 0 && box(d, "roo3").value === "5", "no -> nothing sent, box back at 5: " + box(d, "roo3").value);
  ok(/Planned here is 5 Rmt\. To record more, raise the plan first\./.test(d.body.textContent), "and the old message is shown");

  // 3. within the share: nothing asked, nothing changes
  ({ window, d, st, off } = await boot(plan(false)));
  await openZones(window, d, "3"); await type(window, box(d, "roo2"), 4);
  ok(st.confirms === 0 && off.length === 0 && box(d, "roo2").value === "4", "4 of 5: no question, kept");

  // 4. above the item's whole total (20): free mode would refuse it anyway, so no offer, old message
  ({ window, d, st, off } = await boot(plan(false)));
  await openZones(window, d, "3"); await type(window, box(d, "roo2"), 25);
  ok(st.confirms === 0 && off.length === 0 && box(d, "roo2").value === "5" && /Planned here is 5 Rmt/.test(d.body.textContent), "25 > the item's 20: no offer, cut to 5");

  // 5. the plan is already removed: typed quantity above 5 stays as typed, no question
  ({ window, d, st, off } = await boot(plan(true)));
  await openZones(window, d, "3"); await type(window, box(d, "roo2"), 12);
  ok(st.confirms === 0 && off.length === 0 && box(d, "roo2").value === "12", "plan removed: 12 stays 12");

  // 6. the item's plan is quantity groups (typed per zone): they have their own Remove, no offer
  ({ window, d, st, off } = await boot(plan(false), (v) => { v.item_room_qty = { "3": [{ qty: 5, rooms: ["roo2", "roo3"] }] }; }));
  await openZones(window, d, "3"); await type(window, box(d, "roo2"), 12);
  ok(st.confirms === 0 && off.length === 0 && box(d, "roo2").value === "5", "quantity groups: no offer, cut to 5 as before");
  // 7. Whole level: a total above the level's share (2 zones x 5 = 10) offers the same
  ({ window, d, st, off } = await boot(plan(false)));
  await openZones(window, d, "3");
  const seg = d.querySelector("#sp-modal .sp-areaseg button[data-m=area]"); click(window, seg); await flush(3);
  const wb = d.querySelector("#sp-modal input[data-areaqty]"); await type(window, wb, 15);
  ok(st.confirms === 1 && /You typed 15 Rmt for Whole .*equal share of this item is only 10 Rmt/.test(st.msgs[0]), "Whole level 15 > 10: " + st.msgs[0]);
  ok(off.length === 1 && off[0].off === true, "yes -> remove-plan route");

  console.log(fail ? `FAIL: ${pass} passed, ${fail} failed` : `PASS ${pass} checks`);
  process.exit(fail ? 1 : 0);
}
main().catch((e) => { console.error("TEST FAILURE:", e); process.exit(1); });
