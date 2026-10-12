// jsdom: Site Progress without any stock. (1) empty install: start the first project from Site Progress itself, no Forecast upload;
// (2) "+ New project"; (3) connect stock FROM Site Progress: Google Sheet link, upload, a name mismatch, an error, and Re-sync.
import { JSDOM } from "jsdom";
import fs from "fs";
import assert from "assert";

const html = `<!doctype html><html><body>
<header class="top" id="top"><div class="brand"><h1>Material intelligence</h1><p id="ctx"></p></div>
  <div class="topact"><button id="pick"></button><button id="sync"></button><button id="syncgo"></button><button id="home" hidden></button><input id="file" type="file" hidden></div></header>
<main><section id="drop"></section><section id="busy" hidden></section><section id="report" hidden><div class="bar"><div class="tabs" id="svc"></div></div></section></main></body></html>`;
const dom = new JSDOM(html, { url: "http://localhost/", runScripts: "outside-only", pretendToBeVisual: true });
const { window } = dom;
global.window = window; global.document = window.document; global.HTMLElement = window.HTMLElement; global.MutationObserver = window.MutationObserver;

const STRUCTURE = { id: "p0", type: "project", name: "Thoth Mall", kind: "mall",
  children: [{ id: "l1", type: "level", name: "Level 1", children: [{ id: "z1", type: "zone", name: "Zone A", children: [] }] }] };
const svcState = () => ({ service: "Electrical", room: null, activities: [], mapping: {}, act_pct: {}, overall_pct: 1, items: [], pnl_by_activity: {}, pnl_totals: {},
  pnl_unmapped_value: { items: 0 }, item_rooms: {}, item_progress: {}, item_room_qty: {}, activity_progress: {}, labour_buckets: {}, unmapped: [], labour_only: {}, labour_pct: {}, labour_suggested: {} });
const srv = { built: false, projects: [], extras: [], stock: {}, posts: [], syncs: [], uploads: [], syncFail: null, madeSlug: null };
const view = (slug) => { const cur = srv.stock[slug] || null; return { project: slug, stock: slug, chosen: false, current: cur, options: cur ? [cur] : [] }; };
function route(url, o) {
  const u = new URL(url, "http://localhost"), p = u.pathname, method = (o && o.method) || "GET";
  if (p === "/api/projects") return srv.projects;
  if (p === "/api/siteprogress/projects-without-stock") return srv.extras;
  let m = p.match(/^\/api\/siteprogress\/([^/]+)\/stock$/);
  if (m && method === "GET") return view(m[1]);
  if (m && method === "POST") { const b = JSON.parse(o.body); srv.posts.push([m[1], b]); return view(m[1]); }
  if (p === "/api/sync-sheet" && method === "POST") {
    if (srv.syncFail) { const e = new Error(JSON.stringify({ detail: srv.syncFail })); e.status = 400; throw e; }
    const link = o.body.get("link"), project = o.body.get("project"); srv.syncs.push({ link, project });
    const slug = srv.madeSlug || project.toLowerCase().replace(/[^a-z0-9]+/g, "-");
    srv.stock[slug.startsWith("thoth") ? "thoth-mall" : slug] = { stock: slug, name: project, kind: "Google Sheet", created: "2026-10-12T12:00:00", source_link: link, materials: 5, links: { saved: 0, found: 0 } };
    return { run_id: "r9", meta: { project, project_slug: slug } };
  }
  if (p === "/api/upload" && method === "POST") {
    const f = o.body.get("file"), project = o.body.get("project"); srv.uploads.push({ name: f && f.name, project });
    const slug = project.toLowerCase().replace(/[^a-z0-9]+/g, "-");
    srv.stock[slug] = { stock: slug, name: project, kind: "Upload", filename: f.name, created: "2026-10-12T12:30:00", source_link: null, materials: 9, links: { saved: 0, found: 0 } };
    return { run_id: "r10", meta: { project, project_slug: slug } };
  }
  if (srv.built && (m = p.match(/^\/api\/siteprogress\/([^/]+)(\/.*)?$/))) {     // the structure + BOQ exist: the normal page loads
    const rest = m[2] || "";
    if (rest === "") return { slug: m[1], structure: STRUCTURE, rooms: 1, services: ["Electrical"], activities: { Electrical: [] }, progress_summary: {}, has_boq: true };
    if (rest === "/service/Electrical") return svcState();
    if (rest.startsWith("/pnl/")) return { service: "Electrical", room: null, project: { done_value: 0, remaining_value: 0, pct_value_done: 1 }, by_activity: {}, waste: { available: false }, rated_items: 0, total_items: 0, unmapped_value: { items: 0 } };
    if (rest.startsWith("/links/")) return { service: "Electrical", has_run: !!srv.stock[m[1]], stock_names: [], material_units: {}, items: [] };
    if (rest.startsWith("/realistic/")) return { service: "Electrical", has_run: !!srv.stock[m[1]], items: [] };
    if (rest === "/dpr/today") return { count: 0 };
  }
  if (/^\/api\/siteprogress\/[^/]+$/.test(p)) { const e = new Error("no Site Progress data for this project yet"); e.status = 404; throw e; }
  throw new Error("unmocked route: " + p);
}
global.fetch = window.fetch = async (url, o) => { let r; try { r = route(url, o); } catch (e) {
  return { ok: false, status: e.status || 404, json: async () => ({}), text: async () => e.message }; }
  return { ok: true, status: 200, json: async () => r, text: async () => JSON.stringify(r) }; };
window.confirm = () => true; window.alert = () => {};
window.eval(fs.readFileSync(new URL("../frontend/siteprogress.js", import.meta.url), "utf8"));
const flush = async (n = 25) => { for (let i = 0; i < n; i++) await new Promise((r) => setTimeout(r, 0)); };
const T = (el) => (el ? el.textContent : "").replace(/\s+/g, " ").trim();
const $ = (id) => document.getElementById(id === "sp" ? "siteprogress" : id);
let pass = 0; const ok = (c, m) => { assert.ok(c, "FAIL: " + m); pass++; };
const openSP = async () => { document.querySelector('.spnav button[data-v="siteprogress"]').click(); await flush(30); };

async function main() {
  Object.defineProperty(document, "readyState", { value: "complete", configurable: true });
  document.dispatchEvent(new window.Event("DOMContentLoaded")); await flush();

  // ---- 1. empty install: no stock, no project anywhere ----
  await openSP();
  ok(/Set up Site Progress/.test(T($("siteprogress"))) && $("sp-np-name") && /Building structure.*Bill of Quantities.*Open/.test(T($("siteprogress"))) && !/Upload a stock register/.test(T($("siteprogress"))) && !$("sp-newproj"), "empty state IS the wizard (name + the 3 steps), no Forecast detour, no New project button -> " + T($("siteprogress")));
  $("sp-np-go").click(); await flush(3);
  ok(/Type the project name first/.test(T(document.body)) || $("sp-np-name"), "empty name is refused");
  $("sp-np-name").value = "Thoth Mall"; $("sp-np-go").click(); await flush(30);
  ok(/Set up Site Progress/.test(T($("sp"))) && $("sp-step1") && $("sp-step2"), "the 3-step wizard opens for the new project");
  ok([...document.querySelectorAll("#sp-proj option")].map((o) => o.value).join() === "thoth-mall,__new__" && $("sp-proj").value === "thoth-mall", "dropdown shows the new project (selected) and, last, + New project…");
  ok(!$("sp-stock") && !/Stock:/.test(T($("sp"))), "no stock line anywhere on the bar; the wizard needs no stock");
  // structure + BOQ get done (server side), the page now loads normally with its toolbar
  srv.built = true; $("sp-proj").dispatchEvent(new window.Event("change", { bubbles: true })); await flush(30);
  ok(T($("sp-linkbtn")) === "Connect stock", "toolbar word is Connect stock while no stock is connected -> " + T($("sp-linkbtn")));

  // ---- 2. connect stock from Site Progress: Google Sheet link (no stock existed, so the link tab is first) ----
  $("sp-linkbtn").click(); await flush(5);
  ok($("sp-modal") && document.querySelector('[data-sktab="link"].on'), "no stock exists -> opens on the Google Sheet link tab");
  ok(/Stock will be named\s*Thoth Mall/.test(T($("sp-modal"))), "name is fixed to the project, nothing to type");
  $("sp-modal-save").click(); await flush(3);
  ok(/Paste the sheet link first/.test(T($("sp-sk-err"))) && srv.syncs.length === 0, "empty link -> inline message, no request");
  srv.syncFail = "the link did not return an Excel file. Make sure the sheet is Published to web as Microsoft Excel (.xlsx) and the link ends in output=xlsx.";
  $("sp-sk-link").value = "https://docs.google.com/spreadsheets/d/e/X/pub?output=csv"; $("sp-modal-save").click(); await flush(10);
  ok(/did not return an Excel file/.test(T($("sp-sk-err"))) && $("sp-modal") && !$("sp-modal-save").disabled, "server error shown inline, modal stays, button usable again");
  srv.syncFail = null;
  const LINK = "https://docs.google.com/spreadsheets/d/e/X/pub?output=xlsx";
  $("sp-sk-link").value = "  " + LINK + " "; $("sp-modal-save").click(); await flush(40);
  ok(JSON.stringify(srv.syncs) === JSON.stringify([{ link: LINK, project: "Thoth Mall" }]), "sync-sheet called with the trimmed link and the project's own name -> " + JSON.stringify(srv.syncs));
  ok(!$("sp-modal"), "modal closes on success");
  ok(srv.posts.length === 0, "same slug -> nothing to bind");
  ok(T($("sp-linkbtn")) === "Link stock" && !$("sp-stock"), "toolbar back to Link stock once connected; still no bar line -> " + T($("sp-linkbtn")));
  $("sp-linkbtn").click(); await flush(10);
  ok(/Reading from\s*Thoth Mall · Google Sheet/.test(T($("sp-lk-src"))) && $("sp-lk-resync") && $("sp-lk-change"), "Link stock dialog shows the stock, Re-sync and Change stock… -> " + T($("sp-lk-src")));

  // ---- 3. Re-sync reuses the stored link and the stock's name ----
  $("sp-lk-resync").click(); await flush(30);
  ok(!$("sp-modal"), "dialog closes on Re-sync");
  ok(srv.syncs.length === 2 && srv.syncs[1].link === LINK && srv.syncs[1].project === "Thoth Mall", "Re-sync posts the stored link + stock name");
  ok(srv.posts.length === 0, "Re-sync binds nothing");

  // ---- 4. a stock that lands under another name is bound, not left dangling ----
  srv.madeSlug = "live-sheet-4f2a";
  $("sp-linkbtn").click(); await flush(10); $("sp-lk-change").click(); await flush(5);
  ok(document.querySelector('[data-sktab="pick"].on'), "a stock exists -> Change stock… opens on 'Use existing'");
  document.querySelector('[data-sktab="link"]').click(); await flush(2);
  $("sp-sk-link").value = LINK; $("sp-modal-save").click(); await flush(40);
  ok(srv.posts.length === 1 && srv.posts[0][0] === "thoth-mall" && srv.posts[0][1].stock === "live-sheet-4f2a", "mismatched slug -> POST /stock binds it -> " + JSON.stringify(srv.posts));
  srv.madeSlug = null;

  // ---- 5. upload tab ----
  $("sp-linkbtn").click(); await flush(10); $("sp-lk-change").click(); await flush(5);
  document.querySelector('[data-sktab="file"]').click(); await flush(2);
  ok($("sp-sk-file") && /Upload stock/.test(T($("sp-modal-save"))), "upload tab shows a file chooser and an Upload button");
  $("sp-modal-save").click(); await flush(3);
  ok(/Choose the stock file first/.test(T($("sp-sk-err"))) && srv.uploads.length === 0, "no file -> inline message");
  const file = new window.File(["x"], "stock_oct.xlsx");
  Object.defineProperty($("sp-sk-file"), "files", { value: [file] });
  $("sp-modal-save").click(); await flush(40);
  ok(srv.uploads.length === 1 && srv.uploads[0].name === "stock_oct.xlsx" && srv.uploads[0].project === "Thoth Mall", "upload posts the file + project name");
  $("sp-linkbtn").click(); await flush(10);
  ok(/Reading from\s*Thoth Mall · Upload · stock_oct\.xlsx/.test(T($("sp-lk-src"))) && !$("sp-lk-resync"), "dialog shows the upload; no Re-sync for a file -> " + T($("sp-lk-src")));
  document.getElementById("sp-modal-save") && document.getElementById("sp-modal-cancel") && document.getElementById("sp-modal-cancel").click(); await flush(3);

  // ---- 6. "+ New project…" is the last dropdown row, not a button ----
  ok(!$("sp-newproj") && /\+ New project/.test(T(document.querySelector("#sp-proj option:last-child"))), "no button; last dropdown row is + New project…");
  const pick = (v) => { $("sp-proj").value = v; $("sp-proj").dispatchEvent(new window.Event("change", { bubbles: true })); };
  pick("__new__"); await flush(3);
  ok($("sp-np-name") && /Set up Site Progress/.test(T($("siteprogress"))) && $("sp-proj").value === "thoth-mall", "picking it opens the blank wizard; dropdown still shows the current project");
  $("sp-np-go").click(); await flush(3);
  ok($("sp-np-name"), "empty name keeps the blank wizard");
  $("sp-np-name").value = "Hyatt Annex"; $("sp-np-go").click(); await flush(30);
  ok($("sp-proj").value === "hyatt-annex" && /Building structure/.test(T($("siteprogress"))) && !$("sp-np-name"), "second project created, real wizard shown");
  ok([...document.querySelectorAll("#sp-proj option")].map((o) => o.value).join() === "thoth-mall,hyatt-annex,__new__", "both projects in the dropdown");
  pick("thoth-mall"); await flush(30);
  ok($("sp-proj").value === "thoth-mall", "switching back to the first project works");
  pick("__new__"); await flush(3);
  $("sp-np-name").value = "thoth  mall!"; $("sp-np-go").click(); await flush(30);
  ok($("sp-proj").value === "thoth-mall" && document.querySelectorAll("#sp-proj option").length === 3, "a name with the same slug opens the existing project, no duplicate");
  console.log(`PASS ${pass} checks`);
}
main().catch((e) => { console.error(e); process.exit(1); });
