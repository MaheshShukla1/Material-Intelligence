// jsdom test: a frozen RA is called "Invoiced" everywhere on screen, file names carry no LOCKED, and a Supply + Installation RA
// with nothing logged in Site Progress says so instead of silently showing Installation Rs 0.
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


const together = (status, instThis, instPrev) => {
  const b = clone(FX.bill);
  b.ra.series = "together"; b.ra.stages = ["supply", "install"]; b.ra.status = status; b.ra.label = "Fire · Supply + Installation RA 1";
  b.pct.install = 15; b.unfilled.install = 0;
  b.totals.install = { wo: 0, prev: instPrev, this: instThis, total: instPrev + instThis };
  (b.groups || []).forEach((g) => { g.stages.install = { prev: 0, this: 0, total: 0 }; });
  if (b.other) b.other.stages.install = { prev: 0, this: 0, total: 0 };
  b.lines.forEach((l) => { l.install = { pct: 15, prev: 0, prev_amount: 0, utd: 0, this: 0, amount: 0, source: "progress", overridden: false, reason: "", note: "Work done, from Site Progress" }; });
  return b;
};

async function main() {
  const nav = (d, v) => [...d.querySelectorAll(".spnav button")].find((b) => b.dataset.v === v);
  const openRA = async (over) => {
    const r = await boot(over);
    click(r.window, nav(r.d, "billing")); await sleep(80);
    click(r.window, r.d.querySelector(".bl-rar")); await sleep(80);
    return r;
  };
  const bl = (d) => d.getElementById("billing").textContent;

  // ---- Together draft, nothing in Site Progress: the screen says why Installation is Rs 0
  let { window, d } = await openRA({ bill: together("draft", 0, 0) });
  const note = d.getElementById("bl-noinst");
  ok(!!note && /no installation progress is logged in Site Progress/.test(note.textContent) && /nothing to type/.test(note.textContent), "draft: Installation Rs 0 is explained (Site Progress has nothing yet, nothing to type)");
  ok(/Draft · cut-off/.test(bl(d)), "draft header says Draft");
  const gl = [...d.querySelector(".bl-gr").querySelectorAll("[data-l]")].map((x) => x.dataset.l);
  ok(gl.includes("WO amount") && gl.includes("Supply this bill") && gl.includes("Installation this bill"), "annexure cells carry labels so the phone layout can show them: " + gl.join(" | "));

  // ---- Together draft WITH installation progress: no note
  ({ window, d } = await openRA({ bill: together("draft", 1500, 0) }));
  ok(!d.getElementById("bl-noinst"), "installation has numbers -> no note");

  // ---- Together draft, installation already billed earlier and nothing new: not 'no progress' (it was billed before)
  ({ window, d } = await openRA({ bill: together("draft", 0, 900) }));
  ok(!d.getElementById("bl-noinst"), "installation billed before, nothing new this time -> no 'nothing logged' note");

  // ---- Supply-only RA: never mentions installation
  ({ window, d } = await openRA({}));
  ok(!d.getElementById("bl-noinst"), "a Supply-only RA has no installation note");

  // ---- Frozen Together RA: called Invoiced, own wording, can only be reopened
  ({ window, d } = await openRA({ bill: together("locked", 0, 0) }));
  ok(/Invoiced · cut-off/.test(bl(d)) && !/Locked/.test(bl(d)), "frozen RA header says Invoiced, never Locked");
  ok(/was invoiced/.test(d.getElementById("bl-noinst").textContent) && /New RA bill → Installation/.test(d.getElementById("bl-noinst").textContent), "frozen: says installation was Rs 0 when invoiced and how to bill it later");
  ok(!!d.getElementById("bl-reopen"), "Reopen still there");

  // ---- Home list + KPI
  const ov = clone(FX.overview);
  const mk = (id, status, stage_this) => ({ id, service: "Fire", series: "together", no: 1, label: "Fire · RA 1", status, cutoff: "2026-10-01", created: null, locked_at: null,
    this_total: 100, stages: ["supply", "install"], inv_no: "", inv_date: "", lines: 3, up_to_date: 100, inv_missing: [], stage_this });
  ov.ras = [mk("a", "locked", { supply: 100, install: 0 }), mk("b", "draft", { supply: 100, install: 40 })];
  ({ window, d } = await boot({ ov }));
  click(window, nav(d, "billing")); await sleep(80);
  const chips = [...d.querySelectorAll(".bl-rar .bl-chip")].map((c) => c.textContent).join();
  ok(chips === "Invoiced,Draft", "home list: Invoiced / Draft: " + chips);
  ok(/Invoiced so far/.test(bl(d)) && /1 RA invoiced/.test(bl(d)) && !/\(locked\)/.test(bl(d)), "KPI says 'Invoiced so far · 1 RA invoiced'");

  // ---- Download: row chip, hint, file names
  click(window, d.getElementById("bl-dl")); await sleep(80);
  const m = d.getElementById("bl-modal").textContent;
  ok(/Installation ₹0/.test(m), "download row says 'Installation ₹0' for the RA whose installation is zero");
  const rows = [...d.querySelectorAll("#bl-modal .bl-dr")];
  ok(!/Installation ₹0/.test(rows[1].textContent) && /Draft/.test(rows[1].textContent), "the draft that has installation money does not carry the hint");
  ok(/All invoiced/.test(m), "quick filter says 'All invoiced'");
  const names = () => [...d.querySelectorAll("#bl-modal .bl-fi b")].map((b) => b.textContent.replace(/\u200b/g, ""));
  click(window, d.querySelector('#bl-modal [data-q="locked"]')); await sleep(10);
  ok(names().join() === "thoth-mall_Fire_Supply+Installation_RA-01.xlsx", "an invoiced RA has a clean file name: " + names());
  click(window, d.querySelector('#bl-modal [data-q="none"]')); await sleep(5);
  click(window, d.querySelector('#bl-modal input[data-ra="b"]')); await sleep(10);
  ok(names().join() === "thoth-mall_Fire_Supply+Installation_RA-01_DRAFT.xlsx", "only a draft carries _DRAFT: " + names());
  ok(!/LOCKED|Locked/.test(d.getElementById("bl-modal").textContent), "no 'Locked' anywhere in the Download dialog");

  console.log(`billing invoiced wording UI: ${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
}
main().catch((e) => { console.error("THROW", e); process.exit(1); });
