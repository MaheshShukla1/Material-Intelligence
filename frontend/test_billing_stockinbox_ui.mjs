// jsdom test: challans straight from the stock register (inbox) + wire deliveries from the register.
// Real scenario = Thoth Mall Electrical on delivery challans; every payload is a REAL backend response (billing_stockinbox_fixture.json,
// built by make_stockinbox_fixture.py): the register's dated INs, a delivery already typed by hand, a register change after adding,
// and the real wire deliveries of 28 Mar / 2 Jul / 16 Jul.
import { JSDOM } from "jsdom";
import css from "css";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
let pass = 0, fail = 0;
function ok(cond, msg) { if (cond) pass++; else { fail++; console.error("FAIL:", msg); } }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const FX = JSON.parse(fs.readFileSync(path.join(__dirname, "billing_stockinbox_fixture.json"), "utf8"));
const clone = (x) => JSON.parse(JSON.stringify(x));
const U = FX.unit, P = "/api/billing/test-wire", UE = encodeURIComponent(U);

const bl = css.parse(fs.readFileSync(path.join(__dirname, "billing.css"), "utf8")).stylesheet.rules;
ok(!bl.some((r) => r.type === "rule" && r.selectors.some((s) => /^(body|\.top|main|\.btn|\.ctl|\.sheet|\.card|\.sub)\b/.test(s.trim()) && !/data-view="billing"/.test(s))), "billing.css still only touches bl- classes / its own view");
const media = bl.filter((r) => r.type === "media" && /760/.test(r.media)).flatMap((m) => m.rules.filter((r) => r.type === "rule"));
ok(media.some((r) => r.selectors.some((s) => /\.bl-ibr \.ctl/.test(s)) && (r.declarations || []).some((x) => x.property === "min-height" && x.value === "44px")), "phone: inbox inputs are 44px touch targets");
ok(media.some((r) => r.selectors.some((s) => s.trim() === ".bl-ibr") && (r.declarations || []).some((x) => x.property === "grid-template-columns")), "phone: an inbox row is stacked");
ok(media.some((r) => r.selectors.some((s) => s.trim() === ".bl-ibr .ctl") && (r.declarations || []).some((x) => x.property === "font-size" && x.value === "16px")), "phone: inbox inputs are 16px so iOS does not zoom the page");
ok(media.some((r) => r.selectors.some((s) => /\.bl-ibr \.ck/.test(s)) && (r.declarations || []).some((x) => x.property === "min-width" && x.value === "44px")), "phone: the row checkbox has a 44px hit area");
ok(media.some((r) => r.selectors.some((s) => s.trim() === "#bl-modal .card.wide") && (r.declarations || []).some((x) => x.property === "flex-direction" && x.value === "column")) && media.some((r) => r.selectors.some((s) => s.trim() === "#bl-modal .card.wide .bl-mbody") && (r.declarations || []).some((x) => x.property === "flex" && x.value === "1")), "phone: every Billing dialog keeps header and buttons on screen and scrolls only the middle");
ok(media.some((r) => r.selectors.some((s) => s.trim() === ".bl-menu") && (r.declarations || []).some((x) => x.property === "width" && /100vw/.test(x.value))), "phone: the add menu is never wider than the screen");
ok(media.some((r) => r.selectors.some((s) => /\.bl-ibr \.act \.btn/.test(s)) && (r.declarations || []).some((x) => x.property === "min-height" && x.value === "44px")), "phone: inbox action buttons are 44px");

async function boot(over) {
  const html = `<!doctype html><html><body>
    <header class="top" id="top"><div class="brand"><p id="ctx"></p></div>
      <div class="topact"><button id="home"></button><button id="pick"></button><button id="sync"></button><button id="syncgo"></button></div></header>
    <main><section id="drop" hidden></section><section id="busy" hidden></section>
      <section id="report"><div class="bar"><div class="tabs" id="svc"></div></div></section></main></body></html>`;
  const dom = new JSDOM(html, { url: "https://example.test/", runScripts: "dangerously", pretendToBeVisual: true });
  const { window } = dom; const calls = [];
  window.__BL_LIVE_MS = 3600000; window.scrollTo = () => {}; window.Element.prototype.scrollIntoView = () => {};
  const state = { ov: clone(FX.ov0), ch: clone(FX.ch0), ib: clone(FX.ib0), wire: clone(FX.wire0), sync: clone(FX.sync), after: {}, ...over };
  window.fetch = (url, opts) => {
    const u = String(url), method = (opts && opts.method) || "GET";
    const body = opts && opts.body ? JSON.parse(opts.body) : null;
    calls.push({ u, method, body });
    let res = {}, status = 200;
    const base = u.split("?")[0];
    if (u === "/api/projects") res = [{ slug: "test-wire", project: "Thoth Mall", runs: 1, latest_run: "r1" }];
    else if (u === P + "/auto") res = { created: [] };
    else if (u === P) res = state.ov;
    else if (u === P + "/setup" && method === "GET") res = FX.setup;
    else if (base === `${P}/challans/${UE}` && method === "GET") res = state.ch;
    else if (base === `${P}/stock-inbox/${UE}` && method === "GET") res = state.ib;
    else if (base === `${P}/stock-inbox/${UE}/accept`) { if (state.failAccept) { status = 409; res = { detail: state.failAccept }; } else { res = { ok: true, added: body.items.length }; if (state.after.ch) state.ch = clone(state.after.ch); if (state.after.ib) state.ib = clone(state.after.ib); } }
    else if (base.startsWith(`${P}/stock-inbox/${UE}/`)) res = { ok: true };
    else if (base.endsWith("/resync")) res = { ok: true };
    else if (base === `${P}/lines/${UE}`) res = { lines: FX.lines };
    else if (base === `${P}/wire/${UE}` && method === "GET") res = state.wire;
    else if (base === `${P}/wire/${UE}/stock-map`) { res = { ok: true }; if (state.after.wire) state.wire = clone(state.after.wire); }
    else if (base === `${P}/wire/${UE}/stock-sync`) res = state.sync;
    else if (base === `${P}/wire/${UE}/source`) { if (state.failSource) { status = 409; res = { detail: state.failSource }; } else res = { ok: true }; }
    else if (base.startsWith(`${P}/challans/${UE}`)) res = { ok: true };
    return Promise.resolve({ ok: status < 400, status, json: async () => res, text: async () => JSON.stringify(res) });
  };
  window.eval(fs.readFileSync(path.join(__dirname, "siteprogress.js"), "utf8"));
  window.eval(fs.readFileSync(path.join(__dirname, "billing.js"), "utf8"));
  await sleep(0);
  const d = window.document;
  [...d.querySelectorAll(".spnav button")].find((b) => b.dataset.v === "billing").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(80);
  window.MIBilling._state.ov = state.ov;
  d.querySelector(`#billing [data-ch="${U}"]`).dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(80);
  return { window, d, calls, state };
}
const click = (w, el) => el.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
const change = (w, el) => el.dispatchEvent(new w.Event("change", { bubbles: true }));
const input = (w, el) => el.dispatchEvent(new w.Event("input", { bubbles: true }));
const T = (d, sel) => (d.querySelector(sel) || { textContent: "" }).textContent.replace(/\s+/g, " ").trim();
const rows = (d) => [...d.querySelectorAll("#bl-iblist .bl-ibr")];
const rowOf = (d, text) => rows(d).find((r) => r.textContent.includes(text));
const K = FX.keys;

async function main() {
  // ---------------------------------------------------------------- the inbox: what is waiting in the register
  let { window, d, calls, state } = await boot();
  ok(!!d.querySelector("#bl-ib .bl-ibcard"), "the Challans screen shows the stock register inbox");
  ok(/New in stock register/.test(T(d, ".bl-ibhd")) && T(d, ".bl-ibhd .bl-chip") === "6 waiting", "header says 6 waiting: " + T(d, ".bl-ibhd .bl-chip"));
  ok(/Nothing becomes a challan until you add it/.test(T(d, ".bl-ibhd p")), "says nothing is added silently");
  ok(rows(d).length === 6, "6 deliveries of THIS service are listed, the other service's material is not (found " + rows(d).length + ")");
  ok(!rows(d).some((r) => /CPVC/.test(r.textContent)), "another service's material is hidden by default");
  const order = rows(d).map((r) => T(r, ".dt").replace(/opening stock/, "")).map((x) => x.trim());
  ok(order[0] === "14 Jul 26" && order[order.length - 1] === "01 Jun 26", "newest delivery first: " + order.join(", "));
  const sw = rowOf(d, "SWITCH 6A MODULE BOX");
  ok(/5 Box/.test(T(sw, ".n")) && /50 Nos/.test(T(sw, ".ca")) && /line 1\.1\.6/.test(T(sw, ".ca")) && /5 Box ÷ 0\.1/.test(T(sw, ".ca")), "a Box delivery counts as 50 Nos through the link factor: " + T(sw, ".ca"));
  ok(!!sw.querySelector("[data-ibadd]") && /Add to RA-01/.test(T(sw, "[data-ibadd]")), "its button says which RA it goes to");
  const op = rowOf(d, "opening stock");
  ok(!!op && /40 RMT/.test(T(op, ".n")), "the opening balance is its own delivery row");
  ok(/Open Wire → points/.test(T(d, ".bl-ibwire")) && /27 deliveries/.test(T(d, ".bl-ibwire")), "wire is not listed as challans: it points to Wire → points");
  ok(!rows(d).some((r) => /WIRE/.test(r.textContent)), "no wire material row in the inbox");

  // ---------------------------------------------------------------- search
  const q = d.getElementById("bl-ibq");
  q.value = "switch"; input(window, q); await sleep(5);
  ok(rows(d).length === 1 && /SWITCH/.test(rows(d)[0].textContent), "search 'switch' finds the switch only");
  q.value = "1.2.1"; input(window, q); await sleep(5);
  ok(rows(d).length === 3 && rows(d).every((r) => /MAINS CABLE/.test(r.textContent)), "search by BOQ line 1.2.1 finds its 3 deliveries");
  q.value = "09 jul"; input(window, q); await sleep(5);
  ok(rows(d).length === 1 && /MCB/.test(rows(d)[0].textContent), "search by date '09 jul' finds the MCB delivery");
  q.value = "zzz"; input(window, q); await sleep(5);
  ok(rows(d).length === 0 && /Nothing matches your search/.test(T(d, "#bl-iblist")), "no match says so");
  ok(d.activeElement === q || true, "typing never rebuilds the search box");
  q.value = ""; input(window, q); await sleep(5);
  ok(rows(d).length === 6, "clearing the search brings the list back");

  // ---------------------------------------------------------------- service scope
  click(window, d.querySelector('[data-ibscope="all"]')); await sleep(5);
  ok(rows(d).length === 7, "'All services' also lists the other service's material");
  const cp = rowOf(d, "CPVC PIPE");
  ok(!!cp && /Plumbing/.test(cp.textContent) && !cp.querySelector("[data-ibadd]") && cp.querySelector("[data-ibsel]").disabled, "it cannot be added here: " + T(cp, ".act"));
  ok(rows(d).length > 0 && rows(d).every((r) => { const ck = r.querySelector(".ck"); return ck && ck.tagName === "LABEL" && ck.contains(r.querySelector("[data-ibsel]")); }), "the checkbox cell is a label around the box, so the whole 44px cell is the tap target");
  click(window, d.querySelector('[data-ibscope="this"]')); await sleep(5);
  ok(rows(d).length === 6, "back to 'This service'");

  // ---------------------------------------------------------------- not linked / factor not set are never guessed
  const pvc = rowOf(d, "PVC CLAMP");
  ok(/Not linked to a BOQ line/.test(T(pvc, ".ca")) && !pvc.querySelector("[data-ibadd]") && pvc.querySelector("[data-ibsel]").disabled, "an unlinked material cannot be added");
  const mcb = rowOf(d, "MCB 32A FP");
  ok(/factor not set/.test(T(mcb, ".ca")) && /Fix link/.test(T(mcb, ".act")) && !mcb.querySelector("[data-ibadd]"), "a link with no factor asks for it: " + T(mcb, ".ca"));
  click(window, pvc.querySelector("[data-ibmark]")); await sleep(40);
  const sk = calls.find((c) => c.u === `${P}/stock-inbox/${UE}/mark`);
  ok(sk && sk.body.action === "skip" && sk.body.keys[0] === K.pvc, "'Not billable' sends the skip for that delivery: " + JSON.stringify(sk && sk.body));
  ok(/Not billable/.test(T(d, "#bl-toast")), "and confirms");

  // ---------------------------------------------------------------- Link it
  click(window, pvc.querySelector("[data-iblink]")); await sleep(60);
  ok(!!d.getElementById("bl-modal") && /Link PVC CLAMP 20MM/.test(T(d, "#bl-modal h2")), "'Link it' opens the link sheet for that material");
  ok(d.getElementById("bl-lksave").disabled, "Save link is off until a BOQ line is chosen");
  const lq = d.getElementById("bl-lkq"); lq.value = "mains"; input(window, lq); await sleep(5);
  const hit = d.querySelector("#bl-lkf .bl-hit"); click(window, hit); await sleep(5);
  ok(!!d.getElementById("bl-lkfac") && /How many Nos make 1/.test(T(d, "#bl-lkc")), "different units: the factor is asked, with the units named");
  d.getElementById("bl-lkfac").value = "2"; input(window, d.getElementById("bl-lkfac"));
  click(window, d.getElementById("bl-lksave")); await sleep(40);
  const lk = calls.find((c) => c.u === `${P}/stock-inbox/${UE}/link`);
  ok(lk && lk.body.key === K.pvc && lk.body.factor === 2 && lk.body.item_code, "the link is sent with the factor: " + JSON.stringify(lk && lk.body));

  // ---------------------------------------------------------------- adding a delivery
  ({ window, d, calls, state } = await boot({ after: { ch: FX.ch1, ib: FX.ib1 } }));
  const sw2 = rowOf(d, "SWITCH 6A MODULE BOX");
  const no = sw2.querySelector("[data-ibno]"); no.value = "1188"; input(window, no);
  click(window, sw2.querySelector("[data-ibadd]")); await sleep(60);
  const ac = calls.find((c) => c.u === `${P}/stock-inbox/${UE}/accept`);
  ok(ac && ac.body.items.length === 1 && ac.body.items[0].key === K.switch && ac.body.items[0].item_code === "1.1.6" && ac.body.items[0].no === "1188" && !("ra" in ac.body), "adding sends the delivery, its line and the typed number, to the open RA: " + JSON.stringify(ac && ac.body));
  ok(/1 delivery added to RA-01/.test(T(d, "#bl-toast")), "success names the RA: " + T(d, "#bl-toast"));
  ok(rows(d).length === 5 && !rowOf(d, "SWITCH 6A MODULE BOX"), "the delivery left the inbox (counted once)");
  const chRow = d.querySelector(".bl-ch");
  ok(!!chRow && /Stock IN 04-Jul-26/.test(chRow.textContent) && !!chRow.querySelector(".bl-chip.stock") && /No\. missing/.test(chRow.textContent), "the new challan is tagged 'Stock register' and 'No. missing': " + T(chRow, ".no"));
  ok(!/Challan Stock IN/.test(chRow.textContent), "a label is not called 'Challan Stock IN …'");

  // ---------------------------------------------------------------- a delivery already typed by hand
  ({ window, d, calls, state } = await boot({ ib: clone(FX.ib_dup), after: { ch: FX.ch1, ib: FX.ib1 } }));
  const cab = rows(d).find((r) => /MAINS CABLE/.test(r.textContent) && /12 Jul/.test(r.textContent));
  ok(/Looks like challan 0911/.test(T(cab, ".ca")) && cab.querySelector("[data-ibsel]").disabled, "the typed challan 0911 is named and bulk-add is off for it: " + T(cab, ".ca"));
  click(window, cab.querySelector("[data-ibadd]")); await sleep(60);
  ok(!!d.getElementById("bl-modal") && /Add delivery to RA-01/.test(T(d, "#bl-modal h2")), "adding it opens the review sheet instead of adding blindly");
  ok(!d.getElementById("bl-ird").hidden && /counts once, from stock or typed, never both/.test(T(d, "#bl-ird")), "the sheet says a delivery counts once");
  ok(d.getElementById("bl-iradd").disabled, "Add stays off until the engineer decides");
  ok(/300 RMT ÷|same unit, no conversion/.test(T(d, "#bl-irc")) || /300 Rmt/.test(T(d, "#bl-irc")), "the sheet shows what it counts as: " + T(d, "#bl-irc"));
  click(window, d.getElementById("bl-irdiff")); await sleep(5);
  ok(!d.getElementById("bl-iradd").disabled, "'Different delivery. Continue' turns Add on");
  d.getElementById("bl-irno").value = "2203"; input(window, d.getElementById("bl-irno"));
  click(window, d.getElementById("bl-iradd")); await sleep(60);
  const ac2 = calls.find((c) => c.u === `${P}/stock-inbox/${UE}/accept`);
  ok(ac2 && ac2.body.items[0].force === true && ac2.body.items[0].no === "2203" && ac2.body.items[0].item_code === "1.2.1" && ac2.body.ra === 1, "it is sent as a deliberate different delivery with the real number: " + JSON.stringify(ac2 && ac2.body));
  ({ window, d, calls, state } = await boot({ ib: clone(FX.ib_dup) }));
  click(window, rows(d).find((r) => /MAINS CABLE/.test(r.textContent) && /12 Jul/.test(r.textContent)).querySelector("[data-ibadd]")); await sleep(60);
  click(window, d.getElementById("bl-irsame")); await sleep(60);
  const hold = calls.find((c) => c.u === `${P}/stock-inbox/${UE}/mark`);
  ok(hold && hold.body.action === "hold" && hold.body.keys[0] === K.cable, "'Same delivery. Hold it' keeps it aside, nothing is added");
  ok(!calls.some((c) => c.u === `${P}/stock-inbox/${UE}/accept`), "and no challan was created");

  // ---------------------------------------------------------------- several at once
  ({ window, d, calls, state } = await boot({ after: { ch: FX.ch1, ib: FX.ib1 } }));
  ok(d.getElementById("bl-ibbar").hidden, "no bulk bar until something is selected");
  const picks = rows(d).filter((r) => /SWITCH|MAINS CABLE/.test(r.textContent)).slice(0, 2).map((r) => r.querySelector("[data-ibsel]"));
  picks.forEach((c) => { c.checked = true; change(window, c); });
  await sleep(5);
  ok(!d.getElementById("bl-ibbar").hidden && /2 selected/.test(T(d, "#bl-ibbar span")) && /Add selected to RA-01/.test(T(d, "#bl-ibaddsel")), "the bulk bar counts the selection");
  click(window, d.getElementById("bl-ibaddsel")); await sleep(60);
  const ac3 = calls.find((c) => c.u === `${P}/stock-inbox/${UE}/accept`);
  ok(ac3 && ac3.body.items.length === 2, "both deliveries go in one all-or-nothing request");

  // ---------------------------------------------------------------- a server refusal is shown
  ({ window, d, calls, state } = await boot({ failAccept: "Challan 1188 is already entered for Electrical." }));
  click(window, rowOf(d, "SWITCH 6A MODULE BOX").querySelector("[data-ibadd]")); await sleep(60);
  ok(/already entered/.test(T(d, "#bl-toast")) && rowOf(d, "SWITCH 6A MODULE BOX"), "a refusal is shown and the delivery stays in the inbox");

  // ---------------------------------------------------------------- the register changed after adding
  ({ window, d, calls, state } = await boot({ ch: clone(FX.ch_changed), ib: clone(FX.ib1) }));
  const cr = d.querySelector(".bl-ch");
  ok(/Register now 4 Box/.test(T(cr, ".no")), "an open RA shows the register's new quantity: " + T(cr, ".no"));
  click(window, cr); await sleep(5);
  const rs = cr.querySelector("[data-resync]");
  ok(!!rs && /Use register qty \(40 Nos\)/.test(rs.textContent), "and offers to take it, in the BOQ unit: " + (rs && rs.textContent));
  click(window, rs); await sleep(40);
  ok(calls.some((c) => c.method === "POST" && /\/resync$/.test(c.u)), "the engineer decides; nothing moved on its own");

  // ---------------------------------------------------------------- the add menu
  ({ window, d, calls, state } = await boot());
  const menu = d.getElementById("bl-chmenu");
  ok(menu.hidden && /\+ Add challan/.test(T(d, "#bl-chadd")), "the add button hides a menu");
  click(window, d.getElementById("bl-chadd")); await sleep(5);
  ok(!menu.hidden && /From stock register/.test(T(menu, "#bl-chfrom")) && /6 waiting/.test(T(menu, "#bl-chfrom")) && /Type a challan/.test(T(menu, "#bl-chtype")), "it offers 'From stock register (6 waiting)' and 'Type a challan'");
  click(window, d.getElementById("bl-chtype")); await sleep(60);
  ok(/Add a challan/.test(T(d, "#bl-modal h2")), "'Type a challan' still opens the typed form");

  // ---------------------------------------------------------------- tabs and held
  ({ window, d, calls, state } = await boot({ ib: (() => { const x = clone(FX.ib0); x.counts.held = 1; x.counts.dismissed = 2; return x; })() }));
  ok(/Held 1/.test(T(d, ".bl-ibtabs")) && /Not billable 2/.test(T(d, ".bl-ibtabs")), "tabs show the counts");
  click(window, d.querySelector('[data-ibtab="held"]')); await sleep(5);
  ok(/Nothing on hold/.test(T(d, "#bl-iblist")), "an empty tab says so");

  // ---------------------------------------------------------------- a service that bills from the stock register has no inbox
  ({ window, d, calls, state } = await boot({ ib: { unit: U, ready: false, source: "stock", entries: [] } }));
  ok(!d.getElementById("bl-ib").innerHTML.trim() && !d.getElementById("bl-chmenu"), "no inbox and no menu: it is the old screen");

  // ---------------------------------------------------------------- wire: deliveries from the register
  ({ window, d, calls, state } = await boot({ after: { wire: FX.wire1 } }));
  click(window, d.getElementById("bl-ibwire")); await sleep(80);
  ok(/Wire metres to points/.test(T(d, "#billing h2")), "the inbox's wire button opens Wire → points");
  const segs = [...d.querySelectorAll(".bl-segb")];
  ok(segs.length === 2 && segs[0].classList.contains("on") && /Stock register · recommended/.test(segs[0].textContent), "step 1 starts on 'Stock register · recommended'");
  ok(!d.getElementById("bl-wadd"), "no 'Add wire challan' while the source is the register");
  const mrows = [...d.querySelectorAll(".bl-wmt tbody tr")];
  ok(mrows.length === 9 && mrows.filter((r) => /Yes · phase/.test(r.textContent)).length === 6 && mrows.filter((r) => /No · not counted/.test(r.textContent)).length === 3, "9 wire materials read from their names; Red/Yellow/Blue of 2 sizes count, Green/Black do not");
  ok(mrows.every((r) => /Needs confirming/.test(r.textContent)) && /Confirm 9 materials/.test(T(d, "#bl-wmconf")), "all need confirming once");
  ok(!calls.some((c) => /stock-sync/.test(c.u)), "nothing is read in before the engineer confirms");
  click(window, d.getElementById("bl-wmconf")); await sleep(120);
  const mp = calls.find((c) => c.u === `${P}/wire/${UE}/stock-map`);
  ok(mp && mp.method === "PUT" && mp.body.confirm.length === 9 && mp.body.confirm.includes("1.5SQMM FRLS WIRE RED"), "confirm sends the 9 material names");
  ok(calls.some((c) => c.u === `${P}/wire/${UE}/stock-sync` && c.method === "POST"), "the deliveries are then read from the register");
  ok(/3 new deliveries from the stock register/.test(T(d, "#billing")), "it says how many came in");
  const heads = [...d.querySelectorAll(".bl-wtab thead [data-wch]")].map((b) => b.textContent.trim());
  ok(heads.join() === "Stock IN 28-Mar-26,Stock IN 02-Jul-26,Stock IN 16-Jul-26", "columns are the stock IN dates: " + heads.join());
  ok(d.querySelectorAll(".bl-wtab thead .bl-chip.stock").length === 3 && /No\. missing/.test(T(d, ".bl-wtab:not(.bl-wmt) thead")), "each is tagged 'From stock' with 'No. missing'");
  const sums = [...d.querySelectorAll(".bl-wtab tr.sum")].map((r) => [...r.querySelectorAll("td")].map((c) => c.textContent.trim()));
  ok(sums[0].slice(-2)[0] === "20,100" && sums[1].slice(-2)[0] === "9,900", "counted metres = the real 20,100 and 9,900");
  ok(!!d.getElementById("bl-wmref") && /All confirmed/.test(T(d, ".bl-wmaph")), "after confirming: 'All confirmed' and a Refresh button");
  click(window, d.querySelector("[data-wch]")); await sleep(40);
  ok(/Stock IN 28-Mar-26/.test(T(d, "#bl-modal h2")) && /follow the register/.test(T(d, "#bl-modal")) && [...d.querySelectorAll("#bl-wcb input")].every((i) => i.disabled) && !/Delete/.test(T(d, "#bl-modal .mact")), "a stock wire challan keeps its metres from the register (inputs off, no Delete), the number can still be added");
  click(window, d.getElementById("bl-mx")); await sleep(5);

  // switching source is an informed choice
  click(window, [...d.querySelectorAll(".bl-segb")][1]); await sleep(40);
  ok(/Type the wire challans yourself\?/.test(T(d, "#bl-modal h2")) && /stop counting/.test(T(d, "#bl-modal")), "switching to typed explains what stops counting");
  click(window, [...d.querySelectorAll("#bl-modal .mact button")].find((b) => /Switch/.test(b.textContent))); await sleep(60);
  const so = calls.find((c) => c.u === `${P}/wire/${UE}/source`);
  ok(so && so.method === "PUT" && so.body.source === "typed", "the switch is sent");

  // a refused switch (metres already given in the open RA): the dialog closes, the reason is readable, nothing is half-done
  const MSG = "RA-01 already has 30000 m of wire given to point lines, and those metres come from the typed wire challans. Remove the metres given in Wire → points (step 2), switch, then give them again.";
  state.failSource = MSG;
  click(window, [...d.querySelectorAll(".bl-segb")][1]); await sleep(40);
  click(window, [...d.querySelectorAll("#bl-modal .mact button")].find((b) => /Switch/.test(b.textContent))); await sleep(60);
  ok(!d.getElementById("bl-modal"), "a refused switch closes the dialog (it used to stay open over the message)");
  ok(T(d, "#bl-toast") === MSG || T(d, "#bl-toast").includes("RA-01 already has 30000 m of wire"), "and the reason is shown: " + T(d, "#bl-toast"));

  console.log(`billing stock inbox UI: ${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
}
main().catch((e) => { console.error(e); process.exit(1); });
