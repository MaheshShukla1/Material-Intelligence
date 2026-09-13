// jsdom test for the stale-S.real bug: the drawer's "needed / order" message
// comes from S.real (the /realistic/{service} fetch), which several save
// actions (mark-rooms-done, item-room-qty save, remove quantity group) used
// to refresh S.pnl but NOT S.real -- so the drawer kept showing numbers
// computed from BEFORE the save, silently disagreeing with "Remaining work"
// (from S.svc, which WAS refreshed) on the exact same screen. Real reported
// case: QI2 showed "Remaining work: 340.6" but "628 NOS needed" in the same
// drawer, because S.real was stale.
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

const ROOM_IDS = ["r1", "r2", "r3", "r4"];

function serviceBody() {
  return {
    service: "HVAC", room: null,
    activities: ["CHW Piping"],
    mapping: { "CHW Piping": ["QI1"] },
    act_pct: { "CHW Piping": 25 },
    overall_pct: 25,
    items: [{
      code: "QI1", desc: "32MM HEAVY C CLASS PIPE", unit: "MTR", sub: "Pipe",
      acts: ["CHW Piping"], qty: 2.5, planned: 10, used: 2.5, remaining: 7.5,
      pct: 25, mapped: true, rooms: 4, in_room: true, rate: 94, quick: false,
      done_val: 235, rem_val: 705,
      room_done: 1, room_progress: 0, room_pending: 3, room_total: 4,
      planned_override: false, has_room_groups: false,
    }],
    pnl_by_activity: {}, pnl_totals: {}, pnl_unmapped_value: { items: 0 },
    item_rooms: {}, item_room_qty: {}, item_progress: { QI1: { r1: 1.0 } },
    unmapped: [], labour_only: {}, labour_pct: {}, labour_suggested: {},
  };
}

function structureBody() {
  return {
    kind: "hotel", id: "p0", type: "project", name: "Test Hotel",
    children: [{ id: "f1", type: "floor", name: "Floor 1", children: ROOM_IDS.map((id, i) => ({ id, type: "room", name: `Room ${i + 1}`, children: [] })) }],
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

  const realFetches = [];
  window.fetch = (url, opts) => {
    const u = String(url);
    if (u.includes("/realistic/")) realFetches.push(u);
    let body = {};
    if (u === "/api/projects") body = [{ slug: "hyatt-hotel", project: "Hyatt Hotel", runs: 1, latest_run: "r1" }];
    else if (u === "/api/siteprogress/hyatt-hotel") body = { structure: structureBody(), has_boq: true, services: ["HVAC"] };
    else if (u.startsWith("/api/siteprogress/hyatt-hotel/service/") ||
             u.startsWith("/api/siteprogress/hyatt-hotel/mark-rooms-done") ||
             u.startsWith("/api/siteprogress/hyatt-hotel/item-room-qty") ||
             u.startsWith("/api/siteprogress/hyatt-hotel/item-rooms")) body = serviceBody();
    else if (u.startsWith("/api/siteprogress/hyatt-hotel/pnl/")) body = { done_value: 235, remaining_value: 705 };
    else if (u.startsWith("/api/siteprogress/hyatt-hotel/realistic/")) body = { items: [], shortages: 0 };
    return Promise.resolve({ ok: true, status: 200, json: async () => body, text: async () => JSON.stringify(body) });
  };

  window.eval(fs.readFileSync(path.join(__dirname, "siteprogress.js"), "utf8"));
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));

  const navBtn = [...d.querySelectorAll(".spnav button")].find((b) => b.dataset.v === "siteprogress");
  navBtn.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 60));

  const initialCount = realFetches.length;
  ok(initialCount >= 1, `initial page load fetched /realistic at least once, got ${initialCount}`);

  // -------------------------------------------------- mark-rooms-done refreshes S.real
  const roomsBtn = d.querySelector('[data-roomsedit="QI1"]');
  roomsBtn.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 20));
  const boxes = [...d.querySelectorAll("#sp-modal input[data-room]")];
  boxes.forEach((c) => { c.checked = false; });
  boxes.find((c) => c.dataset.room === "r2").checked = true;

  realFetches.length = 0;
  d.querySelector("#sp-mark-done").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 20));
  ok(realFetches.length >= 1, "clicking 'Mark ticked as done' re-fetches /realistic (S.real), not just S.pnl");

  // -------------------------------------------------- undo also refreshes S.real
  roomsBtn.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 20));
  const boxes2 = [...d.querySelectorAll("#sp-modal input[data-room]")];
  boxes2.forEach((c) => { c.checked = false; });
  boxes2.find((c) => c.dataset.room === "r2").checked = true;

  realFetches.length = 0;
  d.querySelector("#sp-unmark-done").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 20));
  ok(realFetches.length >= 1, "clicking 'Undo done' also re-fetches /realistic");

  // -------------------------------------------------- quantity-group save refreshes S.real
  roomsBtn.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 20));
  const qtyInput = d.querySelector("#sp-roomqty");
  qtyInput.value = "5";
  const boxes3 = [...d.querySelectorAll("#sp-modal input[data-room]")];
  boxes3.forEach((c) => { c.checked = false; });
  boxes3.find((c) => c.dataset.room === "r3").checked = true;

  realFetches.length = 0;
  d.querySelector("#sp-modal-save").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 20));
  ok(realFetches.length >= 1, "saving a room quantity group re-fetches /realistic, so the drawer's order-quantity message can never go stale after this");

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
}

main().catch((e) => { console.error("ERROR:", e.stack); process.exit(1); });
