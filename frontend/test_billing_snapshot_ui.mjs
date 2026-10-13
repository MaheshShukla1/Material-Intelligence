// jsdom test: the MD snapshot on the Billing home (third card "Not invoiced yet", the drafts header, Share snapshot).
// The reported screen: five Installation drafts worth 28,77,527 on Thoth Mall while every card read 0. The overview is the REAL backend
// shape (billing_packages_fixture.json); the five drafts take the exact amounts from that screen. Every expected number is summed
// independently here (never read back from the page) so a wrong total cannot agree with itself.
import { JSDOM } from "jsdom";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
let pass = 0, fail = 0;
function ok(cond, msg) { if (cond) pass++; else { fail++; console.error("FAIL:", msg); } }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const FX = JSON.parse(fs.readFileSync(path.join(__dirname, "billing_packages_fixture.json"), "utf8"));
const clone = (x) => JSON.parse(JSON.stringify(x));
const P = "/api/billing/thoth-mall";
const T = (el) => (el ? el.textContent : "").replace(/\s+/g, " ").trim();
const click = (w, el) => el.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));

const DRAFTS = [["Electrical", 337178], ["Fire", 1306140], ["HVAC", 1147469], ["Plumbing", 51278], ["ELV", 35462]];
const SUM = DRAFTS.reduce((a, d) => a + d[1], 0);                       // 28,77,527, added here, not copied from the page

// five plain services, each with an open Installation draft, in the real overview shape
function scenario(drafts, extra) {
  const ov = clone(FX.ov);
  const svc0 = ov.services[0], ra0 = ov.ras[0];
  ov.services = DRAFTS.map(([n]) => {
    const s = clone(svc0);
    s.service = n; s.base = n; s.package = ""; s.set = true; s.supply_source = "stock"; s.challans = 0; s.wire = false;
    s.ready = { install: { ready: 1, blocked: null, has_draft: drafts.some((d) => d[0] === n), draft_label: "Installation RA 1", next_no: 2 } };
    s.can = clone(s.ready);
    return s;
  });
  ov.bases = DRAFTS.map(([n]) => ({ base: n, split: false, units: [n] }));
  ov.ras = drafts.map(([n, amt], i) => {
    const r = clone(ra0);
    r.id = "ra" + (i + 1); r.service = n; r.base = n; r.package = ""; r.label = n + " · Installation RA 1";
    r.status = "draft"; r.this_total = amt; r.up_to_date = amt; r.stage_this = { install: amt };
    return r;
  });
  ov.contract_value = 1147096759; ov.billed = 0; ov.ready_total = 0; ov.notes = [];
  extra && extra(ov);
  return ov;
}

async function boot(ov, opts) {
  opts = opts || {};
  const html = `<!doctype html><html><body>
    <header class="top" id="top"><div class="brand"><p id="ctx"></p></div>
      <div class="topact"><button id="home"></button><button id="pick"></button><button id="sync"></button><button id="syncgo"></button></div></header>
    <main><section id="drop" hidden></section><section id="busy" hidden></section>
      <section id="report"><div class="bar"><div class="tabs" id="svc"></div></div></section></main></body></html>`;
  const dom = new JSDOM(html, { url: "https://example.test/", runScripts: "dangerously", pretendToBeVisual: true });
  const { window } = dom;
  window.__BL_LIVE_MS = 3600000; window.__BL_BADGE_MS = 3600000;
  window.scrollTo = () => {};
  const state = { ov };
  window.fetch = (url) => {
    const u = String(url);
    let res = {};
    if (u === "/api/projects") res = [{ slug: "thoth-mall", project: "Thoth Mall", runs: 1, latest_run: "r1" }];
    else if (u === P + "/auto") res = { created: [] };
    else if (u === P) res = state.ov;
    return Promise.resolve({ ok: true, status: 200, json: async () => res, text: async () => JSON.stringify(res) });
  };
  if (opts.pre) opts.pre(window);
  window.eval(fs.readFileSync(path.join(__dirname, "siteprogress.js"), "utf8"));
  window.eval(fs.readFileSync(path.join(__dirname, "billing.js"), "utf8"));
  await sleep(0);
  [...window.document.querySelectorAll(".spnav button")].find((b) => b.dataset.v === "billing").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(80);
  return { window, d: window.document, state };
}
const money = (v) => "₹ " + v.toLocaleString("en-IN");
const clip = (w, store, fail) => Object.defineProperty(w.navigator, "clipboard", { configurable: true, value: { writeText: async (t) => { if (fail) throw new Error("denied"); store.push(t); } } });
const monthName = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

async function main() {
  // ---- the reported screen: five drafts, nothing invoiced, nothing ready
  let { window, d, state } = await boot(scenario(DRAFTS));
  let k = [...d.querySelectorAll(".bl-kpi")];
  ok(k.length === 3, "three cards");
  ok(/Contract value/.test(T(k[0])) && /Invoiced so far/.test(T(k[1])) && /Not invoiced yet/.test(T(k[2])), "cards: Contract | Invoiced so far | Not invoiced yet");
  ok(k[2].classList.contains("warn") && k[1].classList.contains("good"), "Not invoiced yet is the amber card, Invoiced is green");
  ok(SUM === 2877527, "independent sum of the five drafts is 28,77,527: " + SUM);
  ok(T(k[2].querySelector("b")) === money(SUM), "the amber card shows the drafts total (no longer 0): " + T(k[2].querySelector("b")));
  ok(/5 drafts ₹ 28,77,527/.test(T(k[2])) && /Ready, no draft ₹ 0/.test(T(k[2])), "the card says where the money is: " + T(k[2].querySelector("i")));
  ok(/₹ 0/.test(T(k[1].querySelector("b"))) && /0 RA invoiced/.test(T(k[1])), "Invoiced stays ₹ 0 / 0 RA");
  ok(!/Ready to bill, no draft yet/.test(T(d.querySelector(".bl-kpis"))), "the old third card title is gone");
  // the header row of the drafts, and the rows themselves
  const card = d.querySelector("#bl-svcl");
  const grp = card.querySelector(".bl-grp");
  ok(grp && card.firstElementChild === grp, "the drafts header is the first thing in the Services card");
  ok(/Not invoiced yet · 5 drafts/.test(T(grp)) && T(grp).endsWith(money(SUM)), "header: '" + T(grp) + "'");
  const rows = [...card.querySelectorAll(".bl-hr")];
  ok(rows.length === 5, "the same five service rows as before, untouched");
  const shown = rows.map((r) => Number(T(r.querySelector(".stt .am")).replace(/[^\d]/g, "")));
  ok(shown.reduce((a, b) => a + b, 0) === SUM, "the five row amounts on screen add up to the card: " + shown.join(" + "));
  ok(rows.every((r) => T(r.querySelector(".act .btn")) === "Open draft →"), "Open draft → is still on every row");
  ok(!!d.querySelector("#bl-share") && T(d.querySelector("#bl-share")) === "Share snapshot" && d.querySelector("#bl-share").classList.contains("primary"), "Share snapshot button");
  ok(!!d.querySelector("#bl-new") && !!d.querySelector("#bl-setup"), "header buttons (Billing setup, + New RA bill) are still there");

  // ---- Contract: full number on a computer, compact one for the phone's small card; the full number is also the tooltip
  const cb = k[0].querySelector("b");
  ok(T(cb.querySelector(".full")) === "₹ 1,14,70,96,759", "Contract full: " + T(cb.querySelector(".full")));
  ok(T(cb.querySelector(".cmp")) === "₹ 114.7 Cr", "Contract compact: " + T(cb.querySelector(".cmp")));
  ok(cb.getAttribute("title") === "₹ 1,14,70,96,759", "tooltip carries the full number");

  // ---- Share: the exact text, copied
  const got = [];
  clip(window, got);
  click(window, d.querySelector("#bl-share")); await sleep(20);
  const now = new window.Date();
  const want = [
    "Thoth Mall – Billing status",
    `As on ${now.getDate()} ${monthName[now.getMonth()]} ${now.getFullYear()}`,
    "",
    "Contract value: ₹ 1,14,70,96,759",
    "Invoiced so far: ₹ 0",
    "Not yet invoiced: ₹ 28,77,527",
    "• RA bills prepared, awaiting invoice (5): ₹ 28,77,527",
    "",
    "Prepared RA bills by service:",
    "• Electrical – ₹ 3,37,178",
    "• Fire – ₹ 13,06,140",
    "• HVAC – ₹ 11,47,469",
    "• Plumbing – ₹ 51,278",
    "• ELV – ₹ 35,462"].join("\n");
  ok(got.length === 1 && got[0] === want, "copied text is exactly the agreed status note:\n" + (got[0] || "(nothing copied)") + "\n--- wanted ---\n" + want);
  ok(/Snapshot copied/.test(T(d.getElementById("bl-toast"))), "a toast says it was copied");

  // ---- it follows the numbers: a refreshed overview (a draft grew, one was invoiced) is what gets shared next
  const ov2 = scenario(DRAFTS);
  ov2.ras[1].this_total = 1406140;                                         // Fire grew by 1,00,000
  ov2.ras[4].status = "locked"; ov2.ras[4].locked_at = "2026-10-12T10:00:00"; ov2.billed = 35462;   // ELV invoiced
  state.ov = ov2;
  const sel = d.getElementById("bl-proj"); sel.dispatchEvent(new window.Event("change", { bubbles: true })); await sleep(80);
  click(window, d.querySelector("#bl-share")); await sleep(20);
  const t2 = got[1] || "";
  ok(/Invoiced so far: ₹ 35,462 \(1 RA\)/.test(t2), "after a refresh: invoiced figure follows (1 RA): " + t2);
  const after = SUM + 100000 - 35462;                                      // 29,42,065, added here
  ok(after === 2942065 && t2.includes(`Not yet invoiced: ${money(after)}\n• RA bills prepared, awaiting invoice (4): ${money(after)}`), "28,77,527 + 1,00,000 - 35,462 = 29,42,065 in 4 drafts: " + t2);
  ok(/• Fire – ₹ 14,06,140/.test(t2) && !/ELV/.test(t2), "Fire's new amount shows, invoiced ELV is no longer in the not-invoiced list: " + t2);
  k = [...d.querySelectorAll(".bl-kpi")];
  ok(T(k[2].querySelector("b")) === money(after), "and so does the card: " + T(k[2].querySelector("b")));

  // ---- ready, no draft yet is part of "not invoiced" too, and is named separately
  ({ window, d } = await boot(scenario(DRAFTS.slice(0, 4), (ov) => {        // ELV has no draft; its ready amount is waiting
    ov.services[4].ready.install = { ready: 2353650, blocked: null, has_draft: false, draft_label: null, next_no: 1 };
    ov.ready_total = 2353650;
  })));
  k = [...d.querySelectorAll(".bl-kpi")];
  const four = SUM - 35462;                                                // 28,42,065
  ok(T(k[2].querySelector("b")) === money(four + 2353650), "drafts + ready = " + money(four + 2353650) + ": " + T(k[2].querySelector("b")));
  ok(/4 drafts ₹ 28,42,065/.test(T(k[2])) && /Ready, no draft ₹ 23,53,650/.test(T(k[2])), "the two parts are shown: " + T(k[2].querySelector("i")));
  ok(/Not invoiced yet · 4 drafts/.test(T(d.querySelector(".bl-grp"))) && T(d.querySelector(".bl-grp")).endsWith("₹ 28,42,065"), "the header counts drafts only: " + T(d.querySelector(".bl-grp")));
  const gt = [];
  clip(window, gt); click(window, d.querySelector("#bl-share")); await sleep(20);
  ok((gt[0] || "").includes(`Not yet invoiced: ${money(four + 2353650)}\n• RA bills prepared, awaiting invoice (4): ${money(four)}\n• Ready to bill, RA not prepared yet: ₹ 23,53,650`), "the shared text names both parts: " + gt[0]);

  // ---- one draft says "1 draft"; no drafts at all: no header, the card is only the ready part
  ({ d } = await boot(scenario(DRAFTS.slice(0, 1))));
  ok(/Not invoiced yet · 1 draft$/.test(T(d.querySelector(".bl-grp").firstElementChild)) && /1 draft ₹ 3,37,178/.test(T(d.querySelectorAll(".bl-kpi")[2])), "singular: 1 draft");
  ({ d } = await boot(scenario([], (ov) => { ov.services[1].ready.install = { ready: 500000, blocked: null, has_draft: false, draft_label: null, next_no: 1 }; ov.ready_total = 500000; })));
  ok(!d.querySelector(".bl-grp"), "no drafts: no 'Not invoiced yet' header");
  ok(T(d.querySelectorAll(".bl-kpi")[2].querySelector("b")) === "₹ 5,00,000" && /0 drafts ₹ 0/.test(T(d.querySelectorAll(".bl-kpi")[2])), "no drafts: the card is the ready amount: " + T(d.querySelectorAll(".bl-kpi")[2]));
  ok(T(d.querySelector(".bl-kpi b .cmp")) === "₹ 114.7 Cr", "compact crore");
  ({ d } = await boot(scenario([], (ov) => { ov.contract_value = 750000; })));
  ok(T(d.querySelector(".bl-kpi b .cmp")) === "₹ 7.5 L", "compact lakh: " + T(d.querySelector(".bl-kpi b .cmp")));
  ({ d } = await boot(scenario([], (ov) => { ov.contract_value = 4321; })));
  ok(T(d.querySelector(".bl-kpi b .cmp")) === "₹ 4,321", "under a lakh: the full number: " + T(d.querySelector(".bl-kpi b .cmp")));

  // ---- wording rules of the note: a service with two drafts says so; a zero part is left out; nothing invoiced says no "(0 RA)"
  { const two = [];
    ({ window, d } = await boot(scenario(DRAFTS.slice(0, 3), (ov) => { const r = clone(ov.ras[2]); r.id = "ra9"; r.this_total = 100000; ov.ras.push(r); })));
    clip(window, two); click(window, d.querySelector("#bl-share")); await sleep(20);
    const t = two[0] || "";
    ok(/• HVAC \(2 RAs\) – ₹ 12,47,469/.test(t) && /\(4\): ₹ 28,90,787/.test(t), "two drafts of one service: '(2 RAs)' and their sum: " + t);
    ok(!/Ready to bill/.test(t) && !/\(0 RA/.test(t) && !/drafts?/.test(t), "no zero 'Ready' line, no '(0 RA)', no 'draft' jargon: " + t);
    const none = [];
    ({ window, d } = await boot(scenario([], (ov) => { ov.services[1].ready.install = { ready: 500000, blocked: null, has_draft: false, draft_label: null, next_no: 1 }; ov.ready_total = 500000; })));
    clip(window, none); click(window, d.querySelector("#bl-share")); await sleep(20);
    ok(!/Prepared RA bills by service/.test(none[0] || "") && /Not yet invoiced: ₹ 5,00,000\n• Ready to bill, RA not prepared yet: ₹ 5,00,000$/.test(none[0] || ""), "no drafts: no by-service list, only the ready part: " + none[0]); }

  // ---- Share where the page is not allowed to use the clipboard
  // (1) clipboard refused, the older copy command works
  let ex = 0;
  ({ window, d } = await boot(scenario(DRAFTS), { pre: (w) => { w.document.execCommand = () => { ex++; return true; }; } }));
  clip(window, [], true); click(window, d.querySelector("#bl-share")); await sleep(30);
  ok(ex === 1 && /Snapshot copied/.test(T(d.getElementById("bl-toast"))) && !d.getElementById("bl-modal"), "clipboard refused, older copy works: copied, no dialog");
  // (2) nothing can copy (a phone on a plain http address): the text is shown, selected, ready to copy by hand
  ({ window, d } = await boot(scenario(DRAFTS)));
  clip(window, [], true); click(window, d.querySelector("#bl-share")); await sleep(30);
  const ta = d.getElementById("bl-snaptxt");
  ok(!!ta && ta.value.startsWith("Thoth Mall – Billing status") && /Not yet invoiced: ₹ 28,77,527/.test(ta.value), "nothing can copy: the text is shown in a dialog to copy by hand");
  ok(ta && ta.hasAttribute("readonly"), "and it is read-only");
  click(window, d.getElementById("bl-mx"));
  ok(!d.getElementById("bl-modal"), "the dialog closes");

  // ---- Share on a phone: the share sheet (WhatsApp is one tap away); cancelling it is silent
  let shared = [], abort = false; const cp = [];
  ({ window, d } = await boot(scenario(DRAFTS), { pre: (w) => {
    w.matchMedia = (q) => ({ matches: /pointer:\s*coarse/.test(q) });
    w.navigator.share = async (x) => { if (abort) { const e = new Error("cancel"); e.name = "AbortError"; throw e; } shared.push(x); };
  } }));
  clip(window, cp); click(window, d.querySelector("#bl-share")); await sleep(30);
  ok(shared.length === 1 && /^Thoth Mall – Billing status/.test(shared[0].text) && cp.length === 0, "phone: the share sheet gets the text, nothing is copied behind it");
  abort = true; d.getElementById("bl-toast") && d.getElementById("bl-toast").classList.remove("on");
  click(window, d.querySelector("#bl-share")); await sleep(30);
  ok(cp.length === 0 && !d.getElementById("bl-modal") && !(d.getElementById("bl-toast") || { classList: { contains: () => false } }).classList.contains("on"), "cancelling the share sheet does nothing (no error, no dialog, no copy)");
  // a computer (no touch) copies even when the browser offers a share function
  const sh2 = [], cp2 = [];
  ({ window, d } = await boot(scenario(DRAFTS), { pre: (w) => { w.matchMedia = () => ({ matches: false }); w.navigator.share = async (x) => sh2.push(x); } }));
  clip(window, cp2); click(window, d.querySelector("#bl-share")); await sleep(30);
  ok(cp2.length === 1 && sh2.length === 0, "computer: copies, does not open an operating-system share box");

  // ---- the rest of the home is untouched
  ok(d.querySelectorAll(".bl-hr").length === 5 && !!d.querySelector(".bl-ras") && /Live · refreshes every minute/.test(T(d.querySelector(".bl-live"))), "service rows, RA list and the live note are still there");

  console.log(`billing snapshot UI: ${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
}
main().catch((e) => { console.error(e); process.exit(1); });
