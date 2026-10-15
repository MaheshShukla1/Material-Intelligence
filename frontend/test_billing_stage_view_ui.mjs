// jsdom test: Billing read one stage at a time (Supply / Installation) -- page lens, a service opened into its two stages, and the
// Supply | Installation tabs inside a draft. The scenario is the real Thoth Mall screen: ELV with a Supply draft AND an Installation draft,
// Excl / Incl / Fire / HVAC with Supply invoiced and Installation in draft, Plumbing with Installation only. Every expected figure is added
// up independently here from the per-RA amounts (never read back from the page), so a wrong total cannot agree with itself.
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
const T = (el) => (el ? el.innerHTML.replace(/<[^>]*>/g, " ") : "").replace(/&amp;/g, "&").replace(/&gt;/g, ">").replace(/\s+/g, " ").trim();   // tags become spaces, like the eye sees them
const click = (w, el) => el.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
const money = (v) => "₹ " + v.toLocaleString("en-IN");

// [service, {supply?, install?}] -- st: "d" draft / "l" invoiced
const SV = [
  ["Electrical › Excl Point Wiring", { supply: ["l", 727950, 2], install: ["d", 72617, 1] }],
  ["Electrical › Incl Point Wiring", { supply: ["l", 6428813, 3], install: ["d", 263621, 2] }],
  ["Fire", { supply: ["l", 9504917, 9], install: ["d", 1463889, 9] }],
  ["HVAC", { supply: ["l", 19314509, 45], install: ["d", 1569671, 26] }],
  ["Plumbing", { install: ["d", 53415, 1] }],
  ["ELV", { supply: ["d", 2364608, 3], install: ["d", 36864, 1] }],
];
// independent sums
const SUP_INV = [727950, 6428813, 9504917, 19314509].reduce((a, b) => a + b, 0);
const SUP_DRAFT = 2364608;
const INS_DRAFT = [72617, 263621, 1463889, 1569671, 53415, 36864].reduce((a, b) => a + b, 0);
const ALL_DRAFT = SUP_DRAFT + INS_DRAFT;

function scenario(extra) {
  const ov = clone(FX.ov);
  const svc0 = ov.services[0], ra0 = ov.ras[0];
  let n = 0;
  ov.services = []; ov.ras = [];
  for (const [name, st] of SV) {
    const s = clone(svc0);
    s.service = name; s.base = name.split(" › ")[0]; s.package = ""; s.set = true; s.supply_source = "stock"; s.challans = 0; s.wire = false; s.mode = "separate";
    s.ready = { supply: { ready: 0, blocked: null, has_draft: !!(st.supply && st.supply[0] === "d"), next_no: 2 }, install: { ready: 0, blocked: null, has_draft: !!st.install, next_no: 2 } };
    s.both_view = { supply: null, install: null };
    for (const k of ["supply", "install"]) {
      if (!st[k]) continue;
      const [status, amt, lines] = st[k];
      const r = clone(ra0);
      r.id = "ra" + (++n); r.service = name; r.base = s.base; r.package = ""; r.series = k; r.no = 1; r.stages = [k];
      r.label = name + " · " + (k === "install" ? "Installation" : "Supply") + " RA 1";
      r.status = status === "d" ? "draft" : "locked"; r.this_total = amt; r.up_to_date = amt; r.lines = lines; r.stage_this = { [k]: amt }; r.cutoff = "2026-10-12";
      r.over_n = 0; r.extra_n = 0;
      ov.ras.push(r);
      s.both_view[k] = { id: r.id, label: r.label, no: 1, status: r.status };
    }
    ov.services.push(s);
  }
  ov.bases = ov.services.map((s) => ({ base: s.service, split: false, units: [s.service] }));
  ov.billed = SUP_INV; ov.ready_total = 0; ov.notes = []; ov.contract_value = 1147096759;
  extra && extra(ov);
  return ov;
}
function billOf(ov, id) {
  const r = ov.ras.find((x) => x.id === id);
  const b = clone(FX.bills.ra5);
  b.ra = { ...b.ra, id: r.id, service: r.service, series: r.series, no: r.no, stages: r.stages, status: r.status, label: r.label, cutoff: r.cutoff, inv_missing: [] };
  b.totals = {}; b.pct = {};
  for (const k of r.stages) { b.totals[k] = { wo: 0, prev: 0, this: r.stage_this[k], total: r.stage_this[k] }; b.pct[k] = 15; }
  b.this_total = r.this_total;
  b.lines = Array.from({ length: r.lines }, (_, i) => { const l = clone(FX.bills.ra5.lines[0]); l.item_code = "L" + i; l.desc = "line " + i; delete l.install; for (const k of r.stages) l[k] = { ...clone(FX.bills.ra5.lines[0].install), amount: 1 }; return l; });
  b.unfilled = {}; b.warnings = []; b.excess = []; b.extra_lines = []; b.extra_total = 0; b.groups = []; b.other = null; b.groups_confirmed = true; b.removed = [];
  return b;
}

async function boot(ov) {
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
    else if (u.startsWith(P + "/ra/")) res = billOf(state.ov, u.slice((P + "/ra/").length));
    return Promise.resolve({ ok: true, status: 200, json: async () => res, text: async () => JSON.stringify(res) });
  };
  window.eval(fs.readFileSync(path.join(__dirname, "siteprogress.js"), "utf8"));
  window.eval(fs.readFileSync(path.join(__dirname, "billing.js"), "utf8"));
  await sleep(0);
  [...window.document.querySelectorAll(".spnav button")].find((b) => b.dataset.v === "billing").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(80);
  return { window, d: window.document, state };
}
const cards = (d) => [...d.querySelectorAll(".bl-kpis.bl-snap .bl-kpi")];
const rowOf = (d, name) => [...d.querySelectorAll("#bl-svcl .bl-hr")].find((r) => r.dataset.unit === name);
const lensBtn = (d, k) => d.querySelector(`[data-lens="${k}"]`);
const raRows = (d) => [...d.querySelectorAll(".bl-ras .bl-rar")];

async function main() {
  ok(SUP_INV === 35976189 && SUP_DRAFT + INS_DRAFT === 5824685, "independent sums: Supply invoiced 3,59,76,189; drafts 58,24,685");
  let { window, d, state } = await boot(scenario());

  // ============ A. default "All" is exactly today's screen
  ok(!!d.querySelector(".bl-lens") && [...d.querySelectorAll("[data-lens]")].map(T).join("|") === "All|Supply|Installation", "lens: All | Supply | Installation");
  ok(lensBtn(d, "all").getAttribute("aria-pressed") === "true" && lensBtn(d, "all").classList.contains("on"), "All is selected on first open");
  let k = cards(d);
  ok(k.length === 3, "still three cards");
  ok(T(k[1].querySelector("b")) === money(SUP_INV) && /4 RA invoiced/.test(T(k[1])), "All: Invoiced so far = ov.billed, 4 RA: " + T(k[1]));
  ok(T(k[2].querySelector("b")) === money(ALL_DRAFT), "All: Not invoiced yet = every draft, both stages: " + T(k[2].querySelector("b")) + " vs " + ALL_DRAFT);
  ok(ALL_DRAFT === 5824685, "independent check: 58,24,685 as on the real screen");
  ok(/7 drafts ₹ 58,24,685/.test(T(k[2])) && /Ready, no draft ₹ 0/.test(T(k[2])), "All: card text as before: " + T(k[2].querySelector("i")));
  ok(!/all stages/.test(T(k[0])), "All: contract card has no 'all stages' tag");
  let sp = k[2].querySelector(".bl-nibar");
  ok(!!sp, "All: the Not invoiced card has the Supply / Installation split bar");
  ok(/Supply ₹ 23,64,608/.test(T(k[2].querySelector(".bl-nikey"))) && /Installation ₹ 34,60,077/.test(T(k[2].querySelector(".bl-nikey"))), "split key: " + T(k[2].querySelector(".bl-nikey")));
  const w1 = parseFloat(sp.children[0].style.width), w2 = parseFloat(sp.children[1].style.width);
  ok(Math.abs(w1 - SUP_DRAFT / ALL_DRAFT * 100) < 0.01 && Math.abs(w1 + w2 - 100) < 0.05, "split bar widths are the true shares: " + w1 + " / " + w2);
  ok(d.querySelectorAll("#bl-svcl .bl-hr").length === 6, "six service rows");
  ok(T(rowOf(d, "ELV").querySelector(".stt")) === "2 drafts open " + money(2364608 + 36864), "ELV (2 drafts) row = both drafts: " + T(rowOf(d, "ELV").querySelector(".stt")));
  ok(T(rowOf(d, "Fire").querySelector(".stt")) === "RA 1 · Draft " + money(1463889), "Fire row: only the Installation draft is open, as today: " + T(rowOf(d, "Fire").querySelector(".stt")));
  ok(raRows(d).length === 11, "All: all 11 RAs listed: " + raRows(d).length);
  const chev = [...d.querySelectorAll("[data-exp]")].map((e) => e.dataset.exp);
  ok(chev.length === 5 && !chev.includes("Plumbing"), "chevron on the 5 services that have both stages, not on Plumbing: " + chev.join(","));
  const allHtml = d.querySelector("#bl-svcl").innerHTML;
  const allRa = d.querySelector(".bl-ras").innerHTML;

  // ============ A. Supply lens
  click(window, lensBtn(d, "supply"));
  ok(lensBtn(d, "supply").getAttribute("aria-pressed") === "true" && d.activeElement === lensBtn(d, "supply"), "Supply lens: selected, focus stays on the button");
  k = cards(d);
  ok(T(k[1].querySelector("b")) === money(SUP_INV) && /4 RA invoiced/.test(T(k[1])), "Supply: Invoiced = the four Supply RAs: " + T(k[1]));
  ok(T(k[2].querySelector("b")) === money(SUP_DRAFT) && /1 draft ₹ 23,64,608/.test(T(k[2])), "Supply: Not invoiced = ELV Supply draft only: " + T(k[2]));
  ok(/all stages/.test(T(k[0])) && T(k[0].querySelector(".full")) === "₹ 1,14,70,96,759", "Supply: contract stays whole and says so");
  ok(!d.querySelector(".bl-nibar"), "Supply: no split bar");
  ok(d.querySelectorAll("[data-exp]").length === 0, "Supply: no row expanders");
  ok(/Supply only/.test(T(d.querySelector(".bl-lenshint"))), "Supply: hint line");
  ok(T(rowOf(d, "Fire").querySelector(".stt")) === "RA 1 · Invoiced " + money(9504917) && T(rowOf(d, "Fire").querySelector(".act .btn")) === "View →", "Supply: Fire = RA 1 Invoiced + View: " + T(rowOf(d, "Fire").querySelector(".stt")));
  ok(T(rowOf(d, "ELV").querySelector(".stt")) === "RA 1 · Draft " + money(SUP_DRAFT) && T(rowOf(d, "ELV").querySelector(".act .btn")) === "Open draft →", "Supply: ELV = only its Supply draft, 23,64,608 (not 24,01,472): " + T(rowOf(d, "ELV").querySelector(".stt")));
  ok(/Nothing to bill yet/.test(T(rowOf(d, "Plumbing"))) && !rowOf(d, "Plumbing").querySelector(".act .btn"), "Supply: Plumbing has no Supply RA, so nothing to bill, no button");
  ok(T(d.querySelector(".bl-grp")) === "Not invoiced yet · 1 draft " + money(SUP_DRAFT), "Supply: drafts header: " + T(d.querySelector(".bl-grp")));
  ok(raRows(d).length === 5 && raRows(d).every((r) => /Supply/.test(T(r.querySelector(".ins"))) && !/Install/.test(T(r.querySelector(".ins")))), "Supply: only the 5 Supply RAs, each with only a Supply chip");
  ok(raRows(d).every((r) => /Supply RA|Supply ·/.test(T(r.firstElementChild)) || /Supply/.test(T(r.firstElementChild))), "Supply: rows are Supply RAs");
  const supSum = raRows(d).reduce((a, r) => a + Number(T(r.querySelector(".n b")).replace(/[^\d]/g, "")), 0);
  ok(supSum === SUP_INV + SUP_DRAFT, "Supply: RA table 'This bill' adds to the Supply total: " + supSum);

  // ============ A. Installation lens
  click(window, lensBtn(d, "install"));
  k = cards(d);
  ok(T(k[1].querySelector("b")) === money(0) && /No Installation RA invoiced yet/.test(T(k[1])), "Install: nothing invoiced, said plainly: " + T(k[1]));
  ok(T(k[2].querySelector("b")) === money(INS_DRAFT) && /6 drafts ₹ 34,60,077/.test(T(k[2])), "Install: six drafts 34,60,077: " + T(k[2]));
  ok(INS_DRAFT === 3460077, "independent check: 34,60,077");
  ok(T(rowOf(d, "ELV").querySelector(".stt")) === "RA 1 · Draft " + money(36864), "Install: ELV = Installation draft 36,864: " + T(rowOf(d, "ELV").querySelector(".stt")));
  ok(T(rowOf(d, "Plumbing").querySelector(".stt")) === "RA 1 · Draft " + money(53415), "Install: Plumbing 53,415");
  ok(raRows(d).length === 6 && raRows(d).every((r) => /Install/.test(T(r.querySelector(".ins"))) && !/Supply/.test(T(r.querySelector(".ins")))), "Install: 6 Installation RAs with only an Install chip");

  // ============ back to All: byte-identical to the start
  click(window, lensBtn(d, "all"));
  ok(d.querySelector("#bl-svcl").innerHTML === allHtml, "back to All: services list is exactly as at the start");
  ok(d.querySelector(".bl-ras").innerHTML === allRa, "back to All: RA table is exactly as at the start");

  // ============ B. a service opened into Supply / Installation
  const elv = rowOf(d, "ELV");
  const cv = elv.querySelector("[data-exp]");
  ok(cv.getAttribute("aria-expanded") === "false" && !d.querySelector(".bl-stsub"), "ELV starts closed: " + cv.outerHTML + " / " + !!d.querySelector(".bl-stsub"));
  click(window, cv);
  const cv2 = rowOf(d, "ELV").querySelector("[data-exp]");
  ok(cv2.getAttribute("aria-expanded") === "true" && d.activeElement === cv2, "ELV opened, focus stays on its chevron");
  const sub = d.querySelector('.bl-stsub[data-sub="ELV"]');
  ok(!!sub && rowOf(d, "ELV").nextElementSibling === sub, "the sub-block sits right under the ELV row");
  const srows = [...sub.querySelectorAll(".bl-strow")];
  ok(srows.length === 2 && /^Supply RA 1 · Draft ₹ 23,64,608 3 lines/.test(T(srows[0])) && /^Installation RA 1 · Draft ₹ 36,864 1 line\b/.test(T(srows[1])), "sub rows: " + srows.map(T).join(" || "));
  ok(/₹ 24,01,472/.test(T(sub.querySelector(".bl-sttot"))) && /view only, not a bill/.test(T(sub.querySelector(".bl-sttot"))), "sub total = 23,64,608 + 36,864 = 24,01,472, marked view only: " + T(sub.querySelector(".bl-sttot")));
  ok(d.querySelectorAll(".bl-stsub").length === 1, "only ELV is open");
  // a live repaint / lens change keeps it open
  click(window, lensBtn(d, "supply")); click(window, lensBtn(d, "all"));
  ok(!!d.querySelector('.bl-stsub[data-sub="ELV"]'), "ELV stays open after changing the lens and coming back");
  // the Installation sub-row opens the Installation draft
  click(window, d.querySelector('.bl-stsub[data-sub="ELV"] .bl-strow:nth-child(2) [data-ra]'));
  await sleep(40);
  ok(/ELV · Installation RA 1/.test(T(d.querySelector(".bl-head h2, h2"))), "Installation sub-row opens ELV's Installation RA: " + T(d.querySelector("h2")));
  ok(d.querySelector(".bl-stab.on") && /Installation/.test(T(d.querySelector(".bl-stab.on"))), "...with the Installation tab on");
  // closing
  click(window, d.querySelector("#bl-back")); await sleep(60);
  ok(!!d.querySelector('.bl-stsub[data-sub="ELV"]'), "Back from a draft: the lens/open state is kept");
  click(window, rowOf(d, "ELV").querySelector("[data-exp]"));
  ok(!d.querySelector(".bl-stsub") && rowOf(d, "ELV").querySelector("[data-exp]").getAttribute("aria-expanded") === "false", "closed again");

  // ============ C. tabs inside a draft
  click(window, rowOf(d, "ELV").querySelector('[data-ra]')); await sleep(40);   // main button: top draft
  const tabs = [...d.querySelectorAll(".bl-stabs .bl-stab")];
  ok(tabs.length === 2, "two tabs on a one-stage draft");
  const onTab = d.querySelector(".bl-stab.on");
  ok(!!onTab && /Supply|Installation/.test(T(onTab)) && onTab.getAttribute("aria-current") === "true", "current stage tab is marked: " + T(onTab));
  const cur = /Supply/.test(T(onTab)) ? "supply" : "install";
  const other = tabs.find((t) => t !== onTab);
  ok(other.tagName === "BUTTON" && !!other.dataset.stab, "the other stage is a button to its RA");
  const kp = [...d.querySelectorAll(".bl-kpis .bl-kpi")];
  ok(kp.length === 4 && kp[3].classList.contains("view") && /Service total, both stages/.test(T(kp[3])), "4th card: Service total, both stages");
  ok(T(kp[3].querySelector("b")) === money(2364608 + 36864) && /view only/.test(T(kp[3])), "service total = 24,01,472: " + T(kp[3]));
  ok(T(kp[0].querySelector("b")) === money(cur === "supply" ? 2364608 : 36864), "This bill KPI is the current stage only: " + T(kp[0].querySelector("b")));
  click(window, other); await sleep(40);
  const on2 = d.querySelector(".bl-stab.on");
  ok(!!on2 && !(/Supply/.test(T(on2)) === (cur === "supply")), "clicking the other tab moves to the other stage's draft: " + T(on2));
  ok(T(d.querySelector(".bl-kpis .bl-kpi")).includes(cur === "supply" ? "₹ 36,864" : "₹ 23,64,608"), "...and This bill follows it");

  // Plumbing: no Supply RA -> the Supply tab is "Nothing billed yet" and not clickable
  click(window, d.querySelector("#bl-back")); await sleep(60);
  click(window, rowOf(d, "Plumbing").querySelector("[data-ra]")); await sleep(40);
  const pt = [...d.querySelectorAll(".bl-stabs .bl-stab")];
  const off = pt.find((t) => t.classList.contains("off"));
  ok(!!off && /Supply/.test(T(off)) && /Nothing billed yet/.test(T(off)) && off.tagName !== "BUTTON", "Plumbing: Supply tab is 'Nothing billed yet', inert: " + pt.map(T).join(" || "));
  const pk = [...d.querySelectorAll(".bl-kpis .bl-kpi")];
  ok(T(pk[3].querySelector("b")) === money(53415) && /Supply not billed/.test(T(pk[3])), "Plumbing service total = 53,415, 'Supply not billed': " + T(pk[3]));

  // Fire Installation draft -> the Supply tab leads to the invoiced RA 1 and says so
  click(window, d.querySelector("#bl-back")); await sleep(60);
  click(window, rowOf(d, "Fire").querySelector("[data-ra]")); await sleep(40);
  const ft = d.querySelector(".bl-stab[data-stab]");
  ok(/Supply/.test(T(ft)) && /RA 1 · Invoiced · 9 lines/.test(T(ft)) && T(ft).includes(money(9504917)), "Fire: Supply tab = invoiced RA 1, 95,04,917: " + T(ft));
  ok(T([...d.querySelectorAll(".bl-kpis .bl-kpi")][3].querySelector("b")) === money(9504917 + 1463889), "Fire service total = supply + install");

  // ============ a Together bill: stage parts only, never a guessed split
  ({ window, d, state } = await boot(scenario((ov) => {
    const r = clone(ov.ras.find((x) => x.service === "ELV" && x.series === "install"));
    r.id = "tg1"; r.series = "together"; r.stages = ["supply", "install"]; r.label = "ELV · Supply + Installation RA 2"; r.no = 2; r.status = "draft";
    r.this_total = 1000; r.up_to_date = 1000; r.lines = 4; r.stage_this = { supply: 700, install: 300 };
    ov.ras.push(r);
    const elvSvc = ov.services.find((x) => x.service === "ELV");
    elvSvc.both_view = { supply: { id: "tg1", no: 2, status: "draft", label: r.label }, install: { id: "tg1", no: 2, status: "draft", label: r.label } };
    // a second service whose Together bill is READY (no draft): the overview cannot split it by stage
    const fire = ov.services.find((x) => x.service === "Fire");
    fire.ready = { together: { ready: 5000, blocked: null, has_draft: false, next_no: 2 } };
    ov.ready_total = 5000;
  })));
  click(window, lensBtn(d, "supply"));
  k = cards(d);
  const tgSup = SUP_DRAFT + 700;                      // ELV Supply draft + the Supply part of the Together draft
  ok(T(k[2].querySelector("b")) === money(tgSup) && /2 drafts/.test(T(k[2])), "Together: Supply lens counts only the Supply part (700), not 1,000: " + T(k[2]));
  ok(/₹ 5,000 Together, not split by stage/.test(T(k[2])), "Together ready 5,000 is reported apart, not added or divided: " + T(k[2]));
  const trow = raRows(d).find((r) => /RA 2/.test(T(r.firstElementChild)) && /ELV/.test(T(r.firstElementChild)));
  ok(!!trow && T(trow.querySelector(".n b")) === money(700) && /Supply ₹ 700/.test(T(trow.querySelector(".ins"))), "Together RA row in Supply lens: ₹ 700 only: " + T(trow));
  ok(T(trow.querySelectorAll(".n")[1]) === "—", "its 'Up to date' is — (mixed with a Together bill), never invented: " + T(trow.querySelectorAll(".n")[1]));
  click(window, lensBtn(d, "install"));
  ok(T(cards(d)[2].querySelector("b")) === money(INS_DRAFT + 300), "Together: Install lens counts the Install part (300)");
  click(window, lensBtn(d, "all"));
  ok(!d.querySelector(".bl-nibar"), "All: the split bar is NOT drawn when part of the total (a ready Together bill) cannot be split by stage");
  ok(T(cards(d)[2].querySelector("b")) === money(ALL_DRAFT + 1000 + 5000), "All: total still = every draft + ready (unchanged rule)");
  // a Together RA screen has both stages in one screen: no tabs
  click(window, trow.dataset.ra ? trow : raRows(d).find((r) => r.dataset.ra === "tg1")); await sleep(40);
  ok(d.querySelectorAll(".bl-stabs").length === 0 && d.querySelectorAll(".bl-kpis .bl-kpi").length === 3, "Together draft screen: no tabs, no 4th card (it already holds both stages)");

  // ============ share snapshot follows the lens
  ({ window, d, state } = await boot(scenario()));
  const copied = [];
  Object.defineProperty(window.navigator, "clipboard", { configurable: true, value: { writeText: async (t) => { copied.push(t); } } });
  click(window, lensBtn(d, "supply"));
  click(window, d.querySelector("#bl-share")); await sleep(30);
  ok(copied.length === 1 && /\(Supply only\)/.test(copied[0]) && copied[0].includes("Not yet invoiced: " + money(SUP_DRAFT)) && copied[0].includes("Invoiced so far: " + money(SUP_INV)), "Share under the Supply lens says so and carries Supply numbers:\n" + copied[0]);
  copied.length = 0;
  click(window, lensBtn(d, "all"));
  click(window, d.querySelector("#bl-share")); await sleep(30);
  ok(copied.length === 1 && !/only\)/.test(copied[0]) && copied[0].includes("Not yet invoiced: " + money(ALL_DRAFT)), "Share under All is the same text as before");

  console.log(`billing stage view UI: ${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
}
main().catch((e) => { console.error(e); process.exit(1); });
