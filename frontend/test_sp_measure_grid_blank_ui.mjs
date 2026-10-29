// jsdom test: the Measurement grid never leaves a box blank -- a number (0 too) means "a quantity can be recorded here", a dash means "it cannot", with the reason.
// Reported (Thoth Mall, screenshot): rows with a "0" in every box, and under them rows whose boxes were simply empty, no explanation.
// Root cause: a BOQ line with no quantity (BOQ qty "—") has no plan, so the backend gives it no zone cells at all; the grid drew them as empty boxes.
// Payload = the REAL router's answer (measure_grid_noqty_fixture.json): 39a..39f and N.1 with a plan; Z.0 (qty 0) and Z.1 (no qty) without one; N.1 is only
// in the first 3 zones (Zones dialog), 39b has 37 m measured in B3 > Zone 2.
import { JSDOM } from "jsdom";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
let pass = 0, fail = 0;
function ok(cond, msg) { if (cond) pass++; else { fail++; console.error("FAIL:", msg); } }
const FX = JSON.parse(fs.readFileSync(path.join(__dirname, "activity_delete_fixture.json"), "utf8"));
const MG = JSON.parse(fs.readFileSync(path.join(__dirname, "measure_grid_fixture.json"), "utf8"));
const NQ = JSON.parse(fs.readFileSync(path.join(__dirname, "measure_grid_noqty_fixture.json"), "utf8"));
const clone = (x) => JSON.parse(JSON.stringify(x));
const flush = async (n = 8) => { for (let i = 0; i < n; i++) await new Promise((r) => setTimeout(r, 0)); };
const SL = FX.slug, SP = "/api/siteprogress/" + SL;
const T = (el) => (el ? el.textContent : "").replace(/\s+/g, " ").trim();
const click = (w, el) => el.dispatchEvent(new w.MouseEvent("click", { bubbles: true, cancelable: true }));
const press = (w, el) => { for (const k of ["mousedown", "mouseup", "click"]) el.dispatchEvent(new w.MouseEvent(k, { bubbles: true, cancelable: true })); };

async function boot(opts = {}) {
  const html = `<!doctype html><html><body>
  <header class="top" id="top"><div class="brand"><h1>Material intelligence</h1><p id="ctx"></p></div>
    <div class="topact"><button id="pick"></button><button id="sync"></button><button id="syncgo"></button><button id="home" hidden></button><input id="file" type="file" hidden></div></header>
  <main><section id="drop"></section><section id="busy" hidden></section><section id="report" hidden><div class="bar"><div class="tabs" id="svc"></div></div></section></main></body></html>`;
  const dom = new JSDOM(html, { url: "http://localhost/", runScripts: "outside-only", pretendToBeVisual: true });
  const { window } = dom; const gets = [];
  const project = clone(FX.project);
  if (opts.structure) project.structure = clone(opts.structure);
  window.confirm = () => true; window.alert = () => {}; window.prompt = () => null;
  window.fetch = async (url, o) => {
    const u = new URL(url, "http://localhost"), method = (o && o.method) || "GET";
    let data = {}, status = 200;
    gets.push(method + " " + u.pathname + u.search);
    if (u.pathname === "/api/projects") data = FX.projects;
    else if (u.pathname === SP) data = project;
    else if (u.pathname === SP + "/service/Fire") data = clone(FX.view_with_empty);
    else if (u.pathname === SP + "/pnl/Fire") data = clone(FX.pnl);
    else if (u.pathname === SP + "/measure-grid/Fire") {
      if (opts.fail) { status = 500; data = { detail: "no BOQ items for service 'Fire'" }; } else data = clone(opts.grid);
    }
    return { ok: status < 400, status, json: async () => data, text: async () => JSON.stringify(data) };
  };
  window.eval(fs.readFileSync(path.join(__dirname, "siteprogress.js"), "utf8"));
  window.eval(fs.readFileSync(path.join(__dirname, "measureview.js"), "utf8"));
  Object.defineProperty(window.document, "readyState", { value: "complete", configurable: true });
  window.document.dispatchEvent(new window.Event("DOMContentLoaded"));
  await flush();
  window.document.querySelector('.spnav button[data-v="siteprogress"]').click();
  await flush(25);
  const d = window.document;
  const sr = () => { const h = d.getElementById("mv-host"); return h ? h.shadowRoot : null; };
  const q = (s) => sr().querySelector(s), qa = (s) => [...sr().querySelectorAll(s)];
  const rowOf = (code) => qa("tr[data-code]").find((r) => r.dataset.code === code);
  return { window, d, gets, sr, q, qa, rowOf };
}

async function main() {
  const grid = clone(NQ); grid.service = "Fire";
  const t = await boot({ structure: MG.thoth.structure, grid });
  click(t.window, t.d.getElementById("sp-measgrid")); await flush(10);
  ok(!!t.sr(), "the view opened");
  click(t.window, t.q("#ow"));                                                  // Only lines with work OFF: every BOQ line, as the engineer in the screenshot sees it
  ok(t.qa("tr[data-code]").length === 9, "all 9 BOQ lines are drawn: " + t.qa("tr[data-code]").length);
  const cells = (code) => [...t.rowOf(code).querySelectorAll("td.c")];
  const txt = (td) => T(td);

  // levels are collapsed to one total each until opened: check that view first (section 4), then open them all
  const collapsed = [...t.qa("tr[data-code]")].map((r) => [r.dataset.code, [...r.querySelectorAll("td.c")].map(T)]);
  click(t.window, t.q("#expand")); await flush(3);

  // 1. EVERY box of EVERY line shows something -- a number or a dash, never empty
  const all = t.qa("tr[data-code] td.c");
  ok(all.length >= 9 * 21, "boxes are drawn: " + all.length);
  ok(all.every((td) => txt(td) !== ""), "no empty box anywhere: " + all.filter((td) => txt(td) === "").length + " empty");

  // 2. a line with a plan: numbers (0 where nothing is done yet); a line without one: a dash in every box, with the reason
  const withPlan = cells("39a").filter((td) => !td.classList.contains("sum"));
  ok(withPlan.length === 21 && withPlan.every((td) => /^\d/.test(txt(td)) && !td.classList.contains("na")), "39a: numbers in every zone: " + withPlan.map(txt).join(","));
  for (const code of ["Z.0", "Z.1"]) {
    const cs = cells(code);
    ok(cs.length === 21 && cs.every((td) => txt(td) === "—" && td.classList.contains("na")), code + ": a dash in every box: " + cs.map(txt).join(","));
    ok(cs.every((td) => /no quantity/i.test(td.title)), code + ": the tooltip says the BOQ line has no quantity: " + cs[0].title);
  }
  ok(T(t.rowOf("Z.1").querySelector("td.q0")) === "—", "its BOQ qty column is a dash as before");

  // 3. an item that is only in some zones: numbers where it is, a dash (not blank) where it is not, a different reason
  const n1 = cells("N.1").filter((td) => !td.classList.contains("sum"));
  const nums = n1.filter((td) => /^\d/.test(txt(td))), dashes = n1.filter((td) => txt(td) === "—");
  ok(nums.length === 3 && nums.length + dashes.length === n1.length && dashes.every((d) => /not in this place/i.test(d.title)), "N.1: 3 numbers, the rest dashes: " + n1.map(txt).join(","));

  // 4. a collapsed level's total: a number when any zone under it holds the item, a dash when none does (read before the levels were opened)
  const col = Object.fromEntries(collapsed);
  ok(col["39a"].length === 3 && col["39a"].every((x) => /^\d/.test(x)), "39a level totals are numbers: " + col["39a"]);
  ok(col["Z.0"].every((x) => x === "—") && col["Z.1"].every((x) => x === "—"), "Z lines' level totals are dashes: " + col["Z.0"] + " | " + col["Z.1"]);
  ok(col["N.1"][0] !== "—" && col["N.1"][1] === "—" && col["N.1"][2] === "—", "N.1: a number for the level it is in, dashes for the others: " + col["N.1"]);

  // 5. selecting a dash box says why, in the formula bar area, and the box takes no typing
  const z = cells("Z.1").find((td) => !td.classList.contains("sum"));
  press(t.window, z); await flush(3);
  ok(/no quantity/i.test(T(t.q("#res"))) && t.q("#fx").disabled, "selected dash box: reason shown, nothing to type: " + T(t.q("#res")));

  // 6. a real zero is still a zero, a real number is untouched
  ok(txt(cells("39b").filter((td) => !td.classList.contains("sum"))[0]) === "0", "a zone with nothing done is 0");

  console.log(`${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
}
main().catch((e) => { console.error(e); process.exit(1); });
