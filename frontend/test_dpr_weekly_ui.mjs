// Export dialog: "Last 7 days" shortcut and the Weekly Progress Report hint. Real siteprogress.js against a mocked server; the requests it sends are the real ones.
import { JSDOM } from "jsdom";
import fs from "fs";
import assert from "assert";

const html = `<!doctype html><html><body>
<header class="top" id="top"><div class="brand"><h1>Material intelligence</h1><p id="ctx"></p></div>
  <div class="topact"><button id="pick"></button><button id="sync"></button><button id="syncgo"></button><button id="home" hidden></button>
  <input id="file" type="file" hidden></div></header>
<main>
<section id="drop"></section><section id="busy" hidden></section><section id="report" hidden>
  <div class="bar"><div class="tabs" id="svc"></div></div>
</section>
</main></body></html>`;
const dom = new JSDOM(html, { url: "http://localhost/", runScripts: "outside-only", pretendToBeVisual: true });
const { window } = dom;
global.window = window; global.document = window.document; global.HTMLElement = window.HTMLElement; global.MutationObserver = window.MutationObserver;

const STRUCTURE = { id: "p0", type: "project", name: "Thoth Mall", kind: "mall", children: [
  { id: "l1", type: "floor", name: "B1", children: [{ id: "z1", type: "room", name: "Zone 1", children: [] }, { id: "z2", type: "room", name: "Zone 2", children: [] }] },
  { id: "l2", type: "floor", name: "B2", children: [{ id: "z3", type: "room", name: "Zone 1", children: [] }] }] };
const svcState = () => ({ service: "Electrical", room: null, activities: [], mapping: {}, act_pct: {}, overall_pct: 0, items: [], pnl_by_activity: {},
  pnl_totals: {}, pnl_unmapped_value: { items: 0 }, item_rooms: {}, item_progress: {}, item_room_qty: {}, activity_progress: {}, labour_buckets: {},
  unmapped: [], labour_only: {}, labour_pct: {}, labour_suggested: {} });

const DAYS = { "2026-10-12": { manpower: { Fire: { supervisor: 1, skilled: 10, helper: 9 } }, holdups: "Yesterday hold-up", plan: "", date: "2026-10-12" } };
const META = { client: "Thoth Developers", contractor: "", prepared_by: "" };
let lastHref = null, posted = [], dayGets = [];
function route(url, opts) {
  const u = new URL(url, "http://localhost"); const p = u.pathname; const P = "/api/siteprogress/thoth-mall";
  if (p === "/api/projects") return [{ slug: "thoth-mall", project: "Thoth Mall" }];
  if (p === P) return { slug: "thoth-mall", structure: STRUCTURE, rooms: 3, services: ["Electrical", "Fire"], activities: { Electrical: [], Fire: [] }, progress_summary: {}, has_boq: true };
  if (p === P + "/service/Electrical" || p === P + "/service/Fire") return svcState();
  if (p === P + "/pnl/Electrical") return { service: "Electrical", room: null, project: { done_value: 0, remaining_value: 0, pct_value_done: 0 }, by_activity: {}, waste: { available: false }, rated_items: 0, total_items: 0, unmapped_value: { items: 0 } };
  if (p === P + "/realistic/Electrical") return { service: "Electrical", has_run: false, items: [] };
  if (p === P + "/dpr/today") return { count: 2 };
  if (p === P + "/dpr/day" && (!opts || !opts.method || opts.method === "GET")) {
    const date = u.searchParams.get("date"); dayGets.push(date);
    const d = DAYS[date] || {};
    return { date, services: ["Electrical", "Fire"], manpower: d.manpower || {}, holdups: d.holdups || "", plan: d.plan || "", ...META };
  }
  if (p === P + "/dpr/day") { posted.push(JSON.parse(opts.body)); return { ok: true }; }
  throw new Error("unmocked route: " + p);
}
global.fetch = window.fetch = async (url, opts) => { const r = route(url, opts); return { ok: true, status: 200, json: async () => r, text: async () => JSON.stringify(r) }; };
window.HTMLAnchorElement.prototype.click = function () { lastHref = this.href; };
window.confirm = () => true; window.alert = () => {};
window.eval(fs.readFileSync(new URL("../frontend/siteprogress.js", import.meta.url), "utf8"));
const flush = async (n = 20) => { for (let i = 0; i < n; i++) await new Promise((r) => setTimeout(r, 0)); };
let fails = 0;
const ok = (c, m) => { if (c) console.log("PASS:", m); else { fails++; console.log("FAIL:", m); } };
const $ = (id) => document.getElementById(id);
const on = (b) => /eaf3de/i.test(b.style.background) || /eaf3de/i.test(b.getAttribute("style") || "");
const q = () => new URL(lastHref).searchParams;
const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

async function main() {
  Object.defineProperty(document, "readyState", { value: "complete", configurable: true });
  document.dispatchEvent(new window.Event("DOMContentLoaded"));
  await flush();
  document.querySelector('.spnav button[data-v="siteprogress"]').click();
  await flush(20);
  $("sp-where").click(); await flush(10);

  const set = (id, v) => { $(id).value = v; $(id).dispatchEvent(new window.Event("input", { bubbles: true })); };
  const help = () => $("sp-dpr-sumhelp").textContent;

  ok(!!$("sp-dpr-week") && /Last 7 days/.test($("sp-dpr-week").textContent), "a 'Last 7 days' button is in the export dialog");
  ok(!/Weekly Progress Report \(7 days chosen\)/.test(help()), "one day chosen: no weekly hint");

  // the reported scenario: end date = a Sunday the engineer picked, then 'Last 7 days'
  set("sp-dpr-end", "2026-10-18");
  $("sp-dpr-week").click(); await flush(2);
  ok($("sp-dpr-start").value === "2026-10-12" && $("sp-dpr-end").value === "2026-10-18", "Last 7 days = the 7 days ending on the End date (12 to 18 Oct, 7 days inclusive)");
  ok(/Weekly Progress Report \(7 days chosen\)/.test(help()), "the dialog says it will be the Weekly Progress Report");
  ok(/DPR_thoth-mall_2026-10-12_to_2026-10-18\.xlsx/.test(help()), "file name shows the week's range");

  // across a month end and a year end
  set("sp-dpr-end", "2026-11-02"); $("sp-dpr-week").click(); await flush(2);
  ok($("sp-dpr-start").value === "2026-10-27", "7 days ending 2 Nov start on 27 Oct (month boundary)");
  set("sp-dpr-end", "2027-01-03"); $("sp-dpr-week").click(); await flush(2);
  ok($("sp-dpr-start").value === "2026-12-28", "7 days ending 3 Jan start on 28 Dec (year boundary)");

  // end date empty -> today
  set("sp-dpr-end", ""); $("sp-dpr-week").click(); await flush(2);
  const t = new Date().toISOString().slice(0, 10);
  ok($("sp-dpr-end").value === t, "no End date picked: the week ends today");

  // not exactly 7 days -> no weekly hint
  set("sp-dpr-end", "2026-10-18"); set("sp-dpr-start", "2026-10-13");
  ok(!/Weekly Progress Report/.test(help()), "6 days chosen: no weekly hint (it stays a period report)");
  set("sp-dpr-start", "2026-10-11");
  ok(!/Weekly Progress Report/.test(help()), "8 days chosen: no weekly hint");

  // the request that goes out for a week is the ordinary export request with the 7-day range
  set("sp-dpr-start", "2026-10-12"); set("sp-dpr-end", "2026-10-18");
  [...document.querySelectorAll("button")].find((b) => b.textContent.trim() === "Export DPR").click(); await flush(5);
  ok(q().get("start") === "2026-10-12" && q().get("end") === "2026-10-18" && q().get("summary") === "none", "export request = start, end, summary=none (DPR only)");

  console.log(fails ? `\n${fails} FAILED` : "\nALL PASSED");
  process.exit(fails ? 1 : 0);
}
main().catch((e) => { console.log("CRASH", e); process.exit(1); });
