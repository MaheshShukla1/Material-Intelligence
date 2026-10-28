// jsdom test: Site Progress hero, "not in any activity" banner and toolbar.
// Payloads: the real backend responses stored in activity_delete_fixture.json (Fire, activities Pumps/Pipes/Empty);
// only pnl.unmapped_value is set per case, in the exact shape pnl.rollup_pnl returns it.
import { JSDOM } from "jsdom";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
let pass = 0, fail = 0;
function ok(cond, msg) { if (cond) pass++; else { fail++; console.error("FAIL:", msg); } }
const FX = JSON.parse(fs.readFileSync(path.join(__dirname, "activity_delete_fixture.json"), "utf8"));
const clone = (x) => JSON.parse(JSON.stringify(x));
const flush = async (n = 8) => { for (let i = 0; i < n; i++) await new Promise((r) => setTimeout(r, 0)); };
const SL = FX.slug, SP = "/api/siteprogress/" + SL;
const T = (el) => (el ? el.textContent : "").replace(/\s+/g, " ").trim();

async function boot(unmapped, mutate) {
  const html = `<!doctype html><html><body>
  <header class="top" id="top"><div class="brand"><h1>Material intelligence</h1><p id="ctx"></p></div>
    <div class="topact"><button id="pick"></button><button id="sync"></button><button id="syncgo"></button><button id="home" hidden></button><input id="file" type="file" hidden></div></header>
  <main><section id="drop"></section><section id="busy" hidden></section><section id="report" hidden><div class="bar"><div class="tabs" id="svc"></div></div></section></main></body></html>`;
  const dom = new JSDOM(html, { url: "http://localhost/", runScripts: "outside-only", pretendToBeVisual: true });
  const { window } = dom; const posts = [], gets = [];
  const view = clone(FX.view_with_empty);
  const pnl = clone(FX.pnl);
  if (unmapped) { pnl.unmapped_value = unmapped; view.pnl_unmapped_value = unmapped; }
  const prompts = [];
  if (mutate) mutate(view);
  window.prompt = (m) => { prompts.push(m); return "Wall Piping"; };
  window.confirm = () => true; window.alert = () => {};
  window.fetch = async (url, o) => {
    const u = new URL(url, "http://localhost"), method = (o && o.method) || "GET";
    let data = {};
    gets.push(method + " " + u.pathname);
    if (u.pathname === "/api/projects") data = FX.projects;
    else if (u.pathname === SP) data = FX.project;
    else if (u.pathname === SP + "/service/Fire") data = clone(view);
    else if (u.pathname === SP + "/pnl/Fire") data = clone(pnl);
    else if (method === "POST" && u.pathname === SP + "/activity") { posts.push(JSON.parse(o.body)); data = clone(view); }
    return { ok: true, status: 200, json: async () => data, text: async () => JSON.stringify(data) };
  };
  window.eval(fs.readFileSync(path.join(__dirname, "siteprogress.js"), "utf8"));
  Object.defineProperty(window.document, "readyState", { value: "complete", configurable: true });
  window.document.dispatchEvent(new window.Event("DOMContentLoaded"));
  await flush();
  window.document.querySelector('.spnav button[data-v="siteprogress"]').click();
  await flush(25);
  return { window, d: window.document, posts, gets, prompts };
}
const click = (w, el) => el.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));

async function main() {
  // ---- 1. nothing unassigned: no banner, no 'Not counted'
  let { window, d } = await boot({ items: 0, planned_value: 0, done_value: 0, remaining_value: 0, full_value: 0 });
  ok(d.getElementById("sp-unmapped").hidden, "no unassigned items: banner hidden");
  ok(d.getElementById("sp-uncwrap").hidden && d.getElementById("sp-cover").hidden, "no unassigned items: 'Not counted' and coverage bar hidden");
  ok(/Work done/.test(T(d.querySelector(".sp-stats"))) && /Remaining/.test(T(d.querySelector(".sp-stats"))), "Work done and Remaining still shown");

  // ---- 1b. the fixture's own real response has exactly 1 unassigned item: singular wording
  ({ window, d } = await boot(null));
  ok(/1 BOQ item is not in any activity/.test(T(d.getElementById("sp-unmapped"))) && /1 item in no activity/.test(T(d.getElementById("sp-unch"))), "singular: " + T(d.getElementById("sp-unmapped")));

  // ---- 2. the real scenario: 130 items outside every activity
  const UN = { items: 130, planned_value: 95000, done_value: 0, remaining_value: 95000, full_value: 95000 };
  ({ window, d } = await boot(UN));
  const ban = d.getElementById("sp-unmapped");
  ok(!ban.hidden, "banner shown");
  ok(/130 BOQ items are not in any activity/.test(T(ban)), "title: " + T(ban.querySelector(".sp-unm-t b")));
  ok(/₹[\s\S]*95,000 remaining are not counted in the numbers above/.test(T(ban)) && /done/.test(T(ban)), "says done / remaining are not counted: " + T(ban));
  ok(T(ban.querySelector("#sp-unm-add")) === "Add to an activity", "one clear button");
  ok(!d.getElementById("sp-uncwrap").hidden && /Not counted/.test(T(d.getElementById("sp-uncwrap"))) && /95,000/.test(T(d.getElementById("sp-unc"))), "'Not counted' stat shows the unassigned planned value: " + T(d.getElementById("sp-unc")));
  ok(/130 items in no activity/.test(T(d.getElementById("sp-unch"))), "stat hint: " + T(d.getElementById("sp-unch")));
  // coverage bar: done / remaining / not counted, as shares of the rated BOQ value
  const cv = d.getElementById("sp-cover");
  const w = ["sp-cv-d", "sp-cv-r", "sp-cv-u"].map((i) => parseFloat(d.getElementById(i).style.width));
  const cp = FX.pnl.project.planned_value, share = Math.round(100 * cp / (cp + 95000));
  ok(!cv.hidden && Math.abs(w[0] + w[1] + w[2] - 100) < 0.5, "coverage bar fills 100%: " + w.join(" + "));
  ok(Math.abs(w[2] - 100 * 95000 / (cp + 95000)) < 0.2, "uncounted segment = 95000 / (" + cp + " + 95000)");
  ok(new RegExp("Only " + share + "% of the rated BOQ value").test(T(cv)), "coverage text: " + T(cv));
  ok(!d.getElementById("sp-ring").hasAttribute("title") && !d.querySelector(".sp-hero").hidden && !ban.classList.contains("first") && !d.querySelector(".sp-steps"), "no first-run card, hero always shown, ring has no extra tooltip");
  const emptyMap = (v) => { Object.keys(v.mapping).forEach((k) => { v.mapping[k] = []; }); };
  ({ window, d } = await boot(UN, emptyMap));
  ok(!d.querySelector(".sp-hero").hidden && !d.getElementById("sp-unmapped").hidden && /130 BOQ items are not in any activity/.test(T(d.getElementById("sp-unmapped"))), "nothing in any activity yet: same banner and hero, no special card");

  // ---- 3. unassigned items with no rate: no invented coverage figure
  ({ window, d } = await boot({ items: 4, planned_value: null, done_value: null, remaining_value: null, full_value: null }));
  ok(!d.getElementById("sp-unmapped").hidden, "unrated unassigned items still get the banner");
  ok(T(d.getElementById("sp-unc")) === "—" && d.getElementById("sp-cover").hidden, "no rate: Not counted shows a dash and there is no coverage bar (never a guessed number)");

  // ---- 4. banner button: pick the activity, the Add items dialog opens for it
  ({ window, d } = await boot(UN));
  click(window, d.getElementById("sp-unm-add")); await flush(10);
  let m = d.getElementById("sp-modal");
  ok(!!m && /Add items to which activity/.test(T(m.querySelector("h2"))), "picker opens");
  const picks = [...m.querySelectorAll("[data-pick]")].map((b) => b.dataset.pick);
  ok(JSON.stringify(picks) === JSON.stringify(["Pumps", "Pipes", "Empty"]), "lists the real activities: " + picks);
  ok(!!m.querySelector("#sp-pick-new"), "has + New activity");
  ok(m.querySelector("#sp-modal-save").hidden, "no useless Save button");
  click(window, m.querySelector('[data-pick="Pipes"]')); await flush(10);
  m = d.getElementById("sp-modal");
  ok(!!m && /Pipes/.test(T(m.querySelector("h2"))), "the BOQ items dialog opens for Pipes: " + T(m && m.querySelector("h2")));
  click(window, d.getElementById("sp-modal-x")); await flush(3);

  // ---- 5. toolbar: three labelled buttons, rare actions inside More
  const bar = d.querySelector(".sp-tools");
  ok([...bar.children].filter((c) => c.querySelector("button, input") || c.tagName === "BUTTON").length >= 3, "toolbar has the main buttons");
  ok(T(d.getElementById("sp-newact")) === "+ New activity" && /Measurement/.test(T(d.getElementById("sp-measgrid"))) && /More/.test(T(d.getElementById("sp-more"))), "New activity / Measurement / More");
  const menu = d.getElementById("sp-moremenu");
  ok(menu.hidden, "More menu closed at first");
  click(window, d.getElementById("sp-more")); ok(!menu.hidden && d.getElementById("sp-more").getAttribute("aria-expanded") === "true", "More opens");
  ok([...menu.querySelectorAll("button")].map(T).join("|") === "Set rates|Link stock|Re-upload BOQ|Refresh", "menu: " + [...menu.querySelectorAll("button")].map(T).join("|"));
  click(window, d.body); ok(menu.hidden, "click outside closes it");
  click(window, d.getElementById("sp-more")); click(window, d.getElementById("sp-rates")); await flush(10);
  ok(menu.hidden && !!d.getElementById("sp-modal"), "Set rates opens its dialog and the menu closes");
  click(window, d.getElementById("sp-modal-x")); await flush(3);
  const g0 = d.defaultView.__g = 0;
  click(window, d.getElementById("sp-more")); click(window, d.getElementById("sp-refresh")); await flush(15);
  ok(menu.hidden, "Refresh closes the menu");
  // + New activity still works exactly as before
  let r = await boot(UN); click(r.window, r.d.getElementById("sp-newact")); await flush(10);
  ok(r.prompts.length === 1 && r.posts.length === 1 && r.posts[0].op === "create" && r.posts[0].name === "Wall Piping", "+ New activity still creates through the same endpoint");

  // ---- 6. Structure panel is untouched: no phone-only collapse was added
  r = await boot(UN);
  const railHd = r.d.querySelector(".sp-rail .sp-railhd");
  click(r.window, railHd.querySelector("#sp-railttl")); ok(!r.d.querySelector(".sp-rail").classList.contains("open"), "tapping the Structure title does nothing new");

  console.log(fail ? `FAIL: ${pass} passed, ${fail} failed` : `PASS ${pass} checks`);
  process.exit(fail ? 1 : 0);
}
main().catch((e) => { console.error("TEST FAILURE:", e); process.exit(1); });
