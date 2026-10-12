// jsdom test: drafts open by themselves when installation is logged (the app asks the server on opening Billing), a per-service
// switch turns that off, and an open Billing screen refreshes itself every few seconds -- without ever disturbing a dialog or typing.
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
  window.__BL_LIVE_MS = over && over.live ? over.live : 3600000;
  window.scrollTo = () => {};
  const state = { ov: clone(FX.overview), bill: clone(FX.bill), setup: clone(FX.setup), ...over };
  window.fetch = (url, opts) => {
    const u = String(url), method = (opts && opts.method) || "GET";
    const body = opts && opts.body ? JSON.parse(opts.body) : null;
    calls.push({ u, method, body });
    let res = {}, status = 200;
    if (u === "/api/projects") res = state.projects || [{ slug: "thoth-mall", project: "Thoth Mall", runs: 1, latest_run: "r1" }];
    else if (u === "/api/billing/thoth-mall/auto") res = { created: state.auto || [] };
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
  const nav = (d, v) => [...d.querySelectorAll(".spnav button")].find((b) => b.dataset.v === v);
  const text = (d) => d.getElementById("billing").textContent;

  // ---- opening Billing asks the server to open drafts FIRST, then loads the list; a created draft is announced
  let { window, d, calls } = await boot({ auto: [{ id: "ra9", label: "Electrical · Installation RA 1", service: "Electrical", install: 5000 }] });
  click(window, nav(d, "billing")); await sleep(80);
  const urls = calls.map((c) => c.method + " " + c.u);
  const ia = urls.indexOf("POST /api/billing/thoth-mall/auto"), io = urls.indexOf("GET /api/billing/thoth-mall");
  ok(ia >= 0 && io > ia, "POST /auto happens before the overview is read: " + urls.join(" | "));
  ok(/Electrical · Installation RA 1 was opened automatically from Site Progress/.test(d.getElementById("bl-toast").textContent), "toast tells what was opened");

  // ---- nothing created: no toast, list still shows
  ({ window, d, calls } = await boot());
  click(window, nav(d, "billing")); await sleep(80);
  ok(!d.getElementById("bl-toast"), "nothing opened -> no toast");

  // ---- the server being unreachable for /auto never blocks the list
  ({ window, d, calls } = await boot());
  const real = window.fetch;
  window.fetch = (u, o) => String(u).endsWith("/auto") ? Promise.reject(new Error("net")) : real(u, o);
  click(window, nav(d, "billing")); await sleep(80);
  ok(!!d.querySelector(".bl-kpis"), "if /auto fails the Billing home still loads");

  // ---- setup switch
  const st = clone(FX.setup);
  st.services.forEach((x, i) => { x.auto_draft = i !== 0; });
  ({ window, d, calls } = await boot({ setup: st }));
  click(window, nav(d, "billing")); await sleep(80);
  click(window, d.getElementById("bl-setup")); await sleep(80);
  const boxes = [...d.querySelectorAll('.bl-sr [data-f="auto_draft"]')];
  ok(boxes.length === st.services.length && boxes[0].checked === false && boxes[1].checked === true, "one switch per service, shows the saved state");
  ok(/Open draft automatically/.test(d.querySelector(".bl-sr").textContent), "labelled in plain words");
  boxes[1].checked = false;
  click(window, d.querySelector('.bl-sr[data-i="1"] [data-save]')); await sleep(60);
  const saved = calls.filter((c) => c.u.endsWith("/setup") && c.method === "POST").pop();
  ok(saved && saved.body.auto_draft === false && saved.body.service === st.services[1].service, "Save sends the switch: " + JSON.stringify(saved && saved.body));
  // a service the backend does not report a value for is treated as ON (default)
  const old = clone(FX.setup); old.services.forEach((x) => { delete x.auto_draft; });
  ({ window, d } = await boot({ setup: old }));
  click(window, nav(d, "billing")); await sleep(80);
  click(window, d.getElementById("bl-setup")); await sleep(80);
  ok([...d.querySelectorAll('.bl-sr [data-f="auto_draft"]')].every((b) => b.checked), "no value from the server -> default ON");

  // ---- live refresh of the home screen
  const ov1 = clone(FX.overview);
  ({ window, d, calls } = await boot({ ov: ov1, live: 60 }));
  click(window, nav(d, "billing")); await sleep(40);
  const before = text(d);
  const ov2 = clone(FX.overview); ov2.billed = 777777;
  ov2.ras = ov2.ras.map((r) => ({ ...r, this_total: 424242 }));
  window.__state = null;
  // swap what the server returns
  const fetchOrig = window.fetch;
  window.fetch = (u, o) => String(u) === "/api/billing/thoth-mall" ? Promise.resolve({ ok: true, status: 200, json: async () => ov2, text: async () => "" }) : fetchOrig(u, o);
  await sleep(250);
  ok(/4,24,242/.test(text(d)) && text(d) !== before, "home refreshed by itself with the new numbers");

  // ---- a dialog open: no refresh underneath it
  click(window, d.getElementById("bl-dl")); await sleep(40);
  const n0 = calls.length; await sleep(200);
  ok(calls.slice(n0).filter((c) => c.u === "/api/billing/thoth-mall").length === 0 && !!d.getElementById("bl-modal"), "no refresh while a dialog is open");
  click(window, d.getElementById("bl-mx")); await sleep(5);

  // ---- RA draft screen refreshes; typing in a field pauses it; an invoiced RA never refreshes
  const b1 = clone(FX.bill);
  ({ window, d, calls } = await boot({ bill: b1, live: 60 }));
  click(window, nav(d, "billing")); await sleep(40);
  click(window, d.querySelector(".bl-rar")); await sleep(60);
  ok(/Draft/.test(text(d)), "on a draft");
  const kpi = () => d.querySelector(".bl-kpi b").textContent;
  const k0 = kpi();
  const b2 = clone(FX.bill); b2.this_total = 1234567; b2.totals.supply.this = 1234567;
  const f2 = window.fetch;
  window.fetch = (u, o) => String(u).includes("/ra/" + FX.raid) && !(o && o.method) ? Promise.resolve({ ok: true, status: 200, json: async () => b2, text: async () => "" }) : f2(u, o);
  await sleep(250);
  ok(kpi() !== k0 && /12,34,567/.test(kpi()), "draft RA screen follows Site Progress live: " + k0 + " -> " + kpi());
  const inp = d.getElementById("bl-ino"); inp.focus();
  const b3 = clone(b2); b3.this_total = 5550000; b3.totals.supply.this = 5550000;
  window.fetch = (u, o) => String(u).includes("/ra/" + FX.raid) && !(o && o.method) ? Promise.resolve({ ok: true, status: 200, json: async () => b3, text: async () => "" }) : f2(u, o);
  await sleep(250);
  ok(!/55,50,000/.test(kpi()) && d.activeElement === inp, "while someone types in a field the screen is left alone");
  inp.blur(); await sleep(250);
  ok(/55,50,000/.test(kpi()), "after typing stops it catches up");
  const lk = clone(FX.bill); lk.ra.status = "locked";
  ({ window, d, calls } = await boot({ bill: lk, live: 60 }));
  click(window, nav(d, "billing")); await sleep(40);
  click(window, d.querySelector(".bl-rar")); await sleep(60);
  const n1 = calls.length; await sleep(250);
  ok(!calls.slice(n1).some((c) => /\/ra\//.test(c.u)), "an invoiced RA is frozen: never re-fetched");

  console.log(`billing auto-draft UI: ${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
}
main().catch((e) => { console.error("THROW", e); process.exit(1); });
