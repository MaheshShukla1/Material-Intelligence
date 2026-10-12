// jsdom test for the challan screens of the Billing tab. Every payload comes from the REAL backend
// run on the real Thoth BOQ + the user's real HVAC invoice workbook (billing_challan_fixture.json,
// built by make_challan_fixture.py). Pixel layout is covered by test_billing_challans_chromium.py.
import { JSDOM } from "jsdom";
import css from "css";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
let pass = 0, fail = 0;
function ok(cond, msg) { if (cond) pass++; else { fail++; console.error("FAIL:", msg); } }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const FX = JSON.parse(fs.readFileSync(path.join(__dirname, "billing_challan_fixture.json"), "utf8"));
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
ok(decl(find(".bl-ch", "(max-width:760px)"), "grid-template-columns") === "1fr auto", "phone: challan row is a stacked card");
ok(decl(find(".bl-crow .btn", "(max-width:760px)"), "min-height") === "44px" && decl(find(".bl-crow .ctl", "(max-width:760px)"), "min-height") === "44px", "phone: challan line controls are 44px touch targets");
ok(decl(find(".bl-chform", "(max-width:760px)"), "grid-template-columns") === "1fr", "phone: challan form is one column");
ok(decl(find(".bl-sch", "(max-width:760px)"), "display") === "none", "phone: stock-check header row is replaced by cards");
ok(!bl.some((r) => r.type === "rule" && r.selectors.some((s) => /^(body|\.top|main|\.btn|\.ctl|\.sheet|\.card)\b/.test(s.trim()) && !/data-view="billing"/.test(s))), "billing.css still only touches bl- classes / its own view");

// ---------------------------------------------------------------- harness
async function boot(over) {
  const html = `<!doctype html><html><body>
    <header class="top" id="top"><div class="brand"><p id="ctx"></p></div>
      <div class="topact"><button id="home"></button><button id="pick"></button><button id="sync"></button><button id="syncgo"></button></div></header>
    <main><section id="drop" hidden></section><section id="busy" hidden></section>
      <section id="report"><div class="bar"><div class="tabs" id="svc"></div></div></section></main></body></html>`;
  const dom = new JSDOM(html, { url: "https://example.test/", runScripts: "dangerously", pretendToBeVisual: true });
  const { window } = dom; const calls = [];
  const state = { ov: clone(FX.ov_open), ch: clone(FX.ch_open), setup: clone(FX.setup0), bill: clone(FX.bill), preview: clone(FX.preview), ...over };
  window.fetch = (url, opts) => {
    const u = String(url), method = (opts && opts.method) || "GET";
    let body = null, form = null;
    if (opts && opts.body) { if (typeof opts.body === "string") body = JSON.parse(opts.body); else { form = {}; for (const [k, v] of opts.body.entries()) form[k] = v; } }
    calls.push({ u, method, body, form });
    let res = {}, status = 200;
    const P = "/api/billing/thoth-mall";
    if (u === "/api/projects") res = [{ slug: "thoth-mall", project: "Thoth Mall", runs: 1, latest_run: "r1" }];
    else if (u === P) res = state.ov;
    else if (u === P + "/setup" && method === "GET") res = state.setup;
    else if (u === P + "/setup") res = { ok: true };
    else if (u === P + "/import-sheet/HVAC") {
      const ch = form.lock_upto !== "0" ? clone(FX.preview_lock) : clone(state.preview);
      if (form.commit === "true") ch.committed = true;
      res = { challans: ch, challan_note: null, typed: state.typed || null, committed: form.commit === "true", applied: state.typed ? state.typed.changes.length : 0 };
    }
    else if (u === P + "/challans/HVAC" && method === "GET") res = state.ch;
    else if (u === P + "/challans/HVAC" && method === "POST") {
      if (!body.lines.length) { status = 400; res = { detail: "A challan needs at least one line." }; } else res = { ok: true, id: "cNEW" };
    }
    else if (u.startsWith(P + "/challans/HVAC/")) res = { ok: true };
    else if (u === P + "/lines/HVAC") res = { lines: FX.hvac_lines };
    else if (u === `${P}/ra/${FX.raid}`) res = state.bill;
    return Promise.resolve({ ok: status < 400, status, json: async () => res, text: async () => JSON.stringify(res) });
  };
  window.eval(fs.readFileSync(path.join(__dirname, "siteprogress.js"), "utf8"));
  window.eval(fs.readFileSync(path.join(__dirname, "billing.js"), "utf8"));
  await sleep(0);
  const d = window.document;
  [...d.querySelectorAll(".spnav button")].find((b) => b.dataset.v === "billing").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(80);
  return { window, d, calls, state };
}
const click = (w, el) => el.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
const change = (w, el) => el.dispatchEvent(new w.Event("change", { bubbles: true }));
const input = (w, el) => el.dispatchEvent(new w.Event("input", { bubbles: true }));
const txt = (d, sel) => (d.querySelector(sel) || { textContent: "" }).textContent;

async function main() {
  // ---- home: only challan-sourced services get the Challans link
  let { window, d, calls } = await boot();
  const links = [...d.querySelectorAll("#billing [data-ch]")];
  ok(links.length === 1 && /^Challans · 12$/.test(links[0].textContent.trim()) && links[0].dataset.ch === "HVAC", "home: HVAC card shows 'Challans · 12'; stock-sourced Fire has none");
  click(window, links[0]); await sleep(60);
  ok(/Challans · HVAC/.test(txt(d, "#billing h2")), "the Challans link opens the HVAC challan screen");

  // ---- challan screen (real payload: 12 challans from the user's workbook, all RA-01 open)
  const rows = [...d.querySelectorAll(".bl-ch")];
  ok(rows.length === 12, "one row per challan (12)");
  ok(/RA-01 · open/.test(d.getElementById("billing").textContent), "the open RA group is titled RA-01 · open");
  ok(/Challan 614/.test(rows[0].textContent) && /2026-07-04/.test(rows[0].textContent), "challan number and date shown");
  ok(!rows[0].classList.contains("ex") && rows.every((r) => !r.classList.contains("ex")), "challan lines start collapsed");
  click(window, rows[0]); await sleep(5);
  ok(rows[0].classList.contains("ex") && rows[0].querySelectorAll(".bl-cl").length === FX.ch_open.challans[0].lines.length, "expanding shows every line of the challan");
  ok(!!rows[0].querySelector("[data-edit]") && !!rows[0].querySelector("[data-del]"), "an open challan can be edited and deleted");
  const kp = [...d.querySelectorAll(".bl-kpi b")].map((x) => x.textContent);
  ok(kp.join() === "12,12,0", "KPIs: 12 entered, 12 in the open RA, 0 on hold");
  ok(/challans total .* BOQ qty is 1/.test(d.getElementById("billing").textContent), "lines above the BOQ qty are called out");

  // ---- re-tag: the select posts the challan back with the new RA (hold -> null)
  const sel = rows[0].querySelector(".bl-retag");
  ok([...sel.options].map((o) => o.value).join() === "1,2,3,hold", "RA choices: open, next two, on hold");
  sel.value = "hold"; change(window, sel); await sleep(60);
  const rt = calls.find((c) => /\/challans\/HVAC\/c\d+$/.test(c.u) && c.method === "POST");
  const c0 = FX.ch_open.challans[0];
  ok(rt && rt.body.ra === null && rt.body.no === c0.no && rt.body.date === c0.date && rt.body.lines.length === c0.lines.length &&
     rt.body.lines.every((l, i) => l.item_code === c0.lines[i].item_code && l.qty === c0.lines[i].qty), "re-tag posts the SAME challan (no, date, every line and qty) with ra=null");

  // ---- add a challan from a real BOQ line
  ({ window, d, calls } = await boot());
  click(window, d.querySelector("#billing [data-ch]")); await sleep(60);
  click(window, d.getElementById("bl-chadd")); await sleep(40);
  ok(d.getElementById("bl-cdate").value.length === 10, "date defaults to today");
  ok(d.getElementById("bl-cra").value === String(FX.ch_open.open_no), "RA defaults to the open RA");
  d.getElementById("bl-cno").value = "700";
  const target = FX.hvac_lines.find((l) => l.desc.split(/\s+/).length > 4);
  d.getElementById("bl-cfind").value = target.desc.split(/\s+/).slice(0, 3).join(" "); input(window, d.getElementById("bl-cfind")); await sleep(5);
  const hit = [...d.querySelectorAll("#bl-cfound .bl-hit")].find((h) => h.dataset.c === target.item_code);
  ok(!!hit && /already on challans/.test(hit.textContent), "search finds the line and shows what challans already carry");
  click(window, hit); await sleep(5);
  ok(d.querySelectorAll("#bl-clist .bl-crow").length === 1 && d.activeElement === d.querySelector("#bl-clist [data-q]"), "the clicked line is added and its qty box is focused");
  const q = d.querySelector("#bl-clist [data-q]"); q.value = "3"; input(window, q);
  ok(![...d.querySelectorAll("#bl-cfound .bl-hit")].some((h) => h.dataset.c === target.item_code), "an added line is no longer offered");
  click(window, [...d.querySelectorAll("#bl-modal .mact .btn")].find((b) => /Save challan/.test(b.textContent))); await sleep(60);
  const add = calls.find((c) => c.u === "/api/billing/thoth-mall/challans/HVAC" && c.method === "POST");
  ok(add && add.body.no === "700" && add.body.ra === FX.ch_open.open_no && add.body.lines.length === 1 && add.body.lines[0].item_code === target.item_code && add.body.lines[0].qty === 3, "save posts number, date, RA and {item_code, qty}");
  ok(!d.getElementById("bl-modal"), "modal closes after a successful save");

  // ---- server rejection is shown
  click(window, d.getElementById("bl-chadd")); await sleep(40);
  d.getElementById("bl-cno").value = "701";
  click(window, [...d.querySelectorAll("#bl-modal .mact .btn")].find((b) => /Save challan/.test(b.textContent))); await sleep(60);
  ok(/at least one line/.test(txt(d, "#bl-toast")) && !!d.getElementById("bl-modal"), "a rejected save keeps the modal open and shows the reason");
  click(window, d.getElementById("bl-mx"));

  // ---- locked / open / held groups (RA-01 locked, 640 for RA-02, 641 on hold)
  ({ window, d, calls } = await boot({ ch: clone(FX.ch_locked), ov: clone(FX.ov_locked) }));
  click(window, d.querySelector("#billing [data-ch]")); await sleep(60);
  const t2 = d.getElementById("billing").textContent;
  ok(/RA-02 · open/.test(t2) && /On hold/.test(t2) && /RA-01 · invoiced/.test(t2), "groups: RA-02 open, On hold, RA-01 locked");
  const lockedRows = [...d.querySelectorAll(".bl-ch.st-locked")];
  ok(lockedRows.length === 12 && lockedRows.every((r) => !r.querySelector(".bl-retag") && /RA-01 invoiced/.test(r.textContent)), "the 12 challans of the locked RA show a 'locked' chip and cannot be re-tagged");
  click(window, lockedRows[0]); await sleep(5);
  ok(!lockedRows[0].querySelector("[data-edit]") && !lockedRows[0].querySelector("[data-del]"), "a frozen challan has no Edit / Delete");
  const openRow = [...d.querySelectorAll(".bl-ch.st-open")].find((r) => /Challan 640/.test(r.textContent));
  ok(/Challan 640/.test(openRow.textContent), "challan 640 sits in the open RA-02 group");
  const heldRow = [...d.querySelectorAll(".bl-ch")].find((r) => /Challan 641/.test(r.textContent));
  ok(heldRow.classList.contains("st-held") && heldRow.querySelector(".bl-retag").value === "hold", "challan 641 shows as on hold");
  click(window, openRow); await sleep(5);
  click(window, openRow.querySelector("[data-del]")); await sleep(10);
  ok(/Delete challan 640\?/.test(d.getElementById("bl-modal").textContent), "delete asks first");
  click(window, [...d.querySelectorAll("#bl-modal .mact .btn")].find((b) => b.textContent === "Delete")); await sleep(60);
  ok(calls.some((c) => c.method === "DELETE" && /\/challans\/HVAC\/c\d+$/.test(c.u)), "delete sends DELETE");

  // ---- import: preview, opening-RA option, commit
  ({ window, d, calls } = await boot({ ch: clone(FX.ch_empty) }));
  click(window, d.querySelector("#billing [data-ch]")); await sleep(60);
  ok(/No challan yet/.test(d.getElementById("billing").textContent), "empty state explains how to start");
  click(window, d.getElementById("bl-chimp")); await sleep(20);
  const f = d.getElementById("bl-file");
  Object.defineProperty(f, "files", { value: [new window.File(["x"], "HVAC_MAIN_INVOICE_SHEET.xlsx")], configurable: true });
  change(window, f); await sleep(60);
  const pv = calls.find((c) => /import-sheet\/HVAC$/.test(c.u));
  ok(pv && pv.form.commit === "false" && pv.form.lock_upto === "0" && pv.form.file && pv.form.file.name === "HVAC_MAIN_INVOICE_SHEET.xlsx", "choosing a file sends a PREVIEW (commit=false) and saves nothing");
  const m = d.getElementById("bl-modal").textContent;
  ok(/Challanstoimport12/.test(m.replace(/\s+/g, "")) && /Rowsnotpaired0/.test(m.replace(/\s+/g, "")), "preview shows 12 challans to import, 0 rows not paired");
  ok(/RA-01 · 45 lines/.test(m) && /1,88,21,413\.8[78]/.test(m), "preview shows what RA-01 comes to, in Indian grouping (₹ 1,88,21,413.88)");
  ok(/more challan qty than the BOQ qty/.test(m), "lines above the BOQ qty are called out before importing");
  const lock = d.getElementById("bl-lock");
  ok(!!lock && [...lock.options].map((o) => o.value).join() === "0,1", "an 'already submitted' choice is offered for RA-01");
  lock.value = "1"; change(window, lock); await sleep(60);
  ok(calls.filter((c) => /import-sheet\/HVAC$/.test(c.u)).pop().form.lock_upto === "1" && /RA-01: ₹/.test(d.getElementById("bl-modal").textContent), "choosing it re-checks with lock_upto=1 and shows the opening RA total");
  click(window, [...d.querySelectorAll("#bl-modal .mact .btn")].find((b) => b.textContent === "Import")); await sleep(80);
  const cm = calls.filter((c) => /import-sheet\/HVAC$/.test(c.u)).pop();
  ok(cm.form.commit === "true" && cm.form.lock_upto === "1", "Import commits with lock_upto=1");

  // ---- RA bill screen: invoice workbook link, challans source, jump to challans
  ({ window, d, calls } = await boot());
  click(window, d.querySelector(".bl-rar")); await sleep(80);
  const inv = d.getElementById("bl-dl");
  ok(inv && inv.tagName === "BUTTON" && /Download/.test(inv.textContent) && !d.getElementById("bl-invoice"), "RA screen has one Download button (invoice workbook + All services Excel live in its dialog; see test_billing_download_ui.mjs)");
  ok(/Challans/.test(d.querySelector(".bl-lr .bl-src").textContent), "supply quantity is labelled as coming from Challans");
  const exp = d.querySelector(".bl-lr"); click(window, exp); await sleep(5);
  ok(/Challan \d+/.test(exp.querySelector(".x").textContent), "expanding a line names the challans behind its quantity");
  click(window, d.getElementById("bl-tochallans")); await sleep(60);
  ok(/Challans · HVAC/.test(txt(d, "#billing h2")), "'Challans' on the RA screen jumps to the service's challans");

  // ---- import from Billing home and from the draft RA screen (next to Download), not only inside Challans
  ({ window, d, calls } = await boot());
  ok(!!d.getElementById("bl-imph") && d.getElementById("bl-imph").previousElementSibling === d.getElementById("bl-dl"), "Billing home: 'Upload filled sheet' sits right next to Download");
  click(window, d.getElementById("bl-imph")); await sleep(80);
  ok(!!d.getElementById("bl-file") && /Upload filled sheet/.test(d.getElementById("bl-modal").textContent), "one challan-billed service -> straight to the file picker, no extra question");
  ok(calls.some((c) => c.u === "/api/billing/thoth-mall/challans/HVAC" && c.method === "GET"), "the service's own 'already submitted?' info is loaded (not borrowed from another screen)");
  const fh = d.getElementById("bl-file");
  Object.defineProperty(fh, "files", { value: [new window.File(["x"], "HVAC_MAIN_INVOICE_SHEET.xlsx")], configurable: true });
  change(window, fh); await sleep(80);
  ok(!!d.getElementById("bl-lock") && [...d.getElementById("bl-lock").options].map((o) => o.value).join() === "0,1", "opened from Billing home it still offers the 'RA-01 already submitted' choice");
  const nRa0 = calls.length;
  click(window, [...d.querySelectorAll("#bl-modal .mact .btn")].find((b) => b.textContent === "Import")); await sleep(120);
  ok(calls.slice(nRa0).some((c) => /import-sheet\/HVAC$/.test(c.u) && c.form.commit === "true"), "Import from Billing home commits");
  ok(!!d.getElementById("bl-new") && !/Challans · HVAC/.test(txt(d, "#billing h2")), "after importing you land back on Billing home, not inside Challans");
  ({ window, d, calls } = await boot({ ov: Object.assign(clone(FX.ov_open), { services: clone(FX.ov_open.services).map((x) => ({ ...x, supply_source: "stock" })) }) }));
  ok(!!d.getElementById("bl-imph") && /Upload filled sheet/.test(d.getElementById("bl-imph").textContent), "stock-register services with open drafts still get the button: typed quantities go into a draft");
  { const ov0 = clone(FX.ov_open); ov0.services.forEach((x) => { x.supply_source = "stock"; }); ov0.ras = ov0.ras.filter((r) => r.status !== "draft");
    ({ window, d, calls } = await boot({ ov: ov0 }));
    ok(!d.getElementById("bl-imph"), "no challan-billed service and no open draft -> nothing to upload into, so no button"); }
  { const ov2 = clone(FX.ov_open); ov2.services.find((x) => x.service === "Fire").supply_source = "challan";
    ({ window, d, calls } = await boot({ ov: ov2 }));
    click(window, d.getElementById("bl-imph")); await sleep(40);
    const pk = [...d.querySelectorAll("#bl-modal [data-u]")];
    ok(pk.map((b) => b.textContent).sort().join() === "Fire,HVAC" && !d.getElementById("bl-file"), "two challan-billed services -> ask which one first");
    click(window, pk.find((b) => b.textContent === "Fire")); await sleep(40);
    ok(!!d.getElementById("bl-file"), "choosing the service opens the file picker"); }
  ({ window, d, calls } = await boot());
  click(window, d.querySelector(".bl-rar")); await sleep(80);
  const ir = d.getElementById("bl-impra");
  ok(ir && ir.previousElementSibling === d.getElementById("bl-dl"), "draft RA screen: 'Upload filled sheet' sits right next to Download");
  click(window, ir); await sleep(80);
  ok(!!d.getElementById("bl-file"), "RA screen button opens the file picker");
  const fr = d.getElementById("bl-file");
  Object.defineProperty(fr, "files", { value: [new window.File(["x"], "HVAC_MAIN_INVOICE_SHEET.xlsx")], configurable: true });
  change(window, fr); await sleep(80);
  click(window, [...d.querySelectorAll("#bl-modal .mact .btn")].find((b) => b.textContent === "Import")); await sleep(120);
  ok(!!d.getElementById("bl-tochallans") && !/Challans · HVAC/.test(txt(d, "#billing h2")), "after importing you are back on the same draft RA, bill reloaded");
  { const b2 = clone(FX.bill); b2.ra.status = "locked";
    ({ window, d, calls } = await boot({ bill: b2 }));
    click(window, d.querySelector(".bl-rar")); await sleep(80);
    ok(!d.getElementById("bl-impra"), "an invoiced RA has no import button (it is frozen)"); }

  // ---- typed quantities in the same upload (THIS BILL QTY column of the downloaded RA workbook)
  { const typed = { sheet: "Annexure B-BOQ", problems: ["GI pipe 100mm: BOQ qty is 100 and 0 is already billed, so 130 cannot be billed; skipped."],
      unpaired: [{ ra: "RA-01 Supply", row: 12, desc: "Some other pipe", qty: 33 }],
      changes: [{ ra: FX.raid, ra_label: "HVAC · Supply RA 1", stage: "supply", stage_name: "Supply", item_code: "3", desc: "GI pipe 100mm", unit: "Rmt", now: 20, sheet: 35 },
                { ra: FX.raid, ra_label: "HVAC · Supply RA 1", together: true, stage: "install", stage_name: "Installation", item_code: "2", desc: "Butterfly valve 150mm", unit: "Nos", now: 2, sheet: 3 }] };
    ({ window, d, calls } = await boot({ typed }));
    click(window, d.querySelector(".bl-rar")); await sleep(80);
    click(window, d.getElementById("bl-impra")); await sleep(60);
    ok(/Upload filled sheet/.test(d.querySelector("#bl-modal h3, #bl-modal .mhead, #bl-modal").textContent) && !/Import challans/.test(d.getElementById("bl-modal").textContent), "RA screen: the dialog is called Upload filled sheet, no 'Import challans' left");
    const fx = d.getElementById("bl-file");
    Object.defineProperty(fx, "files", { value: [new window.File(["x"], "RA1_filled.xlsx")], configurable: true });
    change(window, fx); await sleep(80);
    const pv2 = calls.filter((c) => /import-sheet\/HVAC$/.test(c.u)).pop();
    ok(pv2.form.commit === "false" && pv2.form.ra_id === FX.raid, "opened from a draft RA, the upload names that RA (ra_id) and is a PREVIEW");
    const tm = d.getElementById("bl-modal").textContent.replace(/\s+/g, " ");
    ok(/Typed quantities to import\s*2/.test(tm) && /Not imported\s*2/.test(tm), "preview counts 2 typed quantities and 2 not imported");
    ok(/HVAC · Supply RA 1 · GI pipe 100mm · 20 → 35 Rmt/.test(tm) && /HVAC · Supply RA 1 · Installation · Butterfly valve 150mm · 2 → 3 Nos/.test(tm), "each typed quantity is listed as MI now → your sheet; the stage is named only inside a Supply + Installation RA");
    ok(/BOQ qty is 100/.test(tm) && /Some other pipe/.test(tm), "refused and unpaired lines are named, not hidden");
    ok(/Challans to import/.test(tm), "the challans of the same file are still read in the same preview");
    click(window, [...d.querySelectorAll("#bl-modal .mact .btn")].find((b) => b.textContent === "Import")); await sleep(100);
    ok(calls.filter((c) => /import-sheet\/HVAC$/.test(c.u)).pop().form.commit === "true", "Import commits once");
    // only typed quantities, no challan sheet at all (an Installation RA download): Import must be enabled
    const typedOnly = { challans: null, challan_note: null, typed, committed: false, applied: 0 };
    ({ window, d, calls } = await boot({ typed }));
    const f0 = window.fetch;
    window.fetch = (u, o) => (/import-sheet\/HVAC$/.test(String(u)) && !(o.body.get("commit") === "true") ? Promise.resolve({ ok: true, status: 200, json: async () => typedOnly, text: async () => JSON.stringify(typedOnly) }) : f0(u, o));
    click(window, d.querySelector(".bl-rar")); await sleep(80);
    click(window, d.getElementById("bl-impra")); await sleep(60);
    const f2 = d.getElementById("bl-file");
    Object.defineProperty(f2, "files", { value: [new window.File(["x"], "RA1_install.xlsx")], configurable: true });
    change(window, f2); await sleep(80);
    const t2 = d.getElementById("bl-modal").textContent.replace(/\s+/g, " ");
    ok(/Typed quantities to import\s*2/.test(t2) && !/Challans to import/.test(t2), "a file with no challan sheet shows only the typed quantities");
    click(window, [...d.querySelectorAll("#bl-modal .mact .btn")].find((b) => b.textContent === "Import")); await sleep(100);
    ok(calls.filter((c) => /import-sheet\/HVAC$/.test(c.u) && c.form && c.form.commit === "true").length === 1, "typed-only file can be imported (button is not blocked by 'no new challans')"); }

  // ---- setup: supply source
  ({ window, d, calls } = await boot());
  click(window, d.getElementById("bl-setup")); await sleep(60);
  const srows = [...d.querySelectorAll(".bl-sr")];
  const hv = srows.find((r) => /HVAC/.test(r.textContent)), fire = srows.find((r) => /^Fire/.test(r.textContent.trim()));
  ok(hv.querySelector('[data-f="supply_source"]').value === "challan", "HVAC: supply quantity from Delivery challans");
  const unset = srows.find((r) => /ELV/.test(r.textContent));
  ok(unset.querySelector('[data-f="supply_source"]').value === "challan", "a service with no setup yet defaults to Delivery challans");
  hv.querySelector('[data-f="supply_source"]').value = "stock";
  click(window, hv.querySelector("[data-save]")); await sleep(60);
  const sv = calls.find((c) => c.u === "/api/billing/thoth-mall/setup" && c.method === "POST");
  ok(sv && sv.body.service === "HVAC" && sv.body.supply_source === "stock" && sv.body.mode === "separate", "saving posts supply_source");

  console.log(`${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
}
main();
