// jsdom test: Item history on the Challans page -- type any BOQ item and read it across every RA, with its challans.
// Real scenario = Hyatt Plumbing "15 mm / 20 mm" (BOQ 4,320 rmt): RA-1 (Supply + Installation, invoiced) took 6,500 rmt, the Supply
// RA-2 draft takes 1,900 more, so 8,400 in all, 4,080 above the BOQ. Every payload is a REAL backend response
// (billing_item_history_fixture.json: the Hyatt RA workbook's challans pushed through the real routes: Together RA-1 locked,
// Supply RA-2 draft, extras accepted). The 0-waiting stock inbox is the real Thoth payload (billing_stockinbox_fixture.json) with its
// waiting rows taken out.
import { JSDOM } from "jsdom";
import css from "css";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
let pass = 0, fail = 0;
function ok(cond, msg) { if (cond) pass++; else { fail++; console.error("FAIL:", msg); } }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const clone = (x) => JSON.parse(JSON.stringify(x));
const FX = JSON.parse(fs.readFileSync(path.join(__dirname, "billing_item_history_fixture.json"), "utf8"));
const TH = JSON.parse(fs.readFileSync(path.join(__dirname, "billing_stockinbox_fixture.json"), "utf8"));
const P = "/api/billing/" + FX.slug;

// ---- css: only bl- classes, a one-column detail screen on a phone, 44px touch targets
const bl = css.parse(fs.readFileSync(path.join(__dirname, "billing.css"), "utf8")).stylesheet.rules;
ok(!bl.some((r) => r.type === "rule" && r.selectors.some((s) => /^(body|\.top|main|\.btn|\.ctl|\.sheet|\.card|\.sub)\b/.test(s.trim()) && !/data-view="billing"/.test(s))), "billing.css still only touches bl- classes");
const m900 = bl.filter((r) => r.type === "media" && /900/.test(r.media)).flatMap((m) => m.rules.filter((r) => r.type === "rule"));
const m760 = bl.filter((r) => r.type === "media" && /760/.test(r.media)).flatMap((m) => m.rules.filter((r) => r.type === "rule"));
const decl = (rules, sel, prop) => rules.some((r) => r.selectors.some((s) => s.trim() === sel) && (r.declarations || []).some((x) => x.property === prop));
ok(decl(m900, ".bl-ih", "grid-template-columns") && decl(m900, ".bl-ih.has-sel .bl-ih-l", "display") && decl(m900, ".bl-ih-back", "display"), "phone: the item opens as its own screen with a Back button");
ok(decl(m760, ".bl-ih-find #bl-ihq", "font-size") && m760.some((r) => r.selectors.some((s) => /\.bl-ih \.btn/.test(s)) && r.declarations.some((x) => x.property === "min-height" && x.value === "44px")), "phone: 16px search box (no iOS zoom) and 44px buttons");

async function boot(over) {
  const html = `<!doctype html><html><body>
    <header class="top" id="top"><div class="brand"><p id="ctx"></p></div>
      <div class="topact"><button id="home"></button><button id="pick"></button><button id="sync"></button><button id="syncgo"></button></div></header>
    <main><section id="drop" hidden></section><section id="busy" hidden></section>
      <section id="report"><div class="bar"><div class="tabs" id="svc"></div></div></section></main></body></html>`;
  const dom = new JSDOM(html, { url: "https://example.test/", runScripts: "dangerously", pretendToBeVisual: true });
  const { window } = dom; const calls = [];
  window.__BL_LIVE_MS = 3600000; window.scrollTo = () => {}; window.Element.prototype.scrollIntoView = function () { this.__scrolled = true; };
  // 0 waiting, nothing held: the real Thoth inbox with its waiting rows taken out
  const ib = clone(TH.ib0); ib.entries = ib.entries.filter((e) => e.state !== "waiting" && e.state !== "held");
  ib.counts = { ...ib.counts, waiting: 0, held: 0 };
  const state = { ov: clone(FX.ov), ib, failHist: null, ...over };
  window.fetch = (url, opts) => {
    const u = String(url), method = (opts && opts.method) || "GET";
    calls.push({ u, method });
    let res = {}, status = 200;
    const base = u.split("?")[0];
    const hm = new RegExp("^" + P + "/item-history/(.+)$").exec(base);
    if (u === "/api/projects") res = [{ slug: FX.slug, project: "Hyatt Hotel", runs: 1, latest_run: "r1" }];
    else if (u === P + "/auto") res = { created: [] };
    else if (u === P) res = state.ov;
    else if (u === P + "/setup" && method === "GET") res = FX.setup;
    else if (hm) {
      const unit = decodeURIComponent(hm[1]);
      if (state.failHist === unit) { status = 500; res = { detail: "boom" }; }
      else res = FX.hist[unit] || { service: unit, ras: [], lines: [] };
    }
    else if (/\/challans\/[^/]+$/.test(base) && method === "GET") res = FX.ch[decodeURIComponent(base.split("/").pop())] || FX.ch.Plumbing;
    else if (/\/stock-inbox\/[^/]+$/.test(base) && method === "GET") res = state.ib;
    else if (base === `${P}/ra/${FX.ra2.id}`) res = FX.ra2.bill;
    return Promise.resolve({ ok: status < 400, status, json: async () => res, text: async () => JSON.stringify(res) });
  };
  window.eval(fs.readFileSync(path.join(__dirname, "siteprogress.js"), "utf8"));
  window.eval(fs.readFileSync(path.join(__dirname, "billing.js"), "utf8"));
  await sleep(0);
  const d = window.document;
  [...d.querySelectorAll(".spnav button")].find((b) => b.dataset.v === "billing").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(80);
  window.MIBilling._state.ov = state.ov;
  await window.MIBilling.showChallans("Plumbing");
  await sleep(60);
  return { window, d, calls, state };
}
const click = (w, el) => el.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
const type = (w, el, v) => { el.value = v; el.dispatchEvent(new w.Event("input", { bubbles: true })); };
const T = (d, sel) => (d.querySelector(sel) || { textContent: "" }).textContent.replace(/\s+/g, " ").trim();
const rowsOf = (d) => [...d.querySelectorAll("#bl-ihres .bl-ih-row")];
const pick = (w, d, text) => { const r = rowsOf(d).find((x) => x.textContent.includes(text)); if (r) click(w, r); return r; };
const CODE = "2.01.b.2";

async function main() {
  const plum = FX.hist.Plumbing.lines;
  const moved = plum.filter((l) => l.rows.length || l.challans.length);
  const idle = plum.find((l) => !l.rows.length && !l.challans.length);

  // ------------------------------------------------------------ the card is on the page, and the page below it is intact
  let { window: w, d, calls } = await boot();
  ok(!!d.querySelector("#bl-ih .bl-ih") && /Find any BOQ item/.test(T(d, ".bl-ih-find")), "the Challans page has the 'Find any BOQ item' card");
  ok(!!d.querySelector("#bl-ihq") && /e\.g\. 15 mm/.test(d.querySelector("#bl-ihq").placeholder), "a search box with an example");
  ok(calls.filter((c) => c.u.includes("/item-history/")).map((c) => c.u).join() === `${P}/item-history/Plumbing`, "only this service's history is fetched until you ask for All services");
  ok(d.querySelectorAll(".bl-ch").length === FX.ch.Plumbing.challans.length && /Challans · Plumbing/.test(T(d, ".bl-head")), `the challan list below is untouched (${FX.ch.Plumbing.challans.length} challans)`);
  ok(d.querySelector("#bl-ih").compareDocumentPosition(d.querySelector("#bl-ib")) & 4, "the search card sits above the stock register block");

  // nothing typed: the lines that have moved, 8 at a time
  ok(rowsOf(d).length === 8 && T(d, "#bl-ihres .bl-ih-rh") === `Lines billed or delivered ${moved.length} lines`, `nothing typed shows the ${moved.length} lines that have moved, 8 first: ` + T(d, "#bl-ihres .bl-ih-rh"));
  ok(T(d, "#bl-ihmore") === `Show all ${moved.length}`, "Show all N");
  click(w, d.querySelector("#bl-ihmore"));
  ok(rowsOf(d).length === moved.length && T(d, "#bl-ihmore") === "Show fewer", "Show all opens the whole list");
  ok(/Pick an item to see its history/.test(T(d, "#bl-ihcard")), "nothing is open yet");

  // ------------------------------------------------------------ the reported item: RA-1 6,500, RA-2 1,900
  type(w, d.querySelector("#bl-ihq"), "15 mm");
  const hit = rowsOf(d).find((r) => r.textContent.includes("15 mm / 20 mm") && r.textContent.includes(CODE));
  ok(!!hit, "typing '15 mm' finds the 15 mm / 20 mm line");
  const chips = [...hit.querySelectorAll(".bl-chip")].map((c) => c.textContent.trim());
  ok(chips.join() === "RA-01,RA-02,Above BOQ", "its row says which RAs it is in, and that it is above the BOQ: " + chips.join());
  ok(hit.querySelector(".bl-chip.locked").textContent === "RA-01" && hit.querySelectorAll(".bl-chip.draft")[0].textContent === "RA-02", "RA-01 invoiced (green), RA-02 draft (amber)");
  click(w, hit);
  const ans = T(d, ".bl-ih-ans");
  ok(/Supply RA-01 6,500\s*·\s*RA-02 draft 1,900/.test(ans), "the answer line gives RA-1 6,500 and RA-2 1,900: " + ans);
  ok(/Total 8,400 of BOQ 4,320 rmt · 4,080 above the BOQ/.test(ans), "and the total against the BOQ: " + ans);
  ok(/Installation RA-01 3,800/.test(ans), "installation billed with RA-1 is stated apart: " + ans);
  ok(/Challans received 8,400 rmt · billed 8,400 rmt/.test(T(d, ".bl-ih-ans")) === false, "no mismatch note when challans and bills agree");
  ok(d.querySelector("#bl-ihroot").classList.contains("has-sel") && /Draft/.test(T(d, ".bl-ih-led")), "the card is open, RA-wise table below it");
  const x = T(d, ".bl-ih-x");
  ok(/RA-01 \(invoiced\) bills 2,180 rmt above the BOQ/.test(x) && /RA-02 \(draft\) bills 1,900 rmt above the BOQ/.test(x), "the extra over BOQ is spelled out per RA: " + x);
  ok(!!d.querySelector(".bl-ih-bar .xs") && d.querySelectorAll(".bl-ih-bar .seg").length === 2, "the bar has one segment per RA and a hatched part above the BOQ");
  const led = [...d.querySelectorAll(".bl-ih-lr")].map((r) => r.textContent.replace(/\s+/g, " ").trim());
  ok(led.length === 3 && /RA-01.*Invoiced.*Supply.*6,500.*6,500/.test(led[0]) && /Installation.*3,800.*3,800/.test(led[1]) && /RA-02.*Draft.*Supply.*1,900.*8,400/.test(led[2]), "ledger: RA-1 supply, RA-1 installation, RA-2 supply: " + led.join(" | "));
  ok(!/₹/.test(T(d, ".bl-ih-led")), "rupees are hidden by default (site engineers read quantities)");
  click(w, d.querySelector("[data-ihamt]"));
  ok(/₹ 29,24,936/.test(T(d, ".bl-ih-led")) && /₹ 8,54,981/.test(T(d, ".bl-ih-led")) && /Hide ₹/.test(T(d, "[data-ihamt]")), "Show ₹ adds the amounts (RA-1 ₹ 29,24,936, RA-2 ₹ 8,54,981): " + T(d, ".bl-ih-led"));
  ok(w.localStorage.getItem("bl-ih-amt") === "1", "the choice is remembered on this phone");
  const grp = [...d.querySelectorAll(".bl-ih-grp")].map((g) => g.querySelector("summary").textContent.replace(/\s+/g, " ").trim());
  ok(grp.length === 2 && /^Challans in RA-01 · 4 · 6,500 rmt/.test(grp[0]) && /^Challans in RA-02 · 2 · 1,900 rmt/.test(grp[1]), "challans are grouped under their RA: " + grp.join(" | "));
  ok([...d.querySelectorAll(".bl-ih-ch .bl-link")].map((b) => b.textContent).join() === "Challan 1354,Challan 1356,Challan 1362,Challan 1374,Challan 119,Challan 121", "the six real challans, by RA");

  // a challan opens in the list below
  click(w, [...d.querySelectorAll("[data-ihch]")].find((b) => b.textContent === "Challan 1362"));
  const chRow = [...d.querySelectorAll("#billing .bl-ch")].find((r) => r.dataset.id === "c6" && /1362/.test(r.textContent));
  ok(chRow && chRow.classList.contains("ex") && chRow.classList.contains("bl-ih-flash") && chRow.__scrolled === true, "tapping a challan opens and highlights it in the challan list");

  // the RA draft opens on that very line
  click(w, d.querySelector("[data-ihra].btn"));
  await sleep(80);
  ok(calls.some((c) => c.u === `${P}/ra/${FX.ra2.id}`) && !!d.querySelector("#billing .bl-ra, #billing [data-back], #bl-back"), "'Open RA-02 draft' goes to that draft");
  const ln = [...d.querySelectorAll("#billing .bl-lr")].find((r) => r.dataset.code === CODE);
  ok(!!ln && ln.classList.contains("open") && ln.__scrolled === true, "and the line is already open in it");

  // ------------------------------------------------------------ search by challan no, by ref, nothing found
  ({ window: w, d, calls } = await boot());
  type(w, d.querySelector("#bl-ihq"), "1362");
  ok(rowsOf(d).some((r) => r.textContent.includes(CODE)), "a challan number finds the lines on that challan");
  type(w, d.querySelector("#bl-ihq"), CODE);
  ok(rowsOf(d).length === 1, "the Client Ref no. finds exactly that line");
  type(w, d.querySelector("#bl-ihq"), "zzzz nothing");
  ok(rowsOf(d).length === 0 && /Nothing matches/.test(T(d, "#bl-ihres")), "no match says so");
  type(w, d.querySelector("#bl-ihq"), idle.desc.split(" ").slice(0, 2).join(" "));
  const idleRow = rowsOf(d).find((r) => r.textContent.includes(idle.item_code));
  ok(!!idleRow && /Not billed yet/.test(idleRow.textContent), "a BOQ line nobody has billed is still found, and says 'Not billed yet'");
  click(w, idleRow);
  ok(/Not in any RA yet/.test(T(d, ".bl-ih-ans")) && /No RA has this line yet/.test(T(d, ".bl-ih-card")) && /No challan for this line/.test(T(d, ".bl-ih-card")), "its card says so, with no invented number");

  // chips
  type(w, d.querySelector("#bl-ihq"), "");
  click(w, d.querySelector('[data-ihchip="over"]'));
  const over = FX.ch.Plumbing.over.map((o) => o.item_code).sort();
  ok(rowsOf(d).length === over.length && /Above BOQ · 3/.test(T(d, "#bl-ihchips")), "the 'Above BOQ' chip lists the 3 lines over their BOQ");
  click(w, d.querySelector('[data-ihchip="draft"]'));
  ok(rowsOf(d).every((r) => /RA-02/.test(r.textContent)) && rowsOf(d).length > 0, "'In open draft' lists lines in the RA-02 draft");

  // ------------------------------------------------------------ the "Check" notes open the item
  ({ window: w, d, calls } = await boot());
  const links = [...d.querySelectorAll("#billing .bl-note.over [data-ihopen]")];
  ok(links.length === 3 && links.every((b) => /See its history/.test(b.textContent)), "each Check note has 'See its history →'");
  ok(/accept the extra inside the RA/.test(T(d, ".bl-note.over")), "the Check note's own words are unchanged");
  click(w, links.find((b) => b.dataset.ihopen === "3.01.4"));
  await sleep(30);
  ok(/50 mm \(Urinal\)/.test(T(d, ".bl-ih-hd h3")) && /Total 540 of BOQ 351 rmt · 189 above the BOQ/.test(T(d, ".bl-ih-ans")), "the note opens that line: 540 of BOQ 351, 189 above: " + T(d, ".bl-ih-ans"));
  ok(/RA-01 210/.test(T(d, ".bl-ih-ans")) && /RA-02 draft 330/.test(T(d, ".bl-ih-ans")), "RA-1 210, RA-2 330");
  click(w, d.querySelector("[data-ihback]"));
  ok(!d.querySelector("#bl-ihroot").classList.contains("has-sel") && /Pick an item/.test(T(d, "#bl-ihcard")), "Back to search closes it");

  // ------------------------------------------------------------ all services
  type(w, d.querySelector("#bl-ihq"), "32 mm");
  ok(!rowsOf(d).some((r) => /HVAC/.test(r.textContent)), "HVAC lines are not mixed in while the search is on this service");
  click(w, d.querySelector('[data-ihscope="all"]'));
  await sleep(40);
  ok(calls.some((c) => c.u === `${P}/item-history/HVAC`), "All services fetches the other services' history");
  const hv = rowsOf(d).find((r) => /HVAC/.test(r.textContent) && /32 mm dia/.test(r.textContent));
  ok(!!hv && /Above BOQ/.test(hv.textContent), "an HVAC line is found, with its service named, and flagged above BOQ");
  click(w, hv);
  ok(/HVAC/.test(T(d, ".bl-ih-hd small")) && /Total 857\.7 of BOQ 2\.5 mtr|Total 857\.7 of BOQ 2\.5 rmt/i.test(T(d, ".bl-ih-ans")), "its card: 857.7 billed against a BOQ of 2.5: " + T(d, ".bl-ih-ans"));

  // ------------------------------------------------------------ the stock register strip
  ({ window: w, d, calls } = await boot());
  ok(!!d.querySelector("#bl-ib .bl-ibslim") && !d.querySelector("#bl-ib .bl-ibcard") && /0 waiting/.test(T(d, "#bl-ib")), "with nothing waiting the stock register is one slim line");
  ok(/Nothing new for/.test(T(d, "#bl-ib .bl-ibslim")), "and says why: " + T(d, "#bl-ib .bl-ibslim"));
  click(w, d.querySelector("#bl-ibopen"));
  ok(!!d.querySelector("#bl-ib .bl-ibcard") && !d.querySelector("#bl-ib .bl-ibslim"), "Details opens the full register card, as before");

  // ------------------------------------------------------------ a failing history never breaks the page
  ({ window: w, d, calls } = await boot({ failHist: "Plumbing" }));
  ok(/could not be loaded/.test(T(d, "#bl-ihres")) && d.querySelectorAll(".bl-ch").length === FX.ch.Plumbing.challans.length, "if the history cannot load, the card says so and the challan list still works");

  console.log(`item history ui: ${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
}
main().catch((e) => { console.error(e); process.exit(1); });
