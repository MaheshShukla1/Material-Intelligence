// jsdom test: Work Orders (packages). Electrical is billed under three Work Orders, each with its OWN RA 1; the home screen shows one
// tile per Work Order, the overall annexure adds them up (invoiced / draft / to date), Billing setup splits a service into Work Orders.
// Every payload is a REAL backend response (billing_packages_fixture.json, built by make_packages_fixture.py from the real Thoth BOQ).
import { JSDOM } from "jsdom";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
let pass = 0, fail = 0;
function ok(cond, msg) { if (cond) pass++; else { fail++; console.error("FAIL:", msg); } }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const FX = JSON.parse(fs.readFileSync(path.join(__dirname, "billing_packages_fixture.json"), "utf8"));
const clone = (x) => JSON.parse(JSON.stringify(x));
const { EX, PW, OT } = FX.units;
const P = "/api/billing/thoth-mall";

async function boot(over) {
  const html = `<!doctype html><html><body>
    <header class="top" id="top"><div class="brand"><p id="ctx"></p></div>
      <div class="topact"><button id="home"></button><button id="pick"></button><button id="sync"></button><button id="syncgo"></button></div></header>
    <main><section id="drop" hidden></section><section id="busy" hidden></section>
      <section id="report"><div class="bar"><div class="tabs" id="svc"></div></div></section></main></body></html>`;
  const dom = new JSDOM(html, { url: "https://example.test/", runScripts: "dangerously", pretendToBeVisual: true });
  const { window } = dom; const calls = [];
  window.__BL_LIVE_MS = 3600000;
  window.scrollTo = () => {};
  const state = { ov: clone(FX.ov), overall: clone(FX.overall), setup: clone(FX.setup), pk: clone(FX.packages_plain), ...over };
  window.fetch = (url, opts) => {
    const u = String(url), method = (opts && opts.method) || "GET";
    const body = opts && opts.body ? JSON.parse(opts.body) : null;
    calls.push({ u, method, body });
    let res = {}, status = 200;
    if (u === "/api/projects") res = [{ slug: "thoth-mall", project: "Thoth Mall", runs: 1, latest_run: "r1" }];
    else if (u === P + "/auto") res = { created: [] };
    else if (u === P) res = state.ov;
    else if (u === P + "/setup" && method === "GET") res = state.setup;
    else if (u === P + "/setup") res = { ok: true };
    else if (u === P + "/overall/Electrical") res = state.overall;
    else if (u === P + "/overall/HVAC") res = clone(FX.overall_hvac);
    else if (u.startsWith(P + "/overall/Electrical/xlsx")) return Promise.resolve({ ok: true, status: 200, headers: { get: (h) => h === "Content-Disposition" ? 'attachment; filename="thoth-mall_Electrical_overall_annexure.xlsx"' : null }, blob: async () => new window.Blob(["x"]) });
    else if (u === P + "/packages/Electrical" && method === "GET") res = state.pk;
    else if (u === P + "/packages/HVAC" && method === "GET") res = clone(FX.packages_hvac);
    else if (u === P + "/packages/Electrical") { if (state.fail) { status = 409; res = { detail: state.fail }; } else res = { ok: true, units: [], removed_drafts: state.removed || 0 }; }
    else if (u === P + "/meta") res = { ok: true };
    else if (/\/ra\/[^/]+$/.test(u) && method === "GET") res = FX.bills[u.split("/").pop()] || { ok: true };
    else if (u.startsWith(P + "/ra/")) res = { ok: true };
    return Promise.resolve({ ok: status < 400, status, json: async () => res, text: async () => JSON.stringify(res) });
  };
  window.URL.createObjectURL = () => "blob:x"; window.URL.revokeObjectURL = () => {};
  window.eval(fs.readFileSync(path.join(__dirname, "siteprogress.js"), "utf8"));
  window.eval(fs.readFileSync(path.join(__dirname, "billing.js"), "utf8"));
  await sleep(0);
  const d = window.document;
  [...d.querySelectorAll(".spnav button")].find((b) => b.dataset.v === "billing").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(80);
  return { window, d, calls, state };
}
const document_has = (d, el) => d.body.contains(el);
const click = (w, el) => el.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
const change = (w, el) => el.dispatchEvent(new w.Event("change", { bubbles: true }));
const input = (w, el) => el.dispatchEvent(new w.Event("input", { bubbles: true }));
const T = (d, sel) => (d.querySelector(sel) || { textContent: "" }).textContent.replace(/\s+/g, " ").trim();

async function main() {
  // ---------------------------------------------------------------- home
  let { window, d, calls } = await boot();
  const sec = d.getElementById("billing");
  const units = [...sec.querySelectorAll(".bl-hr")];
  const elec = units.filter((t) => /^Electrical › /.test(T(t, ".nm b")));
  ok(elec.length === 3, "Electrical shows one row per Work Order (3)");
  ok(elec.map((t) => T(t, ".nm b")).join("|") === "Electrical › Excl. point wiring|Electrical › Only point wiring|Electrical › Other Electrical", "rows are named by Work Order: " + elec.map((t) => T(t, ".nm b")));
  const tile = (n) => units.find((t) => T(t, ".nm b") === "Electrical › " + n);
  ok(/RA 1/.test(tile("Excl. point wiring").textContent) && /Draft/.test(tile("Excl. point wiring").textContent), "Excl. point wiring has its own RA 1, Draft");
  ok(!!tile("Excl. point wiring").querySelector(".act [data-ra]") && /Open draft/.test(T(tile("Excl. point wiring"), ".act")), "a draft row's main button is Open draft →");
  ok(/Nothing to bill yet|Ready/.test(tile("Other Electrical").querySelector(".stt").textContent), "a Work Order without a bill says what it has: " + T(tile("Other Electrical"), ".stt"));
  ok(/Supply 75% · Install 15%/.test(tile("Only point wiring").textContent), "each row carries its own payment term");
  ok(elec.every((t) => !!t.querySelector('[data-overall="Electrical"]')), "every Work Order row links to the Electrical overall annexure");
  ok(!!sec.querySelector('[data-overall="HVAC"]') && !/Split into Work Orders/.test(sec.querySelector("#bl-svcl").textContent), "a plain service row keeps Annexure → and shows no split text");
  ok(sec.querySelectorAll("[data-wo]").length === 0, "Work Orders… is no longer a loose link on the home screen");
  click(window, tile("Excl. point wiring").querySelector("[data-menu]")); await sleep(5);
  ok(!!sec.querySelector(".bl-rowmenu [data-mwo]") && !!sec.querySelector(".bl-rowmenu [data-msetup]"), "the ··· menu of a split service offers Work Orders… and Billing setup");
  click(window, d.body); await sleep(10);
  ok(!sec.querySelector(".bl-rowmenu"), "clicking elsewhere closes the menu");
  const hv = units.find((t) => T(t, ".nm b") === "HVAC");
  click(window, hv.querySelector("[data-menu]")); await sleep(5);
  ok(!sec.querySelector(".bl-rowmenu [data-mwo]") && !!sec.querySelector(".bl-rowmenu [data-msetup]"), "a plain service's ··· menu has Billing setup only (no Work Orders…)");
  click(window, d.body); await sleep(10);
  ok(sec.querySelectorAll("[data-overall]").length === units.length, "every row has exactly one Annexure → link");
  ok(/5 services · 7 Work Orders/.test(T(sec, ".bl-kpi i")) || /5 services · 7 Work Orders/.test(sec.textContent), "KPI counts services and Work Orders: " + [...sec.querySelectorAll(".bl-kpi i")].map((x) => x.textContent));
  ok(units.length === 7, "one row per billing unit (7)");
  const rows = [...sec.querySelectorAll(".bl-rar")];
  ok(rows.some((r) => /Electrical › Only point wiring · RA 1/.test(r.textContent)) && rows.some((r) => /Electrical › Excl. point wiring · RA 1/.test(r.textContent)),
     "the RA list names both RA 1s by Work Order: " + rows.map((r) => T(r, "b")).join(" ; "));
  // a tile's RA link opens that RA
  // the RA list row opens that RA (an invoiced Work Order has no draft to open from its service row)
  click(window, [...sec.querySelectorAll(".bl-rar")].find((r) => /Only point wiring · RA 1/.test(r.textContent))); await sleep(30);
  ok(calls.some((c) => c.u === `${P}/ra/${FX.ids.pw}`), "the RA list row opens that Work Order's RA");

  // ---------------------------------------------------------------- overall annexure
  ({ window, d, calls } = await boot());
  click(window, d.querySelector('.bl-hr [data-overall="Electrical"]')); await sleep(60);
  const s2 = d.getElementById("billing");
  ok(/Electrical, overall annexure/.test(T(s2, "h2")), "overall screen title");
  ok(calls.some((c) => c.u === P + "/overall/Electrical"), "it reads /overall/Electrical");
  const O = FX.overall;
  const kp = [...s2.querySelectorAll(".bl-kpi b")].map((x) => x.textContent.replace(/\s+/g, " ").trim());
  const fmt = (v) => "₹ " + Math.round(v).toLocaleString("en-IN");
  ok(kp[0] === fmt(O.totals.wo_value) && kp[1] === fmt(O.totals.invoiced) && kp[2] === fmt(O.totals.draft), "KPIs: Work Order value / Invoiced / In draft: " + kp.join(" | "));
  ok(kp[3].startsWith(fmt(O.totals.todate)), "KPI: billed to date = invoiced + draft");
  const blocks = [...s2.querySelectorAll(".bl-wob")];
  ok(blocks.length === 3 && blocks.map((b) => T(b, ".bl-wohd b")).join("|") === "Excl. point wiring|Only point wiring|Other Electrical", "one block per Work Order");
  const pwB = blocks[1];
  ok(/RA 1 · Invoiced/.test(T(pwB, ".bl-wohd .bl-chip")) && /RA 1 · Draft/.test(T(blocks[0], ".bl-wohd .bl-chip")) && /No RA yet/.test(T(blocks[2], ".bl-wohd .bl-chip")), "each Work Order header shows its latest RA and its status");
  // groups with nothing billed are hidden, with a way to show them
  const names = (b) => [...b.querySelectorAll(".bl-or:not(.tot) .gn")].map((x) => x.textContent);
  ok(names(pwB).length === O.units[1].rows.filter((r) => r.billed).length && names(pwB).length >= 1, "only groups with something billed are listed: " + names(pwB));
  ok(/Groups with nothing billed are hidden/.test(T(s2, "#bl-ovh")) && !!d.getElementById("bl-ovshow"), "a hidden-groups note with a show link");
  ok(names(blocks[2]).length === 0 && /Nothing billed in this Work Order yet/.test(blocks[2].textContent), "a Work Order with nothing billed says so");
  click(window, d.getElementById("bl-ovshow")); await sleep(5);
  ok(names([...d.querySelectorAll(".bl-wob")][2]).length === O.units[2].rows.length, "Show hidden groups lists every group of the Work Order");
  click(window, d.getElementById("bl-ovhide")); await sleep(5);
  // subtotal of a Work Order equals the server's figures
  const sub = (b) => [...b.querySelectorAll(".bl-or.tot .n")].map((x) => x.textContent.replace(/\s+/g, " ").trim());
  const u1 = O.units[1];
  ok(sub([...d.querySelectorAll(".bl-wob")][1]).join("|").includes(fmt(u1.wo_value)) && sub([...d.querySelectorAll(".bl-wob")][1]).join("|").includes(fmt(u1.invoiced)), "subtotal row = the Work Order's figures");
  ok(/Electrical, all Work Orders/.test(T(d, ".bl-ovtot")) && T(d, ".bl-ovtot").includes(fmt(O.totals.todate).replace(/\s/g, " ")), "grand total row");
  // Invoiced only: draft disappears everywhere and billed-to-date drops to invoiced
  click(window, d.querySelector('[data-ovm="inv"]')); await sleep(5);
  const s3 = d.getElementById("billing");
  ok(![...s3.querySelectorAll(".bl-kpi span")].some((x) => /In draft/.test(x.textContent)) && !/In draft/.test(T(s3, ".bl-oh")), "Invoiced only: no In draft KPI or column");
  ok([...s3.querySelectorAll(".bl-kpi b")].map((x) => x.textContent).some((t) => t.startsWith(fmt(O.totals.invoiced))), "Invoiced only: billed to date = invoiced");
  ok(names([...s3.querySelectorAll(".bl-wob")][0]).length === 0, "Invoiced only: a Work Order that only has a draft shows nothing billed");
  click(window, d.querySelector('[data-ovm="all"]')); await sleep(5);
  ok([...d.querySelectorAll(".bl-kpi span")].some((x) => /In draft/.test(x.textContent)), "Invoiced + drafts brings it back");
  // download
  click(window, d.getElementById("bl-ovdl")); await sleep(30);
  ok(calls.some((c) => c.u === `/api/billing/thoth-mall/overall/Electrical/xlsx?drafts=1`), "Download hits the xlsx endpoint with drafts=1");
  click(window, d.querySelector('[data-ovm="inv"]')); await sleep(5);
  click(window, d.getElementById("bl-ovdl")); await sleep(30);
  ok(calls.some((c) => c.u === `/api/billing/thoth-mall/overall/Electrical/xlsx?drafts=0`), "…and drafts=0 when Invoiced only");
  ok(/Downloaded thoth-mall_Electrical_overall_annexure/.test(d.getElementById("bl-toast").textContent), "toast names the downloaded file");
  // links from a Work Order block
  click(window, d.querySelector('.bl-wob [data-ra]')); await sleep(30);
  ok(calls.filter((c) => /\/ra\/ra\d+$/.test(c.u)).length >= 1, "a Work Order block opens its RA");
  click(window, d.getElementById("bl-toverall")); await sleep(60);
  ok(/overall annexure/.test(T(d, "#billing h2")), "the RA screen has an Overall annexure button");
  click(window, d.getElementById("bl-back")); await sleep(60);
  ok(!!d.querySelector(".bl-hr"), "← Billing returns to the home service rows");

  // ---------------------------------------------------------------- setup lists Work Orders and edits them
  ({ window, d, calls } = await boot());
  click(window, d.getElementById("bl-setup")); await sleep(60);
  const names2 = [...d.querySelectorAll(".bl-sr")].map((r) => T(r, "b"));
  ok(names2.join("|") === FX.setup.services.map((x) => x.package ? x.base + " › " + x.package : x.service).join("|") && names2.length === 7, "setup lists one row per Work Order: " + names2);
  const wolinks = [...d.querySelectorAll('.bl-sr [data-wo="Electrical"]')];
  ok(wolinks.length === 1 && /Work Orders…/.test(wolinks[0].textContent), "one 'Work Orders…' link for the split service, not one per row");
  ok(!d.querySelector('.bl-sr [data-wo="HVAC"]') && !/Split into Work Orders/.test(T(d, ".bl-sets")), "plain service rows in setup carry no split link");
  const sel = d.getElementById("bl-wosel");
  ok(!!sel && [...sel.options].map((o) => o.textContent).join("|") === FX.setup.services.filter((x) => !x.package).map((x) => x.service).join("|"), "ONE place to split: a card in setup listing the services that are not split yet");

  // edit the split Electrical: modal lists the names and every group with its Work Order
  window.fetch = ((orig) => (u, o) => String(u) === P + "/packages/Electrical" && !(o && o.method) ? Promise.resolve({ ok: true, status: 200, json: async () => clone(FX.packages_split), text: async () => "" }) : orig(u, o))(window.fetch);
  click(window, wolinks[0]); await sleep(60);
  const m = d.getElementById("bl-modal");
  ok(!!m && /Work Orders · Electrical/.test(m.textContent), "Work Orders dialog opens");
  const nameIn = [...m.querySelectorAll("[data-name]")].map((x) => x.value);
  ok(nameIn.join("|") === "Excl. point wiring|Only point wiring|Other Electrical", "names pre-filled from the saved Work Orders: " + nameIn);
  ok([...m.querySelectorAll("[data-rest]")].findIndex((x) => x.checked) === 2, "'Takes the rest' marks the remainder Work Order");
  const gsel = [...m.querySelectorAll("[data-g]")];
  ok(gsel.length === FX.packages_split.groups.length, "one selector per annexure group");
  const gi = (nm) => FX.packages_split.groups.findIndex((g) => g.name.startsWith(nm));
  ok(gsel[gi("POINT WIRING")].selectedOptions[0].textContent.startsWith("Only point wiring") && gsel[gi("Cable Tray")].selectedOptions[0].textContent.startsWith("Excl. point wiring"), "groups show the Work Order they are in");
  const busy = [...m.querySelectorAll(".bl-busy")].map((x) => x.textContent.trim());
  ok(busy.join("|") === "1 RA · 0 challans: its groups are fixed|1 RA · 3 challans: its groups are fixed", "Work Orders that already bill say so, and the free one does not: " + busy);
  // a busy Work Order's groups cannot be moved out, and no group can be moved into it
  ok(gsel[gi("POINT WIRING")].disabled === false && [...gsel[gi("Earthing")].options].find((o) => /Only point wiring/.test(o.textContent)).disabled, "cannot move a group INTO a Work Order that already bills");
  // rename + save posts the whole definition with `was`
  const nin = m.querySelectorAll("[data-name]")[0]; nin.value = "Excl. point wiring (WO-2)"; input(window, nin); change(window, nin); await sleep(5);
  click(window, d.getElementById("bl-wosave")); await sleep(60);
  const post = calls.find((c) => c.u === P + "/packages/Electrical" && c.method === "POST");
  ok(post && post.body.items.length === 3, "Save posts the Work Orders");
  const it = post.body.items;
  ok(it[0].name === "Excl. point wiring (WO-2)" && it[0].was === "Excl. point wiring" && it[0].groups.length === 2, "the renamed Work Order keeps `was` and its two groups");
  ok(it[1].name === "Only point wiring" && it[1].was === "Only point wiring" && it[1].groups.length === 1 && it[1].groups[0].from === FX.packages_split.groups[gi("POINT WIRING")].from, "Only point wiring: its group with real sheet rows");
  ok(it[2].rest === true && it[2].name === "Other Electrical" && !it[2].groups, "the remainder is sent as rest, without groups");
  ok(/Work Orders saved/.test(d.getElementById("bl-toast").textContent) && /payment term/.test(d.getElementById("bl-toast").textContent), "toast, and a pointer to the payment term");
  ok(/Billing setup/.test(T(d, "#billing h2")), "after saving the person lands in Billing setup to set each payment term");

  // ---------------------------------------------------------------- first split of a plain service from the setup screen
  ({ window, d, calls } = await boot());
  click(window, d.getElementById("bl-setup")); await sleep(60);
  d.getElementById("bl-wosel").value = "HVAC"; click(window, d.getElementById("bl-wosplit")); await sleep(60);
  ok(!!d.getElementById("bl-modal") && /Work Orders · HVAC/.test(d.getElementById("bl-modal").textContent), "the card opens the Work Orders dialog for the chosen service");
  click(window, d.getElementById("bl-mx")); await sleep(5);

  // plain service: two blank Work Orders to name; every group starts in the remainder; the server's refusal reaches the person
  const plainSetup = clone(FX.setup); { const first = plainSetup.services.findIndex((x) => x.base === "Electrical"); const u = { ...plainSetup.services[first], service: "Electrical", package: "" };
    plainSetup.services = plainSetup.services.filter((x) => x.base !== "Electrical"); plainSetup.services.splice(first, 0, u); }
  const state2 = { setup: plainSetup, pk: clone(FX.packages_plain), fail: "Electrical RA 1 (Supply + Installation) (invoiced) has lines of more than one Work Order, so it cannot be moved into one. Reopen and delete it first, or keep this service as one Work Order." };
  ({ window, d, calls } = await boot(state2));
  click(window, d.getElementById("bl-setup")); await sleep(60);
  d.getElementById("bl-wosel").value = "Electrical"; click(window, d.getElementById("bl-wosplit")); await sleep(60);
  const m2 = d.getElementById("bl-modal");
  ok([...m2.querySelectorAll("[data-name]")].length === 2 && [...m2.querySelectorAll("[data-name]")].every((x) => x.value === ""), "plain service: two blank Work Orders to name");
  ok([...m2.querySelectorAll("[data-g]")].every((s) => +s.value === 0), "every group starts in the remainder Work Order");
  const nm = [...m2.querySelectorAll("[data-name]")]; nm[0].value = "Excl. point wiring"; input(window, nm[0]);
  ok(d.querySelectorAll("#bl-modal [data-name]")[0] === nm[0] && document_has(d, nm[0]), "typing a name never rebuilds the box under the cursor");
  ok(/^Excl. point wiring \(rest\)$/.test(d.querySelector("#bl-modal [data-g]").options[0].textContent) && /^Work Order 2$/.test(d.querySelector("#bl-modal [data-g]").options[1].textContent), "…but the dropdowns are relabelled at once: " + [...d.querySelector("#bl-modal [data-g]").options].map((o) => o.textContent));
  change(window, nm[0]);
  const nm1 = [...d.querySelectorAll("#bl-modal [data-name]")][1]; nm1.value = "Only point wiring"; input(window, nm1); change(window, nm1); await sleep(5);
  const gs2 = [...d.querySelectorAll("#bl-modal [data-g]")];
  const pwI = FX.packages_plain.groups.findIndex((g) => g.name.startsWith("POINT WIRING"));
  gs2[pwI].value = "1"; change(window, gs2[pwI]); await sleep(5);
  ok(/Only point wiring[^]*1 group/.test(T(d, "#bl-modal .bl-wosum")), "a live summary: groups and value per Work Order");
  click(window, d.getElementById("bl-wosave")); await sleep(60);
  const p2 = calls.find((c) => c.u === P + "/packages/Electrical" && c.method === "POST");
  ok(p2 && p2.body.items[0].rest === true && p2.body.items[1].groups.length === 1 && !p2.body.items[1].was, "first split: remainder + one ticked group, no `was`");
  ok(/more than one Work Order/.test(d.getElementById("bl-toast").textContent) && !!d.getElementById("bl-modal"), "the server's reason is shown and the dialog stays open");

  // ---------------------------------------------------------------- per-Work-Order W/O number in the invoice details
  ({ window, d, calls } = await boot());
  click(window, d.getElementById("bl-setup")); await sleep(60);
  click(window, d.getElementById("bl-idet")); await sleep(60);
  const wono = [...d.querySelectorAll("#bl-modal [data-wono]")];
  ok(wono.length === 3, "a W/O number box for each Work Order (not for plain services)");
  wono[1].value = "WO/PW/1"; input(window, wono[1]);
  click(window, d.getElementById("bl-isave")); await sleep(60);
  const mp = calls.find((c) => c.u === P + "/meta" && c.method === "POST");
  ok(mp && mp.body.wo_of && mp.body.wo_of[PW] === "WO/PW/1" && Object.keys(mp.body.wo_of).length === 3, "saved per Work Order: " + JSON.stringify(mp && mp.body.wo_of));

  console.log(`billing packages UI: ${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
}
main().catch((e) => { console.error(e); process.exit(1); });
