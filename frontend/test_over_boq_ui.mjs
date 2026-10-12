// jsdom: quantity above the BOQ (item level). Reported scenario (ELV, Thoth Mall): "Supply & laying of 2 Core cable" planned 12,650 Mtr;
// the engineer types 13,000. It used to snap back to 12,650 without a word. Now: the typed number is sent to /progress/item-qty (not clamped),
// the row shows the amber "Over BOQ +350 Mtr" tag and a "Back to BOQ qty" link, and the link calls the undo route.
// A number at or under the planned quantity still goes through the usual /progress/item route; items added from stock (quick) never show the tag.
import { JSDOM } from "jsdom";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
let pass = 0, fail = 0;
function ok(cond, msg) { if (cond) pass++; else { fail++; console.error("FAIL:", msg); } }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const click = (w, el) => el.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
const T = (el) => (el ? el.textContent : "").replace(/\s+/g, " ").trim();

const CABLE = "e#7", QUICK = "q#1", GROUPED = "g#1", BOQ = 12650;
const STRUCTURE = { kind: "mall", id: "p0", type: "project", name: "Thoth Mall",
  children: [{ id: "lv1", type: "level", name: "L1", children: [{ id: "z1", type: "room", name: "Zone 1", children: [] }] }] };

function boot() {
  const html = `<!doctype html><html><body>
    <header class="top"><div class="brand"><p id="ctx"></p></div><div class="topact"></div></header><main></main></body></html>`;
  const dom = new JSDOM(html, { url: "https://example.test/", runScripts: "dangerously", pretendToBeVisual: true });
  const w = dom.window, d = w.document;
  const posts = [];
  const st = { planned: BOQ, used: 8000, over: false };      // server-side truth for the cable
  const groupedRow = () => ({ code: GROUPED, desc: "Jet fan, Zone 5", unit: "Nos", sub: "Other", acts: ["Cable laying"], qty: 9, planned: 9, used: 4, remaining: 5, pct: (4 / 9) * 100,
    mapped: true, rooms: 1, in_room: true, rate: 1, quick: false, done_val: 0, rem_val: 0, planned_override: false, boq_planned: 9, has_room_groups: true });
  const itemRow = () => ({ code: CABLE, desc: "Supply & laying of 2 Core cable", unit: "Mtr", sub: "Cable", acts: ["Cable laying"], qty: BOQ, planned: st.planned,
    used: st.used, remaining: Math.max(0, st.planned - st.used), pct: (st.used / st.planned) * 100, mapped: true, rooms: 1, in_room: true, rate: 10,
    quick: false, done_val: st.used * 10, rem_val: 0, planned_override: st.over, boq_planned: BOQ });
  const quickRow = () => ({ code: QUICK, desc: "Added from stock", unit: "Nos", sub: "Other", acts: ["Cable laying"], qty: 10, planned: 10, used: 12, remaining: 0, pct: 100,
    mapped: true, rooms: 1, in_room: true, rate: 0, quick: true, done_val: 0, rem_val: 0, planned_override: true, boq_planned: 10 });
  const body = () => ({ service: "ELV", room: null, activities: ["Cable laying"], mapping: { "Cable laying": [CABLE, QUICK, GROUPED] }, act_pct: { "Cable laying": 0 }, overall_pct: 0,
    items: [itemRow(), quickRow(), groupedRow()], pnl_by_activity: {}, pnl_totals: {}, pnl_unmapped_value: { items: 0 }, item_rooms: {}, item_room_qty: {}, item_progress: {},
    unmapped: [], labour_only: {}, labour_pct: {}, labour_suggested: {} });
  w.fetch = (url, opts) => {
    const u = String(url); let status = 200, b = {};
    const post = opts && opts.method === "POST";
    const pb = post ? JSON.parse(opts.body) : null;
    if (post) posts.push({ url: u, body: pb });
    if (u === "/api/projects") b = [{ slug: "thoth-mall", project: "Thoth Mall", runs: 1, latest_run: "r1" }];
    else if (u === "/api/siteprogress/projects-without-stock") b = [];
    else if (u === "/api/siteprogress/thoth-mall") b = { structure: STRUCTURE, has_boq: true, services: ["ELV"] };
    else if (post && u.endsWith("/progress/item-qty")) { if (pb.qty > st.planned) { st.planned = pb.qty; st.over = true; st.used = pb.qty; } else st.used = pb.qty; b = body(); }
    else if (post && u.endsWith("/progress/item-qty/undo")) { st.planned = BOQ; st.over = false; st.used = BOQ; b = body(); }
    else if (post && u.endsWith("/progress/item")) { st.used = Math.min(1, pb.frac) * st.planned; b = body(); }
    else if (u.startsWith("/api/siteprogress/thoth-mall/service/")) b = body();
    else if (u.startsWith("/api/siteprogress/thoth-mall/pnl/")) b = { done_value: 0, remaining_value: 0 };
    else if (u.startsWith("/api/siteprogress/thoth-mall/realistic/")) b = { items: [], shortages: 0 };
    else if (u.endsWith("/stock")) { status = 404; }
    return Promise.resolve({ ok: status < 400, status, json: async () => b, text: async () => JSON.stringify(b) });
  };
  w.eval(fs.readFileSync(path.join(__dirname, "siteprogress.js"), "utf8"));
  return { w, d, posts, st };
}
const qtyInput = (d, code) => d.querySelector(`input[data-qty="${code}"]`);
const type = (w, inp, v) => { inp.value = String(v); inp.dispatchEvent(new w.Event("change", { bubbles: true })); };

async function main() {
  const c = boot(); const { w, d, posts } = c;
  await sleep(0); await sleep(0);
  click(w, [...d.querySelectorAll(".spnav button")].find((b) => b.dataset.v === "siteprogress"));
  await sleep(80);
  ok(qtyInput(d, CABLE), "cable row with its quantity box is on screen");
  ok(!d.querySelector("[data-over]") && !d.querySelector("[data-overundo]"), "no Over-BOQ tag while within the BOQ");

  // below planned: the usual route, nothing new
  posts.length = 0; type(w, qtyInput(d, CABLE), 10000); await sleep(60);
  ok(posts.some((p) => p.url.endsWith("/progress/item") && Math.abs(p.body.frac - 10000 / BOQ) < 1e-9), `10,000 of 12,650 uses /progress/item, got ${JSON.stringify(posts.map((p) => p.url))}`);
  ok(!posts.some((p) => p.url.endsWith("/progress/item-qty")), "the over-BOQ route is not used below planned");
  ok(!d.querySelector("[data-over]"), "still no tag");

  // the report: 13,000 on a planned 12,650
  posts.length = 0; type(w, qtyInput(d, CABLE), 13000); await sleep(80);
  const q = posts.find((p) => p.url.endsWith("/progress/item-qty"));
  ok(q && JSON.stringify(q.body) === JSON.stringify({ service: "ELV", item_code: CABLE, qty: 13000 }), `13,000 is sent as typed to /progress/item-qty, got ${JSON.stringify(posts)}`);
  ok(!posts.some((p) => p.url.endsWith("/progress/item")), "the clamping route is not used for it");
  const tag = d.querySelector(`[data-over="${CABLE}"]`);
  ok(tag && /Over BOQ \+350 Mtr/.test(T(tag)), `amber tag reads "Over BOQ +350 Mtr", got "${T(tag)}"`);
  ok(/12,?650/.test(tag.title) && /Billing/.test(tag.title), "tag tooltip names the BOQ qty and Billing");
  ok(T(d.querySelector(`[data-overundo="${CABLE}"]`)) === "Back to BOQ qty", "Back to BOQ qty link is next to it");
  ok(Number(qtyInput(d, CABLE).value) === 13000, `the box keeps 13000, got ${qtyInput(d, CABLE).value}`);
  ok(!d.querySelector(`[data-over="${QUICK}"]`) && d.querySelectorAll("[data-over]").length === 1, "a stock-added (quick) item never shows the tag");

  // back to BOQ
  posts.length = 0; click(w, d.querySelector(`[data-overundo="${CABLE}"]`)); await sleep(80);
  ok(posts.length >= 1 && posts[0].url.endsWith("/progress/item-qty/undo") && JSON.stringify(posts[0].body) === JSON.stringify({ service: "ELV", item_code: CABLE }), `undo route called, got ${JSON.stringify(posts)}`);
  ok(!d.querySelector("[data-over]") && !d.querySelector("[data-overundo]"), "tag and link gone after Back to BOQ qty");
  ok(Number(qtyInput(d, CABLE).value) === BOQ, `box back to the BOQ qty, got ${qtyInput(d, CABLE).value}`);

  // an item whose quantity is set zone by zone is never "over": a bigger number is capped as before
  posts.length = 0; type(w, qtyInput(d, GROUPED), 12); await sleep(60);
  ok(!posts.some((p) => p.url.endsWith("/progress/item-qty")) && posts.some((p) => p.url.endsWith("/progress/item") && p.body.frac === 1), `grouped item: 12 of 9 goes to the usual route at 100%, got ${JSON.stringify(posts.map((p) => [p.url, p.body]))}`);
  ok(!d.querySelector(`[data-over="${GROUPED}"]`), "no tag for it");

  console.log(`${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
}
main().catch((e) => { console.error(e); process.exit(1); });
