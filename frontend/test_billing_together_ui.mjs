// jsdom test: Download dialog -- "Supply + Installation together (for us)". Runs on the REAL overview payload of the backend scenario
// (billing_together_fixture.json, written by test_billing_together.py with MI_DUMP_JSON): Fire has Supply RA 1 invoiced + Installation
// RA 1 as an open draft, HVAC has no RA. The built workbook itself is checked by test_billing_together.py.
import { JSDOM } from "jsdom";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
let pass = 0, fail = 0;
function ok(cond, msg) { if (cond) pass++; else { fail++; console.error("FAIL:", msg); } }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const FX = JSON.parse(fs.readFileSync(path.join(__dirname, "billing_together_fixture.json"), "utf8"));
const clone = (x) => JSON.parse(JSON.stringify(x));
const SLUG = FX.slug, P = "/api/billing/" + SLUG;

async function boot(ov) {
  const html = `<!doctype html><html><body>
    <header class="top" id="top"><div class="brand"><p id="ctx"></p></div>
      <div class="topact"><button id="home"></button><button id="pick"></button><button id="sync"></button><button id="syncgo"></button></div></header>
    <main><section id="drop" hidden></section><section id="busy" hidden></section>
      <section id="report"><div class="bar"><div class="tabs" id="svc"></div></div></section></main></body></html>`;
  const dom = new JSDOM(html, { url: "https://example.test/", runScripts: "dangerously", pretendToBeVisual: true });
  const { window } = dom; const calls = [], clicks = [];
  window.URL.createObjectURL = () => "blob:x"; window.URL.revokeObjectURL = () => {};
  window.HTMLAnchorElement.prototype.click = function () { clicks.push({ download: this.download }); };
  window.fetch = (url, opts) => {
    const u = String(url), method = (opts && opts.method) || "GET";
    calls.push({ u, method });
    let res = {};
    if (u === "/api/projects") res = [{ slug: SLUG, project: "Thoth Mall", runs: 1, latest_run: "r1" }];
    else if (u === P) res = ov;
    else if (u === P + "/setup") res = { services: [], meta: {} };
    else if (u.startsWith(P + "/download")) {
      const both = new URLSearchParams(u.split("?")[1]).getAll("both"), ras = (new URLSearchParams(u.split("?")[1]).get("ras") || "").split(",").filter(Boolean);
      return Promise.resolve({ ok: true, status: 200, headers: { get: () => `attachment; filename="${both.length + ras.length > 1 ? "x.zip" : "one.xlsx"}"` }, blob: async () => ({ size: 10 }) });
    }
    return Promise.resolve({ ok: true, status: 200, json: async () => res, text: async () => JSON.stringify(res) });
  };
  window.eval(fs.readFileSync(path.join(__dirname, "siteprogress.js"), "utf8"));
  window.eval(fs.readFileSync(path.join(__dirname, "billing.js"), "utf8"));
  await sleep(0);
  const d = window.document;
  [...d.querySelectorAll(".spnav button")].find((b) => b.dataset.v === "billing").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(80);
  return { window, d, calls, clicks };
}
const click = (w, el) => el.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
const tick = (w, el, on) => { el.checked = on; el.dispatchEvent(new w.Event("change", { bubbles: true })); };
const files = (d) => [...d.querySelectorAll("#bl-modal .bl-fi b")].map((x) => x.textContent);
const notes = (d) => [...d.querySelectorAll("#bl-modal .bl-fi small")].map((x) => x.textContent);
const q = (url) => new URLSearchParams(url.split("?")[1]);

async function main() {
  let { window, d, calls, clicks } = await boot(clone(FX.overview));
  click(window, d.getElementById("bl-dl")); await sleep(80);
  const card = d.getElementById("bl-bothcard");
  ok(!!card, "the Download dialog has the 'Supply + Installation together' card");
  ok(/Supply \+ Installation together/.test(card.textContent) && /For us/.test(card.textContent), "card is named and tagged 'For us'");
  ok(/Not a bill: no RA number, no invoice number, nothing locks/.test(card.textContent) && /client still gets the two invoices separately/.test(card.textContent), "card says it is not a bill and the client invoices stay separate");
  const boxes = [...card.querySelectorAll("[data-both]")];
  ok(boxes.length === 1 && boxes[0].dataset.both === "Fire", "only Fire is offered (HVAC has no RA, so it is not listed)");
  ok(/Supply RA 1 \(invoiced\) \+ Installation RA 1 \(draft\)/.test(card.textContent), "the row says which RAs it reads: " + card.textContent.replace(/\s+/g, " "));
  ok(!boxes[0].checked && !card.classList.contains("on"), "nothing is ticked by default: the usual download is unchanged");
  ok(!files(d).some((n) => /TOGETHER/.test(n)), "no combined file in the list until ticked");
  const before = files(d).length;

  // ---- tick it
  tick(window, d.querySelector("#bl-bothcard [data-both]"), true); await sleep(20);
  const f = files(d);
  ok(f.length === before + 1 && f.includes("test-thoth_Fire_Supply+Installation_TOGETHER_Supply-RA01_Install-RA01_INTERNAL.xlsx"), "ticked: the combined file is listed with the server's exact name: " + f.join(" | "));
  ok(notes(d).some((n) => /^Internal view · not a bill · Supply RA 1 \(invoiced\) \+ Installation RA 1 \(draft\)$/.test(n)), "its note says internal view, not a bill, and which RAs");
  ok(d.getElementById("bl-bothcard").classList.contains("on"), "card is highlighted when ticked");
  const wn = [...d.querySelectorAll("#bl-modal .bl-wn")].map((x) => x.textContent);
  ok(wn.some((t) => /Fire: the Supply \+ Installation view reads a draft RA/.test(t)), "warning: it reads a draft RA, numbers can still change");
  ok(!wn.some((t) => /has no RA yet/.test(t)), "no 'half empty' warning: both stages have an RA");

  // ---- download only the combined file (untick every RA)
  click(window, [...d.querySelectorAll("#bl-modal [data-q]")].find((b) => b.dataset.q === "none")); await sleep(20);
  ok(files(d).length === 1 && d.getElementById("bl-dl-go").textContent === "Download .xlsx" && !d.getElementById("bl-dl-go").disabled, "alone it is ONE .xlsx and the button is enabled");
  click(window, d.getElementById("bl-dl-go")); await sleep(60);
  const dlc = calls.filter((c) => c.u.startsWith(P + "/download")).pop();
  ok(dlc && q(dlc.u).getAll("both").join() === "Fire" && !q(dlc.u).get("ras"), "request: both=Fire and no ras: " + (dlc && dlc.u));
  ok(clicks.length === 1 && clicks[0].download === "one.xlsx", "the browser got the file");

  // ---- with the RAs as well it is one zip, and the service name travels as given
  ({ window, d, calls, clicks } = await boot(clone(FX.overview)));
  click(window, d.getElementById("bl-dl")); await sleep(80);
  tick(window, d.querySelector("#bl-bothcard [data-both]"), true); await sleep(20);
  ok(/Download 3 files \(\.zip\)/.test(d.getElementById("bl-dl-go").textContent), "2 RA workbooks + the combined file = one zip of 3");
  click(window, d.getElementById("bl-dl-go")); await sleep(60);
  const z = calls.filter((c) => c.u.startsWith(P + "/download")).pop();
  ok(q(z.u).getAll("both").join() === "Fire" && q(z.u).get("ras").split(",").length === 2, "request carries the 2 RAs and both=Fire");

  // ---- unticking
  ({ window, d } = await boot(clone(FX.overview)));
  click(window, d.getElementById("bl-dl")); await sleep(80);
  tick(window, d.querySelector("#bl-bothcard [data-both]"), true); await sleep(20);
  tick(window, d.querySelector("#bl-bothcard [data-both]"), false); await sleep(20);
  ok(!files(d).some((n) => /TOGETHER/.test(n)), "unticking removes it again");

  // ---- one stage only: offered, with the plain warning that the other half stays empty
  const one = clone(FX.overview);
  one.services.find((s) => s.service === "Fire").both_view.install = null;
  ({ window, d } = await boot(one));
  click(window, d.getElementById("bl-dl")); await sleep(80);
  ok(/Supply RA 1 \(invoiced\) \+ Installation: no RA yet/.test(d.getElementById("bl-bothcard").textContent), "row: 'Installation: no RA yet'");
  tick(window, d.querySelector("#bl-bothcard [data-both]"), true); await sleep(20);
  ok([...d.querySelectorAll("#bl-modal .bl-wn")].some((w) => /Installation has no RA yet, so that half of the view stays empty\. Nothing is guessed\./.test(w.textContent)), "warning: the empty half stays empty, nothing guessed");
  ok(files(d).includes("test-thoth_Fire_Supply+Installation_TOGETHER_Supply-RA01_Install-none_INTERNAL.xlsx"), "file name says Install-none, as the server names it");

  // ---- no service has a Supply or Installation RA: no card at all
  const none = clone(FX.overview);
  none.services.forEach((s) => { s.both_view = { supply: null, install: null }; });
  ({ window, d } = await boot(none));
  click(window, d.getElementById("bl-dl")); await sleep(80);
  ok(!d.getElementById("bl-bothcard"), "nothing to combine: the card is not shown");
  ok(!!d.querySelector("#bl-modal [data-dl='combined']") && !!d.querySelector("#bl-modal [data-dl='invoice']"), "the other download options are still there");

  // ---- an older server without both_view: no card, no crash
  const old = clone(FX.overview);
  old.services.forEach((s) => { delete s.both_view; });
  ({ window, d } = await boot(old));
  click(window, d.getElementById("bl-dl")); await sleep(80);
  ok(!d.getElementById("bl-bothcard") && !!d.getElementById("bl-modal"), "no both_view in the payload: dialog still opens without the card");

  console.log(`together UI: ${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
}
main().catch((e) => { console.error(e); process.exit(1); });
