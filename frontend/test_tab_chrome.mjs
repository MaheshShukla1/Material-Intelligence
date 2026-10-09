// jsdom + CSS-structure test for the per-tab header chrome and the phone layout
// of Site Progress. (Real pixel layout is covered separately by
// test_phone_layout_chromium.py -- jsdom has no layout engine.)
//
// Reproduces the reported scenarios:
//   1. Tab bar drives <body data-view>, the hook siteprogress.css uses to show
//      only each tab's own header controls.
//   2. Tapping "Site Progress" twice, then "Forecast", used to leave the whole
//      Forecast page blank (the second tap re-snapshotted the already-hidden
//      sections). Real flow: report visible -> SP -> SP -> Forecast.
//   3. Site Progress renders the service pills inside .sp-main, a sibling of
//      the Structure rail inside .sp-layout, which is what lets the phone CSS
//      order pills above the tree.
//   4. The CSS rules themselves (parsed, not grepped).
import { JSDOM } from "jsdom";
import css from "css";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
let pass = 0, fail = 0;
function ok(cond, msg) { if (cond) pass++; else { fail++; console.error("FAIL:", msg); } }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ------------------------------------------------------------ CSS structure
function rulesOf(file) { return css.parse(fs.readFileSync(path.join(__dirname, file), "utf8")).stylesheet.rules; }
const norm = (s) => s.replace(/\s+/g, "");
function decl(rule, prop) { const d = (rule.declarations || []).find((x) => x.property === prop); return d ? d.value.replace(/\s+/g, " ").trim() : null; }
function find(rules, selector, media) {
  const pool = media == null ? rules.filter((r) => r.type === "rule")
    : rules.filter((r) => r.type === "media" && norm(r.media) === norm(media)).flatMap((m) => m.rules.filter((r) => r.type === "rule"));
  const hits = pool.filter((r) => r.selectors.map((s) => s.replace(/\s+/g, " ").trim()).includes(selector));
  if (!hits.length) return undefined;
  // a selector can be declared more than once in one media block (an older rule
  // plus ours): merge the declarations, later ones winning, like the cascade.
  return { declarations: hits.flatMap((h) => h.declarations || []).reverse().filter((d, i, a) => a.findIndex((x) => x.property === d.property) === i) };
}
const sp = rulesOf("siteprogress.css");
const base = rulesOf("style.css");

let r = find(sp, 'body[data-view="siteprogress"] #report');
ok(r && decl(r, "display") === "none !important", "Site Progress view force-hides the Forecast report");
r = find(sp, 'body[data-view="siteprogress"] .topact');
ok(r && decl(r, "visibility") === "hidden", "Site Progress hides the run controls (desktop: keeps the tab bar steady)");
ok(["#ctx", "#fresh"].every((id) => { const x = find(sp, `body[data-view="siteprogress"] ${id}`); return x && decl(x, "visibility") === "hidden"; }),
   "Site Progress hides the run/file line and freshness line");
r = find(sp, 'body[data-view="siteprogress"] .topact', "(max-width:760px)");
ok(r && decl(r, "display") === "none", "on a phone the hidden run controls give their space back");
r = find(sp, 'body[data-view="inventory"] #leadwrap');
ok(r && decl(r, "display") === "none !important", "Inventory hides Lead time (a forecast-only setting)");
ok(!sp.concat(base).some((x) => x.type === "rule" && x.selectors.some((s) => /body\[data-view="forecast"\]/.test(s))), "Forecast tab has no hiding rules (unchanged)");

r = find(sp, ".sp-layout", "(max-width:900px)");
ok(r && decl(r, "grid-template-columns") === "minmax(0,1fr)", "phone grid column is minmax(0,1fr) so wide content cannot blow the column past the screen");
r = find(sp, ".sp-main", "(max-width:900px)");
ok(r && decl(r, "display") === "contents", ".sp-main dissolves into the grid on phones");
r = find(sp, ".sp-main>.sp-pills", "(max-width:900px)");
ok(r && decl(r, "order") === "0", "pills ordered first on phones");
r = find(sp, ".sp-rail", "(max-width:900px)");
ok(r && decl(r, "order") === "1", "structure tree second on phones");
r = find(sp, ".sp-main>*", "(max-width:900px)");
ok(r && decl(r, "order") === "2", "rest of the content last on phones");
r = find(sp, ".sp-pill", "(hover:none),(pointer:coarse)");
ok(r && decl(r, "min-height") === "44px", "service pills are 44px touch targets");
// desktop layout untouched: no base-level order/contents on these
ok(!find(sp, ".sp-main"), "no desktop-level .sp-main rule (desktop layout unchanged)");

r = find(base, ".topact", "(max-width:760px)");
ok(r && decl(r, "flex") === "1 1 100%", "phone: run controls take the row instead of staying one-line wide (the 768px-on-a-412px-screen bug)");
r = find(base, ".topact", "(max-width:1100px)");
ok(r && decl(r, "flex-wrap") === "wrap", "tablet: run controls wrap instead of clipping Export/Delete");
r = find(base, ".right", "(max-width:760px)");
ok(r && decl(r, "flex-wrap") === "wrap", "phone: Forecast filter bar wraps instead of clipping 'Show me'");
// base .topact rule itself is untouched (desktop single line)
r = find(base, ".topact");
ok(r && decl(r, "flex") === "none", "desktop .topact rule unchanged");

// ------------------------------------------------------------ behaviour
async function main() {
  const html = `<!doctype html><html><body>
    <header class="top" id="top"><div class="brand"><p id="ctx"></p></div>
      <div class="topact"><button id="home"></button><button id="pick"></button><button id="sync"></button><button id="syncgo"></button></div></header>
    <main>
      <section id="drop" hidden></section><section id="busy" hidden></section>
      <section id="report"><div class="bar"><div class="tabs" id="svc"></div></div></section>
    </main></body></html>`;
  const dom = new JSDOM(html, { url: "https://example.test/", runScripts: "dangerously", pretendToBeVisual: true });
  const { window } = dom; const d = window.document;
  window.fetch = (url) => {
    const u = String(url); let body = {};
    if (u === "/api/projects") body = [{ slug: "hyatt-hotel", project: "Hyatt Hotel", runs: 1, latest_run: "r1" }];
    else if (u === "/api/siteprogress/hyatt-hotel") body = { structure: { kind: "hotel", id: "p0", type: "project", name: "Hyatt Hotel", children: [] }, has_boq: true, services: ["Electrical", "HVAC"], rooms: 0 };
    else if (u.startsWith("/api/siteprogress/hyatt-hotel/service/")) body = { service: "Electrical", room: null, activities: [], mapping: {}, act_pct: {}, overall_pct: 0, items: [], pnl_by_activity: {}, pnl_totals: {}, pnl_unmapped_value: { items: 0 }, item_rooms: {}, item_room_qty: {}, unmapped: [] };
    else if (u.startsWith("/api/siteprogress/hyatt-hotel/pnl/")) body = { done_value: 0, remaining_value: 0 };
    else if (u.startsWith("/api/siteprogress/hyatt-hotel/realistic/")) body = { items: [], shortages: 0 };
    return Promise.resolve({ ok: true, status: 200, json: async () => body, text: async () => JSON.stringify(body) });
  };
  window.eval(fs.readFileSync(path.join(__dirname, "siteprogress.js"), "utf8"));
  await sleep(0); await sleep(0);

  const nav = (v) => [...d.querySelectorAll(".spnav button")].find((b) => b.dataset.v === v);
  const tap = (v) => nav(v).dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  ok(!!nav("siteprogress"), "tab bar injected");

  tap("siteprogress"); await sleep(40);
  ok(d.body.dataset.view === "siteprogress", "body[data-view] = siteprogress after tapping Site Progress");
  ok(d.getElementById("report").hidden === true, "Forecast report hidden on Site Progress");
  tap("inventory"); await sleep(10);
  ok(d.body.dataset.view === "inventory", "body[data-view] = inventory");
  ok(d.getElementById("report").hidden === false, "report visible again on Inventory");
  tap("forecast"); await sleep(10);
  ok(d.body.dataset.view === "forecast", "body[data-view] = forecast");

  // Real reported flow: Forecast open -> Site Progress -> (tap Site Progress again) -> Forecast
  ok(d.getElementById("report").hidden === false, "precondition: report visible");
  tap("siteprogress"); await sleep(40);
  tap("siteprogress"); await sleep(40);
  tap("forecast"); await sleep(10);
  ok(d.getElementById("report").hidden === false, "double-tap Site Progress then Forecast: report is visible again (was blank)");
  ok(d.getElementById("drop").hidden === true && d.getElementById("busy").hidden === true, "...and drop/busy keep their original hidden state");

  // Site Progress structure: pills live in .sp-main, sibling of .sp-rail
  tap("siteprogress"); await sleep(60);
  const pill = d.querySelector('#sp-pills .sp-pill[data-s="Electrical"]');
  ok(!!pill, "service pills rendered");
  pill.dispatchEvent(new window.MouseEvent("click", { bubbles: true })); await sleep(80);
  const layout = d.querySelector(".sp-layout");
  ok(!!layout, ".sp-layout rendered for a service view");
  if (layout) {
    const kids = [...layout.children].map((c) => c.className);
    ok(kids.includes("sp-rail") && kids.includes("sp-main"), `.sp-layout children are .sp-rail + .sp-main, got ${JSON.stringify(kids)}`);
    ok(!!layout.querySelector(":scope > .sp-main > #sp-pills"), "#sp-pills is a direct child of .sp-main");
    ok(!!layout.querySelector(":scope > .sp-main > .sp-hero"), ".sp-hero is a direct child of .sp-main (so it orders after the tree)");
  }
  console.log(`${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
}
main().catch((e) => { console.error("THROW", e); process.exit(1); });
