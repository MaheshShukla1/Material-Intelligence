// jsdom test for the Rooms/Zones dialog's per-room DONE QUANTITY box.
//
// Reported scenario (Thoth Mall, live): a jet fan item planned at 9 Nos in
// Zone 5; the site has fitted 5. Ticking Zone 5 could only mean "9 done", and
// the dialog's one quantity box edits the PLAN, not the done qty.
// Rules pinned here:
//   * a room planned at >1 unit gets a number box (value = what is recorded,
//     "of 9 Nos" beside it); tick = the full plan; typing a number = partial
//     AND ticks the row;
//   * "Mark ticked as done" posts the typed quantity to /progress/rooms --
//     but only when a ticked row is genuinely partial; plain full ticks keep
//     using the original /mark-rooms-done exactly as before;
//   * a room planned at exactly 1 count, a room with no known plan, and a
//     failed plan fetch all leave the dialog exactly as it was (tick only);
//   * never above the plan; never a guessed number.
import { JSDOM } from "jsdom";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
let pass = 0, fail = 0;
function ok(cond, msg) { if (cond) pass++; else { fail++; console.error("FAIL:", msg); } }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const click = (w, el) => el.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));

const Z4 = "z4", Z5 = "z5", Z6 = "z6";
const FAN = "e#16", ONE = "h#11", NOPLAN = "q#1";

function item(code, unit, planned, desc) {
  return { code, desc, unit, sub: "Other", acts: ["Jet Fan Installation"], qty: planned, planned, used: 0, remaining: planned,
    pct: 0, mapped: true, rooms: 3, in_room: true, rate: 3875, quick: false, done_val: 0, rem_val: 0,
    room_done: 0, room_progress: 0, room_pending: 3, room_total: 3, planned_override: false, has_room_groups: true };
}
function serviceBody(progress = {}, groupRooms = [Z5]) {
  return {
    service: "HVAC", room: null, activities: ["Jet Fan Installation"],
    mapping: { "Jet Fan Installation": [FAN, ONE, NOPLAN] }, act_pct: { "Jet Fan Installation": 0 }, overall_pct: 0,
    items: [item(FAN, "Nos", 9, "2500 CFM Normal Mode (Zone-5)"), item(ONE, "Nos", 3, "Exhaust fan"), item(NOPLAN, "Nos", 4, "quick item")],
    pnl_by_activity: {}, pnl_totals: {}, pnl_unmapped_value: { items: 0 },
    item_rooms: {}, item_room_qty: { [FAN]: [{ rooms: groupRooms, qty: 9 }] },
    item_progress: progress, unmapped: [], labour_only: {}, labour_pct: {}, labour_suggested: {},
  };
}
const STRUCTURE = { kind: "mall", id: "p0", type: "project", name: "Thoth Mall",
  children: [{ id: "lv1", type: "level", name: "B1", children: [Z4, Z5, Z6].map((id, i) => ({ id, type: "room", name: `Zone ${i + 4}`, children: [] })) }] };

const PLANS = {
  [FAN]: { unit: "Nos", rooms: { [Z5]: { planned: 9, frac: 0 } } },
  [ONE]: { unit: "Nos", rooms: { [Z4]: { planned: 1, frac: 0 }, [Z5]: { planned: 1, frac: 0 }, [Z6]: { planned: 1, frac: 0 } } },
  [NOPLAN]: { unit: "Nos", rooms: { [Z4]: { planned: null, frac: 0 }, [Z5]: { planned: null, frac: 0 } } },
};

async function boot({ planFails = false, plans = PLANS, groupRooms = [Z5], structure = STRUCTURE } = {}) {
  const html = `<!doctype html><html><body>
    <header class="top"><div class="brand"><p id="ctx"></p></div><div class="topact"></div></header><main></main></body></html>`;
  const dom = new JSDOM(html, { url: "https://example.test/", runScripts: "dangerously", pretendToBeVisual: true });
  const w = dom.window, d = w.document;
  const posts = [], gets = [];
  w.fetch = (url, opts) => {
    const u = String(url);
    let status = 200, body = {};
    if (opts && opts.method === "POST") posts.push({ url: u, body: JSON.parse(opts.body) });
    else gets.push(u);
    if (u === "/api/projects") body = [{ slug: "thoth-mall", project: "Thoth Mall", runs: 1, latest_run: "r1" }];
    else if (u === "/api/siteprogress/thoth-mall") body = { structure, has_boq: true, services: ["HVAC"] };
    else if (u.startsWith("/api/siteprogress/thoth-mall/item-room-plan")) {
      if (planFails) { status = 500; body = { detail: "boom" }; }
      else body = plans[new URL(u, "https://x.test").searchParams.get("item_code")] || { rooms: {} };
    }
    else if (u.startsWith("/api/siteprogress/thoth-mall/service/") || u.includes("/mark-rooms-done") || u.includes("/progress/rooms")) body = serviceBody({}, groupRooms);
    else if (u.startsWith("/api/siteprogress/thoth-mall/pnl/")) body = { done_value: 0, remaining_value: 0 };
    else if (u.startsWith("/api/siteprogress/thoth-mall/realistic/")) body = { items: [], shortages: 0 };
    return Promise.resolve({ ok: status < 400, status, json: async () => body, text: async () => JSON.stringify(body) });
  };
  w.eval(fs.readFileSync(path.join(__dirname, "siteprogress.js"), "utf8"));
  await sleep(0); await sleep(0);
  click(w, [...d.querySelectorAll(".spnav button")].find((b) => b.dataset.v === "siteprogress"));
  await sleep(60);
  return { w, d, posts, gets };
}
async function openZones(ctx, code) {
  const btn = ctx.d.querySelector(`[data-roomsedit="${code}"]`);
  click(ctx.w, btn);
  await sleep(40);      // dialog opens, then the plan arrives
  return ctx.d.querySelector("#sp-modal");
}
const box = (m, id) => m.querySelector(`input[data-roomqty="${id}"]`);
const tick = (m, id) => m.querySelector(`input[data-room="${id}"]`);
const type = async (w, inp, v) => { inp.value = String(v); inp.dispatchEvent(new w.Event("input", { bubbles: true })); inp.dispatchEvent(new w.Event("change", { bubbles: true })); };
const toggle = (w, cb, on) => { cb.checked = on; cb.dispatchEvent(new w.Event("change", { bubbles: true })); };

async function main() {
  // ================= 1. the reported case: Zone 5 planned 9, 5 fitted
  let ctx = await boot();
  let m = await openZones(ctx, FAN);
  ok(!!m, "Zones dialog opened for e#16");
  ok(ctx.gets.some((u) => u.includes("/item-room-plan?service=HVAC&item_code=e%2316")), `plan fetched with query params (item code url-encoded), got ${JSON.stringify(ctx.gets.filter((u) => u.includes("plan")))}`);
  let b5 = box(m, Z5);
  ok(!!b5, "Zone 5 (plan 9 Nos) gets a quantity box");
  ok(b5 && b5.value === "0", `box starts at what is recorded (0), got ${b5 && b5.value}`);
  ok(m.querySelector(".sp-roomqty .u").textContent.replace(/\s+/g, " ").trim() === "of 9 Nos", `shows the plan beside the box, got "${m.querySelector(".sp-roomqty .u").textContent}"`);
  ok(!!m.querySelector("#sp-qty-hint") && /PLAN/.test(m.querySelector("#sp-qty-hint").textContent), "the top Quantity box is labelled as the PLAN box (the 9->5 trap)");
  ok(tick(m, Z5).checked === false, "nothing is ticked by default (unchanged rule)");

  await type(ctx.w, b5, 5);
  ok(tick(m, Z5).checked === true, "typing a number ticks the row");
  ok(b5.closest(".sp-roomcell").classList.contains("is-partial"), "5 of 9 is shown as partial");
  ctx.posts.length = 0;
  click(ctx.w, ctx.d.querySelector("#sp-mark-done"));
  await sleep(40);
  const pr = ctx.posts.find((p) => p.url.includes("/progress/rooms"));
  ok(!!pr, "Mark ticked as done posted to /progress/rooms");
  ok(pr && JSON.stringify(pr.body) === JSON.stringify({ service: "HVAC", item_code: FAN, entries: [{ room: Z5, qty: 5 }] }), `posted Zone 5 = 5 exactly, got ${JSON.stringify(pr && pr.body)}`);
  ok(!ctx.posts.some((p) => p.url.includes("/mark-rooms-done")), "the 100% route is NOT also called for it");
  ok(!ctx.posts.some((p) => p.url.includes("/item-room-qty")), "the PLAN (quantity group) is never touched by a done-quantity save");
  ok(!ctx.d.querySelector("#sp-modal"), "dialog closes after saving");

  // ================= 2. plain tick still means the full plan, via the ORIGINAL route
  ctx = await boot(); m = await openZones(ctx, FAN);
  toggle(ctx.w, tick(m, Z5), true);
  ok(box(m, Z5).value === "9", `ticking fills the full plan (9), got ${box(m, Z5).value}`);
  ok(box(m, Z5).closest(".sp-roomcell").classList.contains("is-full"), "ticked = shown as full");
  ctx.posts.length = 0;
  click(ctx.w, ctx.d.querySelector("#sp-mark-done")); await sleep(40);
  ok(ctx.posts.length === 1 && ctx.posts[0].url.includes("/mark-rooms-done") && ctx.posts[0].body.done === true && ctx.posts[0].body.rooms[0] === Z5,
    `a plain full tick uses the original /mark-rooms-done unchanged, got ${JSON.stringify(ctx.posts)}`);

  // untick puts back what was recorded
  ctx = await boot(); m = await openZones(ctx, FAN);
  await type(ctx.w, box(m, Z5), 5);
  toggle(ctx.w, tick(m, Z5), false);
  ok(box(m, Z5).value === "0", "unticking puts the recorded value back (0)");
  toggle(ctx.w, tick(m, Z5), true);
  ok(box(m, Z5).value === "9", "ticking again = full plan, a previously typed number does not linger");

  // ================= 3. never above the plan
  ctx = await boot(); m = await openZones(ctx, FAN);
  await type(ctx.w, box(m, Z5), 12);
  ok(box(m, Z5).value === "9", `12 on a plan of 9 is clamped to 9, got ${box(m, Z5).value}`);
  ok(/raise the plan/i.test((ctx.d.querySelector(".sp-toast, #sp-toast, .toast") || ctx.d.body).textContent), "told why: raise the plan first");
  await type(ctx.w, box(m, Z5), -3);
  ok(box(m, Z5).value === "0" && tick(m, Z5).checked === false, "a negative/blank entry falls back to the recorded value and unticks");

  // ================= 4. Select all / clear drive the boxes (no change event on those buttons)
  ctx = await boot(); m = await openZones(ctx, FAN);
  click(ctx.w, m.querySelector("#sp-rooms-all"));
  ok(box(m, Z5).value === "9", "Select all fills the full plan");
  click(ctx.w, m.querySelector("#sp-rooms-none"));
  ok(box(m, Z5).value === "0", "Clear all puts the recorded value back");
  click(ctx.w, m.querySelector("#sp-rooms-all"));
  ctx.posts.length = 0;
  click(ctx.w, ctx.d.querySelector("#sp-mark-done")); await sleep(40);
  ok(ctx.posts.length === 1 && ctx.posts[0].url.includes("/mark-rooms-done"), "Select all + Mark done = the original full route, as before");

  // ================= 5. recorded partial shows up when reopened
  ctx = await boot({ plans: { ...PLANS, [FAN]: { unit: "Nos", rooms: { [Z5]: { planned: 9, frac: 5 / 9 } } } } });
  m = await openZones(ctx, FAN);
  ok(box(m, Z5).value === "5", `recorded 5/9 shows as 5, got ${box(m, Z5).value}`);
  ok(box(m, Z5).closest(".sp-roomcell").classList.contains("is-partial"), "recorded partial is shown amber");
  ctx.posts.length = 0;
  click(ctx.w, ctx.d.querySelector("#sp-unmark-done")); await sleep(40);
  ok(ctx.posts.length === 0, "Undo with nothing ticked still does nothing (unchanged guard)");

  // ================= 6. two zones, one partial + one full, in one save
  const two = { ...PLANS, [FAN]: { unit: "Nos", rooms: { [Z5]: { planned: 9, frac: 0 }, [Z6]: { planned: 6, frac: 0 } } } };
  ctx = await boot({ plans: two, groupRooms: [Z5, Z6] });
  m = await openZones(ctx, FAN);
  ok(!!box(m, Z5) && !!box(m, Z6), "both zones get a box");
  await type(ctx.w, box(m, Z5), 5);
  toggle(ctx.w, tick(m, Z6), true);
  ok(box(m, Z6).value === "6", "the second zone, just ticked, shows its own full plan (6)");
  ctx.posts.length = 0;
  click(ctx.w, ctx.d.querySelector("#sp-mark-done")); await sleep(40);
  const p2 = ctx.posts.filter((x) => x.url.includes("/progress/rooms"));
  ok(p2.length === 1 && JSON.stringify(p2[0].body.entries) === JSON.stringify([{ room: Z5, qty: 5 }, { room: Z6, qty: 6 }]),
    `partial + full ticks go in ONE request, got ${JSON.stringify(p2.map((x) => x.body))}`);
  ok(!ctx.posts.some((x) => x.url.includes("/mark-rooms-done")), "nothing left over for the 100% route");

  // ================= 7. nothing to say beyond a tick: plan 1 per room, unknown plan, failed fetch
  ctx = await boot(); m = await openZones(ctx, ONE);
  ok(!!m && m.querySelectorAll("input[data-roomqty]").length === 0, "plan = 1 Nos per room: no number box, tick only (hotel 1-fan-per-room)");
  ok(!m.querySelector("#sp-qty-hint"), "...and no extra hint");
  toggle(ctx.w, tick(m, Z4), true);
  ctx.posts.length = 0;
  click(ctx.w, ctx.d.querySelector("#sp-mark-done")); await sleep(40);
  ok(ctx.posts.length === 1 && ctx.posts[0].url.includes("/mark-rooms-done"), "1-per-room tick uses the original route");

  ctx = await boot(); m = await openZones(ctx, NOPLAN);
  ok(!!m && m.querySelectorAll("input[data-roomqty]").length === 0, "unknown plan: no box, never a guessed number");

  ctx = await boot({ planFails: true }); m = await openZones(ctx, FAN);
  ok(!!m && m.querySelectorAll("input[data-roomqty]").length === 0, "plan fetch fails: the dialog is exactly what it was");
  toggle(ctx.w, tick(m, Z5), true);
  ctx.posts.length = 0;
  click(ctx.w, ctx.d.querySelector("#sp-mark-done")); await sleep(40);
  ok(ctx.posts.length === 1 && ctx.posts[0].url.includes("/mark-rooms-done"), "...and Mark ticked as done still works");

  // ================= 8. closing before the plan arrives never paints into a new dialog
  ctx = await boot();
  click(ctx.w, ctx.d.querySelector(`[data-roomsedit="${FAN}"]`));
  click(ctx.w, ctx.d.querySelector("#sp-modal-x"));        // closed immediately
  await sleep(40);
  ok(!ctx.d.querySelector("#sp-modal"), "closed dialog stays closed (late plan is ignored)");

  // ================= 9. dialog design: short help, plan box tucked away, state styling
  ctx = await boot(); m = await openZones(ctx, FAN);
  ok(m.classList.contains("sp-zones"), "Zones dialog is scoped with .sp-zones (styling never leaks to other dialogs)");
  ok(m.querySelector(".sheethd p").textContent.trim().length < 160, "header is one short instruction, no wall of text");
  const planInp = m.querySelector("#sp-roomqty");
  ok(!!planInp && !!planInp.closest(".sp-planbox") && !m.querySelector("details"), "planned-quantity box is always visible (not tucked into a collapsed section)");
  ok(/planned quantity/i.test(m.querySelector(".sp-planbox label").textContent), "its label says it is the PLANNED quantity");
  ok(/PLAN/.test(m.querySelector("#sp-qty-hint").textContent) && /next to each name/.test(m.querySelector("#sp-qty-hint").textContent) && !/zone|room/i.test(m.querySelector("#sp-qty-hint").textContent), "hint right under it says: this is the plan; what is fitted goes in the box next to each name (no zone/room noun)");
  ok(/part of it|partial/i.test(m.querySelector("#sp-zhelp").textContent) && !/zone|room/i.test(m.querySelector("#sp-zhelp").textContent), "one short noun-free help line explains typing a number for a partial quantity");
  let cell5 = box(m, Z5).closest(".sp-roomcell");
  ok(!cell5.classList.contains("is-on"), "untouched zone is not highlighted");
  type(ctx.w, box(m, Z5), 5);
  ok(cell5.classList.contains("is-on") && cell5.classList.contains("is-partial") && !cell5.classList.contains("is-full"), "typing 5 of 9: ticked + partial");
  toggle(ctx.w, tick(m, Z5), false);
  ok(!cell5.classList.contains("is-on"), "unticking clears the highlight");
  toggle(ctx.w, tick(m, Z5), true);
  ok(cell5.classList.contains("is-on") && cell5.classList.contains("is-full"), "ticking = full plan: ticked + full");
  click(ctx.w, ctx.d.querySelector("#sp-rooms-none"));
  ok(!cell5.classList.contains("is-on"), "Clear all clears the highlight");
  // the plan box still saves the PLAN exactly as before
  ctx.posts.length = 0;
  m.querySelector("#sp-roomqty").value = "9";
  toggle(ctx.w, tick(m, Z5), true);
  click(ctx.w, ctx.d.querySelector("#sp-modal-save")); await sleep(40);
  ok(ctx.posts.length === 1 && ctx.posts[0].url.includes("/item-room-qty") && ctx.posts[0].body.qty === 9, `Save zones with the plan box still posts /item-room-qty, got ${JSON.stringify(ctx.posts.map((p) => p.url))}`);

  // ================= 10. wording follows the project type (mall = zones; hotel/hospital/other = areas)
  // and the new text never names the wrong thing, whatever the area is called
  // (Banquet Hall, Lobby, Zone 5, Room 101 ...).
  const HOTEL = { kind: "hotel", id: "p0", type: "project", name: "Hyatt",
    children: [{ id: "fl1", type: "floor", name: "Ground", children: [
      { id: Z4, type: "room", name: "Banquet Hall", children: [] }, { id: Z5, type: "room", name: "Room 101", children: [] }, { id: Z6, type: "room", name: "Lobby", children: [] }] }] };
  const words = (mm) => ({
    title: mm.querySelector("h2").textContent,
    instr: mm.querySelector(".sheethd p").textContent.split(" — ").slice(1).join(" — "),
    label: mm.querySelector('label[for="sp-roomqty"]').textContent,
    hint: mm.querySelector("#sp-qty-hint").textContent, help: mm.querySelector("#sp-zhelp").textContent,
    save: mm.querySelector("#sp-modal-save").textContent });
  ctx = await boot({ structure: HOTEL, groupRooms: [Z4, Z5, Z6], plans: { [FAN]: { unit: "Nos", rooms: { [Z4]: { planned: 9, frac: 0 }, [Z5]: { planned: 9, frac: 0 }, [Z6]: { planned: 9, frac: 0 } } } } });
  m = await openZones(ctx, FAN);
  let wd = words(m), all = Object.values(wd).join(" | ");
  ok(/areas/i.test(wd.label) && /Areas/.test(wd.title) && wd.save === "Save areas", `hotel: label/title/save say areas (a leaf can be a banquet hall, lobby, room...), got ${JSON.stringify(wd)}`);
  ok(!/zone|room/i.test(all), `hotel: neither "zone" nor "room" anywhere in the dialog wording, got ${all}`);
  ok(m.querySelectorAll("input[data-roomqty]").length === 3 && /Banquet Hall/.test(m.textContent), "hotel: a Banquet Hall / Lobby row gets the same number box as any room");
  ctx = await boot(); m = await openZones(ctx, FAN);
  wd = words(m); all = Object.values(wd).join(" | ");
  ok(/zones/i.test(wd.label) && /Zones/.test(wd.title) && wd.save === "Save zones", `mall: label/title/save say zones, got ${JSON.stringify(wd)}`);
  ok(!/room|area/i.test(all), `mall: no stray "room"/"area" anywhere in the dialog text, got ${all}`);

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
}
main().catch((e) => { console.error("ERROR:", e.stack); process.exit(1); });
