// jsdom test: Site Progress > Measurement (the grid's reading side: numbers, evidence, hover, search, close; measureview.js). Editing: test_sp_measure_grid_edit.mjs.
// Reported scenario (Thoth Mall, Fire > Piping Work): the grid was empty ("-" everywhere) although quantities were recorded, and "BOQ qty" was
// really the edited planned quantity. The payloads are the REAL backend responses of GET /measure-grid/{service} (measure_grid_fixture.json,
// made by the real router: 39b = 37 + 9.4 m measured in B3 > Zone 2 and 58 m typed in B3 > Zone 5; 39a filled to its plan of 5,120 over a BOQ of 4,200).
import { JSDOM } from "jsdom";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
let pass = 0, fail = 0;
function ok(cond, msg) { if (cond) pass++; else { fail++; console.error("FAIL:", msg); } }
const FX = JSON.parse(fs.readFileSync(path.join(__dirname, "activity_delete_fixture.json"), "utf8"));
const MG = JSON.parse(fs.readFileSync(path.join(__dirname, "measure_grid_fixture.json"), "utf8"));
const clone = (x) => JSON.parse(JSON.stringify(x));
const flush = async (n = 8) => { for (let i = 0; i < n; i++) await new Promise((r) => setTimeout(r, 0)); };
const SL = FX.slug, SP = "/api/siteprogress/" + SL;
const T = (el) => (el ? el.textContent : "").replace(/\s+/g, " ").trim();
const click = (w, el) => el.dispatchEvent(new w.MouseEvent("click", { bubbles: true, cancelable: true }));
const press = (w, el) => { for (const k of ["mousedown", "mouseup", "click"]) el.dispatchEvent(new w.MouseEvent(k, { bubbles: true, cancelable: true })); };

async function boot(opts = {}) {
  const html = `<!doctype html><html><body>
  <header class="top" id="top"><div class="brand"><h1>Material intelligence</h1><p id="ctx"></p></div>
    <div class="topact"><button id="pick"></button><button id="sync"></button><button id="syncgo"></button><button id="home" hidden></button><input id="file" type="file" hidden></div></header>
  <main><section id="drop"></section><section id="busy" hidden></section><section id="report" hidden><div class="bar"><div class="tabs" id="svc"></div></div></section></main></body></html>`;
  const dom = new JSDOM(html, { url: "http://localhost/", runScripts: "outside-only", pretendToBeVisual: true });
  const { window } = dom; const gets = [];
  const project = clone(FX.project);
  if (opts.structure) project.structure = clone(opts.structure);
  window.confirm = () => true; window.alert = () => {}; window.prompt = () => null;
  window.fetch = async (url, o) => {
    const u = new URL(url, "http://localhost"), method = (o && o.method) || "GET";
    let data = {}, status = 200;
    gets.push(method + " " + u.pathname + u.search);
    if (u.pathname === "/api/projects") data = FX.projects;
    else if (u.pathname === SP) data = project;
    else if (u.pathname === SP + "/service/Fire") data = clone(FX.view_with_empty);
    else if (u.pathname === SP + "/pnl/Fire") data = clone(FX.pnl);
    else if (u.pathname === SP + "/measure-grid/Fire") {
      if (opts.fail) { status = 500; data = { detail: "no BOQ items for service 'Fire'" }; } else data = clone(opts.grid);
    }
    return { ok: status < 400, status, json: async () => data, text: async () => JSON.stringify(data) };
  };
  window.eval(fs.readFileSync(path.join(__dirname, "siteprogress.js"), "utf8"));
  window.eval(fs.readFileSync(path.join(__dirname, "measureview.js"), "utf8"));
  Object.defineProperty(window.document, "readyState", { value: "complete", configurable: true });
  window.document.dispatchEvent(new window.Event("DOMContentLoaded"));
  await flush();
  window.document.querySelector('.spnav button[data-v="siteprogress"]').click();
  await flush(25);
  const d = window.document;
  const sr = () => { const h = d.getElementById("mv-host"); return h ? h.shadowRoot : null; };
  const q = (s) => sr().querySelector(s), qa = (s) => [...sr().querySelectorAll(s)];
  const rowOf = (code) => qa("tr[data-code]").find((r) => r.dataset.code === code);
  return { window, d, gets, sr, q, qa, rowOf };
}

async function main() {
  // ---- 1. the Measurement button opens the view directly (no menu of unfinished Export / Import entries)
  let t = await boot({ structure: MG.thoth.structure, grid: MG.thoth.fire });
  const btn = t.d.getElementById("sp-measgrid");
  ok(btn && T(btn) === "Measurement" && !btn.hasAttribute("aria-haspopup"), "toolbar: plain Measurement button, no menu: " + T(btn));
  ok(!t.gets.some((g) => g.includes("measure-grid")), "nothing is fetched until the button is pressed");
  click(t.window, btn); await flush(10);
  ok(t.gets.filter((g) => g === "GET " + SP + "/measure-grid/Fire").length === 1, "one read of the real route: " + t.gets.filter((g) => g.includes("measure")).join(","));
  ok(!!t.sr(), "the view opened");
  ok(/Measurement/.test(T(t.q(".top h1"))) && /Fire · 7 BOQ lines/.test(T(t.q(".top p"))), "title / subtitle: " + T(t.q(".top"))) ;
  ok(/All changes saved/.test(T(t.q("#state"))) && !/View only/.test(T(t.sr())), "state pill says saved; it is no longer a view-only screen");

  // ---- 1b. a light screen: lines with nothing recorded are hidden until asked for, the place can be picked, no strips of status text
  ok(t.qa("tr[data-code]").map((r) => r.dataset.code).join() === "39a,39b" && T(t.q("#cnt")) === "2 of 7 lines", "default: only the lines with work (39a, 39b), and it says 2 of 7: " + T(t.q("#cnt")));
  ok(t.q("#ow").getAttribute("aria-pressed") === "true" && t.q("#dock").hidden, "the details panel is closed until asked for");
  ok(!t.q("#hov") && !t.q("#basis") && !t.q(".status") && !t.q("#ask") && !t.q(".legend"), "no hover strip, note, status bar, question bar or legend text");
  click(t.window, t.q("#ow"));
  ok(t.qa("tr[data-code]").length === 7 && T(t.q("#cnt")) === "7 lines" && t.q("#ow").getAttribute("aria-pressed") === "false", "Only lines with work OFF: all 7 lines: " + T(t.q("#cnt")));

  // ---- 2. the real scenario: levels collapsed, numbers are Site Progress's own
  ok(t.qa("th.grp").length === 3 && ["B3", "B2", "B1"].every((n, i) => T(t.qa("th.grp")[i]).includes(n)), "three levels as column groups: " + t.qa("th.grp").map(T).join("|"));
  const r39b = t.rowOf("39b"), cells = (r) => [...r.querySelectorAll("td")].map(T);
  let c = cells(r39b);
  // Sr | line | unit | B3 | B2 | B1 | BOQ qty | Site plan | To date | Pending
  ok(c[0] === "39b" && /100 mm/.test(c[1]) && c[2] === "Rmt", "39b row labels: " + c.slice(0, 3));
  ok(c[3] === "104.4" && c[4] === "0" && c[5] === "0", "B3 collapsed = 46.4 + 58 = 104.4; B2 / B1 apply but nothing is recorded there yet = 0 (blank would mean 'not in this level'): " + c.slice(3, 6));
  ok(c[6] === "1,947.5" && c[7] === "1,259.5" && c[8] === "104.4" && c[9] === "1,843.1", "BOQ qty 1,947.5 / Site plan 1,259.5 / To date 104.4 / Pending 1,843.1: " + c.slice(6));
  ok(!/▲/.test(c[7]), "plan below BOQ: no warning");
  const r39a = t.rowOf("39a"); c = cells(r39a);
  ok(c[6] === "4,200" && /▲\s*5,120/.test(c[7]) && c[8] === "5,120" && c[9] === "0", "39a: BOQ 4,200, plan 5,120 flagged, done 5,120, pending floors at 0: " + c.slice(6));
  ok(r39a.querySelector("td.q1").classList.contains("warn") && r39a.querySelector("td.q3").classList.contains("warn"), "plan above BOQ and done above BOQ are both marked");
  ok(/BOQ's own quantity/.test(t.q("th.q0").title) && /Site Progress works with/.test(t.q("th.q1").title), "the columns explain themselves on hover (no note strip): " + t.q("th.q0").title);

  // ---- 3. a row opens its details under the grid: location split, evidence, latest entries
  ok(t.q("#dock").hidden, "details are closed by default"); click(t.window, t.q("#details"));
  ok(!t.q("#dock").hidden && /Click a box to see its line here/.test(T(t.q("#dock"))), "Details opens them: before any click, a hint");
  press(t.window, t.rowOf("39b").querySelector("td.rl"));
  let dock = T(t.q("#dock"));
  ok(/b\) 100 mm dia/.test(dock) && /104\.4 Rmt to date/.test(dock) && /5\.4% of BOQ/.test(dock), "details: headline vs BOQ: " + dock.slice(0, 160));
  ok(/BY LOCATIONB3104\.4B20B10EVIDENCE/.test(dock), "by location: B3 holds 104.4, B2 and B1 are 0: " + dock);
  ok(/46\.4 Rmt backed by 2 measurement entries/.test(dock), "evidence: measured part");
  ok(/58 Rmt recorded without entries \(zone tick or typed quantity\)/.test(dock), "evidence: the typed part is NOT passed off as measured");
  ok(/Riser 2 → Zone 2 end · 9\.4/.test(dock) && /FACP-1 → Riser 2 · 37/.test(dock) && /B3 ▸ Zone 2/.test(dock), "latest entries, newest first, with their level: " + dock);
  ok(dock.indexOf("Zone 2 end") < dock.indexOf("FACP-1"), "newest entry first");

  // ---- 4. expand a level: zones appear; a zone cell shows its own quantity; clicking it names the place
  click(t.window, t.q("#expand"));
  ok(t.qa("th.leaf").length === 21, "expand all: 21 zone columns: " + t.qa("th.leaf").length);
  const zr = t.rowOf("39b"); const zc = [...zr.querySelectorAll("td.c")].map(T);
  ok(zc[1] === "46.4" && zc[4] === "58" && zc[0] === "0" && zc.length === 21, "zone cells: Zone 2 = 46.4, Zone 5 = 58, an untouched zone = 0: " + zc.join(","));
  ok(zr.querySelectorAll("td.c.zero").length === 19, "zones with 0 are muted");
  press(t.window, zr.querySelectorAll("td.c")[1]);
  dock = T(t.q("#dock"));
  ok(/46\.4 Rmt backed by 2 measurement entries/.test(dock), "cell click opens its line in the details: " + dock.slice(0, 120));
  ok(/B3 ▸ Zone 2/.test(T(t.q("#nm"))) && t.q("#fx").value === "46.4", "the formula bar names the place and holds the quantity: " + T(t.q("#nm")) + " / " + t.q("#fx").value);
  ok(t.rowOf("39b").querySelectorAll("td.c.sel").length === 1, "exactly one selected cell");

  // ---- 4b. hover, like a sheet: the row and the column under the mouse light up, and a status line names them
  const over = (el) => el.dispatchEvent(new t.window.MouseEvent("mouseover", { bubbles: true }));
  const R = (code) => t.rowOf(code);
  over(R("39c").querySelectorAll("td.c")[1]);                                    // 39c > B3 > Zone 2
  ok(R("39c").classList.contains("hv") && t.qa("tr.hv").length === 1, "hover: that row is lit");
  ok(t.qa("td.c.hc").length === 7 && t.qa("td.c.hc").every((td) => td.dataset.col === R("39c").querySelectorAll("td.c")[1].dataset.col), "hover: the whole Zone 2 column is lit (7 lines): " + t.qa("td.c.hc").length);
  ok(t.qa("thead th.hh").length === 1 && T(t.q("thead th.hh")) === "Zone 2", "hover: the Zone 2 column header is lit: " + t.qa("thead th.hh").map(T));
  over(R("39b").querySelectorAll("td.c")[4]);                                    // moves on: the old row / column go dark
  ok(t.qa("tr.hv").length === 1 && R("39b").classList.contains("hv") && !R("39c").classList.contains("hv"), "moving on: only the new row is lit");
  press(t.window, R("39b").querySelectorAll("td.c")[4]);                         // a click re-draws the table: the hover must survive it
  ok(R("39b").classList.contains("hv") && t.qa("td.c.hc").length === 7, "hover survives a click (table redrawn)");
  ok(R("39b").querySelectorAll("td.c.sel").length === 1, "and the selected cell is still marked on top of it");
  over(R("39a").querySelector("td.rl"));                                         // row label: row only, no column
  ok(R("39a").classList.contains("hv") && t.qa("td.c.hc").length === 0, "row label hover: row lit, no column");
  over(R("39a").querySelector("td.q1"));                                         // right-hand numbers: row only
  ok(R("39a").classList.contains("hv") && t.qa("td.c.hc").length === 0, "BOQ / plan / done / pending cells light the row too");
  t.q("#grid").dispatchEvent(new t.window.MouseEvent("mouseleave"));
  ok(t.qa("tr.hv, td.hc, th.hh").length === 0, "leaving the grid clears everything");
  click(t.window, t.q("#collapse")); over(R("39b").querySelectorAll("td.c")[0]);
  ok(R("39b").classList.contains("hv") && t.qa("th.hh").length === 1 && /B3/.test(T(t.q("th.hh"))), "a collapsed level lights its heading");
  click(t.window, t.q("#expand"));

  // ---- 4c. pick ONE place: a short list of what was done there (the place dropdown, and the arrows)
  const IDS = MG.thoth.ids, pick = (id) => { const sel_ = t.q("#plc"); sel_.value = id; sel_.dispatchEvent(new t.window.Event("change", { bubbles: true })); };
  click(t.window, t.q("#ow"));                                                  // back ON (it was turned off above)
  ok(T(t.q("#plc")).includes("All places") && t.qa("#plc option").length === 22 && t.qa("#plc optgroup").length === 3, "the dropdown lists all places under their levels: " + t.qa("#plc option").length);
  pick(IDS[1]);
  ok(t.qa("th.leaf").length === 1 && T(t.q("th.leaf")) === "Zone 2" && T(t.q("th.lv")) === "B3", "one place: one column (B3 > Zone 2): " + t.qa("thead th").map(T));
  ok(t.qa("tr[data-code]").map((r) => r.dataset.code).join() === "39a,39b" && T(t.q("#cnt")) === "2 of 7 lines", "the lines with work in THAT place (39a ticked, 39b = 46.4): " + t.qa("tr[data-code]").map((r) => r.dataset.code));
  ok(T(R("39b").querySelector("td.c")) === "46.4" && t.q("#expand").hidden, "its number is there, and Expand / Collapse step aside");
  const sq = t.q("#q"); sq.value = "80 mm"; sq.dispatchEvent(new t.window.Event("input", { bubbles: true }));
  ok(t.qa("tr[data-code]").map((r) => r.dataset.code).join() === "39c", "a search still finds any line in it, so new work can be added: " + t.qa("tr[data-code]").map((r) => r.dataset.code));
  pick(IDS[1]);
  sq.value = ""; sq.dispatchEvent(new t.window.Event("input", { bubbles: true }));
  click(t.window, t.q("#p1")); ok(T(t.q("th.leaf")) === "Zone 3", "the arrow goes to the next place: " + T(t.q("th.leaf")));
  click(t.window, t.q("#p0")); click(t.window, t.q("#p0")); ok(T(t.q("th.leaf")) === "Zone 1" && t.qa("th.leaf").length === 1, "and back");
  pick(""); ok(t.qa("th.grp").length === 3 && t.qa("tr[data-code]").map((r) => r.dataset.code).join() === "39a,39b", "All places: the whole sheet again");
  click(t.window, t.q("#ow")); click(t.window, t.q("#expand"));

  // ---- 5. the box is a real input only while it is being edited; the only others are search, the formula bar and the hidden Excel file picker
  ok(t.qa("td.c input").length === 0 && t.qa("input").map((i) => i.id || i.type).sort().join() === "fx,q,xfile", "no input sits in a box until you edit it: " + t.qa("input").map((i) => i.id || i.type));
  ok(!/Discard|\bSave\b/.test(T(t.sr())), "no Save / Discard anywhere (boxes save by themselves): " + T(t.sr()).slice(0, 200));
  ok(T(t.q("#xexp")) === "Export Excel" && T(t.q("#ximp")) === "Import Excel", "Export Excel and Import Excel buttons are in the bar: " + T(t.q("#xexp")) + " / " + T(t.q("#ximp")));

  // ---- 6. search
  const s = t.q("#q"); s.value = "100 mm"; s.dispatchEvent(new t.window.Event("input", { bubbles: true }));
  ok(t.qa("tr[data-code]").map((r) => r.dataset.code).join() === "39b", "search '100 mm' keeps 39b only: " + t.qa("tr[data-code]").map((r) => r.dataset.code));
  s.value = "zzz"; s.dispatchEvent(new t.window.Event("input", { bubbles: true }));
  ok(/No BOQ line matches/.test(T(t.q("tbody"))), "no match is said");

  // ---- 7. close
  click(t.window, t.q("#back"));
  ok(!t.d.getElementById("mv-host") && t.d.body.style.overflow !== "hidden", "back closes the view and frees the page scroll");

  // ---- 8. a per-room service: BOQ qty is not known, so it is a dash and Pending is a dash (never a guess)
  t = await boot({ structure: MG.thoth.structure, grid: MG.thoth.hvac });
  click(t.window, t.d.getElementById("sp-measgrid")); await flush(10);
  c = cells(t.rowOf("H.1"));
  ok(c[6] === "—" && c[9] === "—" && c[7] === "210", "per-room qty: BOQ qty and Pending are dashes, Site plan 210 (10 x 21 zones): " + c.slice(6));
  ok(t.rowOf("H.1").querySelector("td.q0").classList.contains("dash"), "dash is styled as unknown");
  ok(/per room/.test(t.q("th.q0").title) && /dash/.test(t.q("th.q0").title), "the column says why on hover: " + t.q("th.q0").title);
  press(t.window, t.rowOf("H.1").querySelector("td.rl")); click(t.window, t.q("#details"));
  dock = T(t.q("#dock"));
  ok(/Nothing recorded yet/.test(dock) && /of site plan|to date/.test(dock) && !/% of BOQ/.test(dock), "details do not pretend a BOQ percentage: " + dock.slice(0, 200));

  // ---- 9. a hotel-shaped project (Floor > Room) works through the same path
  t = await boot({ grid: MG.hotel.fire });
  click(t.window, t.d.getElementById("sp-measgrid")); await flush(10);
  ok(t.qa("th.grp").length === 2 && /1/.test(T(t.qa("th.grp")[0])), "floors become column groups: " + t.qa("th.grp").map(T));
  c = cells(t.rowOf("39b"));
  ok(c[3] === "46.4" && c[4] === "58", "hotel: floor 1 holds the measured 46.4 (room a), floor 2 the typed 58 (room a): " + c.slice(3, 5));

  // ---- 10. the read fails: a plain message, no half-open grid
  t = await boot({ structure: MG.thoth.structure, grid: MG.thoth.fire, fail: true });
  click(t.window, t.d.getElementById("sp-measgrid")); await flush(10);
  ok(!t.d.getElementById("mv-host"), "failed read opens nothing");
  ok(/no BOQ items/.test(T(t.d.getElementById("sp-toast"))), "the server's reason is shown: " + T(t.d.getElementById("sp-toast")));

  console.log(`${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
}
main().catch((e) => { console.error(e); process.exit(1); });
