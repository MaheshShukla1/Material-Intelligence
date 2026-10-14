// jsdom test: ONE quantity for a whole floor / level in the Rooms/Zones dialog ("By zone | Whole B3").
//
// Reported case (billing person, Thoth Mall): the backup of an installation bill needs "B3 total = 420 Rmt" when the site
// did not measure zone by zone, and zone-wise numbers where it did. Rules pinned here:
//   * every level with 2+ zones that have a quantity gets the switch; a level with one zone, or a count item at 1 per room, does not;
//   * "Whole B3" shows ONE box (of the level's total plan) and hides the zone list; tick = the full plan, typing = partial;
//   * nothing is sent until "Mark ticked as done" -- then ONE area-total post (the level's rooms + qty), never a post per zone;
//   * a level the server already holds as a total opens in Whole mode with its figure; By zone brings the zones back unchanged;
//   * zones of a level in Whole mode never leak into the zone-wise posts (Select all included);
//   * replacing quantities that are already recorded asks first; Undo done on a ticked whole level clears the total.
import { JSDOM } from "jsdom";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
let pass = 0, fail = 0;
function ok(cond, msg) { if (cond) pass++; else { fail++; console.error("FAIL:", msg); } }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const click = (w, el) => el.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));

const CABLE = "39", LAMP = "52";
const B3 = ["b3z1", "b3z2", "b3z3", "b3z4", "b3z5", "b3z6", "b3z7"], B2 = ["b2z1", "b2z2", "b2z3", "b2z4", "b2z5", "b2z6", "b2z7"], B1 = ["b1z1"];
const lvl = (id, name, rooms) => ({ id, type: "level", name, children: rooms.map((r, i) => ({ id: r, type: "room", name: `Zone ${i + 1}`, children: [] })) });
const STRUCTURE = { kind: "mall", id: "p0", type: "project", name: "Thoth Mall", children: [lvl("L3", "B3", B3), lvl("L2", "B2", B2), lvl("L1", "B1", B1)] };

function item(code, unit, planned, desc) {
  return { code, desc, unit, sub: "Cable", acts: ["Cabling"], qty: planned, planned, used: 0, remaining: planned, pct: 0, mapped: true, rooms: 15,
    in_room: true, rate: 100, quick: false, done_val: 0, rem_val: 0, room_done: 0, room_progress: 0, room_pending: 15, room_total: 15,
    planned_override: false, has_room_groups: false };
}
const body = () => ({ service: "Fire", room: null, activities: ["Cabling"], mapping: { Cabling: [CABLE, LAMP] }, act_pct: { Cabling: 0 }, overall_pct: 0,
  items: [item(CABLE, "Rmt", 3000, "150 Sq.mm x 3.5C Al. cable"), item(LAMP, "Nos", 15, "Smoke detector")],
  pnl_by_activity: {}, pnl_totals: {}, pnl_unmapped_value: { items: 0 }, item_rooms: {}, item_room_qty: {}, item_progress: {}, unmapped: [],
  labour_only: {}, labour_pct: {}, labour_suggested: {} });
const rooms = (ids, planned, frac = 0) => Object.fromEntries(ids.map((r) => [r, { planned, frac }]));
const PLANS = {
  [CABLE]: () => ({ unit: "Rmt", rooms: { ...rooms(B3, 200), ...rooms(B2, 200), ...rooms(B1, 200) }, pools: [] }),
  [LAMP]: () => ({ unit: "Nos", rooms: { ...rooms(B3, 1), ...rooms(B2, 1), ...rooms(B1, 1) }, pools: [] }),
};

async function boot({ plan = PLANS[CABLE](), confirmAnswer = true } = {}) {
  const dom = new JSDOM(`<!doctype html><html><body><header class="top"><div class="brand"><p id="ctx"></p></div><div class="topact"></div></header><main></main></body></html>`,
    { url: "https://example.test/", runScripts: "dangerously", pretendToBeVisual: true });
  const w = dom.window, d = w.document, posts = [], asked = [];
  w.confirm = (m) => { asked.push(m); return confirmAnswer; };
  w.fetch = (url, opts) => {
    const u = String(url); let status = 200, b = {};
    if (opts && opts.method === "POST") posts.push({ url: u, body: JSON.parse(opts.body) });
    if (u === "/api/projects") b = [{ slug: "thoth-mall", project: "Thoth Mall", runs: 1, latest_run: "r1" }];
    else if (u === "/api/siteprogress/thoth-mall") b = { structure: STRUCTURE, has_boq: true, services: ["Fire"] };
    else if (u.startsWith("/api/siteprogress/thoth-mall/item-room-plan")) b = plan;
    else if (u.startsWith("/api/siteprogress/thoth-mall/service/") || u.includes("/mark-rooms-done") || u.includes("/progress/rooms") || u.includes("/progress/area-total")) b = body();
    else if (u.startsWith("/api/siteprogress/thoth-mall/pnl/")) b = { done_value: 0, remaining_value: 0 };
    else if (u.startsWith("/api/siteprogress/thoth-mall/realistic/")) b = { items: [], shortages: 0 };
    return Promise.resolve({ ok: status < 400, status, json: async () => b, text: async () => JSON.stringify(b) });
  };
  w.eval(fs.readFileSync(path.join(__dirname, "siteprogress.js"), "utf8"));
  await sleep(0); await sleep(0);
  click(w, [...d.querySelectorAll(".spnav button")].find((b) => b.dataset.v === "siteprogress"));
  await sleep(60);
  return { w, d, posts, asked };
}
async function open(ctx, code = CABLE) { click(ctx.w, ctx.d.querySelector(`[data-roomsedit="${code}"]`)); await sleep(60); return ctx.d.querySelector("#sp-modal"); }
const group = (m, name) => [...m.querySelectorAll(".sp-roomgroup")].find((g) => g.querySelector("b").textContent === name);
const seg = (g, mode) => g.querySelector(`.sp-areaseg button[data-m="${mode}"]`);
const abox = (g) => g.querySelector("input[data-areaqty]");
const acb = (g) => g.querySelector("input[data-area]");
const type = (w, inp, v) => { inp.value = String(v); inp.dispatchEvent(new w.Event("input", { bubbles: true })); inp.dispatchEvent(new w.Event("change", { bubbles: true })); };
const toggle = (w, cb, on) => { cb.checked = on; cb.dispatchEvent(new w.Event("change", { bubbles: true })); };
const visible = (el) => !!el && !el.hidden && !el.closest("[hidden]");

async function main() {
  // ================= 1. the switch exists per level; one-zone level and count-at-1 items have none
  let ctx = await boot(); let m = await open(ctx);
  const g3 = group(m, "B3"), g2 = group(m, "B2"), g1 = group(m, "B1");
  ok(!!seg(g3, "area") && !!seg(g2, "area"), "B3 and B2 (7 zones each) have the By zone | Whole switch");
  ok(seg(g3, "area").textContent === "Whole B3" && seg(g3, "zone").textContent === "By zone", `switch reads "By zone | Whole B3", got "${g3.querySelector(".sp-areaseg").textContent}"`);
  ok(!g1.querySelector(".sp-areaseg"), "B1 has one zone: no switch (the zone IS the level)");
  ok(g3.dataset.mode === "zone" && visible(g3.querySelector(".sp-roomcell:not(.sp-areacell)")) && !visible(g3.querySelector(".sp-areacell")), "starts By zone: zone list shown, whole-level box hidden");
  ok(/Whole/.test(m.querySelector("#sp-zhelp").textContent), "the help line mentions the whole-level switch");
  ctx = await boot({ plan: PLANS[LAMP]() }); m = await open(ctx, LAMP);
  ok(m && !m.querySelector(".sp-areaseg"), "a count item at 1 per zone keeps the dialog exactly as it was (ticks only, no switch)");

  // ================= 2. Whole B3: type 420 -> one area-total post, no per-zone post
  ctx = await boot(); m = await open(ctx);
  let g = group(m, "B3");
  click(ctx.w, seg(g, "area"));
  ok(g.dataset.mode === "area" && visible(g.querySelector(".sp-areacell")) && !visible(g.querySelector(".sp-roomcell:not(.sp-areacell)")), "Whole B3 shows one box and hides the 7 zones");
  ok(g.querySelector(".sp-areacell .u").textContent.replace(/\s+/g, " ").trim() === "of 1,400 Rmt".replace(",", "") || /of 1400 Rmt/.test(g.querySelector(".sp-areacell .u").textContent.replace(/\s+/g, " ")), `box shows the level's total plan, got "${g.querySelector(".sp-areacell .u").textContent}"`);
  ok(visible(g.querySelector(".sp-areahint")) && /one line/.test(g.querySelector(".sp-areahint").textContent), "a short note says the bill backup shows it as one line");
  ok(g.querySelector("[data-gall]").hidden && g.querySelector("[data-gnone]").hidden, "select all / clear of the hidden zones are hidden too");
  type(ctx.w, abox(g), 420);
  ok(acb(g).checked && g.querySelector(".sp-areacell").classList.contains("is-partial"), "typing 420 ticks the level and shows it partial");
  ctx.posts.length = 0;
  click(ctx.w, ctx.d.querySelector("#sp-mark-done")); await sleep(60);
  ok(ctx.posts.length === 1 && ctx.posts[0].url.includes("/progress/area-total"), `exactly ONE post, to /progress/area-total, got ${JSON.stringify(ctx.posts.map((p) => p.url))}`);
  ok(JSON.stringify(ctx.posts[0].body) === JSON.stringify({ service: "Fire", item_code: CABLE, rooms: B3, qty: 420 }), `body = B3's 7 rooms + 420, got ${JSON.stringify(ctx.posts[0].body)}`);
  ok(!ctx.d.querySelector("#sp-modal"), "dialog closes after saving");

  // ================= 3. tick only = the whole plan of that level
  ctx = await boot(); m = await open(ctx); g = group(m, "B3");
  click(ctx.w, seg(g, "area")); toggle(ctx.w, acb(g), true);
  ok(abox(g).value === "1400" && g.querySelector(".sp-areacell").classList.contains("is-full"), `ticking fills the level's full plan (1400), got ${abox(g).value}`);
  ctx.posts.length = 0; click(ctx.w, ctx.d.querySelector("#sp-mark-done")); await sleep(60);
  ok(ctx.posts[0] && ctx.posts[0].body.qty === 1400, `full tick posts the full plan, got ${JSON.stringify(ctx.posts[0] && ctx.posts[0].body)}`);

  // ================= 4. never above the plan
  ctx = await boot(); m = await open(ctx); g = group(m, "B3"); click(ctx.w, seg(g, "area"));
  type(ctx.w, abox(g), 5000);
  ok(abox(g).value === "1400", `5000 on a plan of 1400 is clamped, got ${abox(g).value}`);
  type(ctx.w, abox(g), -4);
  ok(!acb(g).checked && abox(g).value === "0", "a negative entry falls back to the recorded value and unticks");

  // ================= 5. switching back brings the zones back; zone-wise posts are exactly as before
  ctx = await boot(); m = await open(ctx); g = group(m, "B2");
  click(ctx.w, seg(g, "area")); click(ctx.w, seg(g, "zone"));
  ok(g.dataset.mode === "zone" && visible(g.querySelector(".sp-roomcell:not(.sp-areacell)")) && !visible(g.querySelector(".sp-areacell")), "By zone brings the zone list back");
  type(ctx.w, g.querySelector('input[data-roomqty="b2z1"]'), 60);
  ctx.posts.length = 0; click(ctx.w, ctx.d.querySelector("#sp-mark-done")); await sleep(60);
  ok(ctx.posts.length === 1 && ctx.posts[0].url.includes("/progress/rooms") && JSON.stringify(ctx.posts[0].body.entries) === JSON.stringify([{ room: "b2z1", qty: 60 }]),
    `a zone figure still goes to /progress/rooms, nothing area-wise, got ${JSON.stringify(ctx.posts)}`);

  // ================= 6. mixed: B3 as a total + B2 zone-wise, in one Mark ticked as done
  ctx = await boot(); m = await open(ctx);
  click(ctx.w, seg(group(m, "B3"), "area")); type(ctx.w, abox(group(m, "B3")), 420);
  type(ctx.w, group(m, "B2").querySelector('input[data-roomqty="b2z2"]'), 80);
  ctx.posts.length = 0; click(ctx.w, ctx.d.querySelector("#sp-mark-done")); await sleep(80);
  const urls = ctx.posts.map((p) => p.url.replace("/api/siteprogress/thoth-mall", ""));
  ok(urls.some((u) => u.startsWith("/progress/area-total")) && urls.some((u) => u.startsWith("/progress/rooms")), `both kinds saved, got ${JSON.stringify(urls)}`);
  const rp = ctx.posts.find((p) => p.url.includes("/progress/rooms"));
  ok(JSON.stringify(rp.body.entries) === JSON.stringify([{ room: "b2z2", qty: 80 }]), "B3's hidden zones are NOT in the zone-wise post");

  // ================= 7. Select all (typical) never ticks the hidden zones of a level in Whole mode
  ctx = await boot(); m = await open(ctx);
  click(ctx.w, seg(group(m, "B3"), "area"));
  click(ctx.w, m.querySelector("#sp-rooms-all"));
  ctx.posts.length = 0; click(ctx.w, ctx.d.querySelector("#sp-mark-done")); await sleep(80);
  const sent = ctx.posts.filter((p) => p.url.includes("/mark-rooms-done")).flatMap((p) => p.body.rooms);
  ok(sent.length === 8 && !B3.some((r) => sent.includes(r)), `Select all marks B2 + B1 zones only (${sent.length} sent), B3 is left to its own total`);
  ok(!ctx.posts.some((p) => p.url.includes("/progress/area-total")), "an untouched, unticked whole-level box posts nothing");

  // ================= 8. a level the server holds as a total opens in Whole mode
  const pooled = PLANS[CABLE](); Object.assign(pooled.rooms, rooms(B3, 200, 0.3)); pooled.pools = [{ area: "B3", rooms: B3, qty: 420 }];
  ctx = await boot({ plan: pooled }); m = await open(ctx); g = group(m, "B3");
  ok(g.dataset.mode === "area" && abox(g).value === "420" && !g.querySelector(".sp-areachip").hidden, "opens as Whole B3 with 420 and the 'total' chip");
  ok(group(m, "B2").dataset.mode === "zone" && group(m, "B2").querySelector(".sp-areachip").hidden, "other levels open By zone");
  ctx.posts.length = 0; click(ctx.w, ctx.d.querySelector("#sp-mark-done")); await sleep(60);
  ok(ctx.posts.length === 0, "opening and pressing Mark with nothing ticked sends nothing");

  // ================= 9. replacing figures that are already recorded asks first
  const spread = PLANS[CABLE](); Object.assign(spread.rooms, rooms(B3, 200, 0.1));     // 140 recorded, not a total
  ctx = await boot({ plan: spread, confirmAnswer: false }); m = await open(ctx); g = group(m, "B3");
  click(ctx.w, seg(g, "area")); type(ctx.w, abox(g), 420);
  ctx.posts.length = 0; click(ctx.w, ctx.d.querySelector("#sp-mark-done")); await sleep(60);
  ok(ctx.asked.length === 1 && /B3 already has 140 Rmt recorded/.test(ctx.asked[0]) && ctx.posts.length === 0, `asks before replacing 140 Rmt, and cancel sends nothing, got ${JSON.stringify(ctx.asked)}`);
  ctx = await boot({ plan: spread, confirmAnswer: true }); m = await open(ctx); g = group(m, "B3");
  click(ctx.w, seg(g, "area")); type(ctx.w, abox(g), 420);
  ctx.posts.length = 0; click(ctx.w, ctx.d.querySelector("#sp-mark-done")); await sleep(60);
  ok(ctx.posts.length === 1 && ctx.posts[0].body.qty === 420, "confirmed -> posted");

  // ================= 10. Undo done on a ticked whole level clears the total
  ctx = await boot({ plan: pooled }); m = await open(ctx); g = group(m, "B3");
  toggle(ctx.w, acb(g), true);
  ctx.posts.length = 0; click(ctx.w, ctx.d.querySelector("#sp-unmark-done")); await sleep(60);
  ok(ctx.posts.length === 1 && ctx.posts[0].url.includes("/progress/area-total") && ctx.posts[0].body.qty === 0, `Undo done posts qty 0 for the level, got ${JSON.stringify(ctx.posts)}`);

  // ================= 11. a server refusal is shown, dialog stays
  ctx = await boot(); m = await open(ctx); g = group(m, "B3");
  const realFetch = ctx.w.fetch;
  ctx.w.fetch = (u, o) => (String(u).includes("/progress/area-total") ? Promise.resolve({ ok: false, status: 400, json: async () => ({ detail: "nope" }), text: async () => JSON.stringify({ detail: "an area total belongs to ONE area" }) }) : realFetch(u, o));
  click(ctx.w, seg(g, "area")); type(ctx.w, abox(g), 100);
  click(ctx.w, ctx.d.querySelector("#sp-mark-done")); await sleep(60);
  ok(!!ctx.d.querySelector("#sp-modal") && /Failed/.test(ctx.d.body.textContent), "a refused save keeps the dialog open and says Failed");

  console.log(`${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
}
main().catch((e) => { console.error(e); process.exit(1); });
