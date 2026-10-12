// jsdom test: taking a line out of a draft RA (the "delete option" on a draft line) and putting it back.
// The bills are the REAL backend payloads captured from the real Thoth BOQ (billing_packages_fixture.json): ra5 is the open
// Installation draft (3 lines), ra4 an invoiced Supply RA. The server's side of the new route is replayed field for field
// (the line leaves `lines`, appears in `removed`, the totals drop by its amount); the backend itself is tested in test_line_remove.py.
import { JSDOM } from "jsdom";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
let pass = 0, fail = 0;
function ok(cond, msg) { if (cond) pass++; else { fail++; console.error("FAIL:", msg); } }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const FX = JSON.parse(fs.readFileSync(path.join(__dirname, "billing_packages_fixture.json"), "utf8"));
const CH = JSON.parse(fs.readFileSync(path.join(__dirname, "billing_challan_fixture.json"), "utf8"));
const clone = (x) => JSON.parse(JSON.stringify(x));
const P = "/api/billing/thoth-mall";
const T = (el) => (el ? el.textContent : "").replace(/\s+/g, " ").trim();
const click = (w, el) => el.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));

async function boot(opts) {
  opts = opts || {};
  const html = `<!doctype html><html><body>
    <header class="top" id="top"><div class="brand"><p id="ctx"></p></div>
      <div class="topact"><button id="home"></button><button id="pick"></button><button id="sync"></button><button id="syncgo"></button></div></header>
    <main><section id="drop" hidden></section><section id="busy" hidden></section>
      <section id="report"><div class="bar"><div class="tabs" id="svc"></div></div></section></main></body></html>`;
  const dom = new JSDOM(html, { url: "https://example.test/", runScripts: "dangerously", pretendToBeVisual: true });
  const { window } = dom; const calls = [];
  window.__BL_LIVE_MS = 3600000; window.__BL_BADGE_MS = 3600000; window.scrollTo = () => {};
  const bills = { ra4: clone(FX.bills.ra4), ra5: clone(FX.bills.ra5) };
  bills.ra5.removed = []; bills.ra5.ra.inv_missing = [];
  const server = { fail: null };
  window.fetch = (url, o) => {
    const u = String(url), method = (o && o.method) || "GET";
    const body = o && o.body ? JSON.parse(o.body) : null;
    calls.push({ u, method, body });
    let res = {}, status = 200;
    const m = u.match(/\/ra\/(ra\d+)\/line\/remove$/);
    if (u === "/api/projects") res = [{ slug: "thoth-mall", project: "Thoth Mall", runs: 1, latest_run: "r1" }];
    else if (u === P + "/auto") res = { created: [] };
    else if (u === P) res = clone(FX.ov);
    else if (m && method === "POST") {
      if (server.fail) { status = 409; res = { detail: server.fail }; }
      else {
        const b = bills[m[1]];
        if (body.remove) {
          const i = b.lines.findIndex((l) => l.item_code === body.item_code);
          const [l] = b.lines.splice(i, 1);
          b.removed.push({ item_code: l.item_code, desc: l.desc, unit: l.unit });
          b.this_total -= l.install.amount; b.totals.install.this -= l.install.amount;
          (b.__gone = b.__gone || {})[l.item_code] = l;
        } else {
          const l = b.__gone[body.item_code];
          b.lines.push(l); b.removed = b.removed.filter((r) => r.item_code !== body.item_code);
          b.this_total += l.install.amount; b.totals.install.this += l.install.amount;
        }
        res = { ok: true, removed: b.removed.length };
      }
    }
    else if (/\/ra\/ra\d+$/.test(u) && method === "GET") res = clone(bills[u.split("/").pop()]);
    else if (/\/challans\//.test(u) && method === "GET") res = CH.ch_empty;
    const reply = { ok: status < 400, status, json: async () => res, text: async () => JSON.stringify(res) };
    return Promise.resolve(reply);
  };
  window.eval(fs.readFileSync(path.join(__dirname, "siteprogress.js"), "utf8"));
  window.eval(fs.readFileSync(path.join(__dirname, "billing.js"), "utf8"));
  await sleep(0);
  [...window.document.querySelectorAll(".spnav button")].find((b) => b.dataset.v === "billing").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(80);
  return { window, d: window.document, calls, bills, server };
}
const open = async (w, d, id) => { click(w, d.querySelector(`[data-ra="${id}"]`)); await sleep(60); };

async function main() {
  let { window, d, calls, bills, server } = await boot();
  await open(window, d, "ra5");
  ok(/Installation RA 1/.test(T(d.querySelector("#billing h2"))), "the draft opens: " + T(d.querySelector("#billing h2")));
  let rows = [...d.querySelectorAll(".bl-lr")];
  ok(rows.length === 3, "three real lines");
  ok(rows.every((r) => r.querySelectorAll(".bl-rm").length === 1), "every draft line has one remove button");
  const b0 = rows[0].querySelector(".bl-rm");
  ok(b0.getAttribute("aria-label") === "Remove this line from the draft" && !!b0.getAttribute("title"), "the button is labelled for a screen reader and has a tooltip");
  ok(/grid-template-columns:[^;]* 44px/.test(d.querySelector(".bl-lh").getAttribute("style")) && /44px/.test(rows[0].getAttribute("style")), "an extra 44px column for it, in header and rows");
  ok(!d.getElementById("bl-removed"), "nothing removed yet: no footer");
  const total0 = bills.ra5.this_total;
  const kpi = () => T(d.querySelector(".bl-kpi b"));
  ok(kpi() === "₹ 4,47,441", "This bill before: " + kpi());

  // ---- remove the second line
  const target = bills.ra5.lines[1], gone = target.install.amount;
  click(window, rows[1].querySelector(".bl-rm")); await sleep(80);
  const post = calls.find((c) => /\/line\/remove$/.test(c.u));
  ok(post && post.method === "POST" && post.u === P + "/ra/ra5/line/remove" && post.body.item_code === target.item_code && post.body.remove === true, "POST /ra/ra5/line/remove {item_code, remove:true}: " + JSON.stringify(post && post.body));
  rows = [...d.querySelectorAll(".bl-lr")];
  ok(rows.length === 2 && !rows.some((r) => r.dataset.code === target.item_code), "the line is gone from the list");
  ok(!rows.some((r) => r.classList.contains("open")), "clicking the button did not also open the line's detail");
  ok(T(d.querySelector(".bl-lines-h h3")).includes("2"), "the Lines count follows: " + T(d.querySelector(".bl-lines-h h3")));
  const expect = "₹ " + Math.round(total0 - gone).toLocaleString("en-IN");
  ok(kpi() === expect, "This bill drops by exactly that line's amount: " + kpi() + " vs " + expect);
  const foot = d.getElementById("bl-removed");
  ok(!!foot && /1 line removed from this draft/.test(T(foot)) && T(foot).includes(target.desc.slice(0, 30)), "footer names the removed line: " + T(foot));
  ok(/Site Progress and other bills are not changed/.test(T(foot)), "and says nothing else changed");
  ok(/Removed from this draft/.test(T(d.getElementById("bl-toast"))), "a toast says it was removed and how to restore");
  ok(!d.querySelector(".bl-modal, #bl-modal"), "no 'are you sure' dialog (it is reversible)");

  // ---- restore it
  click(window, d.querySelector("[data-restore]")); await sleep(80);
  const post2 = calls.filter((c) => /\/line\/remove$/.test(c.u))[1];
  ok(post2 && post2.body.item_code === target.item_code && post2.body.remove === false, "Restore sends remove:false: " + JSON.stringify(post2 && post2.body));
  ok(d.querySelectorAll(".bl-lr").length === 3 && !d.getElementById("bl-removed"), "line is back, footer gone");
  ok(kpi() === "₹ 4,47,441", "This bill is back: " + kpi());

  // ---- two removed: plural footer
  for (const i of [0, 1]) { click(window, d.querySelectorAll(".bl-lr")[0].querySelector(".bl-rm")); await sleep(80); }
  ok(/2 lines removed from this draft/.test(T(d.getElementById("bl-removed"))) && d.querySelectorAll("[data-restore]").length === 2, "plural: " + T(d.querySelector("#bl-removed b")));

  // ---- the server refuses: the page says why and nothing moves
  ({ window, d, calls, bills, server } = await boot());
  await open(window, d, "ra5");
  server.fail = "A locked RA cannot be changed. Reopen it first.";
  click(window, d.querySelectorAll(".bl-lr")[0].querySelector(".bl-rm")); await sleep(80);
  ok(d.querySelectorAll(".bl-lr").length === 3 && /locked RA cannot be changed/.test(T(d.getElementById("bl-toast"))), "refused: rows unchanged, the reason is shown: " + T(d.getElementById("bl-toast")));

  // ---- an invoiced bill: no button, no extra column, no footer
  ({ window, d } = await boot());
  await open(window, d, "ra4");
  ok(/Invoiced/.test(T(d.querySelector("#billing .bl-sub"))), "invoiced bill: " + T(d.querySelector("#billing .bl-sub")));
  ok(d.querySelectorAll(".bl-rm").length === 0 && !d.getElementById("bl-removed"), "an invoiced bill has no remove buttons and no footer");
  ok(!/ 44px/.test(d.querySelector(".bl-lh").getAttribute("style")), "and no extra column");

  // ---- the CSS: a thumb-sized target on a phone
  const css = fs.readFileSync(path.join(__dirname, "billing.css"), "utf8");
  ok(/@media\(max-width:760px\)\{[^}]*\.bl-lr \.rm\{grid-column:1\/-1/.test(css.replace(/\n/g, "")) && /\.bl-rm\{width:auto;min-height:44px/.test(css), "phone CSS: the button is 44px tall and gets its own row");

  console.log(`billing remove-line UI: ${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
}
main().catch((e) => { console.error(e); process.exit(1); });
