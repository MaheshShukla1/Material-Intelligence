// jsdom test: wire metres -> points. Real scenario = Thoth Electrical "Only point wiring" RA 1: the three wire challans of the site's own
// WIRE CHALLAN sheet, 20,100 m -> 1.1.5, 6,000 m -> 1.1.3, 3,900 m -> 1.1.4 = 4,020 + 300 + 130 points = Rs 64,28,813 at 75% supply.
// Every payload is a REAL backend response (billing_wire_fixture.json, built by make_wire_fixture.py from the real Thoth BOQ).
import { JSDOM } from "jsdom";
import css from "css";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
let pass = 0, fail = 0;
function ok(cond, msg) { if (cond) pass++; else { fail++; console.error("FAIL:", msg); } }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const FX = JSON.parse(fs.readFileSync(path.join(__dirname, "billing_wire_fixture.json"), "utf8"));
const clone = (x) => JSON.parse(JSON.stringify(x));
const { PW } = FX.units;
const P = "/api/billing/thoth-mall";
const PWU = encodeURIComponent(PW);

const bl = css.parse(fs.readFileSync(path.join(__dirname, "billing.css"), "utf8")).stylesheet.rules;
ok(!bl.some((r) => r.type === "rule" && r.selectors.some((s) => /^(body|\.top|main|\.btn|\.ctl|\.sheet|\.card|\.sub)\b/.test(s.trim()) && !/data-view="billing"/.test(s))), "billing.css still only touches bl- classes / its own view");
const media = bl.filter((r) => r.type === "media" && /760/.test(r.media)).flatMap((m) => m.rules.filter((r) => r.type === "rule"));
ok(media.some((r) => r.selectors.some((s) => /\.bl-wrow \.ctl/.test(s)) && (r.declarations || []).some((x) => x.property === "min-height" && x.value === "44px")), "phone: wire inputs are 44px touch targets");
ok(media.some((r) => r.selectors.some((s) => s.trim() === ".bl-wrow") && (r.declarations || []).some((x) => x.property === "grid-template-columns")), "phone: a wire row is stacked");

async function boot(over) {
  const html = `<!doctype html><html><body>
    <header class="top" id="top"><div class="brand"><p id="ctx"></p></div>
      <div class="topact"><button id="home"></button><button id="pick"></button><button id="sync"></button><button id="syncgo"></button></div></header>
    <main><section id="drop" hidden></section><section id="busy" hidden></section>
      <section id="report"><div class="bar"><div class="tabs" id="svc"></div></div></section></main></body></html>`;
  const dom = new JSDOM(html, { url: "https://example.test/", runScripts: "dangerously", pretendToBeVisual: true });
  const { window } = dom; const calls = [];
  window.__BL_LIVE_MS = 3600000; window.scrollTo = () => {};
  const state = { ov: clone(FX.ov2), wire: clone(FX.wire2), ch: clone(FX.ch2), failPut: null, ...over };
  window.fetch = (url, opts) => {
    const u = String(url), method = (opts && opts.method) || "GET";
    const body = opts && opts.body ? JSON.parse(opts.body) : null;
    calls.push({ u, method, body });
    let res = {}, status = 200;
    if (u === "/api/projects") res = [{ slug: "thoth-mall", project: "Thoth Mall", runs: 1, latest_run: "r1" }];
    else if (u === P + "/auto") res = { created: [] };
    else if (u === P) res = state.ov;
    else if (u === P + "/setup" && method === "GET") res = FX.setup;
    else if (u === `${P}/challans/${PWU}` && method === "GET") res = state.ch;
    else if (u === `${P}/wire/${PWU}` && method === "GET") res = state.wire;
    else if (u === `${P}/wire/${PWU}/alloc`) { if (state.failPut) { status = 409; res = { detail: state.failPut }; } else res = { ok: true }; }
    else if (u.startsWith(`${P}/wire/${PWU}/challans`)) res = { ok: true };
    else if (u === P + "/wire-scheme") res = { ok: true };
    else if (u === `${P}/ra/${FX.ra}`) res = FX.bill;
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
const T = (d, sel) => (d.querySelector(sel) || { textContent: "" }).textContent.replace(/\s+/g, " ").trim();
const num = (s) => Number(String(s).replace(/[^0-9.]/g, ""));

async function main() {
  // ---------------------------------------------------------------- the link appears only where wire matters
  let { window, d, calls } = await boot();
  const sec = d.getElementById("billing");
  const wl = [...sec.querySelectorAll("[data-wire]")];
  ok(wl.length === 1 && wl[0].dataset.wire === PW && /Wire → points/.test(wl[0].textContent), "only the point-wiring Work Order shows 'Wire → points' (found " + wl.length + ")");
  ok(!/Wire → points/.test(T(sec, '.bl-svc [data-overall="HVAC"]')), "plain services stay clean");
  click(window, wl[0]); await sleep(80);
  ok(/Wire metres to points/.test(T(d, "#billing h2")), "the link opens the wire screen");

  // ---------------------------------------------------------------- step 1: the matrix, as in the site's WIRE CHALLAN sheet
  const tabs = [...d.querySelectorAll(".bl-wtab")];
  ok(tabs.length === 1, "one wire table");
  const heads = [...d.querySelectorAll(".bl-wtab thead [data-wch]")].map((b) => b.textContent.trim());
  ok(heads.join() === "Challan 1207,Challan 615,Challan 617", "challans in date order: " + heads.join());
  const sz = [...d.querySelectorAll(".bl-wtab tr.sz")].map((r) => r.textContent.trim());
  ok(sz.join("|") === "Wire 1.5 sq mm|Wire 2.5 sq mm", "two wire sizes: " + sz.join("|"));
  const rowOf = (size, colour) => { const all = [...d.querySelectorAll(".bl-wtab tbody tr")]; let on = false;
    for (const r of all) { if (r.classList.contains("sz")) on = r.textContent.includes("Wire " + size + " "); else if (on && r.querySelector("th") && r.querySelector("th").textContent.trim() === colour) return r; } };
  const cells = (r) => [...r.querySelectorAll("td")].map((c) => c.textContent.trim());
  ok(cells(rowOf("1.5", "Red")).join() === "300,3,300,3,900,7,500,Counted", "1.5 Red per challan, total, counted: " + cells(rowOf("1.5", "Red")).join("|"));
  ok(cells(rowOf("1.5", "Black")).slice(-1)[0] === "Not counted" && cells(rowOf("1.5", "Green")).slice(-1)[0] === "Not counted", "Black (neutral) and Green (earth) are not counted");
  ok(rowOf("1.5", "Black").classList.contains("nc") && rowOf("1.5", "Yellow").classList.contains("cnt"), "not-counted colours are muted");
  const sums = [...d.querySelectorAll(".bl-wtab tr.sum")].map((r) => cells(r));
  ok(sums[0].slice(-2)[0] === "20,100" && sums[1].slice(-2)[0] === "9,900", "counted metres = Red + Yellow + Blue: 20,100 and 9,900");
  ok(d.querySelectorAll(".bl-wtab .bl-dot").length >= 10, "every colour has its dot");
  ok(/dedicated phase|every circuit has its own phase/i.test(T(d, "#billing")), "the reason for counting only phase wire is stated");
  const scheme = d.getElementById("bl-wscheme");
  ok(scheme.disabled && scheme.options.length === 2 && /Red · Yellow · Blue/.test(scheme.options[scheme.selectedIndex].textContent), "colour code is legacy R-Y-B and locked once wire is entered");

  // ---------------------------------------------------------------- step 2: metres -> points, from the BOQ text
  const blocks = [...d.querySelectorAll(".bl-wb")];
  ok(blocks.length === 3, "three point lines carry wire (found " + blocks.length + ")");
  const blk = (ref) => blocks.find((b) => b.querySelector(".t b").textContent.trim() === ref);
  const pts = (ref) => T(blk(ref), "[data-pts]");
  ok(pts("1.1.5") === "4,020 of 4,277" && pts("1.1.3") === "300 of 493" && pts("1.1.4") === "130 of 211", "points: " + ["1.1.5", "1.1.3", "1.1.4"].map(pts).join(" | "));
  ok(/average point length upto & including 5 Rmtr/.test(blk("1.1.5").textContent) && /from BOQ text \(upto\)/.test(blk("1.1.5").textContent), "the BOQ's own words are shown as the source of 5 m per point");
  ok(/average point length of 30 Rmtr/.test(blk("1.1.4").textContent) && /30 m/.test(T(blk("1.1.4"), ".pp")), "1.1.4 uses 30 m from the BOQ");
  ok(T(blk("1.1.5"), "[data-val]") === "₹ 42,05,925" && T(blk("1.1.3"), "[data-val]") === "₹ 13,55,625" && T(blk("1.1.4"), "[data-val]") === "₹ 8,67,263", "supply value per line at 75%");
  const given = (ref) => blk(ref).querySelector("[data-m]");
  ok(given("1.1.5").value === "20100" && given("1.1.3").value === "6000" && given("1.1.4").value === "3900", "metres given in RA-01: 20100 / 6000 / 3900");
  const cardTxt = [...d.querySelectorAll("#bl-wsizes .bl-wsz")].map((c) => c.textContent.replace(/\s+/g, " ").trim());
  ok(cardTxt.length === 2 && cardTxt.every((x) => /all metres used/.test(x)) && /20,100 m counted · 20,100 m given/.test(cardTxt[0]) && /9,900 m counted · 9,900 m given/.test(cardTxt[1]), "each wire size says all metres used: " + cardTxt.join(" || "));
  ok(d.querySelectorAll("#bl-wsizes .bl-wsz.ok").length === 2, "the size cards are green");

  // ---------------------------------------------------------------- step 3: the bill
  ok(T(d, "#bl-wtot") === "₹ 64,28,813" && T(d, "#bl-wpts") === "4,450", "supply bill from points = ₹ 64,28,813 for 4,450 points");
  ok(/RA-01/.test(T(d, ".bl-wsum")) && /Installation, the balance %, is billed on points completed in Site Progress/.test(T(d, ".bl-wsum")), "step 3 names the RA and says installation is not billed on wire");
  ok(T(d, "#bl-wdirty") === "Unsaved changes" && d.getElementById("bl-wdirty").hidden, "nothing unsaved at the start");

  // ---------------------------------------------------------------- editing: live numbers, honest warnings
  const m5 = given("1.1.5");
  m5.value = "20107"; input(window, m5); await sleep(5);
  ok(pts("1.1.5") === "4,021 of 4,277", "7 m more -> 4,021 whole points (20107/5 = 4021.4)");
  ok(/7 m too many/.test(T(d, "#bl-wsizes")) && d.querySelectorAll("#bl-wsizes .bl-wsz.bad").length === 1, "giving more than was received is flagged on the size card");
  ok(!d.getElementById("bl-wdirty").hidden, "'Unsaved changes' appears");
  const before = calls.length;
  click(window, d.getElementById("bl-wsave")); await sleep(30);
  ok(!calls.slice(before).some((c) => c.method === "PUT") && /too many|more metres/i.test(T(d, "#bl-toast")), "saving more than received is blocked on the screen");
  m5.value = "20100"; input(window, m5); await sleep(5);
  ok(pts("1.1.5") === "4,020 of 4,277" && d.getElementById("bl-wdirty").hidden, "back to the saved metres: no unsaved changes");
  m5.value = "10100"; input(window, m5); await sleep(5);
  ok(pts("1.1.5") === "2,020 of 4,277" && T(d, "#bl-wtot") === "₹ 43,36,313", "a smaller allocation lowers the supply total live: " + T(d, "#bl-wtot"));
  ok(/10,000 m left to give/.test(T(d, "#bl-wsizes")), "and says how many metres are left to give");
  click(window, d.getElementById("bl-wsave")); await sleep(60);
  const put = calls.find((c) => c.method === "PUT" && c.u === `${P}/wire/${PWU}/alloc`);
  const pr = (code) => put && put.body.rows.find((r) => r.item_code === code);
  ok(put && put.body.rows.length === 3 && pr("1.1.5").size === "1.5" && pr("1.1.5").metres === 10100 && pr("1.1.3").size === "2.5" && pr("1.1.3").metres === 6000 &&
     pr("1.1.4").metres === 3900 && put.body.rows.every((r) => !("per" in r)), "save sends the metres of the open RA per line and wire size (BOQ-length lines send no 'per'): " + JSON.stringify(put && put.body));
  ok(/Metres saved/.test(T(d, "#bl-toast")), "success is shown");

  // ---------------------------------------------------------------- a server refusal is shown, nothing is hidden
  ({ window, d, calls } = await boot({ failPut: "“Loop light point”: the BOQ has only 4277 points, but these metres make 4300." }));
  click(window, d.querySelector("[data-wire]")); await sleep(80);
  const m = [...d.querySelectorAll(".bl-wb")].find((b) => b.querySelector(".t b").textContent.trim() === "1.1.5").querySelector("[data-m]"); m.value = "20000"; input(window, m); await sleep(5);
  click(window, d.getElementById("bl-wsave")); await sleep(60);
  ok(/BOQ has only 4277 points/.test(T(d, "#bl-toast")), "the server's reason is shown as it is");

  // ---------------------------------------------------------------- add a point line (BOQ states a length / states none)
  ({ window, d, calls } = await boot());
  click(window, d.querySelector("[data-wire]")); await sleep(80);
  const find = d.getElementById("bl-wfind");
  ok(!!find, "search for a point line is there once wire exists");
  ok(!d.querySelector('#bl-wfound .bl-hit[data-c="1.1.5"]') && !!d.querySelector('#bl-wfound .bl-hit[data-c="1.1.1"]'), "lines already carrying wire are not offered again; the others are");
  ok(/15 m per point/.test(T(d, '#bl-wfound .bl-hit[data-c="1.1.1"]')), "a hit shows the BOQ's metres per point");
  find.value = "1.1.6"; input(window, find); await sleep(5);
  const free = d.querySelector('#bl-wfound .bl-hit[data-c="1.1.6"]');
  ok(!!free && /you type it/.test(free.textContent), "a point line whose BOQ states no length can be searched; the hit says you type the length");
  click(window, free); await sleep(10);
  const nb = [...d.querySelectorAll(".bl-wb")].pop();
  ok(!!nb.querySelector("[data-per]") && d.activeElement === nb.querySelector("[data-m]"), "a typed-length line gets a 'metres per point' box; the metres box is focused");
  const m5b = [...d.querySelectorAll(".bl-wb")].find((b) => b.querySelector(".t b").textContent.trim() === "1.1.5").querySelector("[data-m]"); m5b.value = "20000"; input(window, m5b);
  nb.querySelector("[data-m]").value = "60"; input(window, nb.querySelector("[data-m]")); await sleep(5);
  ok(T(nb, "[data-pts]") === "0 of 55", "no points until the length is typed (never guessed)");
  const b2 = calls.length;
  click(window, d.getElementById("bl-wsave")); await sleep(30);
  ok(!calls.slice(b2).some((c) => c.method === "PUT") && /metres per point/i.test(T(d, "#bl-toast")), "save asks for the length first");
  nb.querySelector("[data-per]").value = "12"; input(window, nb.querySelector("[data-per]")); await sleep(5);
  ok(/^5 of 55$/.test(T(nb, "[data-pts]")), "60 m / 12 m = 5 points");
  click(window, d.getElementById("bl-wsave")); await sleep(60);
  const p2 = calls.slice(b2).find((c) => c.method === "PUT");
  ok(p2 && p2.body.rows.find((r) => r.item_code === "1.1.6" && r.per === 12 && r.metres === 60), "the typed length is sent only for that line");

  // a line added by search carries its own rate and supply %: its supply value is a real number, never NaN
  const addedVal = T(nb, "[data-val]");
  ok(/^₹ [0-9,]+$/.test(addedVal) && !/NaN/.test(T(d, "#bl-wtot")), "a line added by search shows a real supply value (" + addedVal + ") and the total is not NaN (" + T(d, "#bl-wtot") + ")");

  // ---------------------------------------------------------------- add a wire challan
  ({ window, d, calls } = await boot());
  click(window, d.querySelector("[data-wire]")); await sleep(80);
  click(window, d.getElementById("bl-wadd")); await sleep(30);
  ok(d.getElementById("bl-wcdate").value.length === 10 && d.getElementById("bl-wcra").value === String(FX.wire2.open_no), "date defaults to today, RA to the open RA");
  const colLabels = [...d.querySelectorAll("#bl-modal .bl-wcc label span")].map((s) => s.textContent.replace(/\s+/g, " ").trim());
  ok(colLabels.join("|") === "Red|Yellow|Blue|Black (not counted)|Green (not counted)", "five colours, neutral and earth marked not counted: " + colLabels.join("|"));
  d.getElementById("bl-wcno").value = "700";
  d.querySelector('#bl-modal [data-bsz="0"]').value = "4"; input(window, d.querySelector('#bl-modal [data-bsz="0"]'));
  const setv = (k, v) => { const e = d.querySelector(`#bl-modal [data-bv="0:${k}"]`); e.value = v; input(window, e); };
  setv("RED", "300"); setv("YELLOW", "300"); setv("BLACK", "100");
  click(window, d.getElementById("bl-wcaddsz")); await sleep(5);
  ok(d.querySelectorAll("#bl-modal .bl-wcblk").length === 2, "a second wire size can be added on the same challan");
  click(window, d.querySelector('#bl-modal [data-bdel="1"]')); await sleep(5);
  click(window, [...d.querySelectorAll("#bl-modal .mact .btn")].find((b) => /Save challan/.test(b.textContent))); await sleep(60);
  const wc = calls.find((c) => c.method === "POST" && c.u === `${P}/wire/${PWU}/challans`);
  ok(wc && wc.body.no === "700" && wc.body.ra === FX.wire2.open_no && wc.body.sizes.length === 1 && wc.body.sizes[0].size === "4" &&
     JSON.stringify(wc.body.sizes[0].colours) === JSON.stringify({ RED: 300, YELLOW: 300, BLACK: 100 }), "a wire challan posts number, date, RA, size and only the colours typed: " + JSON.stringify(wc && wc.body));

  // ---------------------------------------------------------------- opening a frozen challan (RA locked) cannot change it
  const fz = clone(FX.wire2); fz.challans[0].frozen = true; fz.challans[0].state = "locked";
  ({ window, d, calls } = await boot({ wire: fz }));
  click(window, d.querySelector("[data-wire]")); await sleep(80);
  click(window, d.querySelector("[data-wch]")); await sleep(30);
  ok(![...d.querySelectorAll("#bl-modal .mact .btn")].some((b) => /Save|Delete/.test(b.textContent)) && [...d.querySelectorAll("#bl-modal input")].filter((i) => i.id !== "bl-wcno" && i.type === "number").every((i) => i.disabled || i.dataset.bsz != null), "an invoiced wire challan opens read-only");

  // ---------------------------------------------------------------- colour code (before any wire)
  const w0 = clone(FX.wire0);
  ({ window, d, calls } = await boot({ wire: w0 }));
  click(window, d.querySelector("[data-wire]")); await sleep(80);
  ok(/No wire challan yet/.test(T(d, "#billing")) && !d.getElementById("bl-wfind"), "empty state: no table, no line search");
  const sc = d.getElementById("bl-wscheme");
  ok(!sc.disabled, "colour code can be changed while no wire is entered");
  sc.value = "iec"; change(window, sc); await sleep(60);
  ok(calls.some((c) => c.u === P + "/wire-scheme" && c.method === "PUT" && c.body.scheme === "iec"), "choosing the IEC code is saved for the project");

  // ---------------------------------------------------------------- the points show in Challans, not as an editable challan
  ({ window, d, calls } = await boot());
  click(window, d.querySelector('[data-ch="' + PW + '"]')); await sleep(80);
  const vrow = [...d.querySelectorAll(".bl-ch")].find((r) => /Wire RA-01/.test(r.textContent));
  ok(!!vrow && !/Challan Wire/.test(vrow.textContent) && !vrow.querySelector(".bl-retag") && !vrow.querySelector("[data-edit]") && !vrow.querySelector("[data-del]"), "the wire points are listed as 'Wire RA-01' with no re-tag, edit or delete");
  click(window, vrow); await sleep(5);
  ok(vrow.querySelectorAll(".bl-cl").length === 3 && /Worked out from wire metres/.test(vrow.textContent), "its three lines show the points");
  ok(!!d.getElementById("bl-chwire"), "the Challans screen has a Wire → points button");
  click(window, vrow.querySelector("[data-towire]")); await sleep(80);
  ok(/Wire metres to points/.test(T(d, "#billing h2")), "'Open Wire → points' goes to the wire screen");
  click(window, d.getElementById("bl-wback")); await sleep(60);
  ok(/Challans · /.test(T(d, "#billing h2")), "← Challans comes back");

  // ---------------------------------------------------------------- delete the wire points of the open RA from the Challans screen
  ({ window, d, calls } = await boot());
  click(window, d.querySelector('[data-ch="' + PW + '"]')); await sleep(80);
  let vr = [...d.querySelectorAll(".bl-ch")].find((r) => /Wire RA-01/.test(r.textContent));
  const dbtn = vr.querySelector("[data-wdel]");
  ok(!!dbtn && /Delete/.test(dbtn.textContent) && /danger/.test(dbtn.className), "the open 'Wire RA-01' row has a Delete button like every other challan");
  ok(dbtn.previousElementSibling && dbtn.previousElementSibling.hasAttribute("data-towire"), "it sits next to 'Open Wire → points'");
  click(window, dbtn); await sleep(20);
  const mt = d.getElementById("bl-modal").textContent;
  ok(/Delete Wire RA-01\?/.test(mt) && /4,450 points on 3 lines/.test(mt) && /wire deliveries stay/.test(mt), "it asks first: how many points leave, and that the deliveries stay: " + mt.slice(0, 120));
  click(window, [...d.querySelectorAll("#bl-modal .mact .btn")].find((b) => b.textContent === "Cancel")); await sleep(10);
  ok(!calls.some((c) => c.method === "PUT" && c.u === `${P}/wire/${PWU}/alloc`), "Cancel changes nothing");
  click(window, dbtn); await sleep(20);
  click(window, [...d.querySelectorAll("#bl-modal .mact .btn")].find((b) => b.textContent === "Delete")); await sleep(80);
  const del = calls.find((c) => c.method === "PUT" && c.u === `${P}/wire/${PWU}/alloc`);
  ok(del && Array.isArray(del.body.rows) && del.body.rows.length === 0, "Delete clears the open RA's allocation (rows: []), deliveries are not touched");
  ok(!calls.some((c) => c.method === "DELETE" && /wire/.test(c.u)), "no wire challan (delivery) is deleted");
  ok(/Challans · /.test(T(d, "#billing h2")), "and you stay on the Challans screen");
  { const frozen = clone(FX.ch2); frozen.challans.forEach((c) => { if (c.virtual) c.frozen = true; });
    ({ window, d, calls } = await boot({ ch: frozen }));
    click(window, d.querySelector('[data-ch="' + PW + '"]')); await sleep(80);
    vr = [...d.querySelectorAll(".bl-ch")].find((r) => /Wire RA-01/.test(r.textContent));
    ok(!!vr && !vr.querySelector("[data-wdel]") && !!vr.querySelector("[data-towire]"), "a Wire row of an invoiced RA has no Delete (it is frozen with the bill)"); }

  console.log(`billing wire UI: ${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
}
main().catch((e) => { console.error(e); process.exit(1); });
