// jsdom test of the ProjectBase sheet: the Download card, the one-time Connect dialog and the 3-step hand-off with the total check.
// Every payload is captured from the REAL backend run on the real Thoth project and the real Electrical Billing Request export
// (projectbase_fixture.json, built by make_projectbase_fixture.py). The real file contents are covered by backend/test_projectbase.py
// and the pixel layout by the Chromium run (desktop + phone).
import { JSDOM } from "jsdom";
import css from "css";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
let pass = 0, fail = 0;
function ok(cond, msg) { if (cond) pass++; else { fail++; console.error("FAIL:", msg); } }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const FX = JSON.parse(fs.readFileSync(path.join(__dirname, "projectbase_fixture.json"), "utf8"));
const clone = (x) => JSON.parse(JSON.stringify(x));
const [SUP, INS] = FX.ids;                       // ra6 Electrical › Only point wiring · Supply RA 1, ra7 Electrical › Other Electrical · Installation RA 1

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
ok(decl(find(".bl-pbact .btn", PH), "min-height") === "44px", "phone: Connect / Replace buttons are 44px");
ok(decl(find(".bl-askc", PH), "grid-template-columns") === "1fr", "phone: the two sides of a near-match question stack");
ok(decl(find(".bl-chkr .btn", PH), "min-height") === "44px" && decl(find(".bl-resa .btn", PH), "min-height") === "44px", "phone: Check / Mark as invoiced / Download again are 44px");
ok(decl(find(".bl-drop", null), "min-height") === "130px", "the drop zone is a big target");
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
  const state = { ov: clone(FX.ov0), inspect: clone(FX.inspect), inspectFail: null, locked: new Set(), ...over };
  window.URL.createObjectURL = () => "blob:x";
  window.URL.revokeObjectURL = () => {};
  window.HTMLAnchorElement.prototype.click = function () { clicks.push({ download: this.download }); };
  window.confirm = () => true;
  window.fetch = (url, opts) => {
    const u = String(url), method = (opts && opts.method) || "GET";
    let body = null;
    if (opts && opts.body && typeof opts.body === "string") body = JSON.parse(opts.body);
    calls.push({ u, method, body, form: !!(opts && opts.body && typeof opts.body !== "string") });
    let res = {}, status = 200;
    const P = "/api/billing/thoth-mall";
    if (u === "/api/projects") res = [{ slug: "thoth-mall", project: "Thoth Mall", runs: 1, latest_run: "r1" }];
    else if (u === P) res = state.ov;
    else if (u.startsWith(P + "/projectbase/preview")) {
      const ids = new URLSearchParams(u.split("?")[1]).get("ras").split(",");
      res = { ras: FX.preview.ras.filter((r) => ids.includes(r.id)) };
    }
    else if (u === P + "/projectbase/Electrical/inspect") { if (state.inspectFail) { status = 400; res = { detail: state.inspectFail }; } else res = state.inspect; }
    else if (u === P + "/projectbase/Electrical/connect") { res = FX.connect; state.ov = clone(FX.ov1); }
    else if (u === P + "/projectbase/Electrical" && method === "DELETE") { res = { ok: true }; state.ov = clone(FX.ov0); }
    else if (/\/ra\/[^/]+\/pb-check$/.test(u)) {
      const s = body.subtotal;
      res = s === FX.check_ok.pb ? FX.check_ok : s === FX.check_bad.pb ? FX.check_bad : { ...FX.check_far, pb: s, diff: FX.check_far.mi - s };
    }
    else if (/\/ra\/[^/]+\/lock$/.test(u)) { res = { ok: true }; state.locked.add(u.split("/")[u.split("/").length - 2]); }
    else if (u.startsWith(P + "/download")) {
      return Promise.resolve({ ok: true, status: 200, headers: { get: (h) => /disposition/i.test(h) ? 'attachment; filename="thoth-mall_billing_2026-10-11.zip"' : null }, blob: async () => ({ size: 10 }) });
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
const flat = (el) => { const out = []; const walk = (n) => { if (n.nodeType === 3) out.push(n.nodeValue); else { if (/^(DIV|P|LI|BUTTON|SMALL|B|SPAN|LABEL|H2)$/.test(n.tagName || "")) out.push(" "); n.childNodes.forEach(walk); if (/^(DIV|P|LI|BUTTON)$/.test(n.tagName || "")) out.push(" "); } }; walk(el); return out.join("").replace(/\s+/g, " ").trim(); };
const txt = (d, sel) => (d.querySelector(sel) ? flat(d.querySelector(sel)) : "");
const files = (d) => [...d.querySelectorAll("#bl-modal .bl-fi b")].map((x) => x.textContent);
const dlUrl = (calls) => calls.filter((c) => c.u.includes("/download")).pop();
const q = (url) => new URLSearchParams(url.split("?")[1]);
async function pickFile(w, d, name) {
  const inp = d.getElementById("bl-pbfile");
  Object.defineProperty(inp, "files", { value: [new w.File(["x"], name)], configurable: true });
  change(w, inp); await sleep(60);
}

async function main() {
  // ============ not connected: the card says so and offers Connect, the download has no ProjectBase file
  let { window, d, calls } = await boot();
  click(window, d.getElementById("bl-dl")); await sleep(120);
  const card = d.getElementById("bl-pbcard");
  ok(!!card && /ProjectBase sheet/.test(card.textContent) && /Not connected/.test(card.textContent), "Download has a ProjectBase sheet card that says Not connected");
  ok(card.querySelector('[data-dl="pb"]').disabled && !card.querySelector('[data-dl="pb"]').checked, "its checkbox is disabled until a service is connected");
  const conn = [...card.querySelectorAll("[data-pbc]")].map((b) => b.dataset.pbc);
  ok(conn.join() === "Electrical,Fire,HVAC,Plumbing,ELV", "one Connect… button per service (a Work-Order service once): " + conn.join());
  ok(files(d).length === 2 && files(d).every((n) => !/ProjectBase/.test(n)), "no ProjectBase file in the download yet");
  ok(!/pb=/.test(dlUrl(calls) ? dlUrl(calls).u : "") && !calls.some((c) => c.u.includes("projectbase/preview")), "nothing asks the server for a ProjectBase sheet");

  // ============ Connect: step 1
  click(window, card.querySelector('[data-pbc="Electrical"]')); await sleep(30);
  ok(/Connect ProjectBase for Electrical/.test(txt(d, "#bl-modal h2")), "Connect dialog opens for the service");
  ok(/Step 1 of 2/.test(txt(d, ".bl-pb1")) && /open any Billing Request/.test(txt(d, ".bl-pb1")) && /press Export/.test(txt(d, ".bl-pb1")) && /Drop that file here/.test(txt(d, ".bl-pb1")), "step 1 tells the person what to export and where to drop it");
  ok(/never writes to ProjectBase/.test(txt(d, ".bl-pbk")), "it says MI never writes to ProjectBase");
  ok(d.getElementById("bl-pbgo").hidden && d.getElementById("bl-pbback").hidden, "no Connect / Back button before a file is read");

  // a file that is not a Billing Request: the message is shown in place and the drop zone stays
  window.__r = null;
  const S = { inspectFail: "None of Electrical’s lines were found in this file. Is this the Billing Request of Electrical?" };
  ({ window, d, calls } = await boot(S));
  click(window, d.getElementById("bl-dl")); await sleep(120);
  click(window, d.querySelector('#bl-pbcard [data-pbc="Electrical"]')); await sleep(30);
  await pickFile(window, d, "wrong.xlsx");
  ok(/None of Electrical/.test(txt(d, ".bl-pb1 .bl-wn")) && !!d.getElementById("bl-pbdrop"), "refused file: the reason is shown and the drop zone is still there");
  ok(calls.some((c) => c.u.endsWith("/projectbase/Electrical/inspect") && c.method === "POST" && c.form), "the file went to /inspect as a multipart upload");
  ok(!calls.some((c) => c.u.endsWith("/connect")), "nothing was connected");

  // ============ Connect: step 2 with the real inspect payload
  ({ window, d, calls } = await boot());
  click(window, d.getElementById("bl-dl")); await sleep(120);
  click(window, d.querySelector('#bl-pbcard [data-pbc="Electrical"]')); await sleep(30);
  await pickFile(window, d, "Billing_Request-undefined.xlsx");
  ok(/Step 2 of 2/.test(txt(d, ".bl-pb2")) && /VEIPL-GUJ\/PRO\/26-27\/002/.test(txt(d, ".bl-pbproj")), "step 2 names the project code read from the file");
  const sum = txt(d, ".bl-pbsum");
  ok(/Recognised/.test(sum) && /BOQ lines in the file 339/.test(sum), "layout recognised, 339 BOQ lines");
  ok(/75% · 15% · 5% · 5%/.test(sum), "billing rows per line are read from the file, not assumed");
  ok(/Electrical › Only point wiring 30 of 30 lines matched/.test(sum) && /Electrical › Other Electrical 301 of 301 lines matched/.test(sum), "matched / total per Work Order");
  ok(!d.querySelector(".bl-ask"), "nothing to ask when every line matched by Internal Ref No");
  ok(!d.getElementById("bl-pbgo").hidden && /Connect Electrical/.test(d.getElementById("bl-pbgo").textContent) && !d.getElementById("bl-pbback").hidden, "Back and 'Connect Electrical' appear");
  click(window, d.getElementById("bl-pbback")); await sleep(10);
  ok(!!d.getElementById("bl-pbdrop") && d.getElementById("bl-pbgo").hidden, "Back returns to the drop zone");
  await pickFile(window, d, "Billing_Request-undefined.xlsx");
  click(window, d.getElementById("bl-pbgo")); await sleep(200);
  const cc = calls.find((c) => c.u.endsWith("/projectbase/Electrical/connect"));
  ok(cc && cc.method === "POST" && JSON.stringify(cc.body) === JSON.stringify({ confirm: {}, name: "Billing_Request-undefined.xlsx" }), "Connect posts the file name and no confirmations");

  // ============ back in Download: connected, ticked, previewed
  const c2 = d.getElementById("bl-pbcard");
  ok(!!c2 && /Connected/.test(c2.textContent) && c2.querySelector('[data-dl="pb"]').checked && !c2.querySelector('[data-dl="pb"]').disabled, "after connecting, the card says Connected and is ticked");
  ok(/Electrical · VEIPL-GUJ\/PRO\/26-27\/002/.test(c2.textContent) && c2.querySelector("[data-pbx]") && c2.querySelector('.bl-pbact [data-pbc="Electrical"]'), "connected service shows its project, Replace file and Disconnect");
  ok(/Not connected: Fire, HVAC, Plumbing, ELV/.test(c2.textContent), "the rest are still offered");
  await sleep(150);
  const fl = files(d);
  ok(fl.length === 4 && fl.filter((n) => /_ProjectBase_/.test(n)).length === 2, "download = 2 invoices + 2 ProjectBase files: " + fl.join(" | "));
  ok(fl.includes("thoth-mall_ProjectBase_Electrical-Only_point_wiring_Supply_RA-01_DRAFT.xlsx") && fl.includes("thoth-mall_ProjectBase_Electrical-Other_Electrical_Installation_RA-01_DRAFT.xlsx"), "ProjectBase file names are exactly the server's");
  ok(calls.some((c) => c.u.includes("/projectbase/preview?ras=") && c.u.includes(SUP) && c.u.includes(INS)), "the report of each ticked RA is fetched");
  const rp = txt(d, ".bl-dlR");
  ok(/Lines filled 2 of 2/.test(rp) && /Lines filled 1 of 1/.test(rp), "report: lines filled per RA");
  ok(/Total after import ₹ 2,63,250\.00/.test(rp) && /Total after import ₹ 1,30,660\.88/.test(rp), "report: total after import per RA, to the paisa");
  ok(/Testing \/ Handover rows left empty/.test(rp), "report says Testing / Handover rows are left empty");
  ok(/2 old quantities in the Now column of the exported file are blanked/.test(rp), "the stale placeholder values in the real export are reported as blanked");
  ok(d.getElementById("bl-dl-go").textContent === "Download 4 files (.zip)", "button counts the ProjectBase files");

  // ============ ProjectBase only (Invoice workbook unticked)
  tick(window, d.querySelector('[data-dl="invoice"]'), false); await sleep(30);
  ok(files(d).length === 2 && files(d).every((n) => /_ProjectBase_/.test(n)), "invoice unticked: only the 2 ProjectBase files");
  ok(d.getElementById("bl-dl-go").textContent === "Download 2 files (.zip)", "button: Download 2 files (.zip)");
  click(window, d.getElementById("bl-dl-go")); await sleep(120);
  let du = dlUrl(calls).u, qq = q(du);
  ok(qq.get("pb") === "1" && qq.get("invoice") === "0" && qq.get("pbras") === `${SUP},${INS}` && !qq.get("ras"), "URL: pb=1&invoice=0&pbras=… and no invoice RAs: " + du);

  // ============ hand-off
  ok(/Finish in ProjectBase: 3 steps/.test(txt(d, "#bl-modal h2")), "after the download the hand-off dialog opens");
  ok(/Downloaded/.test(txt(d, ".bl-ho")) && /thoth-mall_billing_2026-10-11\.zip/.test(txt(d, ".bl-ho")), "step 1 is done and names the file");
  ok(/Billing Requests → Create/.test(txt(d, ".bl-ho")) && /any one line/.test(txt(d, ".bl-ho")) && /Import → choose the downloaded file/.test(txt(d, ".bl-ho")), "step 2 explains how to import (a request must exist first)");
  ok(/Import first, then press Mark as invoiced/.test(txt(d, ".bl-tip")), "the tip about importing before freezing is there");
  ok(!d.querySelector("[data-sub]") && d.querySelector(".bl-hs.off"), "step 3 is held back until the person says they imported");
  click(window, d.getElementById("bl-imp")); await sleep(20);
  ok(d.querySelectorAll("[data-sub]").length === 2, "after 'I have imported it' one Subtotal box per RA");
  ok(!d.getElementById("bl-imp"), "the import button is gone");
  // nothing typed
  click(window, d.querySelector(`[data-chk="${SUP}"]`)); await sleep(20);
  ok(!calls.some((c) => /pb-check/.test(c.u)) && /Type the Subtotal/.test(txt(d, "#bl-toast")), "empty Subtotal: asked to type it, nothing sent");
  // exact match
  d.querySelector(`[data-sub="${SUP}"]`).value = String(FX.check_ok.pb);
  click(window, d.querySelector(`[data-chk="${SUP}"]`)); await sleep(60);
  const chk = calls.find((c) => /pb-check/.test(c.u));
  ok(chk && chk.method === "POST" && chk.u.includes(`/ra/${SUP}/`) && chk.body.subtotal === FX.check_ok.pb, "Check posts the typed number to this RA");
  const ok1 = d.querySelector(`[data-res="${SUP}"]`);
  ok(ok1 && ok1.classList.contains("ok") && /Totals match/.test(ok1.textContent) && /₹ 2,63,250\.00/.test(ok1.textContent), "match: green 'Totals match' with both totals");
  ok(!!ok1.querySelector(`[data-lockra="${SUP}"]`) && /Mark as invoiced/.test(ok1.textContent), "match offers Mark as invoiced");
  // mismatch explained by one line
  d.querySelector(`[data-sub="${INS}"]`).value = String(FX.check_bad.pb);
  click(window, d.querySelector(`[data-chk="${INS}"]`)); await sleep(60);
  const bad = d.querySelector(`[data-res="${INS}"]`);
  ok(bad && bad.classList.contains("bad") && /₹ 13,162\.50 apart/.test(bad.textContent), "mismatch: '₹ 13,162.50 apart'");
  ok(/Usually one line/.test(bad.textContent) && bad.querySelectorAll(".bl-sus").length === 2 && /1\.1\.6/.test(bad.textContent) && /MI sent 50/.test(bad.textContent) && /If ProjectBase shows 47/.test(bad.textContent), "mismatch names the lines that would explain it, from the numbers (sent 50 → 47)");
  ok(!!bad.querySelector("[data-again]") && /Mark as invoiced anyway/.test(bad.textContent), "mismatch offers Download again and Mark as invoiced anyway");
  // earlier result survives re-render
  ok(!!d.querySelector(`[data-res="${SUP}"]`) && d.querySelector(`[data-sub="${SUP}"]`).value === String(FX.check_ok.pb), "the first RA's result and typed number stay when the second is checked");
  // a gap no single line explains
  d.querySelector(`[data-sub="${INS}"]`).value = String(FX.check_far.mi - 1);
  click(window, d.querySelector(`[data-chk="${INS}"]`)); await sleep(60);
  const far = d.querySelector(`[data-res="${INS}"]`);
  ok(/₹ 1\.00 apart/.test(far.textContent) && /No single line explains it/.test(far.textContent) && !far.querySelector(".bl-sus"), "no line explains it: says so instead of guessing");
  // download again
  const before = calls.length;
  click(window, d.querySelector(`[data-again="${INS}"]`)); await sleep(80);
  const again = calls.slice(before).find((c) => c.u.includes("/download"));
  ok(again && q(again.u).get("pbras") === INS && q(again.u).get("invoice") === "0" && q(again.u).get("pb") === "1", "Download again fetches only that RA's ProjectBase sheet");
  // mark as invoiced
  click(window, d.querySelector(`[data-lockra="${SUP}"]`)); await sleep(80);
  ok(calls.some((c) => c.method === "POST" && c.u.endsWith(`/ra/${SUP}/lock`)), "Mark as invoiced calls the real lock endpoint");
  ok(/Already marked as invoiced/.test(txt(d, `[data-res="${SUP}"]`)) && !d.querySelector(`[data-lockra="${SUP}"]`), "and the card then says so");

  // ============ with invoices ticked too: one request carries both
  ({ window, d, calls } = await boot({ ov: clone(FX.ov1) }));
  click(window, d.getElementById("bl-dl")); await sleep(150);
  ok(d.querySelector('[data-dl="pb"]').checked && files(d).length === 4, "already-connected project: ProjectBase is ticked by default");
  click(window, d.getElementById("bl-dl-go")); await sleep(120);
  qq = q(dlUrl(calls).u);
  ok(qq.get("ras") === `${SUP},${INS}` && qq.get("pb") === "1" && qq.get("pbras") === `${SUP},${INS}` && qq.get("invoice") !== "0" && qq.get("mode") === "files", "one request: invoice RAs + ProjectBase RAs: " + dlUrl(calls).u);
  ok(/Finish in ProjectBase/.test(txt(d, "#bl-modal h2")), "hand-off follows");
  click(window, d.querySelector("#bl-modal .mact .btn")); await sleep(50);
  ok(!d.getElementById("bl-modal"), "Done closes it");

  // ============ ProjectBase unticked: ordinary download, no hand-off
  ({ window, d, calls } = await boot({ ov: clone(FX.ov1) }));
  click(window, d.getElementById("bl-dl")); await sleep(150);
  tick(window, d.querySelector('[data-dl="pb"]'), false); await sleep(20);
  ok(files(d).length === 2 && d.getElementById("bl-dl-go").textContent === "Download 2 files (.zip)", "unticking ProjectBase leaves the 2 invoices");
  click(window, d.getElementById("bl-dl-go")); await sleep(120);
  ok(!/pb=/.test(dlUrl(calls).u) && !/Finish in ProjectBase/.test(txt(d, "#bl-toast") + txt(d, "#bl-modal")), "no pb in the URL and no hand-off");

  // ============ an RA of a service that is not connected is named, never given a sheet
  const ov = clone(FX.ov1); ov.ras.find((r) => r.id === INS).service = "Fire";
  ({ window, d, calls } = await boot({ ov }));
  click(window, d.getElementById("bl-dl")); await sleep(150);
  ok(files(d).filter((n) => /_ProjectBase_/.test(n)).length === 1, "only the connected service's RA gets a ProjectBase file");
  const w = [...d.querySelectorAll("#bl-modal .bl-wn.pbn")];
  ok(w.length === 1 && /Fire has no ProjectBase sheet connected/.test(w[0].textContent) && w[0].querySelector('[data-pbc="Fire"]'), "a warning names Fire and offers Connect…");
  click(window, d.getElementById("bl-dl-go")); await sleep(120);
  ok(q(dlUrl(calls).u).get("pbras") === SUP && q(dlUrl(calls).u).get("ras") === `${SUP},${INS}`, "ProjectBase only for the connected RA; the invoices still cover both");

  // ============ Disconnect
  ({ window, d, calls } = await boot({ ov: clone(FX.ov1) }));
  click(window, d.getElementById("bl-dl")); await sleep(150);
  click(window, d.querySelector("[data-pbx]")); await sleep(120);
  ok(calls.some((c) => c.method === "DELETE" && c.u.endsWith("/projectbase/Electrical")), "Disconnect calls DELETE");
  ok(/Not connected/.test(txt(d, "#bl-pbcard")) && d.querySelector('#bl-pbcard [data-dl="pb"]').disabled, "card goes back to Not connected");

  // ============ near-match question: asked, never guessed
  const ask = clone(FX.inspect); ask.ask = FX.ask.map((a) => ({ ...a, unit: "Electrical › Other Electrical" })); ask.matched -= 1;
  ({ window, d, calls } = await boot({ inspect: ask }));
  click(window, d.getElementById("bl-dl")); await sleep(120);
  click(window, d.querySelector('#bl-pbcard [data-pbc="Electrical"]')); await sleep(30);
  await pickFile(window, d, "BR.xlsx");
  ok(/1 line needs your OK/.test(txt(d, ".bl-pb2")) && /MI will not guess/.test(txt(d, ".bl-ask")), "step 2 asks about a line that matched only by Client Ref No");
  ok(/MI wording of this line/.test(txt(d, ".bl-askc")) && /OUTDOOR TRANSFORMER/.test(txt(d, ".bl-askc")) && /Ref 3/.test(txt(d, ".bl-askc")), "shows both sides: MI's line and the file's line with its Internal Ref");
  ok(d.querySelector("[data-no]").classList.contains("on") || d.querySelector(".bl-ask .bl-seg .on").textContent === "Skip this line", "default answer is Skip");
  click(window, d.getElementById("bl-pbgo")); await sleep(120);
  ok(JSON.stringify(calls.find((c) => c.u.endsWith("/connect")).body.confirm) === "{}", "left at Skip: nothing is confirmed");
  ({ window, d, calls } = await boot({ inspect: ask }));
  click(window, d.getElementById("bl-dl")); await sleep(120);
  click(window, d.querySelector('#bl-pbcard [data-pbc="Electrical"]')); await sleep(30);
  await pickFile(window, d, "BR.xlsx");
  click(window, d.querySelector("[data-yes]")); await sleep(10);
  ok(d.querySelector("[data-yes]").classList.contains("on"), "Yes, same line is selectable");
  click(window, d.getElementById("bl-pbgo")); await sleep(120);
  ok(JSON.stringify(calls.find((c) => c.u.endsWith("/connect")).body.confirm) === JSON.stringify({ "1.1": "3" }), "Yes sends {item_code: Internal Ref No} to confirm");

  console.log(`${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
}
const tick = (w, el, on) => { el.checked = on; change(w, el); };
main().catch((e) => { console.error("CRASH", e); process.exit(1); });
