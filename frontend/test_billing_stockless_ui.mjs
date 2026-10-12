// jsdom: Billing for a project with no stock run. /api/projects (built from stock runs) is empty after Thoth Mall's stock was
// deleted; Billing must still list Thoth Mall (from /api/siteprogress/projects-without-stock) and open its real overview payload.
import { JSDOM } from "jsdom";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FX = JSON.parse(fs.readFileSync(path.join(__dirname, "billing_packages_fixture.json"), "utf8"));
let pass = 0, fail = 0;
const ok = (c, m) => { if (c) pass++; else { fail++; console.error("FAIL:", m); } };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const P = "/api/billing/thoth-mall";

async function boot(extraFails, stockRuns) {
  const html = `<!doctype html><html><body><header class="top" id="top"><div class="brand"><p id="ctx"></p></div>
    <div class="topact"><button id="home"></button><button id="pick"></button><button id="sync"></button><button id="syncgo"></button></div></header>
    <main><section id="drop" hidden></section><section id="busy" hidden></section><section id="report"><div class="bar"><div class="tabs" id="svc"></div></div></section></main></body></html>`;
  const { window } = new JSDOM(html, { url: "https://example.test/", runScripts: "dangerously", pretendToBeVisual: true });
  window.__BL_LIVE_MS = 3600000; window.__BL_BADGE_MS = 3600000; window.scrollTo = () => {};
  const calls = [];
  window.fetch = (url) => {
    const u = String(url); calls.push(u);
    let res = {}, status = 200;
    if (u === "/api/projects") res = stockRuns ? [{ slug: "thoth-mall", project: "Thoth Mall", runs: 1, latest_run: "r1" }] : [];
    else if (u === "/api/siteprogress/projects-without-stock") { if (extraFails) status = 500; else res = stockRuns ? [] : [{ slug: "thoth-mall", project: "Thoth Mall", runs: 0, latest_run: null, latest: null }]; }
    else if (u === P + "/auto") res = { created: [] };
    else if (u === P) res = JSON.parse(JSON.stringify(FX.ov));
    return Promise.resolve({ ok: status < 400, status, json: async () => res, text: async () => JSON.stringify(res) });
  };
  window.eval(fs.readFileSync(path.join(__dirname, "siteprogress.js"), "utf8"));
  window.eval(fs.readFileSync(path.join(__dirname, "billing.js"), "utf8"));
  await sleep(0);
  [...window.document.querySelectorAll(".spnav button")].find((b) => b.dataset.v === "billing").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(80);
  return { d: window.document, calls };
}
const T = (el) => (el ? el.textContent : "").replace(/\s+/g, " ").trim();

(async () => {
  let { d, calls } = await boot(false, false);
  ok(!/No project yet/.test(T(d.getElementById("billing") || d.body)), "Billing does not say 'No project yet' for a stock-less project");
  ok([...d.querySelectorAll("#bl-proj option")].map((o) => o.value).join() === "thoth-mall", "Thoth Mall is in Billing's project dropdown");
  ok(calls.includes(P), "its overview was fetched: " + calls.join(" "));
  ok(d.querySelector(".bl-kpis"), "home KPIs painted");

  ({ d, calls } = await boot(false, true));                   // normal project with its own stock: listed once, not duplicated
  ok([...d.querySelectorAll("#bl-proj option")].length === 1, "no duplicate project when it already has stock");

  ({ d, calls } = await boot(true, true));                    // the new endpoint failing must not break Billing
  ok(d.querySelector("#bl-proj") && d.querySelector(".bl-kpis"), "Billing still opens when the extra list errors");
  console.log(`${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
})();
