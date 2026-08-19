// jsdom test for the Rooms modal's "Mark ticked as done" action -- reuses
// the SAME room-tick grid as the quantity save, no per-room toggle. Same
// harness pattern as the other siteprogress.js tests.
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
    item_rooms: {}, item_room_qty: {},
    // r1 already done (1.0), r2 partially progressed, r3/r4 untouched
    item_progress: { QI1: { r1: 1.0, r2: 0.4 } },
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

  const posts = [];
  window.fetch = (url, opts) => {
    const u = String(url);
    if (opts && opts.method === "POST") posts.push({ url: u, body: JSON.parse(opts.body) });
    let body = {};
    if (u === "/api/projects") body = [{ slug: "hyatt-hotel", project: "Hyatt Hotel", runs: 1, latest_run: "r1" }];
    else if (u === "/api/siteprogress/hyatt-hotel") body = { structure: structureBody(), has_boq: true, services: ["HVAC"] };
    else if (u.startsWith("/api/siteprogress/hyatt-hotel/service/") ||
             u.startsWith("/api/siteprogress/hyatt-hotel/mark-rooms-done") ||
             u.startsWith("/api/siteprogress/hyatt-hotel/item-room-qty")) body = serviceBody();
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

  const roomsBtn = d.querySelector('[data-roomsedit="QI1"]');
  ok(!!roomsBtn, "the item's Rooms-edit icon rendered");
  roomsBtn.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 20));

  const modalEl = d.querySelector("#sp-modal");
  ok(!!modalEl, "the Rooms modal opened");

  // -------------------------------------------------- no per-room toggle
  const perRoomToggles = modalEl.querySelectorAll("[data-markdone]");
  ok(perRoomToggles.length === 0, "there is NO per-room done toggle -- one shared action only");

  // -------------------------------------------------- done indicator
  const r1Label = [...modalEl.querySelectorAll("label")].find((l) => l.textContent.includes("Room 1"));
  ok(!!r1Label && r1Label.querySelector(".sp-doneck"), "Room 1 (already 100%) shows the done checkmark");
  const r3Label = [...modalEl.querySelectorAll("label")].find((l) => l.textContent.includes("Room 3"));
  ok(!!r3Label && !r3Label.querySelector(".sp-doneck"), "Room 3 (untouched) does NOT show the checkmark");

  // -------------------------------------------------- the shared button exists
  const markBtn = d.querySelector("#sp-mark-done");
  ok(!!markBtn, "'Mark ticked as done' button rendered");
  const saveBtn = d.querySelector("#sp-modal-save");
  ok(!!saveBtn && saveBtn.textContent === "Save rooms", "the original Save rooms button is untouched, still present");

  // -------------------------------------------------- ticking + marking done
  const boxes = [...modalEl.querySelectorAll("input[data-room]")];
  boxes.forEach((c) => { c.checked = false; });
  const r3Box = boxes.find((c) => c.dataset.room === "r3");
  const r4Box = boxes.find((c) => c.dataset.room === "r4");
  r3Box.checked = true; r4Box.checked = true;

  posts.length = 0;
  markBtn.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 20));

  const markPost = posts.find((p) => p.url.includes("/mark-rooms-done"));
  ok(!!markPost, "clicking 'Mark ticked as done' posted to /mark-rooms-done");
  ok(markPost && markPost.body.item_code === "QI1" &&
    JSON.stringify([...markPost.body.rooms].sort()) === JSON.stringify(["r3", "r4"]),
    `posted exactly the ticked rooms (r3, r4), got ${JSON.stringify(markPost && markPost.body)}`);
  ok(markPost && markPost.body.done === true, `mark-done posts done:true, got ${JSON.stringify(markPost && markPost.body)}`);
  ok(!posts.some((p) => p.url.includes("/item-room-qty") || p.url.includes("/item-rooms")),
    "marking done does NOT also touch the quantity/applicability routes -- fully independent action");

  // -------------------------------------------------- empty selection guarded
  const modalStillOpen = d.querySelector("#sp-modal");
  ok(!modalStillOpen, "modal closed after a successful mark-done (matches the normal save flow)");

  // -------------------------------------------------- undo done
  roomsBtn.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 20));
  const modal2 = d.querySelector("#sp-modal");
  ok(!!modal2, "Rooms modal re-opened for the undo check");

  const undoBtn = d.querySelector("#sp-unmark-done");
  ok(!!undoBtn, "'Undo done' button rendered alongside 'Mark ticked as done'");
  ok(undoBtn.textContent === "Undo done", `button reads "Undo done", got "${undoBtn.textContent}"`);

  const boxes2 = [...modal2.querySelectorAll("input[data-room]")];
  boxes2.forEach((c) => { c.checked = false; });
  boxes2.find((c) => c.dataset.room === "r1").checked = true;   // r1 is already done (mocked)

  posts.length = 0;
  undoBtn.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 20));

  const undoPost = posts.find((p) => p.url.includes("/mark-rooms-done"));
  ok(!!undoPost, "clicking 'Undo done' posted to the SAME /mark-rooms-done route");
  ok(undoPost && undoPost.body.done === false, `undo posts done:false, got ${JSON.stringify(undoPost && undoPost.body)}`);
  ok(undoPost && JSON.stringify(undoPost.body.rooms) === JSON.stringify(["r1"]),
    `undo posted exactly the ticked room (r1), got ${JSON.stringify(undoPost && undoPost.body)}`);

  // empty-selection guard applies to undo too
  roomsBtn.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 20));
  const modal3 = d.querySelector("#sp-modal");
  modal3.querySelectorAll("input[data-room]").forEach((c) => { c.checked = false; });
  posts.length = 0;
  d.querySelector("#sp-unmark-done").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 20));
  ok(!posts.some((p) => p.url.includes("/mark-rooms-done")), "undo with nothing ticked does not fire a request");
  ok(!!d.querySelector("#sp-modal"), "modal stays open when the tick-list is empty");

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
}

main().catch((e) => { console.error("ERROR:", e.stack); process.exit(1); });
