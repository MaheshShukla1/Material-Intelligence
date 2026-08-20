// jsdom test for the note-consolidation: the drawer used to say the same
// "issued vs expected" gap TWICE -- once as a standalone warning under
// Linked Stock, once again inside the order-quantity message -- with two
// different framings. Now it's said exactly once, inside the single
// consolidated order-quantity message; the Linked Stock section shows only
// the plain "X issued to date" fact.
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

// exactly what the fixed realtime.combine_item()/sentence() now produce.
function realisticBody() {
  return {
    service: "Plumbing", has_run: true, run: "run1", linked_items: 1, shortages: 1,
    items: [{
      item_code: "QI1", unit: "MTR", planned_total: 8160, used: 4240,
      remaining: 3920, progress_pct: 52,
      order_qty: 3426.0, order_qty_optimistic: 1660.0,
      staged_gap: 1766.0, issued_to_date: 6006.0,
      verdict: "SHORTAGE",
      links: [{ material: "KITEC PE-AL-PEX PIPE COIL (IGC", unit: "MTR",
               on_hand: 494, rate_per_day: 108.3, engine_days_left: 5,
               status: "RED", order_by: null, total_consumed: 6006,
               received: 6500, factor: null, units_match: true,
               need: 3920, shortfall: 3426, verdict: "SHORTAGE",
               optimistic_shortfall: 1660, staged_gap: 1766 }],
      rooms: { done: 106, in_progress: 98, not_started: 0, total: 204 },
      desc: "KITEC PE-AL-PEX PIPE COIL (IGC-309) 1620",
      message: "3920 MTR needed for the remaining 98 rooms — order 3426 MTR "
              + "to be safe. (6006 MTR already issued is 1766 MTR more than "
              + "this progress should have used — if that's staged material "
              + "on site, 1660 MTR more would be enough instead; verify first.)",
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
  fxBtn.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));

  const drawer = d.querySelector("#sp-draw");
  ok(!!drawer, "drawer rendered");
  const fullText = drawer.textContent;

  // -------------------------------------------------- said exactly once
  const count1766 = (fullText.match(/1,?766/g) || []).length;
  ok(count1766 === 1, `the 1766 gap appears exactly once in the whole drawer, got ${count1766} -- text: "${fullText.replace(/\s+/g, " ").slice(0, 400)}"`);

  // -------------------------------------------------- Linked Stock section is now plain
  const linkedStockRows = [...drawer.querySelectorAll(".sp-drow.sub")];
  const linkedStockText = linkedStockRows.map((r) => r.textContent).join(" | ");
  ok(linkedStockText.includes("6,006") && linkedStockText.includes("issued to date"),
    `Linked Stock shows the plain "issued to date" fact, got: "${linkedStockText}"`);
  ok(!linkedStockText.includes("expected for work marked done"),
    `Linked Stock no longer carries its own "vs expected" framing (that moved to the order box), got: "${linkedStockText}"`);
  ok(!linkedStockText.includes("1766") && !linkedStockText.includes("1,766"),
    `the gap number itself doesn't appear in Linked Stock at all anymore, got: "${linkedStockText}"`);

  // -------------------------------------------------- order box carries the full story
  const orderBox = drawer.querySelector(".sp-dlink");
  ok(!!orderBox, "order-quantity box rendered");
  const orderText = orderBox.textContent;
  ok(orderText.includes("3,426") || orderText.includes("3426"), `order box has the safe figure, got: "${orderText}"`);
  ok(orderText.includes("1,660") || orderText.includes("1660"), `order box has the optimistic figure, got: "${orderText}"`);
  ok(orderText.includes("1,766") || orderText.includes("1766"), `order box has the gap, got: "${orderText}"`);
  ok(/verify/i.test(orderText), `framed as verify-first, not asserted, got: "${orderText}"`);

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
}

main().catch((e) => { console.error("ERROR:", e.stack); process.exit(1); });
