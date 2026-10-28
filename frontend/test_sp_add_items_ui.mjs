// jsdom test: the "Add items" dialog (BOQ tab + stock tab). Payloads: real backend responses in activity_delete_fixture.json
// (Fire: items 1,2,3,5; Pumps = 2,3; Pipes = 3,5; Empty = none; item 1 is in no activity).
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

async function boot() {
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
const rows = (d) => [...d.querySelectorAll("#sp-maprows .sp-maprow")];
const codes = (d) => rows(d).map((r) => r.querySelector("input").dataset.c);
const ticked = (d) => rows(d).filter((r) => r.querySelector("input").checked).map((r) => r.querySelector("input").dataset.c);
async function open(window, d, act) { click(window, d.querySelector(`[data-additems="${act}"]`)); await flush(8); }
async function openStock(window, d, act) { await open(window, d, act); click(window, d.getElementById("sp-tab-stock")); await flush(10); }

async function main() {
  let { window, d, posts, st, quick } = await boot();
  // ---- the empty activity: nothing ticked, everything listed
  await open(window, d, "Empty");
  ok(/Add items → Empty/.test(T(d.querySelector("#sp-modal h2"))), "title: " + T(d.querySelector("#sp-modal h2")));
  ok(T(d.querySelector(".sp-srctabs .on")) === "From BOQ" && !!d.getElementById("sp-tab-stock"), "two source tabs, BOQ active");
  ok(JSON.stringify(codes(d)) === JSON.stringify(["1", "2", "3", "5"]) && ticked(d).length === 0, "all 4 BOQ items listed, none ticked");
  ok(/Not in any activity \(1\)/.test(T(d.getElementById("sp-f-un"))), "chip counts the real unassigned items: " + T(d.getElementById("sp-f-un")));
  ok(/0 selected/.test(T(d.getElementById("sp-mapcnt"))) && d.getElementById("sp-mapclr").hidden, "0 selected, Clear hidden");

  // ---- filter: not in any activity, then select all shown, save
  click(window, d.getElementById("sp-f-un")); await flush(2);
  ok(JSON.stringify(codes(d)) === JSON.stringify(["1"]) && d.getElementById("sp-f-un").getAttribute("aria-pressed") === "true", "filter shows only item 1");
  ok(/Select all shown \(1\)/.test(T(d.getElementById("sp-mapalllbl"))), "label counts what is shown");
  click(window, d.getElementById("sp-mapall")); await flush(2);
  ok(JSON.stringify(ticked(d)) === JSON.stringify(["1"]) && /1 selected/.test(T(d.getElementById("sp-mapcnt"))) && d.getElementById("sp-mapall").checked, "select all ticks it");
  click(window, d.getElementById("sp-modal-save")); await flush(15);
  ok(posts.length === 1 && posts[0].activity === "Empty" && posts[0].service === "Fire" && JSON.stringify(posts[0].codes) === JSON.stringify(["1"]), "saved through the same mapping endpoint: " + JSON.stringify(posts[0]));

  // ---- select all + search keeps hidden ticks (persistent Set) and never ticks what is not shown
  ({ window, d, posts, st, quick } = await boot());
  await open(window, d, "Empty");
  const search = d.getElementById("sp-mapsearch");
  search.value = "valve"; search.dispatchEvent(new window.Event("input", { bubbles: true })); await flush(2);
  ok(JSON.stringify(codes(d)) === JSON.stringify(["2", "5"]), "search 'valve' shows items 2 and 5: " + codes(d));
  click(window, d.getElementById("sp-mapall")); await flush(2);
  ok(/2 selected/.test(T(d.getElementById("sp-mapcnt"))), "select all ticks only the 2 shown");
  search.value = ""; search.dispatchEvent(new window.Event("input", { bubbles: true })); await flush(2);
  ok(JSON.stringify(ticked(d)) === JSON.stringify(["2", "5"]) && d.getElementById("sp-mapall").indeterminate, "after clearing the search: 2 and 5 still ticked, header shows partial");
  click(window, d.getElementById("sp-f-sel")); await flush(2);
  ok(JSON.stringify(codes(d)) === JSON.stringify(["2", "5"]), "Selected only lists exactly the ticked items");
  click(window, d.getElementById("sp-f-sel")); await flush(2);
  click(window, d.getElementById("sp-mapclr")); await flush(2);
  ok(ticked(d).length === 0 && /0 selected/.test(T(d.getElementById("sp-mapcnt"))), "Clear unticks everything");

  // ---- ticking works one box at a time, exactly as before (no range-select)
  let cbs = () => rows(d).map((r) => r.querySelector("input"));
  click(window, cbs()[0]); click(window, cbs()[2]); await flush(2);
  ok(JSON.stringify(ticked(d)) === JSON.stringify(["1", "3"]) && /2 selected/.test(T(d.getElementById("sp-mapcnt"))), "two single clicks tick exactly those two: " + ticked(d));

  // ---- switching tab: unsaved ticks ask first
  st.confirmAns = false; st.confirms = 0;
  click(window, d.getElementById("sp-tab-stock")); await flush(5);
  ok(st.confirms === 1 && /Add items → Empty/.test(T(d.querySelector("#sp-modal h2"))), "unsaved ticks + cancel: stays on the BOQ tab");
  st.confirmAns = true;
  click(window, d.getElementById("sp-tab-stock")); await flush(10);
  ok(/Add item from stock → Empty/.test(T(d.querySelector("#sp-modal h2"))) && !!d.getElementById("sp-tab-boq"), "confirm: opens the stock tab: " + T(d.querySelector("#sp-modal h2")));
  ok(T(d.querySelector(".sp-srctabs .on")) === "From stock register", "stock tab is the active one");
  ok(posts.length === 0, "nothing was saved by switching");
  click(window, d.getElementById("sp-tab-boq")); await flush(10);
  ok(/Add items → Empty/.test(T(d.querySelector("#sp-modal h2"))) && ticked(d).length === 0, "back on the BOQ tab");

  // ---- an activity that already has items: starts ticked, save REPLACES the set exactly as before
  ({ window, d, posts, st, quick } = await boot());
  await open(window, d, "Pumps");
  ok(JSON.stringify(ticked(d)) === JSON.stringify(["2", "3"]) && /2 selected/.test(T(d.getElementById("sp-mapcnt"))), "Pumps starts with 2 and 3 ticked");
  st.confirms = 0; click(window, d.getElementById("sp-tab-stock")); await flush(8);
  ok(st.confirms === 0 && /Add item from stock/.test(T(d.querySelector("#sp-modal h2"))), "no unsaved change: switching tab does not ask");
  click(window, d.getElementById("sp-tab-boq")); await flush(10);
  click(window, rows(d).find((r) => r.querySelector("input").dataset.c === "2").querySelector("input")); await flush(1);
  click(window, d.getElementById("sp-modal-save")); await flush(15);
  ok(posts.length === 1 && posts[0].activity === "Pumps" && JSON.stringify(posts[0].codes) === JSON.stringify(["3"]), "unticking 2 and saving sends exactly [3]: " + JSON.stringify(posts[0]));

  // (item 2 was just unticked from Pumps, so items 1 and 2 are now in no activity)
  // ---- ONE "+ Add items" per activity, at the bottom of the activity (where the two old buttons were)
  ok(!d.querySelector("[data-map]") && !d.querySelector("[data-quickadd]"), "no leftover '+ Add BOQ items' / '+ Add item' buttons");
  ok(d.querySelectorAll('[data-additems="Pumps"]').length === 1 && T(d.querySelector('[data-additems="Pumps"]')) === "+ Add items", "filled activity: exactly one '+ Add items'");
  ok(!!d.querySelector('[data-a="Pumps"] .sp-cbody .sp-addfoot [data-additems]') && !d.querySelector('[data-a="Pumps"] .sp-chd [data-additems]'), "it is inside the activity, not in the header next to rename / delete");
  ok(!d.querySelector('[data-a="Pumps"] .sp-addfoot .primary'), "filled activity: not emphasised");
  const eb = d.querySelector('[data-a="Empty"] .sp-addfoot .sp-addbtn');
  ok(d.querySelectorAll('[data-additems="Empty"]').length === 1 && eb.classList.contains("primary") && T(eb) === "+ Add items" && /No items in this activity yet/.test(T(d.querySelector('[data-a="Empty"] .sp-addfoot'))), "empty activity: the same single action, emphasised, with one line of text");
  click(window, d.querySelector('[data-additems="Pumps"]')); await flush(8);
  ok(/Add items → Pumps/.test(T(d.querySelector("#sp-modal h2"))), "the button opens the Add items dialog for that activity");
  click(window, d.getElementById("sp-modal-x")); await flush(3);

  // ---- stock tab: running count, added rows stay marked through a search, short copy
  ({ window, d, posts, st, quick } = await boot());
  await openStock(window, d, "Empty");
  ok(/Add item from stock → Empty/.test(T(d.querySelector("#sp-modal h2"))) && /no BOQ line needed/.test(T(d.querySelector("#sp-modal .sheethd p"))), "original explanation kept: " + T(d.querySelector("#sp-modal .sheethd p")));
  ok(T(d.getElementById("sp-quickcnt")) === "" && T(d.getElementById("sp-modal-save")) === "Done", "nothing added yet: no count, plain Done");
  const addBtn = (name) => d.querySelector(`[data-quickpick="${name}"]`);
  click(window, addBtn("1.0SQMM FRLS WIRE")); await flush(8);
  ok(T(d.getElementById("sp-quickcnt")) === "✓ 1 added to Empty", "count after one add: " + T(d.getElementById("sp-quickcnt")));
  ok(T(d.getElementById("sp-modal-save")) === "Done (1 added)", "Done button shows the count: " + T(d.getElementById("sp-modal-save")));
  ok(quick.length === 1 && quick[0].activity === "Empty" && quick[0].material === "1.0SQMM FRLS WIRE" && quick[0].service === "Fire", "same quick-item endpoint: " + JSON.stringify(quick[0]));
  ok(d.querySelector('[data-mat="1.0SQMM FRLS WIRE"]').classList.contains("sp-just") && T(addBtn("1.0SQMM FRLS WIRE")) === "✓ added", "the row is marked as added");
  const qs = d.getElementById("sp-quicksearch"); qs.value = "wire"; qs.dispatchEvent(new window.Event("input", { bubbles: true })); await flush(3);
  ok(d.querySelector('[data-mat="1.0SQMM FRLS WIRE"]').classList.contains("sp-just") && T(addBtn("1.0SQMM FRLS WIRE")) === "✓ added", "still marked after a search re-render");
  const grp = d.querySelector("[data-quickpick-group]"); ok(!!grp, "the 2 coil sizes are one grouped row");
  click(window, grp); await flush(8);
  ok(quick.length === 2 && quick[1].materials.length === 2 && T(d.getElementById("sp-quickcnt")) === "✓ 2 added to Empty", "a group counts as ONE added item: " + JSON.stringify(quick[1]));
  qs.value = ""; qs.dispatchEvent(new window.Event("input", { bubbles: true })); await flush(3);
  ok(T(addBtn("1.5SQMM FRLS WIRE BLACK (90 MTR/COIL)")) === "✓ added" && T(addBtn("1.5SQMM FRLS WIRE BLACK (270 MTR/COIL)")) === "✓ added" && T(addBtn("10A PVC CONNECTOR")) === "+ Add", "after the re-render the group members are marked, an untouched material is not");
  ok(T(d.getElementById("sp-quickcnt")) === "✓ 2 added to Empty", "count survives the re-render");
  click(window, d.getElementById("sp-modal-save")); await flush(10);
  ok(!d.getElementById("sp-modal"), "Done closes the dialog");

  console.log(fail ? `FAIL: ${pass} passed, ${fail} failed` : `PASS ${pass} checks`);
  process.exit(fail ? 1 : 0);
}
main().catch((e) => { console.error("TEST FAILURE:", e); process.exit(1); });
