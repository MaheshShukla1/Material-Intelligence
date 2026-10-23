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
global.window = window;
global.document = window.document;
global.HTMLElement = window.HTMLElement;
global.MutationObserver = window.MutationObserver;

function hotelStructure() {
  return { id: "p0", type: "project", name: "Fixture Hotel", kind: "hotel",
    children: [{ id: "flo1", type: "floor", name: "Floor 1", children: [
      { id: "roo1", type: "room", name: "Room 101", children: [] }] }] };
}
function mallStructure() {
  return { id: "p0", type: "project", name: "Fixture Mall", kind: "mall",
    children: [{ id: "lev1", type: "level", name: "Ground", children: [
      { id: "zon1", type: "room", name: "Zone A", children: [] }] }] };
}

let qtyModeCalls = [];
let columnCalls = [];
let columnAnswer = null;
let boqResponse = null;   // set per-test before triggering upload
let structureKind = "hotel";

function svcState() {
  return {
    service: "Electrical", room: null, activities: [], mapping: {},
    act_pct: {}, overall_pct: 0, items: [], pnl_by_activity: {}, pnl_totals: {},
    pnl_unmapped_value: { items: 0 }, item_rooms: {}, unmapped: [],
  };
}

function route(url, opts) {
  const u = new URL(url, "http://localhost");
  const method = (opts && opts.method) || "GET";
  const p = u.pathname;
  if (p === "/api/projects") return [{ slug: "fixture", project: "Fixture" }];
  if (p === "/api/siteprogress/fixture") {
    // has_boq:false keeps loadState() on the setup screen (renderSetup),
    // which is the only place #sp-boq-file exists -- exactly where every
    // scenario below needs to trigger from.
    return { slug: "fixture", structure: structureKind === "mall" ? mallStructure() : hotelStructure(),
             rooms: 1, services: [], activities: {},
             progress_summary: { by_service: {}, overall: 0, rooms: 1 }, has_boq: false };
  }
  if (p === "/api/siteprogress/fixture/service/Electrical") return svcState();
  if (p === "/api/siteprogress/fixture/pnl/Electrical")
    return { service: "Electrical", room: null, project: { done_value: 0, remaining_value: 0, pct_value_done: 0 },
             by_activity: {}, waste: { available: false }, rated_items: 0, total_items: 0, unmapped_value: { items: 0 } };
  if (p === "/api/siteprogress/fixture/realistic/Electrical") return { service: "Electrical", has_run: false, items: [] };
  if (method === "POST" && p === "/api/siteprogress/fixture/boq") return boqResponse;
  if (method === "POST" && p === "/api/siteprogress/fixture/boq/column") { const b = JSON.parse(opts.body); columnCalls.push(b); return columnAnswer; }
  if (method === "POST" && p === "/api/siteprogress/fixture/qty-mode") {
    const body = JSON.parse(opts.body);
    qtyModeCalls.push(body);
    return { service: body.service, mode: body.mode, seeded: body.mode === "total" ? 3 : 0 };
  }
  throw new Error("unmocked route: " + method + " " + p);
}
global.fetch = window.fetch = async (url, opts) => ({ ok: true, json: async () => route(url, opts), text: async () => JSON.stringify(route(url, opts)) });
window.confirm = () => true; window.alert = () => {};

const src = fs.readFileSync(new URL("../frontend/siteprogress.js", import.meta.url), "utf8");
window.eval(src);
async function flush(n = 12) { for (let i = 0; i < n; i++) await new Promise((r) => setTimeout(r, 0)); }

async function boot() {
  Object.defineProperty(document, "readyState", { value: "complete", configurable: true });
  document.dispatchEvent(new window.Event("DOMContentLoaded"));
  await flush();
  document.querySelector('.spnav button[data-v="siteprogress"]').click();
  await flush(15);
  assert.ok(document.getElementById("sp-boq-file"), "FAIL: setup screen (with the BOQ file input) did not render -- check the has_boq:false fixture");
}

function triggerUpload() {
  const input = document.getElementById("sp-boq-file");
  assert.ok(input, "FAIL: BOQ file input missing -- is the service page rendered?");
  const fakeFile = new window.File(["dummy"], "boq.xlsx");
  Object.defineProperty(input, "files", { value: [fakeFile], configurable: true });
  input.dispatchEvent(new window.Event("change"));
}


const REAL_COLS = [["Code", "Client Ref No"], ["Description", "BOQ"], ["Unit", "BOQ Unit"], ["Qty", "Design Quantity"], ["Rate", "Supply Rate + Installation Rate"]];
const sheet = (o) => Object.assign({ sheet: "Electrical", format: "projectbase", lines: 477, rated: 477, value: 195015835,
  columns: REAL_COLS.map(([role, header]) => ({ role, header })), check: { status: "none", why: "This sheet has no Amount column, so the rates could not be cross-checked." },
  issues: [], choices: [] }, o || {});
const rep = (sh, svc) => ({ service: svc || "Electrical", lines: sh.lines, rated: sh.rated, value: sh.value, sheets: [sh] });
const T = (el) => (el ? el.textContent : "").replace(/\s+/g, " ").trim();
const upload = async (read, extra) => {
  boqResponse = Object.assign({ slug: "fixture", services: { Electrical: 477 }, skipped: [], seeded_mapping: {}, auto_rated: {}, needs_qty_mode: [], read }, extra || {});
  triggerUpload(); await flush(15);
};
let pass = 0, fail = 0;
const ok = (c, m) => { if (c) pass++; else { fail++; console.error("FAIL:", m); } };

async function main() {
  await boot();

  // 1. Rise Mumbai: Electrical split-rate sheet (no Amount column) + ELV plain Rate: one card, one block per service
  await upload({ Electrical: rep(sheet()), ELV: rep(sheet({ sheet: "ELV", lines: 92, rated: 92, value: 62265794, columns: REAL_COLS.map(([role, header]) => ({ role, header: role === "Rate" ? "Rate" : header })) }), "ELV") });
  let m = document.getElementById("sp-modal");
  ok(!!m && /How your BOQ was read/.test(T(m.querySelector("h2"))), "after the upload a card says how the BOQ was read: " + (m && T(m.querySelector("h2"))));
  const blocks = [...m.querySelectorAll(".sp-rd")];
  ok(blocks.length === 2, "one block per service, got " + blocks.length);
  ok(/Electrical/.test(T(blocks[0])) && /477 lines/.test(T(blocks[0])) && /477 priced/.test(T(blocks[0])) && /₹19\.50 Cr/.test(T(blocks[0])), "the lines / priced / value are told: " + T(blocks[0]));
  ok(/Rate ← Supply Rate \+ Installation Rate/.test(T(blocks[0])) && /Qty ← Design Quantity/.test(T(blocks[0])) && /Code ← Client Ref No/.test(T(blocks[0])), "and which column was used for what: " + T(blocks[0]));
  ok(/no Amount column/.test(T(blocks[0])) && !/tie/.test(T(blocks[0])), "no Amount column: it says plainly the rates could not be cross-checked (not a fake tick)");
  ok(blocks[0].classList.contains("ok") && !m.querySelector(".sp-rd select"), "all priced, nothing to ask: green, no question");
  ok(/Looks right/.test(T(m.querySelector("#sp-modal-save"))), "one tap confirms it");
  click(m.querySelector("#sp-modal-save")); await flush(8);
  ok(!document.getElementById("sp-modal"), "closing it does not block anything");

  // 2. Amount ties: a green tick with the numbers
  await upload({ Electrical: rep(sheet({ lines: 100, rated: 100, value: 1000000, check: { status: "ok", checked: 98, matched: 98, bad: [], sheet_total: 1000000, app_total: 1000000 } })) });
  m = document.getElementById("sp-modal");
  ok(/Amount ties to Qty × Rate on 98 of 98 lines/.test(T(m)), "the tick says what it checked: " + T(m.querySelector(".sp-rd")));
  click(m.querySelector("#sp-modal-save")); await flush(8);

  // 3. Amount does not tie: amber, the first lines that do not tie
  await upload({ Electrical: rep(sheet({ check: { status: "mismatch", checked: 100, matched: 80, sheet_total: 5000, app_total: 6200,
      bad: [{ code: "2", desc: "MCB 16A single pole", qty: 10, rate: 250, amount: 2000, expected: 2500 }] } })) });
  m = document.getElementById("sp-modal");
  ok(m.querySelector(".sp-rd").classList.contains("warn") && /80 of 100 lines/.test(T(m)) && /MCB 16A single pole/.test(T(m)) && /2,000/.test(T(m)) && /2,500/.test(T(m)),
     "a mismatch is amber and names the line, the sheet's amount and Qty × Rate: " + T(m.querySelector(".sp-rd")));
  click(m.querySelector("#sp-modal-save")); await flush(8);

  // 4. Two rate columns: a question, with the sheet's own headers, nothing priced
  await upload({ Electrical: rep(sheet({ rated: 0, value: 0, columns: REAL_COLS.slice(0, 4).map(([role, header]) => ({ role, header })),
      issues: ["More than one rate column (Rate, Unit Rate): choose which one is the rate."], choices: [{ role: "rate", headers: ["Rate", "Unit Rate"] }] })) });
  m = document.getElementById("sp-modal");
  const sel = m.querySelector(".sp-rd select");
  ok(!!sel && [...sel.options].map((o) => o.textContent).join("|").includes("Rate") && [...sel.options].some((o) => o.value === "Unit Rate"), "a dropdown with the columns of the sheet");
  ok(sel.value === "", "nothing is pre-chosen for the engineer");
  ok(m.querySelector(".sp-rd").classList.contains("warn") && /0 priced/.test(T(m)), "amber, 0 priced");
  // choosing -> POST /boq/column, the card is redrawn from the answer
  columnCalls = [];
  columnAnswer = { ok: true, read: { Electrical: rep(sheet({ lines: 477, rated: 477 })) } };
  sel.value = "Unit Rate"; sel.dispatchEvent(new window.Event("change", { bubbles: true }));
  click(m.querySelector(".sp-rd [data-rdpick]")); await flush(10);
  ok(columnCalls.length === 1 && columnCalls[0].sheet === "Electrical" && columnCalls[0].role === "rate" && columnCalls[0].header === "Unit Rate", "the choice is sent: " + JSON.stringify(columnCalls));
  m = document.getElementById("sp-modal");
  ok(m.querySelector(".sp-rd").classList.contains("ok") && /477 priced/.test(T(m)) && !m.querySelector(".sp-rd select"), "the card now shows it priced, no question left");
  click(m.querySelector("#sp-modal-save")); await flush(8);

  // 5. an upload with an issue that is not a question (headings skipped): amber text, still just one tap
  await upload({ Electrical: rep(sheet({ issues: ["1 row with a unit and a quantity look like section headings and were not read as items: MCB 16A"] })) });
  m = document.getElementById("sp-modal");
  ok(m.querySelector(".sp-rd").classList.contains("warn") && /MCB 16A/.test(T(m)), "a skipped-row warning is shown: " + T(m.querySelector(".sp-rd")));
  click(m.querySelector("#sp-modal-save")); await flush(8);

  // 6. the qty-mode question still follows, and also when the card is closed with Cancel / Close
  await upload({ Electrical: rep(sheet()) }, { needs_qty_mode: ["Electrical"] });
  ok(/How your BOQ was read/.test(T(document.querySelector("#sp-modal h2"))), "the read card comes first");
  click(document.getElementById("sp-modal-save")); await flush(10);
  ok(/One-time setup/.test(T(document.querySelector("#sp-modal h2"))), "then the existing one-time qty question, unchanged");
  click(document.getElementById("sp-modal-cancel")); await flush(5);
  await upload({ Electrical: rep(sheet()) }, { needs_qty_mode: ["Electrical"] });
  click(document.getElementById("sp-modal-cancel")); await flush(10);
  ok(/One-time setup/.test(T(document.querySelector("#sp-modal h2"))), "closing the card with Cancel still reaches the qty question");
  click(document.getElementById("sp-modal-cancel")); await flush(5);

  // 7. an answer without `read` (older server) opens no card and nothing breaks
  boqResponse = { slug: "fixture", services: { Electrical: 66 }, skipped: [], seeded_mapping: {}, auto_rated: {}, needs_qty_mode: [] };
  triggerUpload(); await flush(15);
  ok(!document.getElementById("sp-modal"), "no read in the answer: no card");

  // 8. a failed choice shows the reason and keeps the card
  await upload({ Electrical: rep(sheet({ rated: 0, choices: [{ role: "rate", headers: ["Rate", "Unit Rate"] }], issues: ["x"] })) });
  m = document.getElementById("sp-modal");
  const realFetch = global.fetch;
  global.fetch = window.fetch = async (u, o) => (String(u).endsWith("/boq/column") ? { ok: false, status: 400, text: async () => JSON.stringify({ detail: "'Nope' is not a rate column" }), json: async () => ({}) } : realFetch(u, o));
  const s2 = m.querySelector(".sp-rd select"); s2.value = "Unit Rate"; s2.dispatchEvent(new window.Event("change", { bubbles: true }));
  click(m.querySelector(".sp-rd [data-rdpick]")); await flush(10);
  ok(!!document.getElementById("sp-modal") && /not a rate column|could not|Failed/i.test(T(document.getElementById("sp-modal"))), "a refused choice is explained, the card stays");
  global.fetch = window.fetch = realFetch;

  console.log(`BOQ read card: ${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
}
function click(el) { el.dispatchEvent(new window.MouseEvent("click", { bubbles: true })); }
main();
