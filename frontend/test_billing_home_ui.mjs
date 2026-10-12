// jsdom test: the Billing home. One uniform row per billing unit (name+term | status | ONE main button | links | ···),
// green "Invoiced so far", "What is inside" chips on the RA list, and the number on the Billing tab.
// Base payload: the REAL backend overview captured from the real Thoth BOQ (billing_packages_fixture.json: Electrical in 3 Work Orders
// with a real open installation draft and a real invoiced supply RA; Fire/Plumbing/ELV without a payment term). The two states that
// capture does not contain (a service that is READY with no draft yet, and wire) are laid over it, field for field as the backend sends them.
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
const CH = JSON.parse(fs.readFileSync(path.join(__dirname, "billing_challan_fixture.json"), "utf8"));
const P = "/api/billing/thoth-mall";
const rdy = (amt, no) => ({ ready: amt, blocked: null, has_draft: false, draft_label: null, next_no: no || 1 });

function scenario(mod) {
  const ov = clone(FX.ov);
  const u = (n) => ov.services.find((x) => x.service === n);
  mod && mod(ov, u);
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
  const { window } = dom; const calls = [];
  window.__BL_LIVE_MS = 3600000; window.__BL_BADGE_MS = opts.badgeMs || 3600000;
  window.scrollTo = () => {};
  const state = { ov, setup: clone(FX.setup) };
  window.fetch = (url, o) => {
    const u = String(url), method = (o && o.method) || "GET";
    const body = o && o.body ? JSON.parse(o.body) : null;
    calls.push({ u, method, body });
    let res = {}, status = 200;
    if (u === "/api/projects") res = [{ slug: "thoth-mall", project: "Thoth Mall", runs: 1, latest_run: "r1" }];
    else if (u === P + "/auto") res = { created: [] };
    else if (u === P) res = state.ov;
    else if (u === P + "/setup" && method === "GET") res = state.setup;
    else if (/\/challans\//.test(u) && method === "GET") res = CH.ch_empty;   // a real, empty challans payload (billing_challan_fixture.json)
    else if (u === P + "/ra" && method === "POST") res = { id: "newra1", label: "HVAC · Supply RA 1" };
    else if (/\/ra\/[^/]+$/.test(u) && method === "GET") res = FX.bills[u.split("/").pop()] || FX.bills[Object.keys(FX.bills)[0]];
    const reply = { ok: status < 400, status, json: async () => res, text: async () => JSON.stringify(res) };
    return opts.delay && u === P ? new Promise((r) => setTimeout(() => r(reply), opts.delay)) : Promise.resolve(reply);   // a slow server, for the loading tests
  };
  window.eval(fs.readFileSync(path.join(__dirname, "siteprogress.js"), "utf8"));
  window.eval(fs.readFileSync(path.join(__dirname, "billing.js"), "utf8"));
  await sleep(0);
  if (!opts.stayAway) {
    [...window.document.querySelectorAll(".spnav button")].find((b) => b.dataset.v === "billing").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
    await sleep(80);
  }
  return { window, d: window.document, calls, state };
}
const click = (w, el) => el.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
const T = (el) => (el ? el.textContent : "").replace(/\s+/g, " ").trim();

async function main() {
  // the real Thoth shape + the two overlaid states: HVAC ready (supply, no draft yet), Only point wiring has wire
  const base = scenario((ov, u) => {
    u("HVAC").ready.supply = rdy(2353650);
    u("Electrical › Only point wiring").wire = true;
  });
  let { window, d, calls } = await boot(clone(base));
  const sec = d.getElementById("billing");
  const rows = [...sec.querySelectorAll(".bl-hr")];
  const row = (n) => rows.find((r) => T(r.querySelector(".nm b")) === n);

  // ---- uniform: seven units, seven rows, each with the same five slots in the same order
  ok(rows.length === 7, "one row per billing unit (7): " + rows.map((r) => T(r.querySelector(".nm b"))).join(" | "));
  ok(rows.every((r) => [...r.children].map((c) => c.className).join(",") === "nm,stt,act,lks,mn"), "every row has the same slots: name | status | main button | links | menu");
  ok(rows.every((r) => r.querySelector(".lks").children.length === 3), "every row has the same three link slots (Challans, Wire, Annexure), empty ones stay empty so columns line up");
  ok(rows.every((r) => !/Download/.test(r.textContent)), "no row has its own Download (there is one Download, at the top)");
  ok(!sec.querySelector(".bl-svcs, .bl-wo, .bl-sph"), "the old service tiles are gone");
  ok(rows.every((r) => r.querySelectorAll(".act .btn").length <= 1), "at most ONE main button per row");

  // ---- states
  const ex = row("Electrical › Excl. point wiring");
  ok(/RA 1 · Draft/.test(T(ex.querySelector(".stt"))) && /₹ 4,47,441/.test(T(ex.querySelector(".stt"))), "open draft: 'RA 1 · Draft' and its amount: " + T(ex.querySelector(".stt")));
  ok(T(ex.querySelector(".act .btn")) === "Open draft →" && ex.querySelector(".act .btn").classList.contains("primary"), "a draft's main button is Open draft →");
  const hv = row("HVAC");
  ok(/Ready/.test(T(hv.querySelector(".stt"))) && /₹ 23,53,650/.test(T(hv.querySelector(".stt"))), "ready, no draft: 'Ready' and the amount: " + T(hv.querySelector(".stt")));
  ok(T(hv.querySelector(".act .btn")) === "Create draft →", "a ready row's main button is Create draft →");
  const pl = row("Plumbing");
  ok(/Payment term not set/.test(T(pl.querySelector(".stt"))) && /Supply — · Install —/.test(T(pl.querySelector(".nm"))), "no payment term: says so, and shows — instead of a made-up %");
  ok(T(pl.querySelector(".act .btn")) === "Set payment term", "an unset row's main button is Set payment term");
  const ot = row("Electrical › Other Electrical");
  ok(/Nothing to bill yet/.test(T(ot.querySelector(".stt"))) && !ot.querySelector(".act .btn"), "nothing to bill: quiet text, no button");
  ok(/Supply 75% · Install 15%/.test(T(ex.querySelector(".nm"))), "the payment term is on every set row");
  // links
  const pw = row("Electrical › Only point wiring");
  ok(/Challans · 3/.test(T(pw.querySelector(".lks"))) && !!pw.querySelector("[data-wire]"), "a challan service shows Challans · N and (with wire) Wire → points");
  ok(!ex.querySelector("[data-ch]") && !ex.querySelector("[data-wire]"), "a stock-register service shows neither (nothing to open)");
  ok(rows.every((r) => T(r.querySelector("[data-overall]")) === "Annexure →"), "every row has Annexure →");
  click(window, pw.querySelector("[data-ch]")); await sleep(20);
  ok(calls.some((c) => /\/challans\/Electrical/.test(c.u)), "Challans · N opens that unit's challans");

  // ---- KPIs
  ({ window, d, calls } = await boot(clone(base)));
  const k = [...d.querySelectorAll(".bl-kpi")];
  ok(k[1].classList.contains("good") && /Invoiced so far/.test(T(k[1])), "Invoiced so far is the green card");
  ok(k[2].classList.contains("warn") && /Ready to bill, no draft yet/.test(T(k[2])), "Ready to bill, no draft yet stays amber");
  ok(/Live · refreshes every minute/.test(T(d.querySelector(".bl-live"))), "the Services header says it is live (a minute, as the code does)");

  // ---- main buttons act
  const hv2 = [...d.querySelectorAll(".bl-hr")].find((r) => T(r.querySelector(".nm b")) === "HVAC");
  click(window, hv2.querySelector("[data-mk]")); await sleep(60);
  const post = calls.find((c) => c.u === P + "/ra" && c.method === "POST");
  ok(post && post.body.service === "HVAC" && post.body.series === "supply", "Create draft → on a row with ONE ready bill type creates that draft straight away: " + JSON.stringify(post && post.body));
  ok(calls.some((c) => /\/ra\/newra1$/.test(c.u)), "and opens it");
  ({ window, d, calls } = await boot(clone(base)));
  click(window, [...d.querySelectorAll(".bl-hr")].find((r) => T(r.querySelector(".nm b")) === "Electrical › Excl. point wiring").querySelector("[data-ra]")); await sleep(40);
  ok(calls.some((c) => /\/ra\/[^/]+$/.test(c.u) && c.method === "GET"), "Open draft → opens the draft");
  ({ window, d, calls } = await boot(clone(base)));
  click(window, [...d.querySelectorAll(".bl-hr")].find((r) => T(r.querySelector(".nm b")) === "Plumbing").querySelector("[data-setup]")); await sleep(40);
  ok(calls.some((c) => c.u === P + "/setup" && c.method === "GET") && /Billing setup/.test(T(d.querySelector("#billing h2"))), "Set payment term opens Billing setup");
  // two ready bill types: no guess, the New RA dialog opens with the service chosen
  const two = clone(base); two.services.find((x) => x.service === "HVAC").ready.install = rdy(100000);
  ({ window, d, calls } = await boot(two));
  click(window, [...d.querySelectorAll(".bl-hr")].find((r) => T(r.querySelector(".nm b")) === "HVAC").querySelector("[data-mk]")); await sleep(30);
  ok(!calls.some((c) => c.u === P + "/ra" && c.method === "POST") && !!d.getElementById("bl-modal") && /New RA bill/.test(T(d.querySelector("#bl-modal h2"))), "two ready bill types: nothing is created blindly, the New RA bill dialog opens");
  ok(d.querySelector("#bl-modal .bl-pill.on") && /HVAC/.test(T(d.querySelector("#bl-modal .bl-pill.on"))), "…with HVAC already chosen");

  // ---- a draft AND a ready bill type on one unit: the draft leads, the ready amount is not lost
  const mix = clone(base); mix.services.find((x) => x.service === "Electrical › Excl. point wiring").ready.supply = rdy(50000);
  ({ window, d, calls } = await boot(mix));
  const mx = [...d.querySelectorAll(".bl-hr")].find((r) => T(r.querySelector(".nm b")) === "Electrical › Excl. point wiring");
  ok(/Open draft/.test(T(mx.querySelector(".act"))) && /\+ ₹ 50,000 Supply ready/.test(T(mx.querySelector(".stt"))), "draft + ready on one unit: the draft leads and '+ ₹ 50,000 Supply ready' is still shown: " + T(mx.querySelector(".stt")));

  // ---- RA list: what is inside, stage by stage (a stage with nothing is greyed, never a made-up 0)
  const ras = [...d.querySelectorAll(".bl-rar")];
  const inv = ras.find((r) => /Only point wiring/.test(r.textContent)), dr = ras.find((r) => /Excl. point wiring/.test(r.textContent));
  ok(/Supply ₹ 20,85,263/.test(T(inv.querySelector(".ins"))) && /Install —/.test(T(inv.querySelector(".ins"))) && inv.querySelector(".ins .on").textContent.startsWith("Supply"), "invoiced supply RA: Supply amount lit, Install greyed: " + T(inv.querySelector(".ins")));
  ok(/Supply —/.test(T(dr.querySelector(".ins"))) && /Install ₹ 4,47,441/.test(T(dr.querySelector(".ins"))), "installation draft: Install lit, Supply greyed: " + T(dr.querySelector(".ins")));
  ok(/Installation · 3 lines/.test(T(dr.querySelector("small"))), "the RA's type and line count sit under its name");
  ok(/Open →/.test(T(dr.querySelector(".go"))) && /View →/.test(T(inv.querySelector(".go"))), "Open → for a draft, View → for an invoiced RA");
  click(window, dr); await sleep(30);
  ok(calls.some((c) => /\/ra\/[^/]+$/.test(c.u)), "an RA row still opens the RA");

  // ---- the tab badge: services that need a look (an open draft, or ready to bill)
  ({ window, d } = await boot(clone(base)));
  const nb = d.querySelector('.spnav button[data-v="billing"] .bl-badge');
  ok(nb && nb.textContent === "2", "tab badge counts the draft unit + the ready unit = 2: " + (nb && nb.textContent));
  const none = clone(base); none.services.forEach((x) => { for (const kk of Object.keys(x.ready || {})) x.ready[kk] = rdy(0); }); none.ras = none.ras.filter((r) => r.status !== "draft");
  ({ window, d } = await boot(none));
  ok(!d.querySelector(".bl-badge"), "nothing to look at: no badge (no '0')");
  // on another tab the badge still appears shortly after the page opens
  ({ window, d, calls } = await boot(clone(base), { stayAway: true, badgeMs: 20 }));
  await sleep(120);
  const nb2 = d.querySelector('.spnav button[data-v="billing"] .bl-badge');
  ok(nb2 && nb2.textContent === "2", "badge is read once after the page opens, without opening Billing: " + (nb2 && nb2.textContent));
  ok(calls.filter((c) => c.u === P).length === 1, "one overview call for it");

  // ---- opening Billing is never a blank page: skeleton while a slow server answers, then the real home
  ({ window, d, calls } = await boot(clone(base), { delay: 400 }));
  ok(!!d.querySelector("#billing .bl-sk") && /Loading your bills/.test(T(d.getElementById("billing"))) && !d.querySelector("#billing .bl-hr"), "slow server: a skeleton is on screen at once, not a blank page");
  ok(d.getElementById("billing").textContent.trim().length > 0, "the section is never empty");
  await sleep(520);
  ok(!d.querySelector("#billing .bl-sk") && d.querySelectorAll("#billing .bl-hr").length === 7, "when the server answers the skeleton is replaced by the real rows");
  // read in the background after the page loaded: the first visit paints that at once and refreshes behind it
  ({ window, d, calls } = await boot(clone(base), { stayAway: true, badgeMs: 20, delay: 300 }));
  await sleep(420);                                        // the background read has finished
  [...d.querySelectorAll(".spnav button")].find((b) => b.dataset.v === "billing").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(60);                                         // the refresh (300 ms) is still on its way
  ok(d.querySelectorAll("#billing .bl-hr").length === 7 && !d.querySelector("#billing .bl-sk"), "first visit: the rows are on screen before the refresh answers");
  await sleep(450);
  ok(d.querySelectorAll("#billing .bl-hr").length === 7 && calls.filter((c) => c.u === P).length === 2, "then refreshed in place: 2 overview calls in all (background + refresh)");
  // later visits: the screen is not wiped while the refresh runs
  const sec2 = d.getElementById("billing");
  [...d.querySelectorAll(".spnav button")].find((b) => b.dataset.v === "siteprogress").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  [...d.querySelectorAll(".spnav button")].find((b) => b.dataset.v === "billing").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(40);
  ok(sec2.querySelectorAll(".bl-hr").length === 7, "coming back to the tab: the rows stay while the refresh runs");
  await sleep(450);

  console.log(`billing home UI: ${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
}
main().catch((e) => { console.error(e); process.exit(1); });
