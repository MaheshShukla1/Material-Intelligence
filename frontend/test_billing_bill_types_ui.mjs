// jsdom test: the bill type is chosen on EACH new RA (Supply only / Installation only / Together), "Usually billed as"
// is no longer locked, and your company details from another project are offered (never silently applied) and can be cleared.
// Payloads are REAL backend responses (billing_fixture.json, re-captured after the change).
import { JSDOM } from "jsdom";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
let pass = 0, fail = 0;
function ok(cond, msg) { if (cond) pass++; else { fail++; console.error("FAIL:", msg); } }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const FX = JSON.parse(fs.readFileSync(path.join(__dirname, "billing_fixture.json"), "utf8"));
const clone = (x) => JSON.parse(JSON.stringify(x));

// ---------------------------------------------------------------- behaviour
async function boot(over) {
  const html = `<!doctype html><html><body>
    <header class="top" id="top"><div class="brand"><p id="ctx"></p></div>
      <div class="topact"><button id="home"></button><button id="pick"></button><button id="sync"></button><button id="syncgo"></button></div></header>
    <main><section id="drop" hidden></section><section id="busy" hidden></section>
      <section id="report"><div class="bar"><div class="tabs" id="svc"></div></div></section></main></body></html>`;
  const dom = new JSDOM(html, { url: "https://example.test/", runScripts: "dangerously", pretendToBeVisual: true });
  const { window } = dom; const calls = [];
  const state = { ov: clone(FX.overview), bill: clone(FX.bill), setup: clone(FX.setup), ...over };
  window.fetch = (url, opts) => {
    const u = String(url), method = (opts && opts.method) || "GET";
    const body = opts && opts.body ? JSON.parse(opts.body) : null;
    calls.push({ u, method, body });
    let res = {}, status = 200;
    if (u === "/api/projects") res = state.projects || [{ slug: "thoth-mall", project: "Thoth Mall", runs: 1, latest_run: "r1" }];
    else if (u === "/api/billing/thoth-mall") res = state.ov;
    else if (u === "/api/billing/thoth-mall/setup" && method === "GET") res = state.setup;
    else if (u === "/api/billing/thoth-mall/setup") res = { ok: true };
    else if (u === `/api/billing/thoth-mall/ra/${FX.raid}` && method === "GET") res = state.bill;
    else if (u === "/api/billing/thoth-mall/ra") res = { id: FX.raid, label: "Fire · Supply RA 1" };
    else if (/\/ra\/[^/]+\/(line|lock|reopen)$/.test(u)) { if (state.fail) { status = 409; res = { detail: state.fail }; } else res = { ok: true, this_total: 1, unfilled: {} }; }
    else if (u.startsWith("/api/billing/thoth-mall/lines/")) res = { lines: state.bill.lines.map((l) => ({ item_code: l.item_code, desc: l.desc, unit: l.unit, boq_qty: l.boq_qty, rate: l.rate, internal_ref: l.internal_ref })) };
    else if (u.startsWith("/api/billing/thoth-mall/groups/")) res = { ok: true };
    else if (u === "/api/billing/thoth-mall/meta") res = { ok: true };
    return Promise.resolve({ ok: status < 400, status, json: async () => res, text: async () => JSON.stringify(res) });
  };
  window.eval(fs.readFileSync(path.join(__dirname, "siteprogress.js"), "utf8"));
  window.eval(fs.readFileSync(path.join(__dirname, "billing.js"), "utf8"));
  await sleep(0);
  return { window, d: window.document, calls, state };
}
const click = (w, el) => el.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
const type = (w, el, v) => { el.value = v; el.dispatchEvent(new w.Event("input", { bubbles: true })); };
const svcOf = (ov, n) => ov.services.find((s) => s.service === n);

async function main() {
  // ---------------------------------------------------------------- New RA: every bill type, the usual one first
  let { window, d, calls } = await boot();
  const nav = (v) => [...d.querySelectorAll(".spnav button")].find((b) => b.dataset.v === v);
  click(window, nav("billing")); await sleep(80);
  const home = d.getElementById("billing").textContent;
  ok(/Electrical\s*Supply \d+% · Install \d+%/.test(home) && /Fire\s*Supply \d+% · Install \d+%/.test(home), "home: each service row shows its Supply % / Install % (the 'usually billed as' default lives in Billing setup)");
  click(window, d.getElementById("bl-new")); await sleep(20);
  const m = () => d.getElementById("bl-modal");
  const opt = (k) => m().querySelector(`.bl-opt[data-series="${k}"]`);
  // Electrical is the first set service: usually together
  ok([...m().querySelectorAll(".bl-opt")].map((o) => o.dataset.series).join() === "supply,install,together", "Electrical (usually together) still offers Supply only, Installation only and Together, in that order");
  ok(m().querySelector(".bl-opt.on").dataset.series === "together" && /usual for Electrical/.test(opt("together").textContent) && !/usual for/.test(opt("supply").textContent), "its usual type, Together, is pre-selected and marked");
  ok(/Supply only/.test(opt("supply").textContent) && /Installation only/.test(opt("install").textContent) && /Supply \+ Installation together/.test(opt("together").textContent), "the types are named in plain words");
  ok(/RA 1/.test(opt("supply").textContent) && /Ready/.test(opt("supply").textContent), "each ready type shows the RA number it would get and what is ready");
  ok(opt("supply").getAttribute("role") === "radio" && opt("together").getAttribute("aria-checked") === "true" && opt("supply").getAttribute("aria-checked") === "false", "options are radios for keyboard and screen-reader users");
  // a Together service can still bill Supply only
  click(window, opt("supply")); await sleep(5);
  ok(m().querySelector(".bl-opt.on").dataset.series === "supply", "picking Supply only on a Together service works");
  click(window, m().querySelector(".mact .primary")); await sleep(60);
  let post = calls.find((c) => c.u === "/api/billing/thoth-mall/ra" && c.method === "POST");
  ok(post && post.body.service === "Electrical" && post.body.series === "supply", "creating posts {Electrical, supply}");

  // keyboard: Enter / Space on an option selects it
  ({ window, d, calls } = await boot());
  click(window, nav2(d, "billing")); await sleep(80);
  click(window, d.getElementById("bl-new")); await sleep(20);
  opt2(d, "install").dispatchEvent(new window.KeyboardEvent("keydown", { key: "Enter", bubbles: true })); await sleep(5);
  ok(d.querySelector("#bl-modal .bl-opt.on").dataset.series === "install", "Enter on an option selects it");

  // a draft on the same stage greys the overlapping types and names the draft; a free type still works
  ({ window, d, calls } = await boot());
  click(window, nav2(d, "billing")); await sleep(80);
  click(window, d.getElementById("bl-new")); await sleep(20);
  click(window, d.querySelector('#bl-modal [data-svc="Fire"]')); await sleep(5);
  ok(d.querySelector("#bl-modal .bl-opt.on").dataset.series === "install", "Fire (usually separate, Supply draft open): the first FREE usual type is pre-selected");
  ok(opt2(d, "supply").classList.contains("off") && opt2(d, "together").classList.contains("off") && !opt2(d, "install").classList.contains("off"), "Supply and Together are greyed (same stage as the open draft), Installation is free");
  ok(/Supply RA 1 draft is already open/.test(opt2(d, "together").textContent), "the reason names the open draft");
  click(window, opt2(d, "together")); await sleep(5);
  click(window, d.querySelector("#bl-modal .mact .primary")); await sleep(40);
  ok(!calls.some((c) => c.u === "/api/billing/thoth-mall/ra" && c.method === "POST") && /Pick a bill type that is ready/.test(d.getElementById("bl-toast").textContent), "a greyed type cannot be created");

  // a type blocked by a missing % says why
  let ov = clone(FX.overview);
  svcOf(ov, "Fire").can.install = { ready: null, blocked: "Fire: Installation % is not set. Set it in Billing setup; no % is guessed." };
  ({ window, d, calls } = await boot({ ov }));
  click(window, nav2(d, "billing")); await sleep(80);
  click(window, d.getElementById("bl-new")); await sleep(20);
  click(window, d.querySelector('#bl-modal [data-svc="Fire"]')); await sleep(5);
  ok(opt2(d, "install").classList.contains("off") && /Installation % is not set/.test(opt2(d, "install").textContent), "a type with no % set is greyed with the server's reason");

  // ---------------------------------------------------------------- Billing setup: 'Usually billed as' is not locked
  ({ window, d, calls } = await boot());
  click(window, nav2(d, "billing")); await sleep(80);
  click(window, d.getElementById("bl-setup")); await sleep(80);
  ok(/Usually billed as/.test(d.querySelector(".bl-sh").textContent) && !/How it is billed/.test(d.querySelector(".bl-sh").textContent), "setup column is 'Usually billed as'");
  ok(/on every new RA you still choose Supply only, Installation only or Together/.test(d.getElementById("billing").textContent), "the page says the type is chosen on each RA");
  const fx = FX.setup.services.find((x) => x.has_ras);
  const rows = [...d.querySelectorAll(".bl-sr")];
  const modeSels = rows.map((r) => r.querySelector('[data-f="mode"]'));
  ok(modeSels.every((s) => !s.disabled), "no 'How it is billed' select is disabled any more, not even for a service that already has RAs");
  const fire = rows.find((r) => /Fire/.test(r.textContent));
  fire.querySelector('[data-f="mode"]').value = "together";
  click(window, fire.querySelector("[data-save]")); await sleep(60);
  const sp = calls.find((c) => c.u === "/api/billing/thoth-mall/setup" && c.method === "POST");
  ok(sp && sp.body.service === "Fire" && sp.body.mode === "together", "changing the default and Save posts the new mode: " + JSON.stringify(sp && sp.body));
  void fx;

  // ---------------------------------------------------------------- invoice details: company prefilled from another project
  const CO = { contractor: "Vedant Engitech And Infrastructure Pvt. Ltd.", supplier_gstin: "24AADCV2799M1ZP", supplier_state: "GUJARAT", supplier_state_code: "24", supplier_pan: "AADCV2799M", supplier_category: "MEPF" };
  const bare = clone(FX.setup); bare.meta = { work: {}, sac: {}, inv_desc: {} };
  bare.invoice_missing = ["Your company name", "Your GSTIN", "Client company"];
  bare.profile = { fields: CO, from: "hyatt-hotel" };
  ({ window, d, calls } = await boot({ setup: bare, projects: [{ slug: "thoth-mall", project: "Thoth Mall", runs: 1, latest_run: "r1" }, { slug: "hyatt-hotel", project: "Hyatt Hotel", runs: 1, latest_run: "r2" }] }));
  click(window, nav2(d, "billing")); await sleep(80);
  click(window, d.getElementById("bl-setup")); await sleep(80);
  const card = d.querySelector(".bl-metac").textContent;
  ok(/Your company details from Hyatt Hotel are ready/.test(card) && /Vedant Engitech/.test(card) && !!d.getElementById("bl-iuse"), "setup card offers the company details kept from another project, naming it");
  ok(!calls.some((c) => c.u.endsWith("/meta")), "the offer is NOT applied by itself");
  ({ window, d, calls } = await boot({ setup: bare }));                       // source project no longer in the list: its slug is made readable
  click(window, nav2(d, "billing")); await sleep(80);
  click(window, d.getElementById("bl-setup")); await sleep(80);
  ok(/details from Hyatt Hotel are ready/.test(d.querySelector(".bl-metac").textContent), "a project missing from the list is shown by its readable slug, 'Hyatt Hotel'");
  ({ window, d, calls } = await boot({ setup: bare, projects: [{ slug: "thoth-mall", project: "Thoth Mall", runs: 1, latest_run: "r1" }, { slug: "hyatt-hotel", project: "Hyatt Hotel", runs: 1, latest_run: "r2" }] }));
  click(window, nav2(d, "billing")); await sleep(80);
  click(window, d.getElementById("bl-setup")); await sleep(80);
  click(window, d.getElementById("bl-idet")); await sleep(80);
  const f = (k) => d.querySelector(`#bl-modal [data-m="${k}"]`);
  ok(f("contractor").value === CO.contractor && f("supplier_gstin").value === CO.supplier_gstin && f("supplier_state_code").value === "24", "the form opens with your company pre-filled");
  ok(f("contractor").classList.contains("bl-sug") && !!d.getElementById("bl-inote") && /Hyatt Hotel/.test(d.getElementById("bl-inote").textContent), "suggested values are marked and the note names the source project");
  ok(f("client").value === "" && f("wo_no").value === "" && f("sgst").value === "", "client, W/O and GST % are NOT carried over: they belong to the project");
  type(window, f("contractor"), "Edited Co"); 
  ok(!f("contractor").classList.contains("bl-sug"), "editing a suggested value removes its suggestion mark");
  // clear just the company
  click(window, d.getElementById("bl-iclrco")); await sleep(5);
  ok(["contractor", "supplier_gstin", "supplier_pan", "supplier_state", "supplier_state_code", "supplier_category"].every((k) => f(k).value === "") && !d.getElementById("bl-inote"), "'Clear company' empties the company block and drops the note");
  click(window, d.querySelector("#bl-modal #bl-isave")); await sleep(80);
  let mp = calls.filter((c) => c.u.endsWith("/meta") && c.method === "POST").pop();
  ok(mp && mp.body.contractor === "" && mp.body.supplier_gstin === "" && mp.body.client === "", "Save keeps the project empty: every company field is posted blank");

  // 'Clear all' also clears client / GST / services
  const saved = clone(FX.setup); saved.meta = { client: "Thoth Mall", contractor: "Vedant Engitech", wo_no: "WO/12", sgst: 9, cgst: 9, sac: { Fire: "995461" }, inv_desc: {}, work: {} };
  ({ window, d, calls } = await boot({ setup: saved }));
  click(window, nav2(d, "billing")); await sleep(80);
  click(window, d.getElementById("bl-setup")); await sleep(80);
  click(window, d.getElementById("bl-idet")); await sleep(80);
  ok(d.querySelector('#bl-modal [data-m="client"]').value === "Thoth Mall" && d.querySelector('#bl-modal [data-m="sgst"]').value === "9", "a project with saved details opens with them (no offer: it already has them)");
  click(window, d.getElementById("bl-iclrall")); await sleep(5);
  ok([...d.querySelectorAll("#bl-modal [data-m], #bl-modal [data-sac], #bl-modal [data-desc]")].every((el) => el.value === ""), "'Clear all' empties every field of the form");
  click(window, d.querySelector("#bl-modal #bl-isave")); await sleep(80);
  mp = calls.filter((c) => c.u.endsWith("/meta") && c.method === "POST").pop();
  ok(mp && mp.body.client === "" && mp.body.contractor === "" && mp.body.sgst === "", "Save then stores it empty");

  // one click: 'Use them here'
  ({ window, d, calls } = await boot({ setup: bare, projects: [{ slug: "thoth-mall", project: "Thoth Mall", runs: 1, latest_run: "r1" }] }));
  click(window, nav2(d, "billing")); await sleep(80);
  click(window, d.getElementById("bl-setup")); await sleep(80);
  click(window, d.getElementById("bl-iuse")); await sleep(80);
  mp = calls.filter((c) => c.u.endsWith("/meta") && c.method === "POST").pop();
  ok(mp && JSON.stringify(mp.body) === JSON.stringify(CO), "'Use them here' saves exactly the kept company fields, nothing else: " + JSON.stringify(mp && mp.body));

  // no offer, no card line
  ({ window, d, calls } = await boot({ setup: clone(FX.setup) }));
  click(window, nav2(d, "billing")); await sleep(80);
  click(window, d.getElementById("bl-setup")); await sleep(80);
  ok(!d.getElementById("bl-iuse") && !/are ready:/.test(d.querySelector(".bl-metac").textContent), "when the backend offers nothing, nothing is shown");

  // ---------------------------------------------------------------- Download: 'Latest of each service' with a mixed history
  const mixed = clone(FX.overview);
  const mk = (id, series, no, status) => ({ id, service: "Fire", series, no, label: `Fire · ${series} RA ${no}`, status, cutoff: "2026-10-01", created: null, locked_at: null,
    this_total: 100, stages: series === "together" ? ["supply", "install"] : [series], inv_no: "", inv_date: "", lines: 3, up_to_date: 100, inv_missing: [] });
  mixed.ras = [mk("t1", "together", 1, "locked"), mk("s2", "supply", 2, "draft"), mk("i2", "install", 2, "draft")];
  ({ window, d, calls } = await boot({ ov: mixed }));
  click(window, nav2(d, "billing")); await sleep(80);
  click(window, d.getElementById("bl-dl")); await sleep(80);
  const ticked = () => [...d.querySelectorAll("#bl-modal [data-ra]")].filter((c) => c.checked).map((c) => c.dataset.ra).sort().join();
  ok(ticked() === "", "nothing is ticked when Download opens from home");
  click(window, [...d.querySelectorAll("#bl-modal [data-q]")].find((b) => b.dataset.q === "latest")); await sleep(20);
  ok(ticked() === "i2,s2", "Together RA 1 is history once Supply RA 2 and Installation RA 2 followed: 'Latest' quick pick = the two new invoices, ticked: " + ticked());
  ok([...d.querySelectorAll("#bl-modal [data-ra]")].length === 3 && /Supply \+ Installation RA-01/.test(d.getElementById("bl-modal").textContent), "all three are still listed by their type and number, so any can be ticked");
  const half = clone(mixed); half.ras = [mk("t1", "together", 1, "locked"), mk("s2", "supply", 2, "draft")];
  ({ window, d, calls } = await boot({ ov: half }));
  click(window, nav2(d, "billing")); await sleep(80);
  click(window, d.getElementById("bl-dl")); await sleep(80);
  click(window, [...d.querySelectorAll("#bl-modal [data-q]")].find((b) => b.dataset.q === "latest")); await sleep(20);
  ok(ticked() === "s2,t1", "with only a later Supply RA, Together RA 1 still carries the installation stage: it stays in 'Latest': " + ticked());

  console.log(`billing bill types UI: ${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
}
const nav2 = (d, v) => [...d.querySelectorAll(".spnav button")].find((b) => b.dataset.v === v);
const opt2 = (d, k) => d.querySelector(`#bl-modal .bl-opt[data-series="${k}"]`);
main().catch((e) => { console.error("THROW", e); process.exit(1); });
