// jsdom: the reported case. Thoth Mall's stock was deleted, so /api/projects (built from stock runs) no longer lists it, and a
// Google Sheet synced under "Live Sheet 4F2A" is a different stock. Site Progress must still open Thoth Mall, show
// "Stock: not connected", let the person pick the sheet, and re-read the project after the pick.
// Payload shapes are the real ones from backend/test_stock_pick.py's GET/POST /{slug}/stock.
import { JSDOM } from "jsdom";
import fs from "fs";
import assert from "assert";

const html = `<!doctype html><html><body>
<header class="top" id="top"><div class="brand"><h1>Material intelligence</h1><p id="ctx"></p></div>
  <div class="topact"><button id="pick"></button><button id="sync"></button><button id="syncgo"></button><button id="home" hidden></button>
  <input id="file" type="file" hidden></div></header>
<main><section id="drop"></section><section id="busy" hidden></section><section id="report" hidden><div class="bar"><div class="tabs" id="svc"></div></div></section></main></body></html>`;
const dom = new JSDOM(html, { url: "http://localhost/", runScripts: "outside-only", pretendToBeVisual: true });
const { window } = dom;
global.window = window; global.document = window.document; global.HTMLElement = window.HTMLElement; global.MutationObserver = window.MutationObserver;

const STRUCTURE = { id: "p0", type: "project", name: "Thoth Mall", kind: "mall",
  children: [{ id: "l1", type: "level", name: "Level 1", children: [{ id: "z1", type: "zone", name: "Zone A", children: [] }] }] };
const SHEET = { stock: "live-sheet-4f2a", name: "Live Sheet 4F2A", runs: 3, created: "2026-10-12T10:05:00", run_id: "g1",
  kind: "Google Sheet", filename: null, materials: 1204, links: { saved: 148, found: 142 } };
const UPLOAD = { stock: "hyatt-stock", name: "Hyatt stock", runs: 1, created: "2026-08-18T10:00:00", run_id: "u1",
  kind: "Upload", filename: "stock_aug.xlsx", materials: 300, links: { saved: 148, found: 0 } };
const server = { chosen: null, loads: 0, posts: [], stockFails: false };
const stockView = () => {
  const cur = server.chosen ? [SHEET, UPLOAD].find((o) => o.stock === server.chosen) : null;
  return { project: "thoth-mall", stock: server.chosen || "thoth-mall", chosen: !!server.chosen, current: cur || null, options: [SHEET, UPLOAD] };
};
function svcState() { return { service: "Electrical", room: null, activities: [], mapping: {}, act_pct: {}, overall_pct: 1, items: [], pnl_by_activity: {}, pnl_totals: {},
  pnl_unmapped_value: { items: 0 }, item_rooms: {}, item_progress: {}, item_room_qty: {}, activity_progress: {}, labour_buckets: {}, unmapped: [], labour_only: {}, labour_pct: {}, labour_suggested: {} }; }
const calls = [];
function route(url, o) {
  const u = new URL(url, "http://localhost"), p = u.pathname, method = (o && o.method) || "GET";
  calls.push(method + " " + p);
  if (p === "/api/projects") return [];                                              // the stock-run-built list: Thoth Mall is NOT in it
  if (p === "/api/siteprogress/projects-without-stock") return [{ slug: "thoth-mall", project: "Thoth Mall", runs: 0, latest_run: null, latest: null }];
  if (p === "/api/siteprogress/thoth-mall/stock" && method === "GET") { if (server.stockFails) throw new Error("boom"); return stockView(); }
  if (p === "/api/siteprogress/thoth-mall/stock" && method === "POST") { const b = JSON.parse(o.body); server.posts.push(b); server.chosen = b.stock; return stockView(); }
  if (p === "/api/siteprogress/thoth-mall") { server.loads++; return { slug: "thoth-mall", structure: STRUCTURE, rooms: 1, services: ["Electrical"], activities: { Electrical: [] }, progress_summary: {}, has_boq: true }; }
  if (p === "/api/siteprogress/thoth-mall/service/Electrical") return svcState();
  if (p === "/api/siteprogress/thoth-mall/pnl/Electrical") return { service: "Electrical", room: null, project: { done_value: 0, remaining_value: 0, pct_value_done: 1 }, by_activity: {}, waste: { available: false }, rated_items: 0, total_items: 0, unmapped_value: { items: 0 } };
  if (p === "/api/siteprogress/thoth-mall/realistic/Electrical") return { service: "Electrical", has_run: !!server.chosen, items: [] };
  if (p === "/api/siteprogress/thoth-mall/dpr/today") return { count: 0 };
  throw new Error("unmocked route: " + p);
}
global.fetch = window.fetch = async (url, o) => { let r; try { r = route(url, o); } catch (e) { return { ok: false, status: 404, json: async () => ({}), text: async () => String(e.message) }; }
  return { ok: true, status: 200, json: async () => r, text: async () => JSON.stringify(r) }; };
window.confirm = () => true; window.alert = () => {};
window.eval(fs.readFileSync(new URL("../frontend/siteprogress.js", import.meta.url), "utf8"));
const flush = async (n = 20) => { for (let i = 0; i < n; i++) await new Promise((r) => setTimeout(r, 0)); };
const T = (el) => (el ? el.textContent : "").replace(/\s+/g, " ").trim();
let pass = 0; const ok = (c, m) => { assert.ok(c, "FAIL: " + m); pass++; };

async function main() {
  Object.defineProperty(document, "readyState", { value: "complete", configurable: true });
  document.dispatchEvent(new window.Event("DOMContentLoaded"));
  await flush();
  document.querySelector('.spnav button[data-v="siteprogress"]').click();
  await flush(30);

  // 1. Thoth Mall opens although /api/projects is empty
  ok(document.getElementById("sp-proj"), "project bar shown (no 'No project yet')");
  ok([...document.querySelectorAll("#sp-proj option")].map((o) => o.value).join() === "thoth-mall,__new__", "Thoth Mall is in the project dropdown");
  ok(!/No project yet/.test(T(document.getElementById("siteprogress"))), "no 'No project yet' message");
  // 2. stock line: not connected, with a way to connect
  ok(/Stock:\s*not connected/.test(T(document.getElementById("sp-stock"))), "says Stock: not connected -> " + T(document.getElementById("sp-stock")));
  ok(T(document.getElementById("sp-stock-change")) === "Connect stock", "link reads Connect stock");
  // 3. picker lists both stocks with kind / materials / links found
  document.getElementById("sp-stock-change").click(); await flush(3);
  const m = document.getElementById("sp-modal");
  ok(m, "picker opens");
  const t = T(m);
  ok(/Live Sheet 4F2A/.test(t) && /Google Sheet/.test(t) && /1204 materials/.test(t) && /142 of 148 saved links found/.test(t), "sheet row: name, kind, materials, links found -> " + t);
  ok(/Hyatt stock/.test(t) && /Upload/.test(t) && /stock_aug\.xlsx/.test(t) && /0 of 148 saved links found/.test(t), "upload row: name, kind, filename, links found");
  ok(!m.querySelector("input:checked"), "nothing pre-selected when none is connected");
  // 4. saving with nothing picked does nothing
  document.getElementById("sp-modal-save").click(); await flush(3);
  ok(server.posts.length === 0 && document.getElementById("sp-modal"), "no pick -> no request, modal stays");
  // 5. pick the Google Sheet
  const before = server.loads;
  m.querySelector('input[value="live-sheet-4f2a"]').checked = true;
  document.getElementById("sp-modal-save").click(); await flush(30);
  ok(JSON.stringify(server.posts) === JSON.stringify([{ stock: "live-sheet-4f2a" }]), "POSTs exactly {stock: live-sheet-4f2a}");
  ok(!document.getElementById("sp-modal"), "modal closes");
  ok(server.loads > before, "project re-read after the pick");
  ok(/Stock:\s*Live Sheet 4F2A · Google Sheet/.test(T(document.getElementById("sp-stock"))), "line now names the sheet -> " + T(document.getElementById("sp-stock")));
  ok(T(document.getElementById("sp-stock-change")) === "Change", "link reads Change");
  // 6. reopening: current is checked and marked, a way back to the default is offered
  document.getElementById("sp-stock-change").click(); await flush(3);
  const m2 = document.getElementById("sp-modal");
  ok(m2.querySelector('input[value="live-sheet-4f2a"]').checked, "current stock pre-selected");
  ok(/in use/.test(T(m2)), "current stock marked in use");
  ok(m2.querySelector('input[value=""]'), "'back to the stock named like this project' offered");
  m2.querySelector('input[value=""]').checked = true;
  document.getElementById("sp-modal-save").click(); await flush(30);
  ok(server.posts[1].stock === null, "back-to-default posts stock:null");
  ok(/not connected/.test(T(document.getElementById("sp-stock"))), "line back to not connected");
  // 7. the stock endpoint failing must not break the page
  server.stockFails = true;
  document.querySelector("#sp-proj").dispatchEvent(new window.Event("change")); await flush(30);
  ok(document.getElementById("sp-proj"), "page still renders when /stock errors");
  console.log(`PASS ${pass} checks`);
}
main().catch((e) => { console.error(e); process.exit(1); });
