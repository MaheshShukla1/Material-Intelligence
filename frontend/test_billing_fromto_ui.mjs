// jsdom test: the From-To (measurement backup) facts on the Billing screens, with the REAL backend payloads captured from the real Thoth BOQ
// (billing_packages_fixture.json: ra5 = an open Installation draft of three Rmt cable-tray lines) plus the "fromto" facts the server adds
// (backend/test_billing_fromto.py pins how they are computed). Rules pinned here:
//   * Billing home: when open drafts have length lines without From-To, ONE strip says how many rupees (and which draft to open); it is
//     absent when nothing is missing, and in the Supply view; it never blocks anything;
//   * the RA screen: a "Measurement backup" strip above the Annexure and one chip per length line (amber "No From–To" / "From–To 40 of 100 Rmt",
//     green "From–To ✓"); a Supply RA / a bill with no length line shows neither;
//   * a chip opens the SAME measurement sheet as Site Progress (window.MIMeasure) for that line's item, in the BOQ service (not the Work Order
//     name), does not expand the line, and after an entry the RA screen is re-read;
//   * an invoiced RA shows the chips as plain facts (nothing to click: it is frozen);
//   * Download: "With supporting backup" is ticked for you when the RA has measurement entries, or from "Download backup"; it stays off when
//     there are none; the dialog says before downloading which length items have no From–To (₹), and that Annexure D follows when all do.
import { JSDOM } from "jsdom";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
let pass = 0, fail = 0;
function ok(cond, msg) { if (cond) pass++; else { fail++; console.error("FAIL:", msg); } }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const FX = JSON.parse(fs.readFileSync(path.join(__dirname, "billing_packages_fixture.json"), "utf8"));
const CH = JSON.parse(fs.readFileSync(path.join(__dirname, "billing_challan_fixture.json"), "utf8"));
const clone = (x) => JSON.parse(JSON.stringify(x));
const P = "/api/billing/thoth-mall";
const T = (el) => (el ? el.textContent : "").replace(/\s+/g, " ").trim();
const click = (w, el) => el.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));

const CODES = FX.bills.ra5.lines.map((l) => l.item_code);          // 11.1.1, 11.1.2, 11.1.3 -- all Rmt
const line = (code, n, this_, backed, amt) => ({ n, this: this_, backed, unbacked: Math.round((this_ - backed) * 1e4) / 1e4, unbacked_amount: amt });
function fromto() {
  const l = FX.bills.ra5.lines;
  const lines = { [CODES[0]]: line(CODES[0], 0, l[0].install.this, 0, 10000), [CODES[1]]: line(CODES[1], 2, l[1].install.this, 400, 6000), [CODES[2]]: line(CODES[2], 1, l[2].install.this, l[2].install.this, 0) };
  return { lines, items: 3, unbacked_items: 2, with_entries: 2, value: 40000, unbacked_value: 16000, unknown: 0 };
}

async function boot(opts) {
  opts = opts || {};
  const html = `<!doctype html><html><body>
    <header class="top" id="top"><div class="brand"><p id="ctx"></p></div>
      <div class="topact"><button id="home"></button><button id="pick"></button><button id="sync"></button><button id="syncgo"></button></div></header>
    <main><section id="drop" hidden></section><section id="busy" hidden></section>
      <section id="report"><div class="bar"><div class="tabs" id="svc"></div></div></section></main></body></html>`;
  const dom = new JSDOM(html, { url: "https://example.test/", runScripts: "dangerously", pretendToBeVisual: true });
  const { window } = dom; const calls = [];
  window.__BL_LIVE_MS = 3600000; window.__BL_BADGE_MS = 3600000; window.scrollTo = () => {};
  const bills = { ra4: clone(FX.bills.ra4), ra5: clone(FX.bills.ra5) };
  bills.ra5.ra.inv_missing = [];
  if (!opts.noFromto) bills.ra5.fromto = opts.fromto || fromto();
  const ov = clone(FX.ov);
  if (!opts.noFromto) {
    const r5 = ov.ras.find((r) => r.id === "ra5");
    const f = opts.fromto || fromto();
    r5.fromto = { items: f.items, unbacked_items: f.unbacked_items, with_entries: f.with_entries, value: f.value, unbacked_value: f.unbacked_value, unknown: f.unknown };
    if (f.unbacked_value > 0) ov.no_backup = { value: f.unbacked_value, items: f.unbacked_items, drafts: 1 };
  }
  if (opts.locked) { ov.ras.find((r) => r.id === "ra5").status = "locked"; bills.ra5.ra.status = "locked"; }
  const zb = bills.ra5.fromto;
  window.fetch = (url, o) => {
    const u = String(url), method = (o && o.method) || "GET";
    const body = o && o.body ? JSON.parse(o.body) : null;
    calls.push({ u, method, body });
    let res = {}, status = 200;
    if (u === "/api/projects") res = [{ slug: "thoth-mall", project: "Thoth Mall", runs: 1, latest_run: "r1" }];
    else if (u === P + "/auto") res = { created: [] };
    else if (u === P) res = clone(ov);
    else if (u.startsWith(P + "/measurement/coverage")) {
      const f = bills.ra5.fromto;
      res = { ras: [{ id: "ra5", applies: true, items: 3, earlier: [], legacy_ra: false, adjusted: 0,
        ...(f ? { fromto: { items: f.items, unbacked_items: f.unbacked_items, with_entries: f.with_entries, value: f.value, unbacked_value: f.unbacked_value,
          gaps: Object.entries(f.lines).filter(([, v]) => v.unbacked).map(([k, v]) => ({ sr: "", desc: bills.ra5.lines.find((l) => l.item_code === k).desc, unit: "Rmt", unbacked: v.unbacked, this: v.this })) } } : {}) }] };
    }
    else if (u.startsWith("/api/siteprogress/thoth-mall/progress/runs") && method === "POST") {
      res = { svc: { items: [] }, zone: { runs: [], active: 0, done: 0, other: 0 }, summary: { n: 1, backed: 5, done: 5 } };
    }
    else if (u.startsWith("/api/siteprogress/thoth-mall/progress/runs")) {
      res = { unit: "Rmt", runs_ok: true, rooms: {}, summary: { n: 0, backed: 0, done: 100 },
              scopes: [{ key: "~project", kind: "project", label: "Whole project (feeder / main route)", group: "", plan: 1000, done: 100, allowed: true, why: "", writable: true, runs: [], active: 0, other: 100 }] };
    }
    else if (/\/ra\/ra\d+$/.test(u) && method === "GET") res = clone(bills[u.split("/").pop()]);
    else if (/\/challans\//.test(u) && method === "GET") res = CH.ch_empty;
    return Promise.resolve({ ok: status < 400, status, json: async () => res, text: async () => JSON.stringify(res) });
  };
  window.eval(fs.readFileSync(path.join(__dirname, "siteprogress.js"), "utf8"));
  if (opts.noMeasure) delete window.MIMeasure;
  window.eval(fs.readFileSync(path.join(__dirname, "billing.js"), "utf8"));
  await sleep(0);
  [...window.document.querySelectorAll(".spnav button")].find((b) => b.dataset.v === "billing").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(80);
  return { window, d: window.document, calls, bills, ov };
}
const open = async (w, d, id) => { click(w, d.querySelector(`[data-ra="${id}"]`)); await sleep(80); };

async function main() {
  // ================= 1. Billing home
  let { window, d, calls } = await boot();
  let st = d.getElementById("bl-nobk");
  ok(!!st, "the home carries the no-backup strip when open drafts have length lines without From–To");
  ok(/of the open bills has no measurement backup/.test(T(st)) && /2 length lines/.test(T(st)), `it says how much and how many lines, got "${T(st)}"`);
  ok(/Optional/.test(T(st)) && /not held back/.test(T(st)), "and that nothing is held back");
  ok(/₹ ?16,000|₹16,000/.test(T(st)), `in rupees, got "${T(st)}"`);
  ok(st.classList.contains("gap"), "amber, not red");
  ok(!!d.getElementById("bl-new") && !d.getElementById("bl-new").disabled, "the buttons around it are untouched");
  click(window, d.getElementById("bl-nobkgo")); await sleep(80);
  ok(/Installation RA 1/.test(T(d.querySelector("#billing h2"))), "See which opens the draft with the most missing: " + T(d.querySelector("#billing h2")));

  // Supply view: not shown
  ({ window, d } = await boot());
  const supBtn = d.querySelector('[data-lens="supply"]');
  if (supBtn) { click(window, supBtn); await sleep(40); ok(!d.getElementById("bl-nobk"), "the Supply view does not show it (supply has no measurement)"); }

  // nothing missing / no data: nothing at all
  ({ window, d } = await boot({ noFromto: true }));
  ok(!d.getElementById("bl-nobk"), "no strip when the server sends no no_backup");
  ({ window, d } = await boot({ fromto: { lines: {}, items: 0, unbacked_items: 0, with_entries: 0, value: 0, unbacked_value: 0, unknown: 0 } }));
  ok(!d.getElementById("bl-nobk"), "no strip when there is nothing without backup");

  // ================= 2. the RA screen
  ({ window, d, calls } = await boot());
  await open(window, d, "ra5");
  const mb = d.getElementById("bl-mb");
  ok(!!mb && /Measurement backup/.test(T(mb)), "the RA screen has the Measurement backup strip");
  ok(/1 of 3 length lines has From–To/.test(T(mb)) || /1 of 3 length lines have From–To/.test(T(mb)), `strip says how many lines have From–To, got "${T(mb)}"`);
  ok(/₹16,000|₹ 16,000/.test(T(mb)), "and the rupees without: " + T(mb));
  ok(mb.classList.contains("gap") && !!d.getElementById("bl-mbadd") && /Add measurements/.test(T(d.getElementById("bl-mbadd"))), "amber, with an Add measurements button");
  ok(!d.getElementById("bl-mbdl") && !/Download backup/.test(T(mb)), "and NO second download button: downloading is only the Download button at the top");
  ok(mb.compareDocumentPosition(d.querySelector("h3.bl-h3")) & 4, "it sits above the Annexure heading");
  const chips = [...d.querySelectorAll(".bl-lr .bl-ft")];
  ok(chips.length === 3, "one chip per length line, got " + chips.length);
  ok(T(chips[0]) === "📏 No From–To" && chips[0].classList.contains("warn") && chips[0].tagName === "BUTTON", `line 1: no entry, got "${T(chips[0])}"`);
  ok(/^📏 From–To 400 of 1,222 Rmt$/.test(T(chips[1])) || /^📏 From–To 400 of 1222 Rmt$/.test(T(chips[1])), `line 2: partly backed, got "${T(chips[1])}"`);
  ok(T(chips[2]) === "📏 From–To ✓" && chips[2].classList.contains("ok"), `line 3: fully backed, got "${T(chips[2])}"`);

  // chip -> the shared measurement sheet, in the BOQ service
  const row0 = d.querySelectorAll(".bl-lr")[0];
  const wasOpen = row0.classList.contains("open");
  calls.length = 0;
  click(window, chips[0]); await sleep(100);
  ok(row0.classList.contains("open") === wasOpen, "clicking the chip does not expand the line");
  ok(calls.some((c) => c.u.startsWith("/api/siteprogress/thoth-mall/progress/runs?service=Electrical&item_code=" + encodeURIComponent(CODES[0]))),
     "it asks for the measurements of that item in the BOQ service (Electrical), not the Work Order name: " + JSON.stringify(calls.map((c) => c.u)));
  const m = d.getElementById("sp-modal");
  ok(!!m && T(m.querySelector("h2")) === "Measurement · " + CODES[0], "the same measurement sheet opens, for that item: " + (m && T(m.querySelector("h2"))));
  // add an entry from here -> the RA screen is re-read
  const set = (el, v) => { el.value = String(v); el.dispatchEvent(new window.Event("input", { bubbles: true })); };
  set(m.querySelector('[data-m="from"]'), "Riser R1"); set(m.querySelector('[data-m="to"]'), "Floor 3");
  set(m.querySelector('[data-m="start"]'), 0); set(m.querySelector('[data-m="end"]'), 5);
  calls.length = 0;
  click(window, m.querySelector('[data-madd="one"]')); await sleep(120);
  ok(calls.some((c) => c.method === "POST" && /\/progress\/runs$/.test(c.u) && c.body.scope === "project" && c.body.item_code === CODES[0]), "the entry is posted for that item");
  ok(calls.some((c) => c.method === "GET" && /\/ra\/ra5$/.test(c.u)), "and the RA screen is read again, so its chips and strip are current");

  // the strip button fixes gaps: the sheet of the FIRST line without From-To opens (line 1 here), and after an entry the strip is re-read
  await sleep(100);
  const closeSheet = () => { const x = d.getElementById("sp-modal"); if (x) x.remove(); };
  closeSheet(); calls.length = 0;
  click(window, d.getElementById("bl-mbadd")); await sleep(100);
  ok(!!d.getElementById("sp-modal") && T(d.querySelector("#sp-modal h2")) === "Measurement · " + CODES[0], "Add measurements opens the sheet of the first line without From–To: " + T(d.querySelector("#sp-modal h2")));
  ok(!calls.some((c) => /\/download/.test(c.u)) && !d.getElementById("bl-modal"), "it does not open the Download dialog");
  closeSheet();

  // ================= 3. no length line / supply RA -> nothing
  ({ window, d } = await boot({ noFromto: true }));
  await open(window, d, "ra5");
  ok(!d.getElementById("bl-mb") && d.querySelectorAll(".bl-ft").length === 0, "a bill without From–To facts shows no strip and no chips");
  await (async () => { click(window, d.getElementById("bl-back")); await sleep(60); })();
  await open(window, d, "ra4");
  ok(!d.getElementById("bl-mb") && d.querySelectorAll(".bl-ft").length === 0, "a Supply RA shows neither");

  // ================= 4. invoiced: facts only
  ({ window, d } = await boot({ locked: true }));
  await open(window, d, "ra5");
  const lc = [...d.querySelectorAll(".bl-lr .bl-ft")];
  ok(lc.length === 3 && lc.every((c) => c.tagName === "SPAN"), "an invoiced RA shows the chips as plain text (frozen, nothing to click)");
  ok(!d.getElementById("bl-mbadd") && !d.getElementById("bl-mbdl"), "and the strip has no button at all");
  ok(/as invoiced/.test(T(d.getElementById("bl-mb"))), "the strip says it is as invoiced");

  // ================= 5. no sheet available -> plain text, nothing broken
  ({ window, d } = await boot({ noMeasure: true }));
  await open(window, d, "ra5");
  ok([...d.querySelectorAll(".bl-lr .bl-ft")].every((c) => c.tagName === "SPAN"), "without the Site Progress module the chips are plain text");

  // ================= 6. Download
  ({ window, d, calls } = await boot());
  await open(window, d, "ra5");
  click(window, d.getElementById("bl-dl")); await sleep(150);
  const meas = () => d.querySelector('#bl-modal input[data-sh="meas"]');
  ok(!!meas() && meas().checked, "from an RA that has entries, With supporting backup is ticked for you");
  ok(/Annexure D|From–To/.test(T(d.querySelector("#bl-modal"))) , "the dialog mentions the From–To detail");
  const wns = [...d.querySelectorAll("#bl-modal .bl-wn")].map(T);
  ok(wns.some((t) => /2 of 3 length items have quantity without From–To/.test(t) && /16,000/.test(t) && /Annexure D lists only the entries/.test(t)), "it says which length items have no From–To, in ₹: " + JSON.stringify(wns));
  ok(calls.some((c) => c.u.startsWith(P + "/measurement/coverage")), "the facts come from the coverage call");
  ok(d.querySelector("#bl-modal .bl-fi") && /Download/.test(T(d.getElementById("bl-dl-go"))), "the download button is there, nothing is blocked");
  click(window, [...d.querySelectorAll("#bl-modal .mact .btn")].find((b) => /Cancel/.test(T(b)))); await sleep(30);

  // a user who turned it off keeps it off when the dialog is re-opened from the same RA? (a fresh open is a fresh default) -- skipped: not claimed

  // no entries at all: off by default
  ({ window, d, calls } = await boot({ fromto: { lines: { [CODES[0]]: line(CODES[0], 0, 1350.5, 0, 10000) }, items: 1, unbacked_items: 1, with_entries: 0, value: 10000, unbacked_value: 10000, unknown: 0 } }));
  await open(window, d, "ra5");
  click(window, d.getElementById("bl-dl")); await sleep(150);
  ok(!meas().checked, "with no entry at all the backup is not ticked (as it always was)");
  click(window, [...d.querySelectorAll("#bl-modal .mact .btn")].find((b) => /Cancel/.test(T(b)))); await sleep(30);
  // the dialog still says plainly that nothing was entered
  click(window, d.getElementById("bl-dl")); await sleep(150);
  meas().click(); await sleep(60);                                                                       // ticking the backup brings the plain warning
  ok([...d.querySelectorAll("#bl-modal .bl-wn")].some((t) => /no From–To measurement entered for its 1 length item/.test(T(t))), "once the backup is ticked the Download dialog says no From–To was entered: " + JSON.stringify([...d.querySelectorAll("#bl-modal .bl-wn")].map(T)));
  click(window, [...d.querySelectorAll("#bl-modal .mact .btn")].find((b) => /Cancel/.test(T(b)))); await sleep(30);

  // all backed: a calm confirmation
  ({ window, d, calls } = await boot({ fromto: { lines: { [CODES[0]]: line(CODES[0], 2, 1350.5, 1350.5, 0) }, items: 1, unbacked_items: 0, with_entries: 1, value: 10000, unbacked_value: 0, unknown: 0 } }));
  await open(window, d, "ra5");
  ok(d.getElementById("bl-mb").classList.contains("ok") && /All 1 length line has From–To/.test(T(d.getElementById("bl-mb"))), "a fully backed bill gets the green strip: " + T(d.getElementById("bl-mb")));
  ok(!d.getElementById("bl-mbadd") && !d.getElementById("bl-mbdl") && /comes with the Download/.test(T(d.getElementById("bl-mb"))), "with no button, and it points to the Download");
  click(window, d.getElementById("bl-dl")); await sleep(150);
  ok([...d.querySelectorAll("#bl-modal .bl-wn.measinfo")].some((t) => /all 1 length item has From–To/.test(T(t)) && /Annexure D-From-To Detail follows Annexure C/.test(T(t))), "the dialog confirms Annexure D follows");
  ok(!d.getElementById("bl-nobk"), "(and the home has no strip for it)");

  console.log(`billing From-To UI: ${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
}
main();
