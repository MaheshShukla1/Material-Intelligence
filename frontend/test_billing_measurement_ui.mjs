// jsdom test: the Download dialog's "With supporting backup" tick (the Measurement Sheet at the end of the invoice workbook).
// Payloads: the real backend's billing_download_fixture.json (Thoth: HVAC supply RA-02, HVAC INSTALLATION RA-01, Fire supply RA-01).
// Rules pinned: off by default (the workbook is exactly as before); ticking it adds ONE sheet only to installation RAs and the URL says
// so; before downloading the person is told what the sheet cannot show (billed earlier without locations / invoiced before locations
// were kept) and that adjustment lines exist; a Supply RA is named as getting none; a failed coverage check never blocks the download.
import { JSDOM } from "jsdom";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
let pass = 0, fail = 0;
function ok(cond, msg) { if (cond) pass++; else { fail++; console.error("FAIL:", msg); } }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const FX = JSON.parse(fs.readFileSync(path.join(__dirname, "billing_download_fixture.json"), "utf8"));
const clone = (x) => JSON.parse(JSON.stringify(x));
const ID = FX.ids;

// ---------------------------------------------------------------- harness
async function boot(over) {
  const html = `<!doctype html><html><body>
    <header class="top" id="top"><div class="brand"><p id="ctx"></p></div>
      <div class="topact"><button id="home"></button><button id="pick"></button><button id="sync"></button><button id="syncgo"></button></div></header>
    <main><section id="drop" hidden></section><section id="busy" hidden></section>
      <section id="report"><div class="bar"><div class="tabs" id="svc"></div></div></section></main></body></html>`;
  const dom = new JSDOM(html, { url: "https://example.test/", runScripts: "dangerously", pretendToBeVisual: true });
  const { window } = dom; const calls = [], clicks = [];
  const state = { cov: {}, ov: clone(FX.ov0), setup: clone(FX.setup0), bills: { [ID.hvac2]: clone(FX.bill2), [ID.hvac1]: clone(FX.bill1) }, fail: null, ...over };
  window.URL.createObjectURL = (b) => { clicks.push({ blob: b }); return "blob:x"; };
  window.URL.revokeObjectURL = () => {};
  window.HTMLAnchorElement.prototype.click = function () { clicks.push({ href: this.href, download: this.download }); };
  window.fetch = (url, opts) => {
    const u = String(url), method = (opts && opts.method) || "GET";
    let body = null;
    if (opts && opts.body && typeof opts.body === "string") body = JSON.parse(opts.body);
    calls.push({ u, method, body });
    let res = {}, status = 200;
    const P = "/api/billing/thoth-mall";
    if (u === "/api/projects") res = [{ slug: "thoth-mall", project: "Thoth Mall", runs: 1, latest_run: "r1" }];
    else if (u.startsWith(P + "/measurement/coverage")) { if (state.covFail) { status = 500; res = { detail: "boom" }; } else res = { ras: new URLSearchParams(u.split("?")[1]).get("ras").split(",").map((id) => state.cov[id] || { id, applies: true, items: 2, earlier: [], legacy_ra: false, adjusted: 0 }) }; }
    else if (u === P) res = state.ov;
    else if (u === P + "/setup") res = state.setup;
    else if (u === P + "/meta" && method === "POST") { res = { ok: true }; state.ov = clone(FX.ov1); state.setup = clone(FX.setup1); }
    else if (/\/ra\/[^/]+\/invoice$/.test(u) && method === "POST") res = clone(FX.inv_reply);
    else if (/\/ra\/[^/]+$/.test(u)) res = state.bills[u.split("/").pop()];
    else if (u.startsWith(P + "/download")) {
      if (state.fail) { status = 400; res = { detail: state.fail }; }
      else {
        const n = new URLSearchParams(u.split("?")[1]);
        const zip = (n.get("ras") || "").split(",").filter(Boolean).length + (n.get("combined") ? 1 : 0) > 1;
        return Promise.resolve({ ok: true, status: 200, headers: { get: (h) => /disposition/i.test(h) ? `attachment; filename="${zip ? "thoth-mall_billing_2026-10-09.zip" : "one.xlsx"}"` : null },
          blob: async () => ({ size: 10 }) });
      }
    }
    return Promise.resolve({ ok: status < 400, status, json: async () => res, text: async () => JSON.stringify(res) });
  };
  window.eval(fs.readFileSync(path.join(__dirname, "siteprogress.js"), "utf8"));
  window.eval(fs.readFileSync(path.join(__dirname, "billing.js"), "utf8"));
  await sleep(0);
  const d = window.document;
  [...d.querySelectorAll(".spnav button")].find((b) => b.dataset.v === "billing").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(80);
  return { window, d, calls, state, clicks };
}
const click = (w, el) => el.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
const change = (w, el) => el.dispatchEvent(new w.Event("change", { bubbles: true }));
const tick = (w, el, on) => { el.checked = on; change(w, el); };
const modalText = (d) => (d.getElementById("bl-modal") || { textContent: "" }).textContent;
const files = (d) => [...d.querySelectorAll("#bl-modal .bl-fi b")].map((x) => x.textContent);
const goBtn = (d) => d.getElementById("bl-dl-go");
const pickLatest = async (w, d) => { click(w, [...d.querySelectorAll("#bl-modal [data-q]")].find((b) => b.dataset.q === "latest")); await sleep(20); };
const q = (url) => new URLSearchParams(url.split("?")[1]);

const chip = (d) => d.querySelector('#bl-modal [data-sh="meas"]');
const warns = (d, k) => [...d.querySelectorAll(`#bl-modal .bl-wn.${k}`)].map((x) => x.textContent);
const rowOf = (d, id) => d.querySelector(`#bl-modal [data-ra="${id}"]`).closest(".bl-dr");
async function open(over) {
  const ctx = await boot(over);
  click(ctx.window, ctx.d.getElementById("bl-dl")); await sleep(80);
  return ctx;
}
const tickRa = async (ctx, id) => { tick(ctx.window, ctx.d.querySelector(`#bl-modal [data-ra="${id}"]`), true); await sleep(40); };
const covCalls = (ctx) => ctx.calls.filter((c) => c.u.includes("/measurement/coverage"));

async function main() {
  // ---- 1. there, labelled, off by default; the workbook is exactly as before
  let ctx = await open();
  const { window, d } = ctx;
  ok(!!chip(d) && chip(d).closest("label").textContent.trim() === "With supporting backup", "a 'With supporting backup' tick is among the sheets");
  ok(chip(d).checked === false, "it is OFF by default");
  ok([...d.querySelectorAll("#bl-modal [data-sh]")].length === 6 && [...d.querySelectorAll("#bl-modal [data-sh]")].pop().dataset.sh === "meas", "it comes last, after the five existing sheets");
  ok(/Measurement Sheet as the last sheet/.test(d.querySelector("#bl-modal").textContent) && /no rates/i.test(d.querySelector("#bl-modal").textContent), "a hint says what it adds (last sheet, quantities only, no rates)");
  await tickRa(ctx, ID.hvac_i);
  ok(/3 sheets/.test(rowOf(d, ID.hvac_i).textContent), "installation RA: 3 sheets without the tick");
  ok(covCalls(ctx).length === 0, "no coverage call while the tick is off");

  // ---- 2. ticking: 4 sheets, the URL asks for meas, the coverage is fetched for the installation RA only
  tick(window, chip(d), true); await sleep(60);
  ok(/4 sheets/.test(rowOf(d, ID.hvac_i).textContent), "installation RA: 4 sheets with the tick");
  ok(covCalls(ctx).length === 1 && q(covCalls(ctx)[0].u).get("ras") === ID.hvac_i, `coverage asked for the installation RA only, got ${JSON.stringify(covCalls(ctx).map((c) => c.u))}`);
  ok(!d.querySelector("#bl-modal .bl-wn.meas") && !d.querySelector("#bl-modal .bl-wn.measinfo"), "nothing to warn about: no note shown");

  // the URL the Download button fetches
  let got = null;
  window.fetch = ((orig) => (u, o) => { if (String(u).includes("/download")) got = String(u); return orig(u, o); })(window.fetch);
  click(window, goBtn(d)); await sleep(60);
  ok(got && q(got).get("sheets").split(",").slice(-1)[0] === "meas" && q(got).get("sheets").split(",").length === 6 && q(got).get("ras") === ID.hvac_i,
    `download asks for the five sheets + meas for that RA, got ${got}`);

  // ---- 3. warnings from the coverage: earlier RAs without locations + adjustments
  ctx = await open({ cov: { [ID.hvac_i]: { id: ID.hvac_i, applies: true, items: 3, earlier: [{ sr: "39", desc: "150 Sq.mm cable", qty: 300 }, { sr: "", desc: "GI conduit", qty: 40 }], legacy_ra: false, adjusted: 2 } } });
  await tickRa(ctx, ID.hvac_i); tick(ctx.window, chip(ctx.d), true); await sleep(60);
  let w1 = warns(ctx.d, "meas"), w2 = warns(ctx.d, "measinfo");
  ok(w1.length === 1 && /2 items were billed in earlier RAs without locations/.test(w1[0]) && /Location of earlier RAs not recorded/.test(w1[0]) && /39 150 Sq\.mm cable/.test(w1[0]), `says what the sheet cannot show, got ${JSON.stringify(w1)}`);
  ok(w2.length === 1 && /2 items carry one adjustment line/.test(w2[0]) && /matches the bill/.test(w2[0]), `says adjustment lines exist, got ${JSON.stringify(w2)}`);

  // an RA invoiced before locations were kept
  ctx = await open({ cov: { [ID.hvac_i]: { id: ID.hvac_i, applies: true, items: 2, earlier: [], legacy_ra: true, adjusted: 0 } } });
  await tickRa(ctx, ID.hvac_i); tick(ctx.window, chip(ctx.d), true); await sleep(60);
  w1 = warns(ctx.d, "meas");
  ok(w1.length === 1 && /invoiced before locations were kept/.test(w1[0]) && /Location not recorded/.test(w1[0]), `legacy RA is named, got ${JSON.stringify(w1)}`);

  // ---- 4. a Supply RA gets no sheet, and is named for it
  ctx = await open();
  await tickRa(ctx, ID.hvac2); tick(ctx.window, chip(ctx.d), true); await sleep(60);
  ok(/5 sheets/.test(rowOf(ctx.d, ID.hvac2).textContent) && !/6 sheets/.test(rowOf(ctx.d, ID.hvac2).textContent), "a Supply RA still has its 5 sheets");
  w1 = warns(ctx.d, "meas");
  ok(w1.length === 1 && /Supply RA has no installation quantity, so it gets no Measurement Sheet/.test(w1[0]) && /HVAC/.test(w1[0]), `Supply RA is named, got ${JSON.stringify(w1)}`);
  ok(covCalls(ctx).length === 0, "no coverage call for a Supply RA");

  // ---- 5. a failed coverage check never blocks the download
  ctx = await open({ covFail: true });
  await tickRa(ctx, ID.hvac_i); tick(ctx.window, chip(ctx.d), true); await sleep(80);
  ok(!goBtn(ctx.d).disabled && files(ctx.d).length === 1 && warns(ctx.d, "meas").length === 0, "coverage failed: no warning, the download is still available");

  // ---- 6. unticking removes it everywhere
  ctx = await open();
  await tickRa(ctx, ID.hvac_i); tick(ctx.window, chip(ctx.d), true); await sleep(40); tick(ctx.window, chip(ctx.d), false); await sleep(40);
  ok(/3 sheets/.test(rowOf(ctx.d, ID.hvac_i).textContent) && chip(ctx.d).checked === false, "unticked: back to 3 sheets");
  got = null;
  ctx.window.fetch = ((orig) => (u, o) => { if (String(u).includes("/download")) got = String(u); return orig(u, o); })(ctx.window.fetch);
  click(ctx.window, goBtn(ctx.d)); await sleep(60);
  ok(got && !/meas/.test(got), `the URL no longer mentions meas, got ${got}`);

  // ---- 7. the tick stays when the dialog is reopened with the same choices (Fill details round-trip keeps DL)
  ctx = await open();
  await tickRa(ctx, ID.hvac_i); tick(ctx.window, chip(ctx.d), true); await sleep(40);
  ok(chip(ctx.d).checked === true && chip(ctx.d).closest("label").classList.contains("on"), "ticked chip is shown on");

  console.log(`${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
}
main().catch((e) => { console.error(e); process.exit(1); });
