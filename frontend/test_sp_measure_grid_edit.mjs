// jsdom test: Site Progress > Measurement, EDITING (measureview.js + siteprogress.js), against the REAL backend.
// backend/serve_measure_grid_case.py serves the real FastAPI router over the reported Thoth Fire case (39b = 37 + 9.4 m measured in B3 > Zone 2,
// 58 m typed in B3 > Zone 5). Every typed number below really goes through POST /progress/rooms, and is checked by reading the server back.
// Only the screens around the grid (project list, service view) come from the recorded fixture.
import { JSDOM } from "jsdom";
import fs from "fs";
import path from "path";
import { spawn, execFileSync } from "child_process";
import os from "os";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
let pass = 0, fail = 0;
function ok(cond, msg) { if (cond) pass++; else { fail++; console.error("FAIL:", msg); } if (process.env.V) console.error(cond ? "ok  " : "BAD ", msg.slice(0, 90)); }
const FX = JSON.parse(fs.readFileSync(path.join(__dirname, "activity_delete_fixture.json"), "utf8"));
const MG = JSON.parse(fs.readFileSync(path.join(__dirname, "measure_grid_fixture.json"), "utf8"));
const clone = (x) => JSON.parse(JSON.stringify(x));
const flush = async (n = 8) => { for (let i = 0; i < n; i++) await new Promise((r) => setTimeout(r, 0)); };
const SL = FX.slug, SP = "/api/siteprogress/" + SL, REAL = "/api/siteprogress/thoth-mall";
const T = (el) => (el ? el.textContent : "").replace(/\s+/g, " ").trim();
const waitFor = async (fn, ms = 8000) => { const t0 = Date.now(); while (Date.now() - t0 < ms) { if (fn()) return true; await new Promise((r) => setTimeout(r, 25)); } return false; };
const near = (a, b) => Math.abs(a - b) < 1e-6;

// ---- the real backend
const port = 20000 + Math.floor(Math.random() * 20000), BASE = "http://127.0.0.1:" + port;
const py = spawn("python3", ["-m", "backend.serve_measure_grid_case", String(port)], { cwd: path.join(__dirname, ".."), stdio: ["pipe", "pipe", "inherit"] });
await new Promise((res, rej) => { py.stdout.on("data", (b) => { if (String(b).includes("READY")) res(); }); py.on("exit", (c) => rej(new Error("backend exited " + c))); setTimeout(() => rej(new Error("backend did not start")), 40000); });
const real = async (p, o) => { const r = await fetch(BASE + p, o); return r.json(); };
const serverGrid = (svc = "Fire", code) => real(REAL + "/measure-grid/" + svc + (code ? "?code=" + code : ""));
const IDS = (await real("/__ids")).ids;
ok(JSON.stringify(IDS) === JSON.stringify(MG.thoth.ids), "the fixture's place ids are the real backend's ids");

async function boot(o = {}) {
  const html = `<!doctype html><html><body>
  <header class="top" id="top"><div class="brand"><h1>Material intelligence</h1><p id="ctx"></p></div>
    <div class="topact"><button id="pick"></button><button id="sync"></button><button id="syncgo"></button><button id="home" hidden></button><input id="file" type="file" hidden></div></header>
  <main><section id="drop"></section><section id="busy" hidden></section><section id="report" hidden><div class="bar"><div class="tabs" id="svc"></div></div></section></main></body></html>`;
  const dom = new JSDOM(html, { url: "http://localhost/", runScripts: "outside-only", pretendToBeVisual: true });
  const { window } = dom; const log = [];
  const project = clone(FX.project); project.structure = clone(MG.thoth.structure);
  window.confirm = () => true; window.alert = () => {}; window.prompt = () => null;
  if (o.mobile) window.matchMedia = () => ({ matches: true, addEventListener() {}, removeEventListener() {} });
  const t = { window, log, failSave: 0, view: null, downloads: [], lastExport: null };
  window.URL.createObjectURL = () => "blob:test"; window.URL.revokeObjectURL = () => {};
  window.HTMLAnchorElement.prototype.click = function () { t.downloads.push({ name: this.download, href: this.href }); };
  window.fetch = async (url, init) => {
    const u = new URL(url, "http://localhost"), method = (init && init.method) || "GET";
    log.push(method + " " + u.pathname + u.search);
    const forReal = [/\/measure-grid\//, /\/item-room-plan/, /\/progress\//, /\/item-plan-off/, /\/structure$/].some((re) => re.test(u.pathname));
    if (forReal) {
      if (t.failSave > 0 && /\/progress\/rooms$/.test(u.pathname)) { t.failSave--; const d = { detail: "Disk is full (test)" }; return { ok: false, status: 500, json: async () => d, text: async () => JSON.stringify(d) }; }
      let body = init && init.body, headers = init && init.headers;
      if (body && body.constructor && body.constructor.name === "FormData") {          // a file upload: hand the real bytes to node's fetch
        const fd = new FormData();
        for (const [k, v] of body.entries()) {
          if (v && typeof v === "object" && v.name !== undefined) {
            const buf = await new Promise((res) => { const fr = new window.FileReader(); fr.onload = () => res(Buffer.from(new Uint8Array(fr.result))); fr.readAsArrayBuffer(v); });
            fd.append(k, new Blob([buf]), v.name);
          } else fd.append(k, v);
        }
        body = fd; headers = undefined;
      }
      const r = await fetch(BASE + u.pathname.replace(SP, REAL) + u.search, { method, headers, body });
      const buf = Buffer.from(await r.arrayBuffer());
      if (/\/export$/.test(u.pathname)) t.lastExport = buf;
      const txt = buf.toString("utf8");
      return { ok: r.ok, status: r.status, headers: r.headers, json: async () => JSON.parse(txt), text: async () => txt, blob: async () => new window.Blob([buf]) };
    }
    let data = {};
    if (u.pathname === "/api/projects") data = FX.projects;
    else if (u.pathname === SP) data = project;
    else if (u.pathname === SP + "/service/Fire") data = clone(FX.view_with_empty);
    else if (u.pathname === SP + "/pnl/Fire") data = clone(FX.pnl);
    return { ok: true, status: 200, json: async () => data, text: async () => JSON.stringify(data) };
  };
  window.eval(fs.readFileSync(path.join(__dirname, "siteprogress.js"), "utf8"));
  window.eval(fs.readFileSync(path.join(__dirname, "measureview.js"), "utf8"));
  const orig = window.MIView.open; window.MIView.open = (x) => { t.lastOpts = x; return (t.view = orig(x)); };
  Object.defineProperty(window.document, "readyState", { value: "complete", configurable: true });
  window.document.dispatchEvent(new window.Event("DOMContentLoaded"));
  await flush();
  window.document.querySelector('.spnav button[data-v="siteprogress"]').click();
  await flush(25);
  const d = window.document;
  Object.assign(t, {
    d,
    sr: () => d.getElementById("mv-host") && d.getElementById("mv-host").shadowRoot,
    q: (s) => t.sr().querySelector(s), qa: (s) => [...t.sr().querySelectorAll(s)],
    row: (code) => t.qa("tr[data-code]").find((r) => r.dataset.code === code),
    cell: (code, i) => t.row(code).querySelectorAll("td.c")[i],
    ev: (el, type, init = {}) => el.dispatchEvent(new window.MouseEvent(type, { bubbles: true, cancelable: true, ...init })),
    press: (el) => { for (const k of ["mousedown", "mouseup", "click"]) t.ev(el, k); },
    key: (el, key, init = {}) => el.dispatchEvent(new window.KeyboardEvent("keydown", { key, bubbles: true, cancelable: true, ...init })),
    idle: async () => { await flush(3); await t.view.__test.whenIdle(); await flush(6); },
    open: async (o = {}) => { t.ev(d.getElementById("sp-measgrid"), "click"); await waitFor(() => t.sr()); await flush(5); if (!t.sr()) console.error("OPEN FAILED", t.log.slice(-6), (d.getElementById("sp-toast")||{}).textContent); t.ev(t.q("#expand"), "click"); if (!o.keepFilter) t.ev(t.q("#ow"), "click"); },
    // click a box, start typing like a person (the first key opens the box), then finish with Enter / Tab
    type: async (code, i, text, finish = "Enter", wait = true) => {
      t.press(t.cell(code, i)); const g = t.q("#grid");
      t.key(g, text[0] === "-" ? "5" : text[0]);
      const inp = t.q("td input.ed"); if (!inp) return null;
      inp.value = text; inp.dispatchEvent(new window.Event("input", { bubbles: true }));
      t.key(inp, finish); if (wait) await t.idle(); else await flush(25); return inp;
    },
    pending: () => t.view.__test.pending,
  });
  return t;
}
const done = (g, code) => g.rows.find((r) => r.code === code).done;
const cellv = (g, code, i) => g.cells[code + "|" + IDS[i]];

async function main() {
  const t = await boot();
  await t.open();
  ok(!!t.sr() && t.qa("th.leaf").length === 21, "grid open with 21 zones");

  // ---- 1. type a quantity into the box: it is saved by itself (no Save button anywhere), and the totals follow
  ok(!/\bSave\b/.test(T(t.sr())), "there is no Save button to forget");
  await t.type("39c", 0, "20");
  let g = await serverGrid();
  ok(cellv(g, "39c", 0) === 20 && done(g, "39c") === 20, "39c B3 > Zone 1 = 20 is on the server: " + cellv(g, "39c", 0));
  ok(T(t.cell("39c", 0)) === "20", "the box shows 20: " + T(t.cell("39c", 0)));
  let tds = [...t.row("39c").querySelectorAll("td")].map(T);
  ok(tds[tds.length - 2] === "20" && tds[tds.length - 1] === "1,579", "To date 20 and Pending 1,579 (BOQ 1,599 - 20) update in the row: " + tds.slice(-4));
  ok(/All changes saved/.test(T(t.q("#state"))) && /Saved 1 box/.test(T(t.q("#msg"))), "the pill and message say it is saved: " + T(t.q("#state")) + " | " + T(t.q("#msg")));
  ok(t.row("39c").querySelectorAll("td.c.zero").length === 20, "the other 20 boxes of the line stay at 0");
  const view = await real(REAL + "/service/Fire");
  ok(view.items.find((i) => i.code === "39c").used === 20, "Site Progress's own item row reads the same 20: " + view.items.find((i) => i.code === "39c").used);

  // ---- 2. Enter moves down like a sheet: type, Enter, type, Enter
  ok(t.q("td.c.sel") && t.q("td.c.sel").closest("tr").dataset.code === "39d", "Enter moved the selection to the next line, same place");
  await t.type("39d", 0, "10+2.5");
  g = await serverGrid();
  ok(cellv(g, "39d", 0) === 12.5, "a sum 10+2.5 is saved as 12.5: " + cellv(g, "39d", 0));
  ok(/The sum is kept in the box/.test(T(t.q("#msg"))), "it says the sum is kept in the box: " + T(t.q("#msg")));
  ok(g.exprs && g.exprs["39d|" + IDS[0]] === "=10+2.5", "the server remembers the sum next to the quantity: " + JSON.stringify(g.exprs));
  ok(t.cell("39d", 0).classList.contains("fx") && /Formula =10\+2\.5/.test(t.cell("39d", 0).title) && T(t.cell("39d", 0)) === "12.5", "the box shows 12.5, is marked as a formula box: " + t.cell("39d", 0).title);
  t.press(t.cell("39d", 0));
  ok(t.q("#fx").value === "=10+2.5" && /formula kept/.test(T(t.q("#res"))), "the formula bar shows =10+2.5 when the box is selected: " + t.q("#fx").value + " | " + T(t.q("#res")));
  t.key(t.q("#grid"), "F2"); ok(!!t.q("td input.ed") && t.q("td input.ed").value === "=10+2.5", "editing the box starts from the sum, like Excel: " + (t.q("td input.ed") || {}).value);
  t.key(t.q("td input.ed"), "Escape"); await flush(4);
  await t.type("39d", 0, "13"); g = await serverGrid();
  ok(cellv(g, "39d", 0) === 13 && !(g.exprs || {})["39d|" + IDS[0]] && !t.cell("39d", 0).classList.contains("fx"), "typing a plain number replaces the sum (no stale formula): " + cellv(g, "39d", 0));
  await t.type("39d", 0, "3x4+0.5"); g = await serverGrid();
  ok(cellv(g, "39d", 0) === 12.5 && g.exprs["39d|" + IDS[0]] === "=3*4+0.5", "3x4+0.5 is 12.5 and is kept as =3*4+0.5: " + JSON.stringify(g.exprs["39d|" + IDS[0]]));
  await t.type("39d", 0, "10+2.5"); g = await serverGrid();

  // ---- 3. a neighbour is untouched, and changing a number already there (the typed 58 of Zone 5) SETS it, it does not add to it
  await t.type("39b", 3, "50");
  g = await serverGrid();
  ok(cellv(g, "39b", 4) === 58 && cellv(g, "39b", 3) === 50, "the Zone 4 box got 50, Zone 5's 58 is untouched: " + cellv(g, "39b", 3) + "/" + cellv(g, "39b", 4));
  await t.type("39b", 4, "57.5");
  g = await serverGrid();
  ok(cellv(g, "39b", 4) === 57.5 && near(done(g, "39b"), 46.4 + 50 + 57.5), "overwriting 58 with 57.5 is a SET, not an add: " + cellv(g, "39b", 4) + " total " + done(g, "39b"));
  ok(g.rows.find((r) => r.code === "39b").entries.backed === 46.4, "the measured 46.4 is still backed by its 2 entries");

  // ---- 4. a box made of measurement entries is locked, with the reason; typing does not open it
  const lockedBox = t.cell("39b", 1);
  ok(lockedBox.classList.contains("lock") && /measurement entr/.test(lockedBox.title), "B3 > Zone 2 of 39b is marked locked with a reason: " + lockedBox.title);
  t.press(lockedBox); ok(await waitFor(() => t.q('#runs [data-f="from"]')), "the measurement sheet opens under the grid for that box");
  ok(!t.q("#runs").hidden && /Measurement sheet/.test(T(t.q("#runs"))) && /B3 ▸ Zone 2/.test(T(t.q("#runs"))), "it names the item and the place: " + T(t.q("#runs .rh")));
  ok(t.qa("#runs tr[data-run]").length === 2 && /37/.test(T(t.qa("#runs tr[data-run]")[0])) && /9\.4/.test(T(t.qa("#runs tr[data-run]")[1])), "it lists the box's 2 real entries, 37 and 9.4: " + t.qa("#runs tr[data-run]").map(T));
  ok(/Entries 46\.4 Rmt · Box 46\.4 Rmt/.test(T(t.q("#runs .tot"))), "with the total of the entries beside the box: " + T(t.q("#runs .tot")));
  t.key(t.q("#grid"), "9");
  ok(!t.q("td input.ed") && t.sr().activeElement === t.q('#runs [data-f="from"]'), "typing in a locked box opens no number box: the cursor goes to the sheet's From");
  ok(/2 measurement entries/.test(T(t.q("#msg"))) && /sheet below/.test(T(t.q("#msg"))), "and the message says why and where to go: " + T(t.q("#msg")));
  ok(t.q("#fx").disabled && /measurement entr/.test(t.q("#res").title), "the formula bar is read-only there");
  t.key(t.q('#runs [data-f="from"]'), "Escape"); ok(t.sr().activeElement === t.q("#grid"), "Escape goes back to the grid");
  g = await serverGrid(); ok(cellv(g, "39b", 1) === 46.4, "its 46.4 on the server is unchanged");
  t.key(t.q("#grid"), "Delete"); await t.idle();
  g = await serverGrid(); ok(cellv(g, "39b", 1) === 46.4, "Delete on a locked box does nothing either");

  // ---- 5. above the zone's plan: NO question. The item's plan goes by itself, the number is saved exactly as typed, one line says so
  const planBefore = (await real(REAL + "/item-room-plan?service=Fire&item_code=39b")).rooms[IDS[0]].planned;
  ok(Math.abs(planBefore - 1259.5 / 21) < 0.01, "39b plan per zone is the equal share " + planBefore);
  await t.type("39b", 0, "100");
  g = await serverGrid();
  ok(!t.q("#ask") && cellv(g, "39b", 0) === 100 && g.rows.find((r) => r.code === "39b").free === true, "100 (above the 59.98 share) is saved as typed, with no question: " + cellv(g, "39b", 0));
  ok(/Plan of 39b removed/.test(T(t.q("#msg"))) && /1,259\.5 Rmt\) is the limit/.test(T(t.q("#msg"))), "one line says the plan went and that the item's total is the limit: " + T(t.q("#msg")));
  ok(near(done(g, "39b"), 46.4 + 50 + 57.5 + 100), "the other boxes did not change: " + done(g, "39b"));
  await t.type("39b", 5, "150"); g = await serverGrid();
  ok(cellv(g, "39b", 5) === 150 && !/Plan of/.test(T(t.q("#msg"))), "with the plan already removed: saved, no second notice");
  await t.type("39b", 6, "5000"); g = await serverGrid();
  ok(cellv(g, "39b", 6) === 0 && /Not saved/.test(T(t.q("#msg"))), "above the item's whole total it is still refused, with the reason: " + T(t.q("#msg")));

  // ---- 6. whole numbers for a counted item
  await t.type("N.1", 0, "2.5");
  g = await serverGrid(); ok(cellv(g, "N.1", 0) === 0, "N.1 (Nos) refuses 2.5, nothing saved");
  ok(/whole numbers/.test(T(t.q("#msg"))) || /whole numbers/.test(T(t.q("#res"))), "and says it is counted in whole numbers: " + T(t.q("#msg")) + T(t.q("#res")));
  await t.type("N.1", 0, "3"); g = await serverGrid(); ok(cellv(g, "N.1", 0) === 3, "3 is saved");

  // ---- 7. Delete clears to 0, Ctrl+Z brings the number back, Ctrl+Y redoes
  t.press(t.cell("39c", 0)); t.key(t.q("#grid"), "Delete"); await t.idle();
  g = await serverGrid(); ok(cellv(g, "39c", 0) === 0, "Delete -> 0 on the server");
  t.key(t.q("#grid"), "z", { ctrlKey: true }); await t.idle();
  g = await serverGrid(); ok(cellv(g, "39c", 0) === 20 && T(t.cell("39c", 0)) === "20", "Ctrl+Z puts 20 back (on the server too): " + cellv(g, "39c", 0));
  t.key(t.q("#grid"), "y", { ctrlKey: true }); await t.idle();
  g = await serverGrid(); ok(cellv(g, "39c", 0) === 0, "Ctrl+Y does it again");
  t.key(t.q("#grid"), "z", { ctrlKey: true }); await t.idle();

  // ---- 8. paste a block from Excel (tab / newline separated), grouped per line
  t.press(t.cell("39c", 1));
  const paste = (text) => { const e = new t.window.Event("paste", { bubbles: true, cancelable: true }); Object.defineProperty(e, "clipboardData", { value: { getData: () => text } }); t.q("#grid").dispatchEvent(e); };
  paste("5\t6\n7\t8"); await t.idle();
  g = await serverGrid();
  ok(cellv(g, "39c", 1) === 5 && cellv(g, "39c", 2) === 6 && cellv(g, "39d", 1) === 7 && cellv(g, "39d", 2) === 8, "a 2 x 2 block lands on 39c / 39d zones 2-3: " + [cellv(g, "39c", 1), cellv(g, "39c", 2), cellv(g, "39d", 1), cellv(g, "39d", 2)]);
  ok(/Saved 4 boxes/.test(T(t.q("#msg"))), "message: " + T(t.q("#msg")));
  t.press(t.cell("39c", 0)); paste("1\tx\n"); await t.idle();
  ok(/not a valid quantity/.test(T(t.q("#msg"))), "an Excel cell that is not a number is reported, not guessed: " + T(t.q("#msg")));
  g = await serverGrid(); ok(cellv(g, "39c", 0) === 1 && cellv(g, "39c", 1) === 5, "its valid neighbour was still saved");
  t.press(t.cell("39b", 1)); paste("9"); await t.idle();
  g = await serverGrid(); ok(cellv(g, "39b", 1) === 46.4, "pasting onto a locked box changes nothing");

  // ---- 9. a failed save puts the old number back and says why (the number is never left looking saved)
  t.failSave = 1;
  await t.type("39e", 0, "33");
  ok(T(t.cell("39e", 0)) === "0", "failed save: the box shows its old value again: " + T(t.cell("39e", 0)));
  ok(/Not saved: Disk is full/.test(T(t.q("#msg"))) && /not saved/.test(T(t.q("#state"))), "the server's reason is shown and the pill says it: " + T(t.q("#msg")) + " | " + T(t.q("#state")));
  g = await serverGrid(); ok(cellv(g, "39e", 0) === 0, "nothing reached the server");
  await t.type("39e", 0, "33"); g = await serverGrid(); ok(cellv(g, "39e", 0) === 33, "typing it again saves it");

  // ---- 10. rename a level and a zone by double-clicking its heading: the id stays, so the quantities stay
  const before = JSON.stringify((await serverGrid()).cells);
  let hd = t.qa("th.grp")[0]; ok(/B3/.test(T(hd)), "first level is B3");
  t.ev(hd, "dblclick"); let rn = t.q("th input.rn"); ok(!!rn && rn.value === "B3", "double-click turns the heading into a text box holding the name");
  rn.value = "Basement 3"; t.key(rn, "Enter"); await flush(20);
  ok(/Basement 3/.test(T(t.qa("th.grp")[0])) && /renamed to Basement 3/i.test(T(t.q("#msg"))), "heading shows the new name: " + T(t.qa("th.grp")[0]));
  const post = t.log.filter((l) => /POST .*\/structure$/.test(l)); ok(post.length === 1, "one structure save: " + post);
  ok(JSON.stringify((await serverGrid()).cells) === before, "every recorded quantity is exactly as before the rename");
  ok(t.d.querySelector(".sp-tree, #sp-tree") === null || /Basement 3/.test(T(t.d.getElementById("sp-tree"))), "Site Progress's own tree shows the new name too");
  const leafHd = t.qa("th.leaf")[1]; t.ev(leafHd, "dblclick"); rn = t.q("th input.rn");
  rn.value = "Zone 1"; t.key(rn, "Enter"); await flush(12);
  ok(/already used in Basement 3/.test(T(t.q("#msg"))) && t.log.filter((l) => /POST .*\/structure$/.test(l)).length === 1, "a name that already exists in the same level is refused, nothing is posted: " + T(t.q("#msg")));
  t.ev(t.qa("th.leaf")[1], "dblclick"); rn = t.q("th input.rn"); rn.value = "Plant room"; t.key(rn, "Enter"); await flush(20);
  ok(T(t.qa("th.leaf")[1]) === "Plant room", "a zone is renamed too: " + T(t.qa("th.leaf")[1]));
  t.ev(t.qa("th.leaf")[2], "dblclick"); rn = t.q("th input.rn"); rn.value = "Never saved"; t.key(rn, "Escape"); await flush(4);
  ok(T(t.qa("th.leaf")[2]) === "Zone 3", "Escape cancels a rename");

  // ---- 11. a service tab: the other service loads on demand and is editable too
  ok(t.qa(".tab").map(T).join() === "Fire,HVAC", "tabs for the project's services: " + t.qa(".tab").map(T));
  t.ev(t.qa(".tab")[1], "click"); await waitFor(() => t.qa("tr[data-code]").length === 6 && t.row("H.1")); await flush(4);
  ok(t.log.filter((l) => l.includes("/measure-grid/HVAC")).length === 1 && t.qa("tr[data-code]").length === 6, "HVAC loaded once, 6 lines");
  ok(T(t.row("H.1").querySelector("td.q0")) === "—" && T(t.row("H.1").querySelector("td.q3")) === "—", "per-room service: BOQ qty and Pending stay dashes (no invented number)");
  await t.type("H.1", 0, "4"); g = await serverGrid("HVAC");
  ok(cellv(g, "H.1", 0) === 4 && T(t.cell("H.1", 0)) === "4", "a quantity typed in HVAC is saved: " + cellv(g, "H.1", 0));
  t.ev(t.qa(".tab")[0], "click"); await flush(2);
  ok(T(t.cell("39c", 0)) === "1", "Fire is shown again at once, with its edits: " + T(t.cell("39c", 0)));
  await waitFor(() => t.log.filter((l) => l.includes("/measure-grid/Fire") && !l.includes("code=")).length >= 2); await flush(10); ok(t.log.filter((l) => l.includes("/measure-grid/Fire") && !l.includes("code=")).length === 2 && T(t.cell("39c", 0)) === "1", "and re-read in the background, so other people's changes appear");

  // ---- 11b. Excel: export the service, change numbers in the file, import it back (preview first, then the normal save)
  t.ev(t.q("#xexp"), "click"); await waitFor(() => t.downloads.length >= 1); await t.idle();
  ok(t.log.some((l) => /GET .*\/measure-grid\/Fire\/export$/.test(l)) && t.downloads.length === 1, "Export Excel asks the server for the file and downloads it: " + JSON.stringify(t.downloads));
  ok(/^Measurement_thoth-mall_fire_\d{4}-\d{2}-\d{2}\.xlsx$/.test(t.downloads[0].name) && t.lastExport.slice(0, 2).toString() === "PK", "a real .xlsx with a clear name: " + t.downloads[0].name);
  ok(/Downloaded Measurement_thoth-mall_fire/.test(T(t.q("#msg"))), "the message says what was downloaded: " + T(t.q("#msg")));
  const xl = path.join(os.tmpdir(), "mi_grid_" + port + ".xlsx"), xl2 = xl.replace(".xlsx", "_edit.xlsx");
  fs.writeFileSync(xl, t.lastExport);
  const edit = (cells) => execFileSync("python3", ["-c", `
import sys, json
from openpyxl import load_workbook
wb = load_workbook(sys.argv[1]); ws = wb["Measurement"]
ids = {str(ws.cell(3, c).value): c for c in range(4, ws.max_column + 1) if ws.cell(3, c).value}
rows = {str(ws.cell(r, 1).value): r for r in range(6, ws.max_row + 1)}
for code, pid, v in json.loads(sys.argv[3]): ws.cell(rows[code], ids[pid]).value = v
wb.save(sys.argv[2])`, xl, xl2, JSON.stringify(cells)]);
  edit([["39e", IDS[6], 31], ["39e", IDS[7], 4], ["39f", IDS[6], 200], ["N.1", IDS[6], 2.5], ["39b", IDS[1], 99]]);
  const chooseFile = (name, bytes) => { const f = new t.window.File([bytes], name); const inp = t.q("#xfile"); Object.defineProperty(inp, "files", { value: [f], configurable: true }); inp.dispatchEvent(new t.window.Event("change", { bubbles: true })); };
  const beforeX = JSON.stringify((await serverGrid()).cells);
  chooseFile("edited.xlsx", fs.readFileSync(xl2)); await waitFor(() => T(t.q("#layer")));
  let pv = T(t.q("#layer"));
  ok(/Import preview · edited\.xlsx/.test(pv) && /3 boxes will change/.test(pv) && /1 above their zone's equal-share plan/.test(pv) && /1 locked, skipped/.test(pv) && /1 not a valid quantity, skipped/.test(pv), "the preview counts what will happen: " + pv.slice(0, 330));
  ok(/e\) 50 mm dia/.test(pv) && /Basement 3 ▸ Zone 7/.test(pv) && /whole numbers/.test(pv) && /2 measurement entries make up this number/.test(pv), "and names the boxes (with the renamed level) and the reasons: " + pv.slice(300, 900));
  ok(JSON.stringify((await serverGrid()).cells) === beforeX, "the preview saved nothing");
  t.ev(t.q("#pvno"), "click"); ok(T(t.q("#layer")) === "" && JSON.stringify((await serverGrid()).cells) === beforeX, "Cancel closes it and nothing changes");
  chooseFile("edited.xlsx", fs.readFileSync(xl2)); await waitFor(() => T(t.q("#layer")));
  t.ev(t.q("#pvok"), "click"); await t.idle(); await waitFor(() => /Saved 3 boxes/.test(T(t.q("#msg"))));
  ok(!t.q("#ask") && /Plan of 39f removed/.test(T(t.q("#msg"))), "a number above a zone's plan: no question, the plan of that item goes, one line says so: " + T(t.q("#msg")));
  g = await serverGrid();
  ok(cellv(g, "39e", 6) === 31 && cellv(g, "39e", 7) === 4 && cellv(g, "39f", 6) === 200, "the three changes are saved: " + [cellv(g, "39e", 6), cellv(g, "39e", 7), cellv(g, "39f", 6)]);
  ok(cellv(g, "N.1", 6) === 0 && cellv(g, "39b", 1) === 46.4, "the invalid and the locked boxes were not touched");
  ok(/Saved 3 boxes/.test(T(t.q("#msg"))) && /2 skipped/.test(T(t.q("#msg"))), "the message says 3 saved and 2 skipped: " + T(t.q("#msg")));
  ok(T(t.cell("39e", 6)) === "31" && T(t.cell("39f", 6)) === "200", "the grid shows the new numbers");
  t.press(t.cell("39e", 8)); t.key(t.q("#grid"), "z", { ctrlKey: true }); await t.idle();
  g = await serverGrid(); ok(cellv(g, "39e", 6) === 0 && cellv(g, "39e", 7) === 0 && cellv(g, "39f", 6) === 0, "one Ctrl+Z takes the whole import back");
  chooseFile("notes.xlsx", Buffer.from("hello")); await waitFor(() => /Could not import/.test(T(t.q("#msg"))));
  ok(T(t.q("#layer")) === "" && /not an Excel \(\.xlsx\) file/.test(T(t.q("#msg"))), "a file that is not a workbook: a plain reason, no preview: " + T(t.q("#msg")));
  fs.rmSync(xl, { force: true }); fs.rmSync(xl2, { force: true });


  // ---- 11c. the run-list sheet under the grid: From, To, Start, End, Length = |Start - End|, added to the box
  const runsOf = async (code, i) => { const r = await real(REAL + "/progress/runs?service=Fire&item_code=" + code); const sc = r.scopes.find((x) => x.key === IDS[i]); return sc ? sc.runs : []; };
  const fillRun = (o) => { for (const [k, v] of Object.entries(o)) { const i = t.q('#runs [data-f="' + k + '"]'); i.value = v; i.dispatchEvent(new t.window.Event("input", { bubbles: true })); } };
  const posts = () => t.log.filter((l) => /POST .*\/progress\/runs$/.test(l)).length;
  t.press(t.cell("39f", 10)); ok(await waitFor(() => t.q('#runs [data-f="from"]')), "a box of a length item with no entries yet also shows the sheet, ready for the first row");
  ok(t.qa("#runs tr[data-run]").length === 0 && !t.q("#runs [data-f=qty]").disabled, "no rows yet, the Length box can be typed");
  fillRun({ from: "Room WIFI", to: "ELV Room", start: "112", end: "78" });
  ok(t.q('#runs [data-f="qty"]').disabled && t.q('#runs [data-f="qty"]').value === "34", "with both readings the Length is worked out at once, |112 - 78| = 34: " + t.q('#runs [data-f="qty"]').value);
  t.key(t.q('#runs [data-f="end"]'), "Enter"); await t.idle(); await waitFor(() => t.qa("#runs tr[data-run]").length === 1);
  let rr = await runsOf("39f", 10); g = await serverGrid();
  ok(rr.length === 1 && rr[0].from === "Room WIFI" && rr[0].to === "ELV Room" && rr[0].start === 112 && rr[0].end === 78 && rr[0].qty === 34, "Enter saved one entry on the server: " + JSON.stringify(rr));
  ok(cellv(g, "39f", 10) === 34 && T(t.cell("39f", 10)) === "34" && t.cell("39f", 10).classList.contains("lock"), "the box is 34 on the server and in the grid, and is now a locked (entries) box");
  ok(T(t.qa("#runs tr[data-run]")[0]).includes("Room WIFI") && /Entries 34 Rmt · Box 34 Rmt/.test(T(t.q("#runs .tot"))), "the row is on the sheet: " + T(t.qa("#runs tr[data-run]")[0]));
  ok(t.q('#runs [data-f="from"]').value === "Room WIFI" && t.q('#runs [data-f="to"]').value === "ELV Room" && t.q('#runs [data-f="start"]').value === "", "the next row keeps From / To, but never invents readings");
  ok(t.sr().activeElement === t.q('#runs [data-f="from"]'), "and the cursor is back in From");
  fillRun({ from: "Vestibule", qty: "20" }); t.key(t.q('#runs [data-f="qty"]'), "Enter"); await t.idle(); await waitFor(() => t.qa("#runs tr[data-run]").length === 2);
  g = await serverGrid(); rr = await runsOf("39f", 10);
  ok(rr.length === 2 && rr[1].qty === 20 && rr[1].start === null && cellv(g, "39f", 10) === 54, "a typed length (no readings) is a row too, and ADDS to the box: " + cellv(g, "39f", 10));
  // the checks that stop a bad row before it is sent
  let np0 = posts();
  fillRun({ from: "A", to: "", start: "", end: "", qty: "5" }); t.key(t.q('#runs [data-f="qty"]'), "Enter"); await flush(6);
  ok(posts() === np0 && /fill From and To/.test(T(t.q("#rmsg"))), "no To: nothing is sent, it says what is missing: " + T(t.q("#rmsg")));
  fillRun({ from: "A", to: "B", start: "50", end: "50", qty: "" }); t.key(t.q('#runs [data-f="end"]'), "Enter"); await flush(6);
  ok(posts() === np0 && /same/.test(T(t.q("#rmsg"))), "Start = End (length 0) is refused: " + T(t.q("#rmsg")));
  fillRun({ start: "50", end: "" }); t.key(t.q('#runs [data-f="end"]'), "Enter"); await flush(6);
  ok(posts() === np0 && /both readings/i.test(T(t.q("#rmsg"))), "one reading only is refused: " + T(t.q("#rmsg")));
  fillRun({ start: "x1", end: "3" }); t.key(t.q('#runs [data-f="end"]'), "Enter"); await flush(6);
  ok(posts() === np0 && /must be numbers/.test(T(t.q("#rmsg"))), "a reading that is not a number is refused: " + T(t.q("#rmsg")));
  // above the zone's plan (an equal share): no question, the plan goes by itself and the row is added (39e still has its plan)
  t.press(t.cell("39e", 11)); await waitFor(() => t.q('#runs [data-f="from"]') && /Zone 12/.test(T(t.q("#runs .rh")))); np0 = posts();
  const plan39e = (await real(REAL + "/item-room-plan?service=Fire&item_code=39e")).rooms[IDS[11]].planned;
  ok(plan39e > 100 && plan39e < 150, "39e's plan per zone is the equal share " + plan39e);
  fillRun({ from: "Plant", to: "AHU-1", start: "", end: "", qty: "150" }); t.key(t.q('#runs [data-f="qty"]'), "Enter"); await t.idle(); await waitFor(() => t.qa("#runs tr[data-run]").length === 1);
  g = await serverGrid(); rr = await runsOf("39e", 11);
  ok(!t.q("#ask") && cellv(g, "39e", 11) === 150 && rr.length === 1 && g.rows.find((r) => r.code === "39e").free === true && T(t.cell("39e", 11)) === "150", "150 m (above the share) is added in one go, no question, plan removed: " + cellv(g, "39e", 11));
  ok(/Plan of 39e removed/.test(T(t.q("#msg"))), "and one line says so: " + T(t.q("#msg")));
  // with the plan removed the item's own total is the only limit, and the server still says so plainly
  fillRun({ from: "Plant", to: "AHU-2", start: "", end: "", qty: "5000" }); t.key(t.q('#runs [data-f="qty"]'), "Enter"); await t.idle(); await flush(10);
  g = await serverGrid(); ok(cellv(g, "39e", 11) === 150 && /Not saved/.test(T(t.q("#msg"))), "above the item's whole total: refused with a reason, nothing changes: " + T(t.q("#msg")));
  fillRun({ qty: "" });
  t.press(t.cell("39f", 10)); await waitFor(() => t.qa("#runs tr[data-run]").length === 2);
  // cancel keeps the row, struck through; the box goes down; restore puts it back
  t.ev(t.qa("#runs [data-cancel]")[0], "click"); await t.idle(); await waitFor(() => t.qa("#runs tr.x").length === 1);
  g = await serverGrid(); rr = await runsOf("39f", 10);
  ok(cellv(g, "39f", 10) === 20 && rr[0].cancelled === true && T(t.cell("39f", 10)) === "20", "Cancel takes 34 off the box (20 left) and keeps the row on record: " + cellv(g, "39f", 10));
  ok(t.qa("#runs tr.x").length === 1 && /Restore/.test(T(t.qa("#runs tr.x")[0])) && /Entries 20 Rmt/.test(T(t.q("#runs .tot"))), "the cancelled row is struck through with a Restore button, and the entries total follows");
  t.ev(t.q("#runs [data-restore]"), "click"); await t.idle(); await waitFor(() => t.qa("#runs tr.x").length === 0);
  g = await serverGrid(); ok(cellv(g, "39f", 10) === 54 && T(t.cell("39f", 10)) === "54", "Restore puts the 34 back: " + cellv(g, "39f", 10));
  // a box with a TYPED number (no entries): the sheet shows it as the remainder, and a new row adds to it
  t.press(t.cell("39e", 10)); await t.type("39e", 10, "6"); t.press(t.cell("39e", 10)); await waitFor(() => t.q("#runs tr.ot"));
  ok(/Typed or ticked, no entries behind it/.test(T(t.q("#runs"))) && /6$/.test(T(t.q("#runs tr.ot"))), "a typed 6 is shown as 'typed, no entries behind it': " + T(t.q("#runs tr.ot")));
  fillRun({ from: "P", to: "Q", start: "10", end: "4" }); t.key(t.q('#runs [data-f="end"]'), "Enter"); await t.idle(); await waitFor(() => t.qa("#runs tr[data-run]").length === 1);
  g = await serverGrid(); ok(near(cellv(g, "39e", 10), 12) || Math.abs(cellv(g, "39e", 10) - 12) < 1e-3, "the new row ADDS to the typed number (6 + 6 = 12): " + cellv(g, "39e", 10));
  // Enter on a locked box goes to the sheet
  t.press(t.cell("39f", 10)); await waitFor(() => t.qa("#runs tr[data-run]").length === 2); t.key(t.q("#grid"), "Enter");
  ok(t.sr().activeElement === t.q('#runs [data-f="from"]') && !t.q("td input.ed"), "Enter on a box made of entries puts the cursor in the sheet's From");
  // a plain item (Nos) never shows it
  t.press(t.cell("N.1", 10)); ok(t.q("#runs").hidden, "an item counted in Nos has no measurement sheet");

  // ---- 11d. place first: one place as a list of what was done there; a typed line found by search stays after the search is cleared
  const tp = await boot(); await tp.open({ keepFilter: true });
  g = await serverGrid();
  const withWork = g.rows.filter((r) => r.done > 0).map((r) => r.code);
  ok(tp.qa("tr[data-code]").map((r) => r.dataset.code).join() === withWork.join() && withWork.length > 0 && withWork.length < g.rows.length, "opens on the lines that have work: " + tp.qa("tr[data-code]").length + " of " + g.rows.length);
  const pickp = (id) => { const x = tp.q("#plc"); x.value = id; x.dispatchEvent(new tp.window.Event("change", { bubbles: true })); };
  const lastZone = IDS[20], expect = g.rows.filter((r) => (g.cells[r.code + "|" + lastZone] || 0) > 0).map((r) => r.code);
  pickp(lastZone);
  ok(tp.qa("th.leaf").length === 1 && tp.qa("tr[data-code]").map((r) => r.dataset.code).join() === expect.join(), "a place shows only the lines with work in it: [" + tp.qa("tr[data-code]").map((r) => r.dataset.code) + "] expected [" + expect + "]");
  if (!expect.length) ok(/Nothing is recorded in .* yet/.test(T(tp.q("tbody"))), "an untouched place says so: " + T(tp.q("tbody")));
  const sq2 = tp.q("#q"); sq2.value = "40 mm"; sq2.dispatchEvent(new tp.window.Event("input", { bubbles: true }));
  ok(tp.qa("tr[data-code]").map((r) => r.dataset.code).join() === "39f", "search finds a line that has no work here: 39f");
  tp.press(tp.cell("39f", 0)); tp.key(tp.q("#grid"), "3"); const ed = tp.q("td input.ed"); ed.value = "3"; ed.dispatchEvent(new tp.window.Event("input", { bubbles: true })); tp.key(ed, "Enter"); await tp.idle();
  g = await serverGrid(); ok(cellv(g, "39f", 20) === 3, "typing 3 there saves it on the server for that place: " + cellv(g, "39f", 20));
  sq2.value = ""; sq2.dispatchEvent(new tp.window.Event("input", { bubbles: true }));
  ok(tp.qa("tr[data-code]").map((r) => r.dataset.code).includes("39f"), "after clearing the search the line stays: it has work now");
  tp.ev(tp.q("#back"), "click"); await flush(10);

  // ---- 12. closing after changes refreshes Site Progress behind it; closing without changes does not
  const n0 = t.log.filter((l) => l.endsWith("/service/Fire")).length;
  t.ev(t.q("#back"), "click"); await flush(15);
  ok(!t.d.getElementById("mv-host"), "closed");
  ok(t.log.filter((l) => l.endsWith("/service/Fire")).length > n0, "Site Progress reloaded its service view after the edits");
  const t2 = await boot(); await t2.open(); const m0 = t2.log.filter((l) => l.endsWith("/service/Fire")).length;
  t2.ev(t2.q("#back"), "click"); await flush(10);
  ok(t2.log.filter((l) => l.endsWith("/service/Fire")).length === m0, "closing with no edit does not reload anything");

  // ---- 13. the phone: pick a place, type per line
  const tm = await boot({ mobile: true }); tm.ev(tm.d.getElementById("sp-measgrid"), "click"); await waitFor(() => tm.sr()); await flush(5); if (!tm.sr()) console.error("MOBILE OPEN FAILED", tm.log.slice(-5), (tm.d.getElementById("sp-toast") || {}).textContent, !!tm.d.getElementById("sp-measgrid"));
  tm.ev(tm.q("#mow"), "click"); ok(!!tm.q("#msel") && tm.qa("input[data-mr]").length === 7, "phone: a place picker and one input per BOQ line: " + tm.qa("input[data-mr]").length);
  const inp = tm.q('input[data-mr="39f"]'); inp.value = "12"; inp.dispatchEvent(new tm.window.Event("change", { bubbles: true })); await tm.idle();
  g = await serverGrid(); ok(Object.entries(g.cells).some(([k, v]) => k.startsWith("39f|") && v === 12), "phone: a typed quantity is saved through the same route");

  // ---- 14. opened from an item's "Done where" line: that place, its lines with work, the box of that item selected (real backend)
  const t4 = await boot(); await t4.open(); t4.ev(t4.q("#back"), "click"); await flush(10);
  ok(!t4.d.getElementById("mv-host"), "closed before reopening on a place");
  t4.window.MIView.open({ ...t4.lastOpts, place: IDS[1], focus: { code: "39b", place: IDS[1] } }); await waitFor(() => t4.sr()); await flush(8);
  ok(t4.qa("th.leaf").length === 1 && /Zone 2/.test(T(t4.q("th.leaf"))), "opened on ONE place, B3 > Zone 2: " + T(t4.q("th.leaf")));
  const lines4 = t4.qa("tr[data-code]").map((r) => r.dataset.code);
  ok(lines4.includes("39b") && !lines4.includes("39f"), "only the lines with work in that zone: " + lines4);
  const sel4 = t4.qa("td.sel");
  ok(sel4.length === 1 && sel4[0].parentElement.dataset.code === "39b", "the box of 39b in that zone is selected: " + sel4.length + " " + (sel4[0] && sel4[0].parentElement.dataset.code));
  ok(/100 mm dia/.test(T(t4.q("#nm"))) && /46\.4/.test(t4.q("#fx").value), "and its number is in the quantity box above the grid: " + T(t4.q("#nm")) + " | " + t4.q("#fx").value);
  t4.ev(t4.q("#back"), "click"); await flush(10);
  const t5 = await boot(); await t5.open(); t5.ev(t5.q("#back"), "click"); await flush(10);
  t5.window.MIView.open({ ...t5.lastOpts, place: "no-such-place", focus: { code: "nope", place: "no-such-place" } }); await waitFor(() => t5.sr()); await flush(8);
  ok(t5.qa("th.leaf").length === 0 && t5.qa("tr[data-code]").length > 0 && t5.qa("td.sel").length === 0, "an unknown place or line is ignored: the grid opens as always");
  t5.ev(t5.q("#back"), "click"); await flush(10);

  py.stdin.end(); py.kill();
  console.log(`${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
}
main().catch((e) => { console.error(e); py.kill(); process.exit(1); });
