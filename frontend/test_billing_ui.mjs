// jsdom test for the Billing tab (billing.js) against REAL API payloads captured
// from the live backend on the real Thoth multi-sheet BOQ (billing_fixture.json).
// Real pixel layout is covered by test_billing_chromium.py (jsdom has no layout).
import { JSDOM } from "jsdom";
import css from "css";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
let pass = 0, fail = 0;
function ok(cond, msg) { if (cond) pass++; else { fail++; console.error("FAIL:", msg); } }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const FX = JSON.parse(fs.readFileSync(path.join(__dirname, "billing_fixture.json"), "utf8"));
const clone = (x) => JSON.parse(JSON.stringify(x));

// ------------------------------------------------------------------- CSS
const bl = css.parse(fs.readFileSync(path.join(__dirname, "billing.css"), "utf8")).stylesheet.rules;
const norm = (s) => s.replace(/\s+/g, "");
const decl = (rule, p) => { const d = (rule.declarations || []).find((x) => x.property === p); return d ? d.value.replace(/\s+/g, " ").trim() : null; };
function find(sel, media) {
  const pool = media == null ? bl.filter((r) => r.type === "rule")
    : bl.filter((r) => r.type === "media" && norm(r.media) === norm(media)).flatMap((m) => m.rules.filter((r) => r.type === "rule"));
  const hits = pool.filter((r) => r.selectors.map((s) => s.replace(/\s+/g, " ").trim()).includes(sel));
  return hits.length ? { declarations: hits.flatMap((h) => h.declarations || []) } : undefined;
}
ok(decl(find('body[data-view="billing"] #report'), "display") === "none !important", "Billing view hides the Forecast report");
ok(decl(find('body[data-view="billing"] .topact'), "visibility") === "hidden", "Billing hides the run controls");
ok(decl(find(".bl-lh", "(max-width:760px)"), "display") === "none", "phone: wide column header row is replaced by labelled cards");
ok(decl(find(".bl-qtybtn", "(max-width:760px)"), "min-height") === "44px", "phone: quantity button is a 44px touch target");
ok(decl(find(".bl-ras", "(max-width:760px)"), "min-width") === "0", "phone: RA list is not forced wide");
ok(!bl.some((r) => r.type === "rule" && r.selectors.some((s) => /^(body|\.top|main|\.btn|\.ctl|\.sheet|\.card)\b/.test(s.trim()) && !/data-view="billing"/.test(s))),
   "billing.css only touches bl- classes / its own view (no restyling of other tabs)");

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
    if (u === "/api/projects") res = [{ slug: "thoth-mall", project: "Thoth Mall", runs: 1, latest_run: "r1" }];
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

async function main() {
  // ---- nav + view switching
  let { window, d, calls } = await boot();
  const nav = (v) => [...d.querySelectorAll(".spnav button")].find((b) => b.dataset.v === v);
  ok(!!nav("billing"), "Billing button is in the tab bar");
  click(window, nav("billing")); await sleep(80);
  ok(d.body.dataset.view === "billing", "body[data-view] = billing");
  ok(d.getElementById("report").hidden === true, "Forecast report hidden on Billing");
  ok(d.getElementById("billing").hidden === false && d.getElementById("siteprogress").hidden === true, "only #billing is shown");
  click(window, nav("forecast")); await sleep(20);
  ok(d.getElementById("report").hidden === false && d.getElementById("billing").hidden === true, "back on Forecast: report visible, billing hidden");
  click(window, nav("siteprogress")); await sleep(40); click(window, nav("billing")); await sleep(60); click(window, nav("forecast")); await sleep(20);
  ok(d.getElementById("report").hidden === false, "SP -> Billing -> Forecast keeps the report visible (no blank page)");

  // ---- overview from the real payload
  click(window, nav("billing")); await sleep(80);
  const t = d.getElementById("billing").textContent;
  ok(/Contract value/.test(t) && /₹ 1,14,70,96,759/.test(t), "contract value shown with Indian grouping to the rupee");
  ok(/Plumbing\s*Supply — · Install —\s*Payment term not set/.test(t) && /ELV\s*Supply — · Install —\s*Payment term not set/.test(t), "services without a payment term say Payment term not set (never a guessed %)");
  ok(/Fire · RA 1/.test(t) || /Fire\s*· RA 1/.test(t), "the draft RA is listed");
  ok(!!d.querySelector(".bl-chip.draft"), "draft chip");

  // ---- new RA modal: unset services cannot be picked; create posts the right body
  click(window, d.getElementById("bl-new")); await sleep(20);
  ok(d.querySelector('#bl-modal [data-svc="Plumbing"]').disabled === true, "unset service pill is disabled in New RA");
  click(window, d.querySelector('#bl-modal [data-svc="Fire"]')); await sleep(5);
  const opts = [...d.querySelectorAll("#bl-modal .bl-opt")].map((o) => o.dataset.series);
  ok(opts.join() === "supply,install,together", "Fire (usually separate) still offers every bill type: Supply only, Installation only and Together");
  ok(d.querySelector("#bl-modal .bl-opt.on").dataset.series === "install", "pre-selects the first FREE usual type (the Supply draft is already open, so Installation)");
  ok(d.querySelector('#bl-modal .bl-opt[data-series="supply"]').classList.contains("off") && d.querySelector('#bl-modal .bl-opt[data-series="together"]').classList.contains("off"), "Supply and Together are greyed: the open Supply draft carries the same stage");
  ok(/Supply RA 1 draft is already open/.test(d.querySelector('#bl-modal .bl-opt[data-series="together"]').textContent), "and the reason names the draft");
  ok(/usual for Fire/.test(d.querySelector('#bl-modal .bl-opt[data-series="supply"]').textContent) && !/usual for/.test(d.querySelector('#bl-modal .bl-opt[data-series="together"]').textContent), "the service's usual types are marked");
  click(window, d.querySelector('#bl-modal .bl-opt[data-series="install"]'));
  click(window, d.querySelector("#bl-modal .mact .primary")); await sleep(60);
  const post = calls.find((c) => c.u === "/api/billing/thoth-mall/ra" && c.method === "POST");
  ok(post && post.body.service === "Fire" && post.body.series === "install", "creating an RA posts {service, series}");

  // ---- RA bill screen from the real payload
  const b = FX.bill, line0 = b.lines[0];
  const row = d.querySelector(`.bl-lr[data-code="${line0.item_code}"]`);
  ok(!!row, "line row rendered");
  ok(d.querySelectorAll(".bl-lr").length === b.lines.length, "one row per active line");
  ok(/Fire row \d+/.test(row.textContent) && new RegExp("Ref " + line0.internal_ref).test(row.textContent), "row shows Internal Ref No and sheet row");
  ok(/typed by engineer.*gate pass 7/.test(row.querySelector(".x").textContent), "typed quantity shows its reason");
  ok(!!d.querySelector("#bl-lock") && !!d.querySelector("#bl-del") && !d.querySelector("#bl-reopen"), "draft shows Lock + Delete, not Reopen");
  ok(/110 supply lines have no quantity/.test(d.getElementById("billing").textContent), "unfilled manual lines are called out, never guessed");

  // ---- type a quantity
  click(window, d.querySelector(`[data-edit="${line0.item_code}"][data-stage="supply"]`)); await sleep(10);
  d.getElementById("bl-q").value = "0.25"; d.getElementById("bl-r").value = "site visit";
  click(window, d.querySelector("#bl-modal .mact .primary")); await sleep(60);
  const ln = calls.find((c) => /\/line$/.test(c.u));
  ok(ln && ln.body.item_code === line0.item_code && ln.body.stage === "supply" && ln.body.qty === 0.25 && ln.body.reason === "site visit", "typed qty posts item, stage, number, reason");
  // clear back to automatic
  click(window, d.querySelector(`[data-edit="${line0.item_code}"]`)); await sleep(10);
  const back = [...d.querySelectorAll("#bl-modal .mact .btn")].find((x) => /automatic/.test(x.textContent));
  ok(!!back, "an overridden line offers 'Back to automatic'");
  click(window, back); await sleep(40);
  ok(calls.filter((c) => /\/line$/.test(c.u)).pop().body.qty === null, "back to automatic posts qty null");

  // ---- lock flow
  click(window, d.getElementById("bl-lock")); await sleep(10);
  ok(/Mark Fire · Supply RA 1 as invoiced\?/.test(d.getElementById("bl-modal").textContent), "lock asks for confirmation");
  click(window, [...d.querySelectorAll("#bl-modal .mact .btn")].find((x) => x.textContent === "Mark as invoiced")); await sleep(60);
  ok(calls.some((c) => /\/lock$/.test(c.u) && c.method === "POST"), "lock posts");

  // ---- backend error is shown, not swallowed
  ({ window, d, calls } = await boot({ fail: "BOQ qty is 2 and 1 is already billed. More than that cannot be billed." }));
  click(window, [...d.querySelectorAll(".spnav button")].find((x) => x.dataset.v === "billing")); await sleep(80);
  click(window, d.querySelector(".bl-rar")); await sleep(60);
  click(window, d.querySelector("[data-edit]")); await sleep(10); d.getElementById("bl-q").value = "1";
  click(window, d.querySelector("#bl-modal .mact .primary")); await sleep(60);
  ok(/BOQ qty is 2/.test(d.getElementById("bl-toast").textContent), "server rejection reason shown in a toast");

  // ---- locked RA: negative (over-billed) line is red, no edit buttons, Reopen offered
  const locked = clone(FX.bill); locked.ra.status = "locked";
  locked.lines[0].supply = { ...locked.lines[0].supply, this: -1, amount: -3500, utd: 2, prev: 3, overridden: false };
  ({ window, d, calls } = await boot({ bill: locked }));
  click(window, [...d.querySelectorAll(".spnav button")].find((x) => x.dataset.v === "billing")); await sleep(80);
  click(window, d.querySelector(".bl-rar")); await sleep(60);
  ok(!!d.querySelector("#bl-reopen") && !d.querySelector("#bl-lock") && !d.querySelector("[data-edit]"), "locked RA: Reopen only, no editing");
  ok(!!d.querySelector(".bl-lr .n b.neg"), "negative line is marked");
  ok(/−₹ 3,500/.test(d.querySelector(".bl-lr").textContent), "negative amount formatted with a minus and Indian grouping");

  // ---- setup: saving posts what was typed; empty % stays empty (never a default)
  ({ window, d, calls } = await boot());
  click(window, [...d.querySelectorAll(".spnav button")].find((x) => x.dataset.v === "billing")); await sleep(80);
  click(window, d.getElementById("bl-setup")); await sleep(60);
  const plumb = [...d.querySelectorAll(".bl-sr")].find((r) => /Plumbing/.test(r.textContent));
  ok(!!plumb && plumb.classList.contains("unset"), "unset service row is highlighted");
  plumb.querySelector('[data-f="mode"]').value = "separate";
  plumb.querySelector('[data-f="supply_pct"]').value = "70";
  click(window, plumb.querySelector("[data-save]")); await sleep(60);
  const sv = calls.find((c) => c.u === "/api/billing/thoth-mall/setup" && c.method === "POST");
  ok(sv && sv.body.service === "Plumbing" && sv.body.supply_pct === "70" && sv.body.install_pct === "", "setup posts typed Supply %, empty Installation % stays empty (nothing defaulted)");
  click(window, d.querySelector("[data-groups]")); await sleep(10);
  ok(d.querySelectorAll(".bl-gt tbody tr").length >= 1, "annexure groups editor lists the sheet-derived groups");

  console.log(`${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
}
main().catch((e) => { console.error("THROW", e); process.exit(1); });
