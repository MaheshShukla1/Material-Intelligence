// jsdom test proving every user-visible "Room"/"Zone" string in
// siteprogress.js is structure-kind-aware (curLeaf() family), not
// hardcoded -- the actual bug reported: a mall project's UI kept saying
// "room" everywhere except the one BOQ-upload wizard that already used
// leafLabel(). Same harness pattern as the other siteprogress.js tests.
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

const ZONE_IDS = ["z1", "z2", "z3", "z4"];

function serviceBody() {
  return {
    service: "Electrical", room: null,
    activities: ["Ceiling"],
    mapping: { Ceiling: ["QI1"] },
    act_pct: { Ceiling: 0 },
    overall_pct: 0,
    items: [{
      code: "QI1", desc: "MS CONDUIT 25MM", unit: "MTR", sub: "Conduit",
      acts: ["Ceiling"], qty: 63, planned: 252, used: 0, remaining: 252,
      pct: 0, mapped: true, rooms: 4, in_room: true, rate: null, quick: false,
      done_val: null, rem_val: null,
      room_done: 0, room_progress: 0, room_pending: 4, room_total: 4,
      planned_override: false, has_room_groups: false,
    }],
    pnl_by_activity: {}, pnl_totals: {}, pnl_unmapped_value: { items: 0 },
    item_rooms: {}, item_room_qty: {}, item_progress: {},
    unmapped: [], labour_only: {}, labour_pct: {}, labour_suggested: {},
  };
}

function mallStructureBody() {
  return {
    kind: "mall", id: "p0", type: "project", name: "Thoth Mall",
    children: [{
      id: "l1", type: "level", name: "Level 1",
      children: ZONE_IDS.map((id, i) => ({ id, type: "room", name: `Zone ${i + 1}`, children: [] })),
    }],
  };
}

function overallBody() {
  return {
    pct_value_done: 0, done_value: 0, remaining_value: 0, planned_value: 0,
    waste_value: 0, waste_caveat: null,
    by_service: { Electrical: { pct: 0, items: 1, done_value: 0, remaining_value: 0 } },
    rooms_summary: { done: 0, in_progress: 0, not_started: 4, total: 4 },
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

  let lastPrompt = null;
  window.prompt = (msg, def) => { lastPrompt = msg; return null; };   // cancel -- we only inspect the message

  window.fetch = (url) => {
    const u = String(url);
    let body = {};
    if (u === "/api/projects") body = [{ slug: "thoth-mall", project: "Thoth Mall", runs: 1, latest_run: "r1" }];
    else if (u === "/api/siteprogress/thoth-mall") body = { structure: mallStructureBody(), has_boq: true, services: ["Electrical"], rooms: 4 };
    else if (u.startsWith("/api/siteprogress/thoth-mall/service/")) body = serviceBody();
    else if (u.startsWith("/api/siteprogress/thoth-mall/overall")) body = overallBody();
    else if (u.startsWith("/api/siteprogress/thoth-mall/pnl/")) body = { done_value: 0, remaining_value: 0 };
    else if (u.startsWith("/api/siteprogress/thoth-mall/realistic/")) body = { items: [], shortages: 0 };
    return Promise.resolve({ ok: true, status: 200, json: async () => body, text: async () => JSON.stringify(body) });
  };

  window.eval(fs.readFileSync(path.join(__dirname, "siteprogress.js"), "utf8"));
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));

  const navBtn = [...d.querySelectorAll(".spnav button")].find((b) => b.dataset.v === "siteprogress");
  navBtn.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 60));

  // -------------------------------------------------- hero "whole site" stat (Overall pill)
  const overallPill = d.querySelector('[data-s="__overall__"]');
  ok(!!overallPill, "the Overall pill rendered");
  overallPill.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 60));

  const heroLabels = [...d.querySelectorAll(".sp-stat .l")].map((e) => e.textContent);
  ok(heroLabels.some((t) => t.includes("Zones — whole site")),
    `hero stat says "Zones — whole site" for a mall project, got: ${JSON.stringify(heroLabels)}`);
  ok(!heroLabels.some((t) => t.includes("Rooms — whole site")), "hero stat never says 'Rooms' for a mall project");

  // back to the Electrical service pill for the rest of the checks
  const elecPill = d.querySelector('[data-s="Electrical"]');
  ok(!!elecPill, "the Electrical service pill rendered");
  elecPill.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 60));

  // -------------------------------------------------- tree hint text
  const hint = [...d.querySelectorAll(".sub")].map((e) => e.textContent).find((t) => t.includes("Type:"));
  ok(!!hint && hint.includes("zones"), `structure hint says "zones" not "rooms", got: "${hint}"`);

  // -------------------------------------------------- Rooms modal
  const roomsBtn = d.querySelector('[data-roomsedit="QI1"]');
  ok(!!roomsBtn, "the item's Rooms-edit icon rendered");
  roomsBtn.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 20));

  const modalTitle = d.querySelector("#sp-modal h2");
  ok(!!modalTitle && modalTitle.textContent.startsWith("Zones →"),
    `modal title says "Zones → QI1" for a mall project, got "${modalTitle && modalTitle.textContent}"`);

  const modalSub = d.querySelector("#sp-modal p");
  ok(!!modalSub && modalSub.textContent.includes("zones") && !modalSub.textContent.includes("rooms"),
    `modal subtitle uses "zones" throughout, never "rooms" -- got: "${modalSub && modalSub.textContent}"`);

  const qtyLabel = d.querySelector('label[for="sp-roomqty"]');
  ok(!!qtyLabel && qtyLabel.textContent.includes("zones"), `quantity label says "zones", got "${qtyLabel && qtyLabel.textContent}"`);

  const saveBtn = d.querySelector("#sp-modal-save");
  ok(!!saveBtn && saveBtn.textContent === "Save zones", `save button says "Save zones", got "${saveBtn && saveBtn.textContent}"`);

  const markBtn = d.querySelector("#sp-mark-done");
  ok(!!markBtn, "'Mark ticked as done' button present regardless of structure kind");

  // -------------------------------------------------- roomsChipLabel on the item row
  const chip = [...d.querySelectorAll("[data-roomsedit]")].map((b) => b.textContent).join(" ");
  ok(chip.includes("zones") && !chip.includes("rooms"), `item row's room/zone chip says "zones", got: "${chip}"`);

  // -------------------------------------------------- addNode() prompt (the mall zone-add bug)
  window.closeModal ? null : null;   // no-op, keeping structure symmetric
  const addBtn = d.querySelector('[data-add="l1"]');
  ok(!!addBtn, "the Level's 'add child' icon rendered");
  addBtn.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  ok(!!lastPrompt && lastPrompt.startsWith("New Zone name"),
    `adding a leaf under a mall's Level prompts "New Zone name:", not "New room name:" -- got "${lastPrompt}"`);

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
}

main().catch((e) => { console.error("ERROR:", e.stack); process.exit(1); });
