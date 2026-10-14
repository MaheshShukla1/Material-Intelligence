// jsdom test: "Remove plan" for ONE item in the Zones dialog.
//
// Reported case (Thoth Mall, perforated cable tray, Rmt, 21 zones B3/B2/B1 x 7, item planned 1,150): the dialog said "Whole B3 of 383.33 Rmt"
// and "of 54.76 Rmt" on every zone -- the item's total split equally, never typed by anyone -- and a Whole / zone could not take more than that.
// Rules pinned here:
//   * an item with an equal-share plan offers "Remove plan for this item" inside the plan box; an item whose plan is quantity groups does not;
//   * one click posts /item-plan-off and reopens the dialog: no "of X" and no max on any box, a tick does NOT fill a quantity,
//     a notice shows the item total / recorded / left and a "Restore plan" link;
//   * a zone / Whole B3 posts exactly the typed quantity, even above the old share (500 on a 383.33 share);
//   * a tick with no quantity is not sent at all (toast), existing recorded zones are left alone, Undo still works;
//   * restore asks first; a plan that was never removed behaves exactly as before (capped, tick fills the share).
import { JSDOM } from "jsdom";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
let pass = 0, fail = 0;
function ok(cond, msg) { if (cond) pass++; else { fail++; console.error("FAIL:", msg); } }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const click = (w, el) => el.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));

const TRAY = "ii2";
const B3 = [1, 2, 3, 4, 5, 6, 7].map((i) => `b3z${i}`), B2 = [1, 2, 3, 4, 5, 6, 7].map((i) => `b2z${i}`), B1 = [1, 2, 3, 4, 5, 6, 7].map((i) => `b1z${i}`);
const lvl = (id, name, rs) => ({ id, type: "level", name, children: rs.map((r, i) => ({ id: r, type: "room", name: `Zone ${i + 1}`, children: [] })) });
const STRUCTURE = { kind: "mall", id: "p0", type: "project", name: "Thoth Mall", children: [lvl("L3", "B3", B3), lvl("L2", "B2", B2), lvl("L1", "B1", B1)] };
const ALL = [...B3, ...B2, ...B1], SHARE = 1150 / 21;

function item(over = {}) {
  return { code: TRAY, desc: "14 G.G.I Perforated 300 X 50", unit: "Rmt", sub: "Tray", acts: ["Tray"], qty: 1150, planned: 1150, used: 0, remaining: 1150, pct: 0, mapped: true, rooms: 21,
    in_room: true, rate: 100, quick: false, done_val: 0, rem_val: 0, room_done: 0, room_progress: 0, room_pending: 21, room_total: 21, planned_override: true, has_room_groups: false, ...over };
}
function bodyOf(st) {
  return { service: "HVAC", room: null, activities: ["Tray"], mapping: { Tray: [TRAY] }, act_pct: { Tray: 0 }, overall_pct: 0, items: [item()], pnl_by_activity: {}, pnl_totals: {},
    pnl_unmapped_value: { items: 0 }, item_rooms: {}, item_room_qty: st.groups ? { [TRAY]: [{ rooms: B3, qty: 100 }] } : {}, item_progress: {}, unmapped: [], labour_only: {}, labour_pct: {}, labour_suggested: {} };
}
const planOf = (st) => {
  const rooms = Object.fromEntries(ALL.map((r) => [r, { planned: SHARE, frac: st.rec[r] ? st.rec[r] / SHARE : 0 }]));
  const p = { unit: "Rmt", rooms, pools: [], free: st.free };
  if (st.free) { p.total = 1150; p.used = Object.values(st.rec).reduce((a, b) => a + b, 0); }
  return p;
};

async function boot(st = { free: false, groups: false, rec: {} }, { confirmAnswer = true } = {}) {
  const dom = new JSDOM(`<!doctype html><html><body><header class="top"><div class="brand"><p id="ctx"></p></div><div class="topact"></div></header><main></main></body></html>`,
    { url: "https://example.test/", runScripts: "dangerously", pretendToBeVisual: true });
  const w = dom.window, d = w.document, posts = [], asked = [], toasts = [];
  w.confirm = (m) => { asked.push(m); return confirmAnswer; };
  w.fetch = (url, opts) => {
    const u = String(url); let status = 200, b = {};
    if (opts && opts.method === "POST") {
      posts.push({ url: u, body: JSON.parse(opts.body) });
      if (u.includes("/item-plan-off")) st.free = !!JSON.parse(opts.body).off;
    }
    if (u === "/api/projects") b = [{ slug: "thoth-mall", project: "Thoth Mall", runs: 1, latest_run: "r1" }];
    else if (u === "/api/siteprogress/thoth-mall") b = { structure: STRUCTURE, has_boq: true, services: ["HVAC"] };
    else if (u.startsWith("/api/siteprogress/thoth-mall/item-room-plan")) b = planOf(st);
    else if (u.startsWith("/api/siteprogress/thoth-mall/service/") || u.includes("/mark-rooms-done") || u.includes("/progress/rooms") || u.includes("/progress/area-total") || u.includes("/item-plan-off")) b = bodyOf(st);
    else if (u.startsWith("/api/siteprogress/thoth-mall/pnl/")) b = { done_value: 0, remaining_value: 0 };
    else if (u.startsWith("/api/siteprogress/thoth-mall/realistic/")) b = { items: [], shortages: 0 };
    return Promise.resolve({ ok: status < 400, status, json: async () => b, text: async () => JSON.stringify(b) });
  };
  w.eval(fs.readFileSync(path.join(__dirname, "siteprogress.js"), "utf8"));
  await sleep(0); await sleep(0);
  click(w, [...d.querySelectorAll(".spnav button")].find((b) => b.dataset.v === "siteprogress"));
  await sleep(60);
  return { w, d, posts, asked, st };
}
async function open(ctx) { click(ctx.w, ctx.d.querySelector(`[data-roomsedit="${TRAY}"]`)); await sleep(60); return ctx.d.querySelector("#sp-modal"); }
const group = (m, name) => [...m.querySelectorAll(".sp-roomgroup")].find((g) => g.querySelector("b").textContent === name);
const seg = (g, mode) => g.querySelector(`.sp-areaseg button[data-m="${mode}"]`);
const abox = (g) => g.querySelector("input[data-areaqty]");
const acb = (g) => g.querySelector("input[data-area]");
const zbox = (m, id) => m.querySelector(`input[data-roomqty="${id}"]`);
const type = (w, inp, v) => { inp.value = String(v); inp.dispatchEvent(new w.Event("input", { bubbles: true })); inp.dispatchEvent(new w.Event("change", { bubbles: true })); };
const toggle = (w, cb, on) => { cb.checked = on; cb.dispatchEvent(new w.Event("change", { bubbles: true })); };
const toastText = (d) => [...d.querySelectorAll(".toast, #toast, [role=status]")].map((e) => e.textContent).join(" | ");
const visible = (el) => !!el && !el.hidden && !el.closest("[hidden]");
const apiPath = (u) => u.replace("/api/siteprogress/thoth-mall", "").replace(/\?.*/, "");

async function main() {
  // ================= 1. before: the reported screen, plus the offer
  let ctx = await boot(); let m = await open(ctx);
  ok(/of 54\.76 Rmt/.test(zbox(m, "b3z1").parentElement.textContent), `the reported "of 54.76 Rmt" is still there before removing, got "${zbox(m, "b3z1").parentElement.textContent}"`);
  ok(zbox(m, "b3z1").max === "54.7619047619" || Number(zbox(m, "b3z1").max) > 54.7, "box is capped at the share while the plan is on");
  const rm = m.querySelector("#sp-plan-remove");
  ok(!!rm && /Remove plan for this item/.test(rm.textContent) && m.querySelector(".sp-planbox").contains(rm), "the plan box offers 'Remove plan for this item'");
  ok(visible(m.querySelector(".sp-planbox")) && !m.querySelector("#sp-plan-restore"), "plan box stays visible, no restore link yet");

  // plan on: tick still fills the share, typed qty still capped (nothing changed for a hotel-style item)
  toggle(ctx.w, m.querySelector('input[data-room="b3z1"]'), true);
  ok(Math.abs(Number(zbox(m, "b3z1").value) - 54.76) < 0.01, "plan on: a tick fills the zone's share");
  type(ctx.w, zbox(m, "b3z2"), 200);
  ok(Math.abs(Number(zbox(m, "b3z2").value) - 54.76) < 0.01, "plan on: 200 typed is capped at the share");

  // ================= 2. an item with quantity groups is not offered it
  ctx = await boot({ free: false, groups: true, rec: {} }); m = await open(ctx);
  ok(!m.querySelector("#sp-plan-remove"), "an item whose plan is quantity groups does not show 'Remove plan' (groups have their own Remove)");

  // ================= 3. remove: one post, dialog reopens plan-less
  ctx = await boot(); m = await open(ctx);
  ctx.posts.length = 0;
  click(ctx.w, m.querySelector("#sp-plan-remove")); await sleep(120);
  ok(ctx.posts.length === 1 && apiPath(ctx.posts[0].url) === "/item-plan-off" && JSON.stringify(ctx.posts[0].body) === JSON.stringify({ service: "HVAC", item_code: TRAY, off: true }),
    `one post to /item-plan-off {off:true}, got ${JSON.stringify(ctx.posts)}`);
  ok(ctx.asked.length === 0, "removing asks nothing (it is reversible and changes no data)");
  m = ctx.d.querySelector("#sp-modal");
  ok(!!m, "the dialog is open again after the change");
  const z = zbox(m, "b3z1");
  ok(z && !z.hasAttribute("max") && z.placeholder === "qty" && z.value === "", "a zone box has no max, no 'of X', placeholder 'qty', and is empty");
  ok(!/of\s+\d/.test(m.querySelector(".sp-roomcell").textContent), `no "of 54.76" left on a zone, got "${m.querySelector(".sp-roomcell").textContent.replace(/\s+/g, " ")}"`);
  ok(m.querySelector(".sp-planbox").hidden, "the 'planned quantity for the ticked zones' box is hidden while the plan is removed");
  const card = m.querySelector(".sp-freecard");
  ok(card && /Plan removed/.test(card.textContent) && /1150 Rmt/.test(card.textContent.replace(",", "")) && /left 1150/.test(card.textContent.replace(",", "")), `notice shows total and left, got "${card && card.textContent}"`);
  ok(!!m.querySelector("#sp-plan-restore"), "a Restore plan link is there");
  ok(/exactly as typed/.test(m.querySelector("#sp-zhelp").textContent), "help line explains the removed-plan behaviour");
  ok(!/Remove plan for this item/.test(m.textContent), "the remove link is gone while removed");

  // ================= 4. a tick does not fill a quantity; a bare tick sends nothing
  toggle(ctx.w, m.querySelector('input[data-room="b3z1"]'), true);
  ok(zbox(m, "b3z1").value === "", "ticking a zone leaves its box empty (no invented share)");
  ctx.posts.length = 0; click(ctx.w, ctx.d.querySelector("#sp-mark-done")); await sleep(60);
  ok(ctx.posts.length === 0, `a tick with no quantity posts nothing, got ${JSON.stringify(ctx.posts)}`);
  ok(!!ctx.d.querySelector("#sp-modal"), "and the dialog stays open so the number can be typed");

  // ================= 5. zone: exactly what was typed, above the old share
  type(ctx.w, zbox(m, "b3z1"), 100);
  ok(zbox(m, "b3z1").value === "100", "100 stays 100 (the share was 54.76)");
  ok(m.querySelector('input[data-room="b3z1"]').checked && m.querySelector('input[data-room="b3z1"]').closest(".sp-roomcell").classList.contains("is-full"), "typing ticks the zone");
  ctx.posts.length = 0; click(ctx.w, ctx.d.querySelector("#sp-mark-done")); await sleep(80);
  ok(ctx.posts.length === 1 && apiPath(ctx.posts[0].url) === "/progress/rooms" && JSON.stringify(ctx.posts[0].body.entries) === JSON.stringify([{ room: "b3z1", qty: 100 }]),
    `one /progress/rooms post with the typed 100, got ${JSON.stringify(ctx.posts)}`);
  ok(!ctx.d.querySelector("#sp-modal"), "dialog closes after the save");

  // ================= 6. Whole B3: 500 on a 383.33 share goes as 500
  ctx = await boot({ free: true, groups: false, rec: {} }); m = await open(ctx);
  let g = group(m, "B3");
  click(ctx.w, seg(g, "area"));
  ok(!abox(g).hasAttribute("max") && abox(g).placeholder === "qty" && abox(g).value === "", "Whole B3 box: no max, empty, 'qty'");
  ok(!/of\s+\d/.test(g.querySelector(".sp-areacell").textContent), `no "of 383.33" on Whole B3, got "${g.querySelector(".sp-areacell").textContent.replace(/\s+/g, " ")}"`);
  toggle(ctx.w, acb(g), true);
  ok(abox(g).value === "", "ticking Whole B3 does not fill a quantity");
  ctx.posts.length = 0; click(ctx.w, ctx.d.querySelector("#sp-mark-done")); await sleep(60);
  ok(ctx.posts.length === 0, "Whole B3 ticked with no number: nothing sent");
  type(ctx.w, abox(g), 500);
  ok(abox(g).value === "500", "500 stays 500 on Whole B3 (not 383.33)");
  ctx.posts.length = 0; click(ctx.w, ctx.d.querySelector("#sp-mark-done")); await sleep(80);
  ok(ctx.posts.length === 1 && apiPath(ctx.posts[0].url) === "/progress/area-total" && JSON.stringify(ctx.posts[0].body) === JSON.stringify({ service: "HVAC", item_code: TRAY, rooms: B3, qty: 500 }),
    `one area-total post: B3's 7 zones + 500, got ${JSON.stringify(ctx.posts)}`);

  // ================= 7. already-recorded zones show their figure and are not re-posted
  ctx = await boot({ free: true, groups: false, rec: { b3z1: 100, b3z2: 30 } }); m = await open(ctx);
  ok(zbox(m, "b3z1").value === "100" && zbox(m, "b3z2").value === "30", "recorded quantities show as recorded (100 is above the old share)");
  ok(/recorded 130/.test(m.querySelector(".sp-freecard").textContent) && /left 1020/.test(m.querySelector(".sp-freecard").textContent.replace(",", "")), "notice: recorded 130, left 1020");
  toggle(ctx.w, m.querySelector('input[data-room="b3z1"]'), true);
  ok(zbox(m, "b3z1").value === "100", "ticking a recorded zone keeps its figure");
  type(ctx.w, zbox(m, "b3z3"), 45);
  ctx.posts.length = 0; click(ctx.w, ctx.d.querySelector("#sp-mark-done")); await sleep(80);
  ok(ctx.posts.length === 1 && JSON.stringify(ctx.posts[0].body.entries) === JSON.stringify([{ room: "b3z3", qty: 45 }]),
    `only the zone that was typed is sent -- recorded b3z1 stays untouched, got ${JSON.stringify(ctx.posts[0] && ctx.posts[0].body)}`);

  // ================= 8. undo still works as a plain post
  ctx = await boot({ free: true, groups: false, rec: { b3z1: 100 } }); m = await open(ctx);
  toggle(ctx.w, m.querySelector('input[data-room="b3z1"]'), true);
  ctx.posts.length = 0; click(ctx.w, ctx.d.querySelector("#sp-unmark-done")); await sleep(80);
  ok(ctx.posts.length === 1 && apiPath(ctx.posts[0].url) === "/mark-rooms-done" && ctx.posts[0].body.done === false && ctx.posts[0].body.rooms[0] === "b3z1", `Undo done is the normal undo post, got ${JSON.stringify(ctx.posts)}`);

  // ================= 9. restore asks first, then posts off:false and reopens with the plan
  ctx = await boot({ free: true, groups: false, rec: {} }, { confirmAnswer: false }); m = await open(ctx);
  ctx.posts.length = 0; click(ctx.w, m.querySelector("#sp-plan-restore")); await sleep(60);
  ok(ctx.asked.length === 1 && /Restore the equal-share plan/.test(ctx.asked[0]) && ctx.posts.length === 0, "Restore asks first; 'Cancel' sends nothing");
  ctx = await boot({ free: true, groups: false, rec: {} }); m = await open(ctx);
  ctx.posts.length = 0; click(ctx.w, m.querySelector("#sp-plan-restore")); await sleep(120);
  ok(ctx.posts.length === 1 && JSON.stringify(ctx.posts[0].body) === JSON.stringify({ service: "HVAC", item_code: TRAY, off: false }), "Restore posts off:false");
  m = ctx.d.querySelector("#sp-modal");
  ok(m && /of 54\.76 Rmt/.test(zbox(m, "b3z1").parentElement.textContent) && !m.querySelector(".sp-freecard") && !!m.querySelector("#sp-plan-remove"), "after restore the dialog is back to the equal-share plan");

  console.log(`plan-removed UI: ${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
}
main().catch((e) => { console.error(e); process.exit(1); });
