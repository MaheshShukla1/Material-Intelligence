// jsdom test: quantity ABOVE the BOQ in Billing -- a chip on the home row (no red cards), a strip inside the draft, ONE pop-up to accept
// line by line, an Extra tag + "Back to BOQ qty" afterwards, the same pop-up for a typed quantity, and the upload's "Above the BOQ" group.
// Runs on the REAL payloads of the backend scenario (billing_extra_fixture.json, written by test_billing_extra.py with MI_DUMP_FIXTURE).
import { JSDOM } from "jsdom";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
let pass = 0, fail = 0;
function ok(cond, msg) { if (cond) pass++; else { fail++; console.error("FAIL:", msg); } }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const FX = JSON.parse(fs.readFileSync(path.join(__dirname, "billing_extra_fixture.json"), "utf8"));
const clone = (x) => JSON.parse(JSON.stringify(x));
const click = (w, el) => el.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));

async function boot(over = {}) {
  const html = `<!doctype html><html><body>
    <header class="top" id="top"><div class="brand"><p id="ctx"></p></div>
      <div class="topact"><button id="home"></button><button id="pick"></button><button id="sync"></button><button id="syncgo"></button></div></header>
    <main><section id="drop" hidden></section><section id="busy" hidden></section>
      <section id="report"><div class="bar"><div class="tabs" id="svc"></div></div></section></main></body></html>`;
  const dom = new JSDOM(html, { url: "https://example.test/", runScripts: "dangerously", pretendToBeVisual: true });
  const { window } = dom; const calls = [];
  const state = { ov: clone(FX.overview_before), bill: clone(FX.before), ...over };
  const setup = { services: [], meta: {} };
  window.fetch = (url, opts) => {
    const u = String(url), method = (opts && opts.method) || "GET";
    const isForm = opts && opts.body && typeof opts.body !== "string";
    const body = opts && opts.body && !isForm ? JSON.parse(opts.body) : null;
    calls.push({ u, method, body, form: isForm ? opts.body : null });
    let res = {}, status = 200;
    if (u === "/api/projects") res = [{ slug: "thoth-mall", project: "Thoth Mall", runs: 1, latest_run: "r1" }];
    else if (u === "/api/billing/thoth-mall") res = state.ov;
    else if (u === "/api/billing/thoth-mall/setup" && method === "GET") res = state.setup || setup;
    else if (u === `/api/billing/thoth-mall/ra/${FX.raid}` && method === "GET") res = state.bill;
    else if (u === `/api/billing/thoth-mall/ra/${FX.raid}/accept-excess`) { res = { ok: true, n: (body.items || []).length }; if (state.onAccept) state.onAccept(body); }
    else if (/\/ra\/[^/]+\/line$/.test(u)) { res = { ok: true }; if (state.onLine) state.onLine(body); }
    else if (u.startsWith("/api/billing/thoth-mall/import-sheet/")) res = isForm && opts.body.get("commit") === "true" ? { committed: true, applied: 1, challans: null, typed: null } : state.upload;
    else if (u.startsWith("/api/billing/thoth-mall/challans/")) res = { last_locked: 0, mode: "separate", challans: [], over: [], received: {} };
    return Promise.resolve({ ok: status < 400, status, json: async () => res, text: async () => JSON.stringify(res) });
  };
  window.eval(fs.readFileSync(path.join(__dirname, "siteprogress.js"), "utf8"));
  window.eval(fs.readFileSync(path.join(__dirname, "billing.js"), "utf8"));
  await sleep(0);
  return { window, d: window.document, calls, state };
}
const nav = (d, v) => [...d.querySelectorAll(".spnav button")].find((b) => b.dataset.v === v);
async function openRA(h) { click(h.window, nav(h.d, "billing")); await sleep(80); click(h.window, h.d.querySelector(".bl-rar")); await sleep(80); }
const btn = (d, label) => [...d.querySelectorAll("#bl-modal .mact .btn")].find((x) => x.textContent.trim().startsWith(label));
const txt = (d) => d.getElementById("billing").textContent.replace(/\s+/g, " ");

async function main() {
  // ---- Billing home: a chip on that RA row, no red card for it
  let h = await boot();
  click(h.window, nav(h.d, "billing")); await sleep(80);
  const row = h.d.querySelector(".bl-rar");
  ok(!!row.querySelector(".bl-xchip") && /2 above BOQ/.test(row.querySelector(".bl-xchip").textContent), "home: the RA row carries a '2 above BOQ' chip");
  ok(!h.d.querySelector(".bl-note.over") && !/challans total/.test(h.d.getElementById("billing").textContent), "home: no red CHECK card about quantity above the BOQ");

  // ---- inside the draft: one strip instead of the red cards
  await openRA(h);
  const strip = h.d.getElementById("bl-xstrip");
  ok(!!strip && /2 lines are above the BOQ quantity/.test(strip.textContent), "draft: an amber strip says 2 lines are above the BOQ");
  ok(/worth\s*₹\s*1,?20,?000|worth/.test(strip.textContent) && /billed only up to the BOQ for now/.test(strip.textContent), "strip: says what the extra is worth and that nothing extra is billed yet");
  ok(!h.d.querySelector(".bl-note.over"), "draft: the red CHECK cards are gone");
  ok(!h.d.querySelector(".bl-xtag") && !h.d.querySelector("[data-xback]"), "nothing is marked Extra before an accept");

  // ---- the pop-up: nothing posted until Accept; each line has its own tick
  click(h.window, h.d.getElementById("bl-xreview")); await sleep(20);
  const rows = [...h.d.querySelectorAll("#bl-modal .bl-xr")];
  ok(rows.length === 2 && rows.every((r) => r.querySelector("input").checked), "pop-up: two lines, both ticked");
  ok(/Diesel engine pump set/.test(rows[0].textContent + rows[1].textContent) && /Butterfly valve 150mm/.test(rows[0].textContent + rows[1].textContent), "pop-up: the lines are the ones above the BOQ");
  const vrow = rows.find((r) => /Butterfly valve/.test(r.textContent));
  ok(/\+2/.test(vrow.textContent) && /6/.test(vrow.textContent) && /₹\s*7,000/.test(vrow.textContent), "pop-up: valve BOQ 4, challans 6, extra +2, value ₹ 7,000 (rate x extra x supply %)");
  ok(btn(h.d, "Accept 2 and include in bill") && btn(h.d, "Keep to BOQ"), "pop-up: Accept 2 / Keep to BOQ");
  const pump = rows.find((r) => /Diesel engine/.test(r.textContent)).querySelector("input");
  pump.checked = false; pump.dispatchEvent(new h.window.Event("change", { bubbles: true })); await sleep(5);
  ok(!!btn(h.d, "Accept 1 and include in bill"), "unticking one line updates the button to Accept 1");
  ok(!h.calls.some((c) => /accept-excess/.test(c.u)), "nothing is posted before Accept");
  h.state.onAccept = () => { h.state.bill = clone(FX.after); h.state.ov = clone(FX.overview_after); };
  click(h.window, btn(h.d, "Accept 1")); await sleep(120);
  const post = h.calls.find((c) => /accept-excess/.test(c.u));
  ok(post && post.body.accept === true && post.body.items.length === 1 && post.body.items[0].item_code === "2" && post.body.items[0].stage === "supply", "Accept posts only the ticked line");
  ok(!h.d.getElementById("bl-modal"), "pop-up closes after Accept");
  ok(/accepted as Extra over BOQ/.test(h.d.getElementById("bl-toast").textContent), "a toast says what was accepted");

  // ---- after accept: marked inside the draft, own line in the total, per-line undo
  ok(!!h.d.getElementById("bl-xstrip") && /1 line is above the BOQ quantity/.test(h.d.getElementById("bl-xstrip").textContent), "the strip now says 1 line is left above the BOQ (the pump, not accepted)");
  const tag = h.d.querySelector(".bl-lr .bl-xtag");
  ok(!!tag && /Extra \+2/.test(tag.textContent), "the accepted line shows 'Extra +2'");
  ok(!!h.d.getElementById("bl-xsum") && /Extra over BOQ\s*₹\s*7,000/.test(h.d.getElementById("bl-xsum").textContent), "This bill shows 'of which Extra over BOQ ₹ 7,000'");
  const back = h.d.querySelector("[data-xback]");
  ok(!!back && /Back to BOQ qty/.test(back.textContent), "the accepted line offers 'Back to BOQ qty'");
  click(h.window, back); await sleep(100);
  const un = h.calls.filter((c) => /accept-excess/.test(c.u)).pop();
  ok(un.body.accept === false && un.body.items[0].item_code === "2", "Back to BOQ qty posts accept=false for that line");

  // ---- Keep to BOQ posts nothing and leaves the strip
  h = await boot(); await openRA(h);
  click(h.window, h.d.getElementById("bl-xreview")); await sleep(20);
  click(h.window, btn(h.d, "Keep to BOQ")); await sleep(30);
  ok(!h.d.getElementById("bl-modal") && !h.calls.some((c) => /accept-excess/.test(c.u)) && !!h.d.getElementById("bl-xstrip"), "Keep to BOQ: closes, posts nothing, the strip stays");

  // ---- a typed quantity above the BOQ opens the same pop-up (no bare refusal); within the BOQ it does not
  h = await boot(); await openRA(h);
  const valveBtn = [...h.d.querySelectorAll("[data-edit]")].find((b) => b.dataset.edit === "2");
  click(h.window, valveBtn); await sleep(20);
  ok(/no limit/.test(h.d.getElementById("bl-modal").textContent) === false && /Most you can bill now/.test(h.d.getElementById("bl-modal").textContent), "typing: shows the most you can bill before the accept");
  h.d.getElementById("bl-q").value = "3"; click(h.window, h.d.querySelector("#bl-modal .mact .primary")); await sleep(80);
  ok(!h.d.querySelector("#bl-modal .bl-xr") && h.calls.some((c) => /\/line$/.test(c.u) && c.body.qty === 3 && !c.body.accept_excess), "within the BOQ: saved as before, no pop-up");
  click(h.window, [...h.d.querySelectorAll("[data-edit]")].find((b) => b.dataset.edit === "2")); await sleep(20);
  h.d.getElementById("bl-q").value = "7"; click(h.window, h.d.querySelector("#bl-modal .mact .primary")); await sleep(30);
  const xr = h.d.querySelectorAll("#bl-modal .bl-xr");
  ok(xr.length === 1 && /Above the BOQ quantity/.test(h.d.getElementById("bl-modal").textContent) && /You typed/.test(xr[0].textContent) && /\+3/.test(xr[0].textContent), "typing 7 against a BOQ of 4: the pop-up, extra +3");
  ok(!!btn(h.d, "Bill only up to BOQ") && !!btn(h.d, "Accept 1 and include in bill"), "typed: Bill only up to BOQ / Accept 1");
  const before = h.calls.length;
  click(h.window, btn(h.d, "Accept 1")); await sleep(100);
  const line = h.calls.slice(before).find((c) => /\/line$/.test(c.u));
  ok(line && line.body.qty === 7 && line.body.accept_excess === true, "typed + Accept: the quantity is saved with accept_excess");
  click(h.window, [...h.d.querySelectorAll("[data-edit]")].find((b) => b.dataset.edit === "2")); await sleep(20);
  h.d.getElementById("bl-q").value = "7"; click(h.window, h.d.querySelector("#bl-modal .mact .primary")); await sleep(30);
  const b2 = h.calls.length;
  click(h.window, btn(h.d, "Bill only up to BOQ")); await sleep(100);
  const keep = h.calls.slice(b2).find((c) => /\/line$/.test(c.u));
  ok(keep && keep.body.qty === 4 && !keep.body.accept_excess, "typed + Bill only up to BOQ: saves the BOQ quantity (4), no accept");

  // ---- the lock confirmation mentions what is not accepted
  h = await boot(); await openRA(h);
  click(h.window, h.d.getElementById("bl-lock")); await sleep(20);
  ok(/2 lines are above the BOQ quantity and not accepted/.test(h.d.getElementById("bl-modal").textContent), "Mark as invoiced: says 2 lines are above the BOQ and not accepted");
  ok(!/negative/.test(h.d.getElementById("bl-modal").textContent), "and does not call them negative");

  // ---- an invoiced RA: nothing to accept, no undo
  const locked = clone(FX.after); locked.ra.status = "locked";
  h = await boot({ bill: locked }); await openRA(h);
  ok(!h.d.getElementById("bl-xstrip") && !h.d.querySelector("[data-xback]") && !!h.d.querySelector(".bl-xtag"), "invoiced: the Extra stays visible, nothing to accept or undo");

  // ---- Upload filled sheet: the extra is its own ticked group, not a refusal
  h = await boot({ upload: clone(FX.upload_preview) }); await openRA(h);
  const pickFile = async () => {
    click(h.window, h.d.getElementById("bl-impra")); await sleep(20);
    const inp = h.d.getElementById("bl-file");
    Object.defineProperty(inp, "files", { value: [new h.window.File(["x"], "f.xlsx")], configurable: true });
    inp.dispatchEvent(new h.window.Event("change")); await sleep(60);
  };
  await pickFile();
  const up = h.d.getElementById("bl-xup");
  ok(!!up && /Above the BOQ quantity \(1\)/.test(up.textContent) && /GI pipe 100mm/.test(up.textContent) && /Extra \+30/.test(up.textContent), "upload: 'Above the BOQ quantity (1)' lists the GI pipe line with its extra");
  ok(up.querySelector("input").checked, "upload: ticked from the start (typed on purpose)");
  ok(!!h.d.getElementById("bl-doimp") && /Import and accept 1 extra/.test(h.d.getElementById("bl-doimp").textContent), "upload: the button reads 'Import and accept 1 extra'");
  ok(!h.d.querySelector("#bl-impres .bl-callout") && !/cannot be billed/i.test(h.d.getElementById("bl-impres").textContent), "upload: no refusal message for the extra");
  const cb = up.querySelector("input"); cb.checked = false; cb.dispatchEvent(new h.window.Event("change", { bubbles: true })); await sleep(10);
  ok(/^Import$/.test(h.d.getElementById("bl-doimp").textContent.trim()), "unticked: the button is plain Import");
  click(h.window, h.d.getElementById("bl-doimp")); await sleep(30);
  ok(!h.calls.some((c) => c.form && c.form.get("commit") === "true"), "unticked and nothing else to import: no commit is sent");
  const cb2 = h.d.querySelector("#bl-xup input"); cb2.checked = true; cb2.dispatchEvent(new h.window.Event("change", { bubbles: true })); await sleep(10);
  click(h.window, h.d.getElementById("bl-doimp")); await sleep(80);
  const commit = h.calls.find((c) => c.form && c.form.get("commit") === "true");
  const acc = commit && JSON.parse(commit.form.get("accept_over"));
  ok(acc && acc.length === 1 && acc[0].item_code === "3" && acc[0].stage === "install" && acc[0].ra === FX.raid2, "ticked: the commit carries exactly that line in accept_over");

  console.log(`billing extra-over-BOQ UI: ${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
}
main().catch((e) => { console.error("THROW", e); process.exit(1); });
