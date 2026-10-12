// jsdom test: deleting a Site Progress activity. Work recorded on its items => a two-choice dialog ("Only remove the activity" /
// "Remove the activity and its recorded work") with the real Billing impact; nothing recorded => the plain confirm.
// Every payload is a REAL backend response (activity_delete_fixture.json, made by backend/make_activity_delete_fixture.py from the
// real routers: Fire with activities Pumps = items 2,3 and Pipes = items 3,5, work logged, an installation draft open).
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

async function boot(opts) {
  opts = opts || {};
  const html = `<!doctype html><html><body>
  <header class="top" id="top"><div class="brand"><h1>Material intelligence</h1><p id="ctx"></p></div>
    <div class="topact"><button id="pick"></button><button id="sync"></button><button id="syncgo"></button><button id="home" hidden></button><input id="file" type="file" hidden></div></header>
  <main><section id="drop"></section><section id="busy" hidden></section><section id="report" hidden><div class="bar"><div class="tabs" id="svc"></div></div></section></main></body></html>`;
  const dom = new JSDOM(html, { url: "http://localhost/", runScripts: "outside-only", pretendToBeVisual: true });
  const { window } = dom; const posts = [], gets = [], confirms = [];
  const view = opts.view || clone(FX.view_with_empty);
  window.confirm = (m) => { confirms.push(m); return true; };
  window.alert = () => {};
  window.fetch = async (url, o) => {
    const u = new URL(url, "http://localhost"), method = (o && o.method) || "GET";
    let data = {}, status = 200;
    gets.push(method + " " + u.pathname + u.search);
    if (u.pathname === "/api/projects") data = FX.projects;
    else if (u.pathname === SP) data = FX.project;
    else if (u.pathname === SP + "/service/Fire") data = clone(view);
    else if (u.pathname === SP + "/pnl/Fire") data = FX.pnl;
    else if (u.pathname === "/api/billing/" + SL + "/activity-impact") {
      if (opts.impactFails) { status = 500; data = { detail: "boom" }; }
      else data = clone(u.searchParams.get("activity") === "Pipes" ? FX.impact_pipes : (opts.impact || FX.impact_pumps));
    } else if (method === "POST" && u.pathname === SP + "/activity") {
      posts.push(JSON.parse(o.body)); data = clone(view);
    }
    return { ok: status < 400, status, json: async () => data, text: async () => JSON.stringify(data) };
  };
  window.eval(fs.readFileSync(path.join(__dirname, "siteprogress.js"), "utf8"));
  Object.defineProperty(window.document, "readyState", { value: "complete", configurable: true });
  window.document.dispatchEvent(new window.Event("DOMContentLoaded"));
  await flush();
  window.document.querySelector('.spnav button[data-v="siteprogress"]').click();
  await flush(25);
  return { window, d: window.document, posts, gets, confirms };
}
const T = (el) => (el ? el.textContent : "").replace(/\s+/g, " ").trim();
const click = (w, el) => el.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));

async function main() {
  // ---- work recorded on the activity: the two-choice dialog, not a bare confirm
  let { window, d, posts, gets, confirms } = await boot();
  ok(!!d.querySelector('[data-actdel="Pumps"]'), "Site Progress shows the real Fire activities with a delete (×) on each");
  click(window, d.querySelector('[data-actdel="Pumps"]')); await flush(15);
  const m = d.getElementById("sp-modal");
  ok(!!m && confirms.length === 0, "a dialog opens (not the browser confirm)");
  ok(/Delete “Pumps”\?/.test(T(m.querySelector("h2"))), "titled with the activity name: " + T(m.querySelector("h2")));
  ok(/work recorded on 2 BOQ items/.test(T(m)) && /₹26,000 done/.test(T(m)), "says how much is recorded on it (items 2 and 3, ₹26,000 done): " + T(m.querySelector(".sp-delp")));
  const radios = [...m.querySelectorAll('input[name="spdel"]')];
  ok(radios.length === 2 && radios[0].value === "keep" && radios[0].checked && !radios[1].checked, "two choices; 'Only remove the activity' is the default");
  ok(/Default/.test(T(m.querySelector("#sp-dopt-keep"))) && /progress and every bill stay as they are/.test(T(m.querySelector("#sp-dopt-keep"))), "the default says progress and bills stay");
  ok(/work on 1 item that belongs to this activity alone is erased; 1 shared with another activity stays as it is/.test(T(m.querySelector("#sp-dopt-wipe"))) && /cannot be undone/.test(T(m.querySelector("#sp-dopt-wipe"))), "the second choice says what is erased (item 2), what is kept (shared item 3) and that it cannot be undone: " + T(m.querySelector("#sp-dopt-wipe small")));
  ok(/Invoiced RAs never change/.test(T(m)), "states that invoiced RAs never change");
  const save = d.getElementById("sp-modal-save");
  ok(T(save) === "Remove activity only" && !save.classList.contains("sp-danger"), "button: Remove activity only (not red)");
  // the real Billing impact, from the real bill code
  const imp = T(m.querySelector("#sp-delimp"));
  ok(/Billing will change/.test(imp) && /Fire · Installation RA 1 \(draft\)/.test(imp) && /₹ 12,300 → ₹ 9,300/.test(imp), "Billing impact: the draft goes ₹ 12,300 → ₹ 9,300: " + imp);
  ok(gets.includes("GET /api/billing/" + SL + "/activity-impact?service=Fire&activity=Pumps"), "it asked Billing about THIS activity");
  // choose the second one
  radios[1].checked = true; radios[1].dispatchEvent(new window.Event("change", { bubbles: true }));
  ok(T(save) === "Remove activity and work" && save.classList.contains("sp-danger") && m.querySelector("#sp-dopt-wipe").classList.contains("on") && !m.querySelector("#sp-dopt-keep").classList.contains("on"), "choosing it turns the button red and says what it does");
  click(window, save); await flush(15);
  ok(posts.length === 1 && posts[0].op === "delete" && posts[0].name === "Pumps" && posts[0].service === "Fire" && posts[0].with_progress === true, "wipe → delete with with_progress=true: " + JSON.stringify(posts[0]));
  ok(!d.getElementById("sp-modal"), "the dialog closes");

  // ---- the default choice = exactly what delete always did (no with_progress)
  ({ window, d, posts, confirms } = await boot());
  click(window, d.querySelector('[data-actdel="Pumps"]')); await flush(15);
  click(window, d.getElementById("sp-modal-save")); await flush(15);
  ok(posts.length === 1 && posts[0].op === "delete" && !("with_progress" in posts[0]), "default → plain delete, no with_progress: " + JSON.stringify(posts[0]));

  // ---- Cancel / Close change nothing
  ({ window, d, posts } = await boot());
  click(window, d.querySelector('[data-actdel="Pumps"]')); await flush(15);
  click(window, d.getElementById("sp-modal-cancel")); await flush(5);
  ok(posts.length === 0 && !d.getElementById("sp-modal"), "Cancel: nothing is deleted");

  // ---- nothing recorded: no choice to make, the short confirm, and it no longer claims 'its progress is removed'
  ({ window, d, posts, confirms } = await boot());
  click(window, d.querySelector('[data-actdel="Empty"]')); await flush(15);
  ok(!d.getElementById("sp-modal") && confirms.length === 1 && /Delete activity “Empty”\?/.test(confirms[0]) && !/progress is removed/.test(confirms[0]), "no work recorded: the plain confirm: " + confirms[0]);
  ok(posts.length === 1 && posts[0].name === "Empty" && !("with_progress" in posts[0]), "…and a plain delete");

  // ---- every recorded item is also in another activity: nothing could be erased, and the dialog says so
  const shared = clone(FX.view_with_empty); shared.mapping.Pipes = ["3"]; shared.mapping.Pumps = ["3"];
  ({ window, d } = await boot({ view: shared }));
  click(window, d.querySelector('[data-actdel="Pipes"]')); await flush(15);
  ok(/Every item here is also in another activity, so no recorded work is erased/.test(T(d.getElementById("sp-dopt-wipe"))), "all-shared activity: says no recorded work is erased");

  // ---- the preview says the draft would go below zero (the work was invoiced before): not hidden behind '₹ 0'
  const neg = clone(FX.impact_pumps); neg.drafts[0].then = -2500; neg.locked = 1;
  ({ window, d } = await boot({ impact: neg }));
  click(window, d.querySelector('[data-actdel="Pumps"]')); await flush(15);
  const ni = T(d.getElementById("sp-delimp"));
  ok(/₹ 12,300 → −₹ 2,500/.test(ni) && /Below zero/.test(ni) && /credit/.test(ni), "a draft that would go below zero says so: " + ni);

  // ---- no open draft: the preview says nothing is affected
  const none = clone(FX.impact_pumps); none.drafts = []; none.locked = 2;
  ({ window, d } = await boot({ impact: none }));
  click(window, d.querySelector('[data-actdel="Pumps"]')); await flush(15);
  ok(/no open draft bill is affected/.test(T(d.getElementById("sp-delimp"))) && /2 invoiced RAs stay as they are/.test(T(d.getElementById("sp-delimp"))), "no open draft: nothing affected, invoiced RAs stay");

  // ---- Billing unreachable: the choice still works, and says it could not check
  let b2 = await boot({ impactFails: true });
  click(b2.window, b2.d.querySelector('[data-actdel="Pumps"]')); await flush(15);
  ok(/could not be checked/.test(T(b2.d.getElementById("sp-delimp"))), "Billing error: 'could not be checked just now'");
  const r2 = [...b2.d.querySelectorAll('input[name="spdel"]')]; r2[1].checked = true; r2[1].dispatchEvent(new b2.window.Event("change", { bubbles: true }));
  click(b2.window, b2.d.getElementById("sp-modal-save")); await flush(15);
  ok(b2.posts.length === 1 && b2.posts[0].with_progress === true, "…and the delete still goes through");

  console.log(`activity delete UI: ${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
}
main().catch((e) => { console.error(e); process.exit(1); });
