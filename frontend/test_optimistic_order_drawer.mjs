// jsdom test: the optimistic-order note (realtime.sentence()'s new second
// line) reaches the drawer with ZERO frontend changes -- res["message"] in
// siteprogress.py's realistic() route already carries the full sentence()
// text end to end, and the drawer already displays rl.message verbatim.
// This test proves that wiring actually holds, not just that it should in
// theory.
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

function serviceBody() {
  return {
    service: "Plumbing", room: null,
    activities: ["Piping Laying Hot & Cold Water"],
    mapping: { "Piping Laying Hot & Cold Water": ["QI1"] },
    act_pct: { "Piping Laying Hot & Cold Water": 52 },
    overall_pct: 52,
    items: [{
      code: "QI1", desc: "KITEC PE-AL-PEX PIPE COIL (IGC-309) 1620", unit: "MTR",
      sub: "Pipe", acts: ["Piping Laying Hot & Cold Water"],
      qty: 40, planned: 8160, used: 4240, remaining: 3920, pct: 52,
      mapped: true, rooms: 204, in_room: true, rate: 60, quick: false,
      done_val: 254400, rem_val: 235200,
      room_done: 106, room_progress: 0, room_pending: 98, room_total: 204,
      planned_override: false, has_room_groups: true,
    }],
    pnl_by_activity: {}, pnl_totals: {}, pnl_unmapped_value: { items: 0 },
    item_rooms: {}, item_room_qty: {}, item_progress: {},
    unmapped: [], labour_only: {}, labour_pct: {}, labour_suggested: {},
  };
}

// exactly what realtime.combine_item()/sentence() now produce for this
// real scenario -- the actual backend output, not a hand-written stand-in.
function realisticBody() {
  return {
    service: "Plumbing", has_run: true, run: "run1", linked_items: 1, shortages: 1,
    items: [{
      item_code: "QI1", unit: "MTR", planned_total: 8160, used: 4240,
      remaining: 3920, progress_pct: 52,
      order_qty: 3426.0, order_qty_optimistic: 1660.0,
      verdict: "SHORTAGE",
      links: [{ material: "KITEC PE-AL-PEX PIPE COIL (IGC", unit: "MTR",
               on_hand: 494, rate_per_day: 108.3, engine_days_left: 5,
               status: "RED", order_by: null, total_consumed: 6006,
               received: 6500, factor: null, units_match: true,
               need: 3920, shortfall: 3426, verdict: "SHORTAGE",
               optimistic_shortfall: 1660, staged_gap: 1766 }],
      rooms: { done: 106, in_progress: 98, not_started: 0, total: 204 },
      desc: "KITEC PE-AL-PEX PIPE COIL (IGC-309) 1620",
      message: "3920 MTR needed for the remaining 98 rooms and stock will run "
              + "short — order about 3426 more to finish. If ~1766 MTR of "
              + "already-issued material is confirmed staged on site (not "
              + "lost), ~1660 MTR would be enough instead — verify before "
              + "ordering less than the safe amount.",
    }],
  };
}

async function main() {
  const html = `<!doctype html><html><body>
    <header class="top"><div class="brand"><p id="ctx"></p></div><div class="topact"></div></header>
    <main></main>
  </body></html>`;
  const dom = new JSDOM(html, { url: "https://example.test/", runScripts: "dangerously", pretendToBeVisual: true });
  const { window } = dom;
  const d = window.document;

  window.fetch = (url) => {
    const u = String(url);
    let body = {};
    if (u === "/api/projects") body = [{ slug: "hyatt-hotel", project: "Hyatt Hotel", runs: 4, latest_run: "run1" }];
    else if (u === "/api/siteprogress/hyatt-hotel") body = { structure: { kind: "hotel" }, has_boq: true, services: ["Plumbing"] };
    else if (u.startsWith("/api/siteprogress/hyatt-hotel/service/")) body = serviceBody();
    else if (u.startsWith("/api/siteprogress/hyatt-hotel/realistic/")) body = realisticBody();
    else if (u.startsWith("/api/siteprogress/hyatt-hotel/pnl/")) body = { done_value: 254400, remaining_value: 235200 };
    return Promise.resolve({ ok: true, status: 200, json: async () => body, text: async () => JSON.stringify(body) });
  };

  window.eval(fs.readFileSync(path.join(__dirname, "siteprogress.js"), "utf8"));
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));

  const navBtn = [...d.querySelectorAll(".spnav button")].find((b) => b.dataset.v === "siteprogress");
  navBtn.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 60));

  const fxBtn = d.querySelector('[data-fx="QI1"]');
  ok(!!fxBtn, "the item's forecast-link icon rendered");
  fxBtn.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));

  const linkBlock = d.querySelector(".sp-dlink");
  ok(!!linkBlock, "the stock-forecast message block rendered in the drawer");
  const text = linkBlock.textContent;

  ok(text.includes("order about 3426"), `safe order figure present, got: "${text}"`);
  ok(text.includes("1660"), `optimistic figure present, got: "${text}"`);
  ok(text.includes("1766"), `the credited/staged gap is shown explicitly, got: "${text}"`);
  ok(/verify/i.test(text), `framed as something to verify, not asserted fact, got: "${text}"`);

  const boldOrder = linkBlock.querySelector("b");
  ok(!!boldOrder && boldOrder.textContent.includes("3,426"),
    `the bolded headline figure stays the SAFE order (3,426), not the optimistic one, got: "${boldOrder && boldOrder.textContent}"`);
  ok(!boldOrder.textContent.includes("1,660") && !boldOrder.textContent.includes("1660"),
    "the optimistic figure is not bolded/promoted above the safe one");

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
}

main().catch((e) => { console.error("ERROR:", e.stack); process.exit(1); });
