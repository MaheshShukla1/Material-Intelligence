// jsdom test: the "Done where" line under an item on Site Progress -- in which levels / zones the item has been executed, and how much.
// Read-only; the numbers come from GET /done-where/{service} (the bill's own split), so here only what is DRAWN is checked:
//   a level with a few zones lists them with their quantity and a little strip; a level with many says "N of M rooms"; a whole-level total
//   is one number (no zone buttons); an overall figure says so; nothing recorded -> no line; a failed read -> rows still render, no line;
//   a single zone's own view has no line; tapping a zone opens the Measurement grid on that zone with its box selected.
// Rooms roo2, roo3 (floor "1"), roo5, roo6 (floor "2"); items 3 (Rmt), 5, 2 (Nos). Payloads: activity_delete_fixture.json.
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
const click = (w, el) => el.dispatchEvent(new w.MouseEvent("click", { bubbles: true, cancelable: true }));

const WHERE = {
  service: "Fire", word: "floor", leaf: "room",
  items: {
    "3": { unit: "Rmt", sum: 26, rest: 0, areas: [
      { area: "1", n: 2, q: 20, whole: false, cells: [1, 0], done: [{ id: "roo2", name: "a", q: 20 }] },
      { area: "2", n: 2, q: 6, whole: false, cells: [0.3, 0], done: [{ id: "roo5", name: "a", q: 6 }] }] },
    "5": { unit: "Nos", sum: 8, rest: 0, areas: [{ area: "1", n: 2, q: 8, whole: true, cells: [], done: [] }] },
    "2": { unit: "Nos", sum: 12, rest: 0, areas: [{ area: "F3", n: 12, q: 12, whole: false, cells: [1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0],
      done: [0, 1, 2, 3, 4, 5].map((i) => ({ id: "x" + i, name: "Room " + (i + 1), q: 2 }))}] },
  },
};

async function boot(whereFn) {
  const html = `<!doctype html><html><body>
  <header class="top" id="top"><div class="brand"><h1>Material intelligence</h1><p id="ctx"></p></div>
    <div class="topact"><button id="pick"></button><button id="sync"></button><button id="syncgo"></button><button id="home" hidden></button><input id="file" type="file" hidden></div></header>
  <main><section id="drop"></section><section id="busy" hidden></section><section id="report" hidden><div class="bar"><div class="tabs" id="svc"></div></div></section></main></body></html>`;
  const dom = new JSDOM(html, { url: "http://localhost/", runScripts: "outside-only", pretendToBeVisual: true });
  const { window } = dom; const urls = []; const st = { opened: null };
  window.confirm = () => true; window.alert = () => {}; window.prompt = () => null;
  window.MIView = { open: (o) => { st.opened = o; return { refreshItem: async () => {} }; } };
  window.fetch = async (url, o) => {
    const u = new URL(url, "http://localhost"); urls.push(u.pathname + u.search);
    let data = {};
    if (u.pathname === "/api/projects") data = FX.projects;
    else if (u.pathname === SP) data = FX.project;
    else if (u.pathname === SP + "/service/Fire") data = clone(FX.view_with_empty);
    else if (u.pathname === SP + "/pnl/Fire") data = clone(FX.pnl);
    else if (u.pathname === SP + "/done-where/Fire") {
      const r = whereFn(); if (r === "fail") return { ok: false, status: 500, json: async () => ({ detail: "boom" }), text: async () => "boom" };
      data = r;
    } else if (u.pathname === SP + "/measure-grid/Fire") data = { basis: "total", rows: [], cells: {}, locks: {}, exprs: {} };
    return { ok: true, status: 200, json: async () => data, text: async () => JSON.stringify(data) };
  };
  window.eval(fs.readFileSync(path.join(__dirname, "siteprogress.js"), "utf8"));
  Object.defineProperty(window.document, "readyState", { value: "complete", configurable: true });
  window.document.dispatchEvent(new window.Event("DOMContentLoaded"));
  await flush();
  window.document.querySelector('.spnav button[data-v="siteprogress"]').click();
  await flush(25);
  return { window, d: window.document, urls, st };
}
const line = (d, code) => d.querySelector(`.sp-where[data-where="${code}"]`);

async function main() {
  // 1. a zone-wise item: both floors listed, only the zones with work, the strip shows full / part / empty
  let { window, d, urls, st } = await boot(() => clone(WHERE));
  ok(urls.some((u) => u === SP + "/done-where/Fire"), "the line is read from /done-where: " + urls.filter((u) => /done-where/.test(u)));
  const l3 = line(d, "3");
  ok(!!l3, "item 3 has a Done where line");
  ok(/^Done where · Rmt 1 .*a · 20 2 .*a · 6$/.test(T(l3)), "text: " + T(l3));
  const strips = [...l3.querySelectorAll(".sp-wstrip")].map((s) => [...s.children].map((i) => i.className));
  ok(JSON.stringify(strips) === JSON.stringify([["f", ""], ["p", ""]]), "strip: full, empty | part, empty: " + JSON.stringify(strips));
  ok(l3.querySelectorAll("[data-wzone]").length === 2 && l3.querySelector('[data-wzone="roo2"]').dataset.wcode === "3", "two zone buttons, each knows its zone and item");
  ok(/Rmt/.test(l3.querySelector('[data-wzone="roo2"]').title) && /20/.test(l3.querySelector('[data-wzone="roo2"]').title), "the tooltip says the quantity and unit: " + l3.querySelector('[data-wzone="roo2"]').title);
  ok(!l3.querySelector("input, textarea"), "read-only: nothing to type in the line");

  // 2. a whole-level total is ONE number, never zones
  const l5 = line(d, "5");
  ok(/Done where · Nos 1 whole floor · 8 Nos/.test(T(l5)) && !l5.querySelector("[data-wzone]") && !l5.querySelector(".sp-wstrip"), "whole total: " + T(l5));

  // 3. many zones in a level: a count, not a wall of names
  const l2 = line(d, "2");
  ok(/F3 .*6 of 12 rooms · 12 Nos/.test(T(l2)) && !l2.querySelector("[data-wzone]"), "many: " + T(l2));
  ok(l2.querySelectorAll(".sp-wstrip i").length === 12 && l2.querySelectorAll(".sp-wstrip i.f").length === 6, "strip has 12 cells, 6 full");

  // 4. recorded as one overall figure, not by zone
  ({ d } = await boot(() => ({ service: "Fire", word: "floor", leaf: "room", items: { "5": { unit: "Nos", sum: 2, rest: 2, areas: [] } } })));
  const l1 = line(d, "5");
  ok(/^Done where · Nos Overall, no room · 2 Nos$/.test(T(l1)) && !l1.querySelector("[data-wzone]"), "overall: " + T(l1));
  ({ window, d, urls, st } = await boot(() => clone(WHERE)));

  // 5. tapping a zone opens the Measurement grid on that zone, its box selected; tapping the level opens the grid as it always opens
  const l3b = line(d, "3");
  click(window, l3b.querySelector('[data-wzone="roo5"]')); await flush(15);
  ok(st.opened && st.opened.place === "roo5" && st.opened.focus && st.opened.focus.code === "3" && st.opened.focus.place === "roo5", "zone -> grid at roo5 with item 3 selected: " + JSON.stringify(st.opened && { p: st.opened.place, f: st.opened.focus }));
  st.opened = null;
  click(window, l3b.querySelector("[data-wopen]")); await flush(15);
  ok(st.opened && !st.opened.place && !st.opened.focus, "level -> the grid, no place picked: " + JSON.stringify(st.opened && { p: st.opened.place, f: st.opened.focus }));

  // 6. nothing recorded anywhere -> no line at all
  ({ d } = await boot(() => ({ service: "Fire", word: "floor", leaf: "room", items: {} })));
  ok(!d.querySelector(".sp-where") && d.querySelectorAll(".sp-brow").length >= 4, "no data: rows there, no line");

  // 7. the read fails -> the page is exactly as before
  ({ d } = await boot(() => "fail"));
  ok(!d.querySelector(".sp-where") && d.querySelectorAll(".sp-brow").length >= 4, "failed read: rows there, no line");

  // 8. a single zone's own view: no line, and the route is not even asked
  ({ window, d, urls } = await boot(() => clone(WHERE)));
  const before = urls.filter((u) => /done-where/.test(u)).length;
  click(window, d.querySelector('[data-toggle="roo2"]')); await flush(25);
  ok(!d.querySelector(".sp-where") && d.querySelectorAll(".sp-brow").length >= 1, "zone view: rows, no line");
  ok(urls.filter((u) => /done-where/.test(u)).length === before, "zone view: no extra /done-where call");

  // 9. the line is plain markup in the row's name column: it adds no control that takes a quantity
  ({ d } = await boot(() => clone(WHERE)));
  ok(!!d.querySelector('.sp-brow[data-code="3"] .sp-bname .sp-where'), "inside the item's name column");

  console.log(`${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
}
main().catch((e) => { console.error(e); process.exit(1); });
