// jsdom test for the Bug-3 frontend fix in siteprogress.js: loadService()
// used to `await` its three reads one at a time (service, pnl, realistic) --
// now they fire together via Promise.allSettled. Proven here by timing WHEN
// each fetch() call actually happens (not when it resolves): with an
// artificial per-call network delay, a still-sequential implementation would
// space the three fetch() invocations ~delay ms apart; the fixed,
// parallel implementation invokes all three within the same tick.
import { JSDOM } from "jsdom";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

let pass = 0, fail = 0;
function ok(cond, msg) {
  if (cond) pass++;
  else { fail++; console.error("FAIL:", msg); }
}

async function main() {
  const html = `<!doctype html><html><body>
    <header class="top"><div class="brand"><p id="ctx"></p></div><div class="topact"></div></header>
    <main></main>
  </body></html>`;
  const dom = new JSDOM(html, { url: "https://example.test/", runScripts: "dangerously", pretendToBeVisual: true });
  const { window } = dom;
  const d = window.document;

  const DELAY_MS = 30;
  const calls = [];   // { url, calledAt } -- pushed the instant fetch() is invoked
  window.fetch = (url) => {
    calls.push({ url: String(url), calledAt: Date.now() });
    return new Promise((resolve) => {
      setTimeout(() => {
        const u = String(url);
        let body = {};
        if (u === "/api/projects") {
          body = [{ slug: "hyatt-hotel", project: "Hyatt Hotel", runs: 1, latest_run: "r1" }];
        } else if (u === "/api/siteprogress/hyatt-hotel") {
          body = { structure: { kind: "hotel" }, has_boq: true, services: ["Electrical", "HVAC"] };
        } else if (u.startsWith("/api/siteprogress/hyatt-hotel/service/")) {
          body = { service: u.includes("HVAC") ? "HVAC" : "Electrical", room: null,
            activities: [], mapping: {}, act_pct: {}, overall_pct: 0, items: [],
            pnl_by_activity: {}, pnl_totals: {}, pnl_unmapped_value: { items: 0 },
            item_rooms: {}, item_room_qty: {}, unmapped: [] };
        } else if (u.startsWith("/api/siteprogress/hyatt-hotel/pnl/")) {
          body = { done_value: 0, remaining_value: 0 };
        } else if (u.startsWith("/api/siteprogress/hyatt-hotel/realistic/")) {
          body = { items: [], shortages: 0 };
        }
        resolve({ ok: true, status: 200, json: async () => body, text: async () => JSON.stringify(body) });
      }, DELAY_MS);
    });
  };

  window.eval(fs.readFileSync(path.join(__dirname, "siteprogress.js"), "utf8"));
  // let DOMContentLoaded (async in jsdom) fire so injectNav()'s boot() runs
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));

  // Boot: open Site Progress (this fires the FIRST loadService() call as a
  // side effect of loadState() -- not the one under test).
  const navBtn = [...d.querySelectorAll(".spnav button")].find((b) => b.dataset.v === "siteprogress");
  ok(!!navBtn, "Site Progress nav button was injected");
  navBtn.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  // let the boot chain (projects -> state -> first loadService) fully settle
  await new Promise((r) => setTimeout(r, DELAY_MS * 6));

  calls.length = 0;   // clear boot-time calls; we only care about the pill click below

  const pill = [...d.querySelectorAll(".sp-pill")].find((b) => b.dataset.s === "HVAC");
  ok(!!pill, "an HVAC service pill rendered");
  pill.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));

  // Give the synchronous portion of loadService() a chance to run (fetch()
  // calls happen synchronously up to their own internal `await fetch(...)`
  // point) without letting the artificial 30ms network delay elapse yet.
  await new Promise((r) => setTimeout(r, 5));

  const relevant = calls.filter((c) => c.url.includes("/hyatt-hotel/"));
  ok(relevant.length === 3, `all 3 per-service reads were fired (service/pnl/realistic), got ${relevant.length}`);
  ok(relevant.some((c) => c.url.includes("/service/HVAC")), "service read fired");
  ok(relevant.some((c) => c.url.includes("/pnl/HVAC")), "pnl read fired");
  ok(relevant.some((c) => c.url.includes("/realistic/HVAC")), "realistic read fired");

  if (relevant.length === 3) {
    const spread = Math.max(...relevant.map((c) => c.calledAt)) - Math.min(...relevant.map((c) => c.calledAt));
    ok(spread < DELAY_MS, `the 3 fetches were invoked within the same tick (spread ${spread}ms < ${DELAY_MS}ms artificial network delay) -- proves they run in parallel, not one-after-another`);
  }

  // let the delayed responses resolve and confirm the UI actually updates
  await new Promise((r) => setTimeout(r, DELAY_MS * 2));
  ok(d.querySelector(".sp-hero") !== null, "the service view actually rendered after the parallel reads resolved");

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
}

main();
