// Client DPR UI: the "Summary sheets" choice in the Export dialog (default = DPR only, the client copy) and the "Manpower & notes" dialog.
// Real siteprogress.js against a mocked server; the requests it sends are the real ones.
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

  // ---------------- Export dialog ----------------
  ok(/Export today's DPR/.test($("sp-where").textContent), "the export link is still there");
  $("sp-where").click(); await flush(10);
  const chips = [...document.querySelectorAll("#sp-dpr-sum [data-dprsum]")];
  ok(chips.map((b) => b.dataset.dprsum).join() === "none,without,with", "three Summary-sheets choices in order: DPR only, without amount, with amount");
  ok(chips.map((b) => b.textContent.replace(/\s+/g, " ").trim()).join("|") === "DPR only|+ Summary, without amount|+ Summary, with amount ₹ internal", "chip wording");
  ok(on(chips[0]) && !on(chips[1]) && !on(chips[2]), "default = DPR only (the client copy)");
  const pos = (a, b) => a.compareDocumentPosition(b) & window.Node.DOCUMENT_POSITION_FOLLOWING;
  ok(pos($("sp-dpr-svc"), $("sp-dpr-sum")) && pos($("sp-dpr-sum"), $("sp-dpr-floors")), "the choice sits below Service and above Area");
  const t0 = new Date(); const today = new Date().toISOString().slice(0, 10);
  const help = () => $("sp-dpr-sumhelp").textContent.replace(/\s+/g, " ").trim();
  ok(help() === `Client copy: the DPR sheet only.File DPR_thoth-mall_${today}.xlsx`, "helper: client copy + the file name — " + help());

  chips[1].click(); await flush(2);
  const c2 = [...document.querySelectorAll("#sp-dpr-sum [data-dprsum]")];
  ok(on(c2[1]) && !on(c2[0]), "choosing 'without amount' selects only that one");
  ok(/Percentages only, no ₹\./.test(help()) && help().endsWith(`DPR_thoth-mall_${today}_summary-without-amount.xlsx`), "helper for without amount: no rupee, file name — " + help());
  c2[2].click(); await flush(2);
  ok(/Internal use, not for the client\./.test(help()) && help().endsWith(`_summary-with-amount.xlsx`), "helper for with amount: internal, file name");

  // file name follows service / date range / area exactly like the server's
  document.querySelector('#sp-dpr-svc [data-dprsvc="Fire"]').click(); await flush(2);
  ok(help().endsWith(`DPR_thoth-mall_${today}_fire_summary-with-amount.xlsx`), "service goes into the file name — " + help());
  $("sp-dpr-end").value = "2099-01-02"; $("sp-dpr-end").dispatchEvent(new window.Event("input")); await flush(2);
  ok(help().endsWith(`DPR_thoth-mall_${today}_to_2099-01-02_fire_summary-with-amount.xlsx`), "date range goes into the file name — " + help());
  document.querySelector('.sp-roomgroup input[data-dprroom="z1"]').click(); await flush(2);
  ok(help().endsWith(`_to_2099-01-02_fire_area_summary-with-amount.xlsx`), "area goes into the file name — " + help());

  // the request carries the choice
  $("sp-modal-save").click(); await flush(10);
  ok(lastHref && q().get("summary") === "with" && q().get("service") === "Fire" && q().get("end") === "2099-01-02" && q().getAll("rooms").join() === "z1",
     "Export sends summary=with together with service, range and rooms");
  ok(!$("sp-modal"), "the dialog closes after exporting");

  lastHref = null;
  $("sp-where").click(); await flush(10);
  $("sp-modal-save").click(); await flush(10);
  ok(q().get("summary") === "none" && !q().has("service") && !q().has("rooms"), "a fresh dialog exports the DPR only (summary=none)");
  $("sp-where").click(); await flush(10);
  ok(on(document.querySelector('#sp-dpr-sum [data-dprsum="none"]')), "re-opening starts from DPR only again, not from the last choice");
  $("sp-modal-x").click();

  // ---------------- Manpower & notes ----------------
  const link = $("sp-mp-open");
  ok(link && link.textContent === "Manpower & notes" && pos($("sp-where"), link), "'Manpower & notes' link sits right after the export link");
  ok(/Export today's DPR/.test($("sp-where").textContent) && $("sp-where").textContent.indexOf("Manpower") < 0, "the export link's own text is unchanged");
  link.click(); await flush(15);
  const todayLocal = iso(new Date());
  ok(dayGets[dayGets.length - 1] === todayLocal && $("sp-day-date").value === todayLocal, "opens on today's date (the engineer's local day) and loads it");
  const rows = [...document.querySelectorAll("#sp-day-body [data-svc]")];
  ok(rows.map((r) => r.dataset.svc).join() === "Electrical,Fire", "one row per service of the project");
  ok(rows.every((r) => r.querySelectorAll("input[data-role]").length === 3), "three boxes per service: supervisor, skilled, helper");
  ok([...rows[0].querySelectorAll("input")].every((i) => i.value === ""), "nothing is pre-filled for a day without entries");
  ok($("sp-day-client").value === "Thoth Developers" && $("sp-day-contractor").value === "", "the project's client / contractor come from the server");

  // pick another date -> that day's saved entry shows
  $("sp-day-date").value = "2026-10-12"; $("sp-day-date").dispatchEvent(new window.Event("change")); await flush(10);
  ok(dayGets[dayGets.length - 1] === "2026-10-12", "changing the date loads that day");
  const fire = document.querySelector('#sp-day-body [data-svc="Fire"]');
  ok([...fire.querySelectorAll("input")].map((i) => i.value).join() === "1,10,9" && $("sp-day-holdups").value === "Yesterday hold-up", "that day's saved numbers and notes are shown");

  // type today's: Electrical 1/8/12, Fire left empty, notes, parties
  $("sp-day-date").value = "2026-10-13"; $("sp-day-date").dispatchEvent(new window.Event("change")); await flush(10);
  const el = document.querySelector('#sp-day-body [data-svc="Electrical"]').querySelectorAll("input");
  el[0].value = "1"; el[1].value = "8"; el[2].value = "";
  $("sp-day-holdups").value = "Slab opening not handed over"; $("sp-day-plan").value = "Fire pipes B2";
  $("sp-day-contractor").value = "Mahesh Engg"; $("sp-day-prepared").value = "Site Engineer";
  $("sp-modal-save").click(); await flush(10);
  const body = posted[posted.length - 1];
  ok(body && body.date === "2026-10-13", "Save sends the chosen date");
  ok(JSON.stringify(body.manpower.Electrical) === JSON.stringify({ supervisor: 1, skilled: 8, helper: null }), "typed numbers are numbers, an untouched box is null (the server decides: 0 inside a filled row)");
  ok(JSON.stringify(body.manpower.Fire) === JSON.stringify({ supervisor: null, skilled: null, helper: null }), "a service left empty sends all-null (= not entered)");
  ok(body.holdups === "Slab opening not handed over" && body.plan === "Fire pipes B2", "hold-ups and plan are sent");
  ok(body.client === "Thoth Developers" && body.contractor === "Mahesh Engg" && body.prepared_by === "Site Engineer", "client / contractor / prepared by are sent");
  ok(!$("sp-modal"), "the dialog closes after saving");
  ok(/Export today's DPR/.test($("sp-where").textContent) && document.querySelectorAll("#sp-mp-open").length === 1, "still one 'Manpower & notes' link afterwards");
  console.log(fails ? `\n${fails} FAILURE(S)` : "\nALL CLIENT DPR UI TESTS PASSED");
  process.exit(fails ? 1 : 0);
}
main().catch((e) => { console.log("TEST ERROR", e); process.exit(1); });
