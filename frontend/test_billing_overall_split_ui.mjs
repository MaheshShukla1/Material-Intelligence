// jsdom test: the overall annexure read as Supply and Installation.
// Reported scenario: one Work Order whose "In draft" holds a Supply draft AND an Installation draft; the annexure never said how much
// of it was Supply and how much Installation. Every payload is a REAL backend response (billing_overall_split_fixture.json, built by
// backend/make_overall_split_fixture.py): "split" = Supply RA 1 invoiced + Supply RA 2 draft + Installation RA 1 draft,
// "together" = one Together bill, "unset" = Installation % never set.
// Design: nothing is repeated down the table. The In draft card says it once, a group opens (chevron) into its Supply and
// Installation rows, each open draft has its own chip, the black total strip is unchanged.
import { JSDOM } from "jsdom";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
let pass = 0, fail = 0;
function ok(cond, msg) { if (cond) pass++; else { fail++; console.error("FAIL:", msg); } }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const FX = JSON.parse(fs.readFileSync(path.join(__dirname, "billing_overall_split_fixture.json"), "utf8"));
const PK = JSON.parse(fs.readFileSync(path.join(__dirname, "billing_packages_fixture.json"), "utf8"));
const clone = (x) => JSON.parse(JSON.stringify(x));
const P = "/api/billing/test-thoth";
const T = (el) => (el ? el.textContent.replace(/\s+/g, " ").trim() : "");
const fmt = (v) => "₹ " + Math.round(v).toLocaleString("en-IN");
const click = (w, el) => el.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));

async function boot(scn, mod) {
  const S = clone(FX[scn]);
  mod && mod(S);
  const html = `<!doctype html><html><body>
    <header class="top" id="top"><div class="brand"><p id="ctx"></p></div>
      <div class="topact"><button id="home"></button><button id="pick"></button><button id="sync"></button><button id="syncgo"></button></div></header>
    <main><section id="drop" hidden></section><section id="busy" hidden></section>
      <section id="report"><div class="bar"><div class="tabs" id="svc"></div></div></section></main></body></html>`;
  const dom = new JSDOM(html, { url: "https://example.test/", runScripts: "dangerously", pretendToBeVisual: true });
  const { window } = dom; const calls = [];
  window.__BL_LIVE_MS = 3600000;
  window.scrollTo = () => {};
  window.fetch = (url, opts) => {
    const u = String(url), method = (opts && opts.method) || "GET";
    calls.push({ u, method });
    let res = {};
    if (u === "/api/projects") res = [{ slug: "test-thoth", project: "Thoth", runs: 1, latest_run: "r1" }];
    else if (u === P + "/auto") res = { created: [] };
    else if (u === P) res = S.ov;
    else if (u === P + "/setup" && method === "GET") res = clone(PK.setup);
    else if (u === P + "/overall/Fire") res = S.overall;
    else if (/\/ra\/[^/]+$/.test(u) && method === "GET") res = S.bills[u.split("/").pop()] || { ok: true };
    else if (u.startsWith(P + "/ra/")) res = { ok: true };
    return Promise.resolve({ ok: true, status: 200, json: async () => res, text: async () => JSON.stringify(res) });
  };
  window.URL.createObjectURL = () => "blob:x"; window.URL.revokeObjectURL = () => {};
  window.eval(fs.readFileSync(path.join(__dirname, "siteprogress.js"), "utf8"));
  window.eval(fs.readFileSync(path.join(__dirname, "billing.js"), "utf8"));
  await sleep(0);
  const d = window.document;
  [...d.querySelectorAll(".spnav button")].find((b) => b.dataset.v === "billing").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(80);
  const link = d.querySelector('[data-overall="Fire"]');
  if (link) { click(window, link); await sleep(60); }
  return { window, d, calls, S, o: S.overall, u: S.overall.units[0] };
}
const groupRows = (d) => [...d.querySelectorAll(".bl-wob .bl-or:not(.tot):not(.sub)")];
const subRows = (d) => [...d.querySelectorAll(".bl-wob .bl-or.sub")];
const kpi = (d, label) => [...d.querySelectorAll(".bl-kpi")].find((k) => T(k.querySelector("span")) === label);

(async () => {
  // =================================================== the reported scenario
  {
    const { window, d, calls, o, u } = await boot("split");
    const sup = u.stages.supply, ins = u.stages.install;
    ok(/Fire, overall annexure/.test(T(d.querySelector("#billing h2"))), "the overall annexure opens");
    ok(sup.draft > 0 && ins.draft > 0 && sup.invoiced > 0 && ins.invoiced === 0, "fixture is the reported scenario: Supply invoiced, Supply + Installation drafts");

    // 1. the In draft card answers "how much of the draft is Supply and how much Installation" once
    const kd = kpi(d, "In draft");
    ok(T(kd.querySelector("b")) === fmt(o.totals.draft), "In draft card keeps the total: " + T(kd.querySelector("b")));
    ok(!!kd.querySelector(".bl-nibar"), "In draft card has the two-colour Supply / Installation bar");
    const key = T(kd.querySelector(".bl-nikey"));
    ok(key.includes(`Supply ${fmt(sup.draft)}`) && key.includes(`Installation ${fmt(ins.draft)}`), "In draft card names both parts: " + key);
    const ki = kpi(d, "Invoiced");
    ok(!ki.querySelector(".bl-nibar") && /1 RA invoiced · all Supply/.test(T(ki.querySelector("i"))), "Invoiced card: only Supply is invoiced, said in words: " + T(ki.querySelector("i")));

    // 2. the table stays as it was: nothing repeated per row, one chevron per group
    ok(groupRows(d).length === u.rows.filter((r) => r.billed).length && groupRows(d).length >= 1, "only groups with something billed are listed");
    ok(subRows(d).length === 0, "collapsed by default: no Supply / Installation rows");
    ok(groupRows(d).every((r) => { const b = r.querySelector("[data-ovx]"); return b && b.getAttribute("aria-expanded") === "false" && /Show Supply and Installation of/.test(b.getAttribute("aria-label")); }), "every group has a chevron, closed, with a real label");
    ok(!d.querySelector(".bl-ovtot").textContent.includes("Supply") && !d.querySelector(".bl-ovtot").textContent.includes("Installation"), "the black total strip is unchanged: no Supply / Installation text in it");
    const strip = [...d.querySelectorAll(".bl-ovtot div")].map((x) => T(x));
    ok(strip.length === 5 && strip.some((t) => t === "In draft" + fmt(o.totals.draft)), "the strip still reads Work Order, Invoiced, In draft, Billed to date, Done: " + strip.join(" | "));

    // 3. one chip per OPEN draft, each with its stage and its amount, each opens its own RA
    const chips = [...d.querySelectorAll(".bl-wohd .bl-rach")];
    ok(chips.length === 2, "two open drafts, two chips (not just the latest): " + chips.map(T));
    ok(T(chips[0]).startsWith("Supply RA 2 · Draft") && T(chips[0]).includes(fmt(sup.draft)), "Supply draft chip: " + T(chips[0]));
    ok(T(chips[1]).startsWith("Installation RA 1 · Draft") && T(chips[1]).includes(fmt(ins.draft)), "Installation draft chip: " + T(chips[1]));
    ok(chips[0].classList.contains("k-sup") && chips[1].classList.contains("k-ins"), "chips carry the stage colour classes");
    ok(!d.querySelector(".bl-wohd .bl-link[data-ra]"), "the 'Open draft →' link is replaced by the chips");
    click(window, chips[1]); await sleep(40);
    ok(calls.some((c) => c.u === `${P}/ra/${FX.split.ids.i1}` && c.method === "GET"), "the Installation chip opens the Installation draft");
    await boot("split").then(async (b) => {
      const w2 = b.window, d2 = b.d;
      // 4. open one group: its two stages, the numbers the server sent, Done against the stage's own share
      const first = groupRows(d2)[0];
      const idx = u.rows.findIndex((r) => r.billed);
      const row = u.rows[idx];
      click(w2, first.querySelector("[data-ovx]")); await sleep(5);
      const subs = subRows(d2);
      ok(subs.length === 2, "opening a group shows exactly its Supply and Installation rows");
      ok(/Supply/.test(T(subs[0].querySelector(".gn"))) && /Installation/.test(T(subs[1].querySelector(".gn"))), "labelled Supply then Installation");
      const nums = (r) => [...r.querySelectorAll(".n")].map((x) => T(x));
      const sv = row.stages.supply, iv = row.stages.install;
      const [swo, sinv, sdrf, std, sdone] = nums(subs[0]);
      ok(swo === fmt(sv.wo) && sinv === fmt(sv.invoiced) && sdrf === fmt(sv.draft) && std === fmt(sv.invoiced + sv.draft), "Supply row = the server's Supply numbers: " + nums(subs[0]));
      const [iwo, iinv, idrf, itd, idone] = nums(subs[1]);
      ok(iwo === fmt(iv.wo) && iinv === fmt(iv.invoiced) && idrf === fmt(iv.draft) && itd === fmt(iv.invoiced + iv.draft), "Installation row = the server's Installation numbers: " + nums(subs[1]));
      const pct = (v, w) => { const r = Math.round(v / w * 1000) / 10; return r + "%"; };
      ok(sdone === pct(sv.invoiced + sv.draft, sv.wo) && idone === pct(iv.invoiced + iv.draft, iv.wo), "Done is against each stage's own share: " + sdone + " / " + idone);
      ok(sv.invoiced + iv.invoiced === row.invoiced || Math.abs(sv.invoiced + iv.invoiced - row.invoiced) < 0.5, "the two stages add back to the group's Invoiced");
      ok(Math.abs(sv.draft + iv.draft - row.draft) < 0.5, "…and to the group's In draft");
      ok(d2.activeElement && d2.activeElement.dataset.ovx === first.querySelector("[data-ovx]").dataset.ovx, "focus stays on the chevron after the repaint");
      ok(d2.querySelector("[data-ovx]").getAttribute("aria-expanded") === "true", "chevron says it is open");
      // 5. open / close every group at once
      const allBtn = d2.getElementById("bl-ovall");
      ok(!!allBtn && /Open all groups|Close all groups/.test(T(allBtn)), "an open-all link exists: " + T(allBtn));
      const n = groupRows(d2).length;
      if (T(allBtn).startsWith("Open")) click(w2, allBtn);
      await sleep(5);
      ok(subRows(d2).length === 2 * n && /Close all groups/.test(T(d2.getElementById("bl-ovall"))), "Open all groups opens every group");
      click(w2, d2.getElementById("bl-ovall")); await sleep(5);
      ok(subRows(d2).length === 0 && /Open all groups/.test(T(d2.getElementById("bl-ovall"))), "Close all groups closes them again");
      // 6. Invoiced only: no In draft column, in the group rows or the stage rows
      click(w2, first.querySelector("[data-ovx]")); await sleep(5);
      click(w2, d2.querySelector('[data-ovm="inv"]')); await sleep(5);
      const grpsInv = groupRows(d2);
      if (grpsInv.length) {
        click(w2, grpsInv[0].querySelector("[data-ovx]")); await sleep(5);
        ok(subRows(d2).every((r) => !r.querySelector(".drf")), "Invoiced only: stage rows have no In draft cell");
      } else ok(true, "(no invoiced group rows to open)");
      ok(!kpi(d2, "In draft"), "Invoiced only: the In draft card is gone");
    });
  }

  // =================================================== one Together bill: counted once, read in two parts
  {
    const { d, u, o } = await boot("together");
    const chips = [...d.querySelectorAll(".bl-wohd .bl-rach")];
    ok(chips.length === 1 && chips[0].classList.contains("k-tog") && /Supply \+ Installation RA 1 · Draft/.test(T(chips[0])), "a Together draft is one chip: " + chips.map(T));
    const kd = kpi(d, "In draft");
    ok(T(kd.querySelector("b")) === fmt(o.totals.draft), "In draft total is the Together bill once: " + T(kd.querySelector("b")));
    const tk = T(kd.querySelector(".bl-nikey"));
    ok(tk.includes(`Supply ${fmt(u.stages.supply.draft)}`) && tk.includes(`Installation ${fmt(u.stages.install.draft)}`), "…and it is read out as its two parts: " + tk);
  }

  // =================================================== Installation % not set: a dash, never a number
  {
    const { window, d, u, o } = await boot("unset");
    const kd = kpi(d, "In draft");
    ok(!kd.querySelector(".bl-nibar") && /all Supply/.test(T(kd.querySelector("i"))), "only Supply has a draft: no bar, said in words: " + T(kd.querySelector("i")));
    click(window, groupRows(d)[0].querySelector("[data-ovx]")); await sleep(5);
    const [s, i] = subRows(d);
    const nums = (r) => [...r.querySelectorAll(".n")].map((x) => T(x));
    ok(nums(i).every((x) => x === "—"), "Installation row: every figure is a dash: " + nums(i));
    ok(/Installation % not set/.test(T(i.querySelector(".gn"))), "…and it says why: " + T(i.querySelector(".gn")));
    ok(nums(s)[0] !== "—" && nums(s)[2] !== "—", "Supply row is real: " + nums(s));
  }

  // =================================================== a server that does not send the stage cut yet: nothing breaks, nothing is drawn
  {
    const { d, o } = await boot("split", (S) => {
      delete S.overall.totals.stages;
      S.overall.units.forEach((u) => { delete u.stages; u.rows.forEach((r) => delete r.stages); u.ras.forEach((r) => delete r.by_stage); });
    });
    ok(/Fire, overall annexure/.test(T(d.querySelector("#billing h2"))), "older payload still renders the annexure");
    ok(!d.querySelector("[data-ovx]") && !d.getElementById("bl-ovall") && !d.querySelector(".bl-kpi .bl-nibar"), "…with no chevrons, no open-all link, no split bar");
    ok(T(d.querySelector(".bl-ovnote")).indexOf("Open a group") === -1, "…and the note does not talk about opening groups");
  }

  // =================================================== phone: the chevron becomes a full-width 44px button with words
  {
    const css = fs.readFileSync(path.join(__dirname, "billing.css"), "utf8");
    const media = css.slice(css.indexOf("@media(max-width:760px){", css.indexOf(".bl-ovnote")));
    ok(/\.bl-or \.ex \.lbl\{display:none\}/.test(css), "desktop: the chevron is an icon only");
    ok(/\.bl-or \.ex\{grid-column:1\/-1\}/.test(media) && /\.bl-or \.ex \.bl-chev\{width:100%;height:44px/.test(media) && /\.bl-or \.ex \.lbl\{display:inline\}/.test(media), "phone: full-width 44px 'Supply · Installation' button");
    ok(/\.bl-rach\{min-height:44px;width:100%/.test(media), "phone: draft chips are full-width 44px buttons");
  }

  // class names of this screen must not collide with a global rule (siteprogress.css has `.sp{width:min(1480px,…)}`, which once stretched the chips)
  {
    const globalCss = fs.readFileSync(path.join(__dirname, "siteprogress.css"), "utf8") + fs.readFileSync(path.join(__dirname, "style.css"), "utf8");
    const mine = ["bl-stp", "bl-rach", "k-sup", "k-ins", "k-tog"];
    ok(mine.every((c) => !new RegExp("(^|[^\\w-])\\." + c + "([^\\w-]|$)").test(globalCss)), "this screen's class names are not used by any global stylesheet");
    ok(!/\.bl-(stp|rach)\.(sp|in|tg)\b/.test(fs.readFileSync(path.join(__dirname, "billing.css"), "utf8")), "no stage modifier is called sp / in / tg (siteprogress.css owns .sp)");
  }

  console.log(`billing overall split UI: ${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
})();
