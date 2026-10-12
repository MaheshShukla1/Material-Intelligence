// jsdom test of the Download dialog, the invoice-details form and the invoice strip of the Billing tab.
// Every payload comes from the REAL backend run on the real Thoth BOQ + the user's real HVAC invoice
// workbook (billing_download_fixture.json, built by make_download_fixture.py). The real file download and
// the pixel layout are covered by test_billing_download_chromium.py.
import { JSDOM } from "jsdom";
import css from "css";
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
const PH = "(max-width:760px)";
ok(decl(find(".bl-dl", PH), "grid-template-columns") === "1fr", "phone: the download dialog is one column");
ok(decl(find(".bl-dlR", PH), "position") === "static", "phone: the preview is not sticky");
ok(decl(find(".bl-ck", PH), "min-height") === "44px", "phone: sheet tick chips are 44px");
ok(decl(find(".bl-qk .btn,.bl-wn .btn".split(",")[0] + ",.bl-wn .btn", PH) || find(".bl-qk .btn", PH), "min-height") === "44px", "phone: quick-pick buttons are 44px");
ok(decl(find("#bl-modal .ctl", PH), "font-size") === "16px" && decl(find("#bl-modal .ctl", PH), "min-height") === "44px", "phone: modal inputs are 16px / 44px (no iOS zoom)");
ok(decl(find(".bl-dlcard .mact .btn", PH), "min-height") === "48px", "phone: the Download button is 48px");
ok(decl(find(".bl-dr", null), "min-height") === "44px" && decl(find(".bl-dsh", null), "min-height") === "44px", "RA rows are 44px tall (touch)");
ok(decl(find(".bl-isvc", PH), "grid-template-columns") === "1fr", "phone: invoice details rows stack");
ok(!bl.some((r) => r.type === "rule" && r.selectors.some((s) => /^(body|\.top|main|\.btn|\.ctl|\.sheet|\.card)\b/.test(s.trim()) && !/data-view="billing"/.test(s))), "billing.css still restyles nothing outside the Billing tab");

// ---------------------------------------------------------------- harness
async function boot(over) {
  const html = `<!doctype html><html><body>
    <header class="top" id="top"><div class="brand"><p id="ctx"></p></div>
      <div class="topact"><button id="home"></button><button id="pick"></button><button id="sync"></button><button id="syncgo"></button></div></header>
    <main><section id="drop" hidden></section><section id="busy" hidden></section>
      <section id="report"><div class="bar"><div class="tabs" id="svc"></div></div></section></main></body></html>`;
  const dom = new JSDOM(html, { url: "https://example.test/", runScripts: "dangerously", pretendToBeVisual: true });
  const { window } = dom; const calls = [], clicks = [];
  const state = { ov: clone(FX.ov0), setup: clone(FX.setup0), bills: { [ID.hvac2]: clone(FX.bill2), [ID.hvac1]: clone(FX.bill1) }, fail: null, ...over };
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
const q = (url) => new URLSearchParams(url.split("?")[1]);

async function main() {
  // ---- home: one Download button instead of "Download Excel"
  let { window, d, calls, clicks } = await boot();
  const dl = d.getElementById("bl-dl");
  ok(dl && dl.tagName === "BUTTON" && dl.textContent.trim() === "Download", "home has ONE Download button");
  ok(!d.getElementById("bl-xl") && !/Download Excel|All services Excel/.test(d.querySelector(".bl-headact").textContent), "the lone 'Download Excel' link is gone from the header");

  // ---- the dialog opens with the latest RA of every service ticked
  click(window, dl); await sleep(80);
  ok(!!d.getElementById("bl-modal") && /Download/.test(d.querySelector("#bl-modal h2").textContent), "Download dialog opens");
  const ticked = [...d.querySelectorAll("#bl-modal [data-ra]")].filter((x) => x.checked).map((x) => x.dataset.ra).sort();
  ok(ticked.join() === [ID.hvac2, ID.hvac_i, ID.fire].sort().join(), "default = the latest RA of each service/series (HVAC RA-02 supply, HVAC RA-01 install, Fire RA-01), not the locked RA-01");
  ok(files(d).length === 3 && files(d).every((n) => /^thoth-mall_.*_RA-\d\d(_DRAFT)?\.xlsx$/.test(n)), "preview lists one workbook per ticked RA with the real file names: " + files(d).join(" | "));
  ok(files(d).includes("thoth-mall_HVAC_Supply_RA-02_DRAFT.xlsx") && files(d).includes("thoth-mall_Fire_Supply_RA-01_DRAFT.xlsx") && files(d).includes("thoth-mall_HVAC_Installation_RA-01_DRAFT.xlsx"), "file names follow the server's naming exactly");
  ok(goBtn(d).textContent === "Download 3 files (.zip)" && !goBtn(d).disabled, "button: 'Download 3 files (.zip)'");
  ok(/3 files · one zip · 3 RA/.test(d.getElementById("bl-dlsum").textContent), "footer summary: 3 files · one zip · 3 RA");

  // ---- sheet counts per RA follow the service's supply source and the stage
  const rowOf = (id) => d.querySelector(`#bl-modal [data-ra="${id}"]`).closest(".bl-dr");
  ok(/5 sheets/.test(rowOf(ID.hvac2).textContent) && !/no challan/.test(rowOf(ID.hvac2).textContent), "HVAC supply RA (challans): 5 sheets");
  ok(/3 sheets · no challan sheets in an installation RA/.test(rowOf(ID.hvac_i).textContent), "installation RA: 3 sheets, says why no challan sheets");
  ok(/3 sheets · no challan sheets \(stock register\)/.test(rowOf(ID.fire).textContent), "stock-register service: 3 sheets, says why no challan sheets");
  ok(/Inv\. VE\/25-26\/014/.test(rowOf(ID.hvac1).textContent), "a locked RA row shows its invoice no.");

  // ---- warnings: drafts, stock register (with a way out), blank details (with a way out)
  const wn = [...d.querySelectorAll("#bl-modal .bl-wn")];
  ok(wn.some((w) => /3 draft RA are ticked/.test(w.textContent) && /DRAFT — not invoiced yet/.test(w.textContent)), "warning: drafts ticked");
  const stock = wn.find((w) => w.classList.contains("stock"));
  ok(stock && /Fire is on the stock register/.test(stock.textContent) && /Delivery challans/.test(stock.textContent) && !!stock.querySelector('[data-act="setup"]'),
    "warning: Fire is on the stock register and gets no challan sheets, with a 'Billing setup' button");
  ok(!wn.some((w) => w.classList.contains("stock") && /HVAC/.test(w.textContent)), "no stock warning for HVAC (challans)");
  const det = wn.find((w) => w.classList.contains("details"));
  ok(det && /Blank on the Tax Invoice sheet: Your GSTIN/.test(det.textContent) && / \+\d+ more/.test(det.textContent) && !!det.querySelector('[data-act="details"]'), "warning: blank Tax Invoice details, shortened, with 'Fill details'");

  // ---- ticking changes the plan and the URL
  tick(window, d.querySelector(`#bl-modal [data-ra="${ID.fire}"]`), false); await sleep(5);
  ok(files(d).length === 2 && goBtn(d).textContent === "Download 2 files (.zip)", "un-ticking Fire's RA leaves 2 files");
  ok(!d.querySelector("#bl-modal .bl-wn.stock"), "…and the stock-register warning goes with it");
  tick(window, d.querySelector(`#bl-modal [data-ra="${ID.hvac_i}"]`), false); await sleep(5);
  ok(files(d).length === 1 && goBtn(d).textContent === "Download .xlsx" && /1 file · 1 RA/.test(d.getElementById("bl-dlsum").textContent), "one RA → a plain '.xlsx' download, no zip");
  tick(window, d.querySelector(`#bl-modal [data-ra="${ID.hvac2}"]`), false); await sleep(5);
  ok(goBtn(d).disabled && goBtn(d).textContent === "Download" && /Nothing ticked yet/.test(modalText(d)) && /Tick what you need/.test(d.getElementById("bl-dlsum").textContent), "nothing ticked → disabled button and a hint");

  // ---- quick picks
  click(window, d.querySelector('#bl-modal [data-q="locked"]')); await sleep(5);
  ok(files(d).join() === "thoth-mall_HVAC_Supply_RA-01.xlsx", "'All invoiced' ticks only invoiced RAs");
  click(window, d.querySelector('#bl-modal [data-q="all"]')); await sleep(5);
  ok(files(d).length === 4, "'All' ticks all 4 RAs");
  click(window, d.querySelector('#bl-modal [data-q="none"]')); await sleep(5);
  ok(files(d).length === 0, "'Clear' empties it");
  click(window, d.querySelector('#bl-modal [data-q="latest"]')); await sleep(5);
  ok(files(d).length === 3, "'Latest of each service' is back to the default");
  // ---- By type: one tap ticks every RA of that type across all services; each RA stays its own file/invoice
  const qt = (k) => d.querySelector(`#bl-modal [data-qt="${k}"]`);
  ok(["supply", "install", "together"].every((k) => !!qt(k)) && /Supply · 3/.test(qt("supply").textContent) && /Installation · 1/.test(qt("install").textContent) && /Supply \+ Installation · 0/.test(qt("together").textContent),
    "By type shows Supply · 3, Installation · 1, Supply + Installation · 0 (real counts from the RA list): " + [...d.querySelectorAll("#bl-modal [data-qt]")].map((x) => x.textContent));
  ok(qt("together").disabled && !qt("supply").disabled, "a type with no RA is disabled (nothing to tick)");
  click(window, qt("install")); await sleep(5);
  ok(files(d).join() === "thoth-mall_HVAC_Installation_RA-01_DRAFT.xlsx", "By type → Installation ticks only the installation RA: " + files(d).join());
  ok(qt("install").classList.contains("on") && qt("install").getAttribute("aria-pressed") === "true" && !qt("supply").classList.contains("on"), "the chosen type is shown as pressed");
  click(window, qt("supply")); await sleep(5);
  ok(files(d).length === 3 && files(d).every((n) => /_Supply_RA-/.test(n)) && files(d).some((n) => /Fire_Supply/.test(n)) && files(d).some((n) => /HVAC_Supply_RA-01\.xlsx/.test(n)),
    "By type → Supply ticks every Supply RA of EVERY service, as separate files (3): " + files(d).join(" | "));
  tick(window, d.querySelector(`#bl-modal [data-ra="${ID.fire}"]`), false); await sleep(5);
  ok(!qt("supply").classList.contains("on"), "changing the ticks by hand un-presses the type");
  click(window, d.querySelector('#bl-modal [data-q="latest"]')); await sleep(5);
  ok(!qt("supply").classList.contains("on") && !qt("install").classList.contains("on"), "Latest of each service is not a type: nothing pressed");
  const svcBox = d.querySelector(`#bl-modal [data-svc="${[...d.querySelectorAll("#bl-modal .bl-ds")].findIndex((x) => /HVAC/.test(x.textContent))}"]`);
  tick(window, svcBox, true); await sleep(5);
  ok(files(d).filter((n) => /HVAC/.test(n)).length === 3, "ticking a service ticks all its RAs (HVAC: 3)");
  const hvBlock = [...d.querySelectorAll("#bl-modal .bl-ds")].find((x) => /HVAC/.test(x.textContent));
  ok(/3 \/ 3/.test(hvBlock.querySelector(".bl-dsh").textContent) && hvBlock.querySelector("[data-svc]").checked, "the service header counts 3 / 3 and shows ticked");
  ok(d.querySelectorAll("#bl-modal .bl-ds").length === 2 && /No RA yet: Electrical, Plumbing, ELV\./.test(modalText(d)), "services with no RA are not listed as empty cards; one line names them");

  // ---- one workbook mode / combined / sheet ticks
  click(window, d.querySelector('#bl-modal [data-mode="one"]')); await sleep(5);
  ok(files(d).length === 1 && /^thoth-mall_RA_invoices_\d{4}-\d\d-\d\d\.xlsx$/.test(files(d)[0]) && /sheets, named by service/.test(d.querySelector("#bl-modal .bl-fi small").textContent), "'All in one workbook' = ONE file, sheets named by service");
  click(window, d.querySelector('#bl-modal [data-mode="files"]')); await sleep(5);
  tick(window, d.querySelector('#bl-modal [data-dl="combined"]'), true); await sleep(5);
  ok(files(d).some((n) => /_RA_bills_/.test(n)) && files(d).length === 5, "'All services Excel' adds one more file");
  tick(window, d.querySelector('#bl-modal [data-dl="invoice"]'), false); await sleep(5);
  ok(files(d).length === 1 && /_RA_bills_/.test(files(d)[0]) && goBtn(d).textContent === "Download .xlsx", "invoice workbook off → only the All services Excel remains");
  ok(/In the All services Excel/.test(rowOf(ID.hvac2).textContent) && d.querySelector("#bl-modal .bl-shs.off"), "RA rows and sheet ticks say they only matter for the invoice workbook");
  tick(window, d.querySelector('#bl-modal [data-dl="invoice"]'), true); await sleep(5);
  tick(window, d.querySelector('#bl-modal [data-dl="combined"]'), false); await sleep(5);
  for (const k of ["abstract", "challan"]) tick(window, d.querySelector(`#bl-modal [data-sh="${k}"]`), false);
  await sleep(5);
  ok(/3 sheets/.test(rowOf(ID.hvac2).textContent) && !d.querySelector("#bl-modal .bl-wn.stock"), "un-ticking Challan Abstract + Challan: HVAC drops to 3 sheets and the stock warning disappears (nothing is missing)");
  for (const k of ["invoice", "summary", "boq"]) tick(window, d.querySelector(`#bl-modal [data-sh="${k}"]`), false);
  await sleep(5);
  ok(goBtn(d).disabled, "no sheet ticked → nothing to download");
  for (const k of ["invoice", "summary", "boq", "abstract", "challan"]) tick(window, d.querySelector(`#bl-modal [data-sh="${k}"]`), true);
  await sleep(5);

  // ---- the URL the dialog builds for the real download
  tick(window, d.querySelector('#bl-modal [data-svc="' + [...d.querySelectorAll("#bl-modal .bl-ds")].findIndex((x) => /HVAC/.test(x.textContent)) + '"]'), false);
  tick(window, d.querySelector(`#bl-modal [data-ra="${ID.hvac2}"]`), true);
  tick(window, d.querySelector(`#bl-modal [data-ra="${ID.fire}"]`), true);
  tick(window, d.querySelector('#bl-modal [data-dl="combined"]'), true); await sleep(5);
  click(window, goBtn(d)); await sleep(60);
  const dcall = calls.filter((c) => c.u.includes("/download")).pop();
  const p = q(dcall.u);
  ok(dcall.u.startsWith("/api/billing/thoth-mall/download?") && p.get("ras") === [ID.fire, ID.hvac2].join(",") && p.get("mode") === "files" &&
     p.get("sheets") === "invoice,summary,boq,abstract,challan" && p.get("combined") === "1", "download URL: " + dcall.u);
  ok(clicks.some((c) => c.download === "thoth-mall_billing_2026-10-09.zip"), "the browser saves the file under the server's filename (zip)");
  ok(!d.getElementById("bl-modal"), "the dialog closes after a successful download");
  ok(/Downloaded thoth-mall_billing/.test(d.getElementById("bl-toast").textContent), "toast confirms the download");

  // ---- a server error keeps the dialog open and says why
  ({ window, d, calls, clicks } = await boot({ fail: "Tick at least one sheet." }));
  click(window, d.getElementById("bl-dl")); await sleep(80);
  click(window, goBtn(d)); await sleep(60);
  ok(!!d.getElementById("bl-modal") && !goBtn(d).disabled && /Tick at least one sheet/.test(d.getElementById("bl-toast").textContent), "a failed download keeps the dialog, re-enables the button and shows the server's reason");

  // ---- 'Fill details': the form, the save, and back to the SAME dialog with the SAME ticks
  ({ window, d, calls, clicks } = await boot());
  click(window, d.getElementById("bl-dl")); await sleep(80);
  click(window, d.querySelector('#bl-modal [data-act="details"]')); await sleep(80);
  ok(/Invoice details/.test(d.querySelector("#bl-modal h2").textContent) && !d.querySelector("#bl-modal .bl-dl"), "'Fill details' opens the Invoice details form");
  const f = (k) => d.querySelector(`#bl-modal [data-m="${k}"]`);
  ok(f("client").value === "Thoth Mall" && f("contractor").value === "Vedant Engitech And Infrastructure Pvt. Ltd." && f("wo_no").value === "WO/12", "the form is pre-filled with what is already saved");
  ok(f("project_name").value === "Thoth Mall", "Project defaults to the project's name (as a suggestion in the box, saved only on Save)");
  ok(f("sgst").value === "" && f("cgst").value === "", "GST % is empty: no default rate is invented");
  ok(d.querySelectorAll("#bl-modal [data-sac]").length === FX.setup0.services.length, "one SAC / invoice-line row per service");
  ok(d.querySelectorAll("#bl-modal [data-rno]").length === 3, "and an invoice no. / date row for each of the 3 RAs being downloaded");
  const sacHV = [...d.querySelectorAll("#bl-modal .bl-isvc")].find((r) => /^HVAC/.test(r.textContent.trim()));
  ok(sacHV.querySelector("[data-desc]").placeholder === "Supply and Installation of HVAC Work", "the invoice line shows the automatic wording as its placeholder");
  for (const [k, v] of Object.entries(FX.meta_full)) { if (k === "sac") continue; const el = f(k); if (el) { el.value = String(v); } }
  d.querySelectorAll("#bl-modal [data-sac]").forEach((el, i) => { el.value = FX.meta_full.sac[FX.setup0.services[i].service] || ""; });
  d.querySelector(`#bl-modal [data-rno="${ID.hvac2}"]`).value = "VE/25-26/021"; d.querySelector(`#bl-modal [data-rdt="${ID.hvac2}"]`).value = "2026-10-08";
  click(window, d.getElementById("bl-isave")); await sleep(120);
  const metaCall = calls.find((c) => c.u.endsWith("/meta") && c.method === "POST");
  ok(metaCall && metaCall.body.supplier_gstin === "24AADCV2799M1ZP" && metaCall.body.sgst === "9" && metaCall.body.client_gstin === "24ABCDE1234F1Z5" &&
     metaCall.body.sac.HVAC === "995461" && metaCall.body.sac.Fire === "995461" && metaCall.body.wo_no === "WO/12", "Save posts every detail to /meta");
  const invCalls = calls.filter((c) => /\/ra\/[^/]+\/invoice$/.test(c.u));
  ok(invCalls.length === 1 && invCalls[0].u.includes(ID.hvac2) && invCalls[0].body.no === "VE/25-26/021" && invCalls[0].body.date === "2026-10-08", "only the RA whose invoice no./date changed is posted");
  ok(!!d.querySelector("#bl-modal .bl-dl") && files(d).length === 3, "after saving you are back in the Download dialog with the same 3 RAs ticked");
  ok(!d.querySelector("#bl-modal .bl-wn.details"), "and the 'blank details' warning is gone (all details are filled)");

  // ---- the RA screen: Download button preselects THIS RA; the invoice strip saves on change
  ({ window, d, calls, clicks } = await boot());
  click(window, [...d.querySelectorAll(".bl-rar")].find((r) => r.dataset.ra === ID.hvac2)); await sleep(100);
  ok(!d.getElementById("bl-invoice") && !/All services Excel/.test(d.querySelector(".bl-headact").textContent) && !!d.getElementById("bl-dl"), "RA screen: the two download links became one Download button");
  const ino = d.getElementById("bl-ino"), ida = d.getElementById("bl-ida");
  ok(ino && ida.type === "date" && ino.value === "" && ida.value === "", "invoice strip: no invoice no./date yet (nothing invented)");
  ok(/Blank on the Tax Invoice sheet: Your GSTIN/.test(d.getElementById("bl-invm").textContent) && /blank$/.test(d.getElementById("bl-invchip").textContent), "the strip lists what is blank on the Tax Invoice sheet");
  ino.value = "VE/25-26/021"; change(window, ino); await sleep(60);
  const ic = calls.find((c) => /\/invoice$/.test(c.u));
  ok(ic && ic.u.includes(ID.hvac2) && ic.body.no === "VE/25-26/021" && ic.body.date === "", "leaving the invoice no. box saves it (no Save button to forget)");
  ok(/Saved/.test(d.getElementById("bl-invsaved").textContent), "…and says Saved");
  const left = FX.inv_reply.missing.length;
  ok(d.getElementById("bl-invchip").textContent === (left ? left + " blank" : "Complete") && d.getElementById("bl-invchip").classList.contains(left ? "draft" : "locked"), "the chip follows the server's list of what is still blank (" + left + ")");
  click(window, d.getElementById("bl-dl")); await sleep(80);
  ok(files(d).join() === "thoth-mall_HVAC_Supply_RA-02_DRAFT.xlsx", "Download from an RA screen has just that RA ticked: " + files(d).join());
  click(window, d.querySelector('#bl-modal .mact .btn')); await sleep(5);
  ok(!d.getElementById("bl-modal"), "Cancel closes the dialog");

  // ---- locked RA screen has the strip too (the invoice is raised after locking)
  ({ window, d, calls, clicks } = await boot());
  click(window, [...d.querySelectorAll(".bl-rar")].find((r) => r.dataset.ra === ID.hvac1)); await sleep(100);
  ok(d.getElementById("bl-ino").value === "VE/25-26/014" && d.getElementById("bl-ida").value === "2026-07-05", "a LOCKED RA shows its saved invoice no. and date, still editable");

  // ---- setup: the old header form became one card that opens the same details form
  ({ window, d, calls, clicks } = await boot());
  click(window, d.getElementById("bl-setup")); await sleep(80);
  ok(!d.getElementById("bl-msave") && /Invoice & annexure details/.test(d.getElementById("billing").textContent), "Billing setup: 'Annexure header' became 'Invoice & annexure details'");
  ok(/11 details still blank/.test(d.querySelector(".bl-metac").textContent) && /Your GSTIN/.test(d.querySelector(".bl-metac").textContent), "the card counts what is still blank (11, from the server)");
  click(window, d.getElementById("bl-idet")); await sleep(80);
  ok(/Invoice details/.test(d.querySelector("#bl-modal h2").textContent) && d.querySelectorAll("#bl-modal [data-rno]").length === 0, "Edit invoice details opens the form (no per-RA rows here)");
  click(window, d.getElementById("bl-isave")); await sleep(80);
  ok(/Invoice & annexure details/.test(d.getElementById("billing").textContent) && !d.getElementById("bl-modal"), "saving returns to Billing setup");

  console.log(`billing download UI: ${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
}
main().catch((e) => { console.error(e); process.exit(1); });
