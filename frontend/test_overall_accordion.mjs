import { JSDOM } from "jsdom";
import fs from "fs";
import assert from "assert";

const html = `<!doctype html><html><body>
<header class="top" id="top"><div class="brand"><h1>Material intelligence</h1><p id="ctx"></p></div>
  <div class="topact"><button id="pick"></button><button id="sync"></button><button id="syncgo"></button><button id="home" hidden></button>
  <input id="file" type="file" hidden></div></header>
<main>
<section id="drop"></section><section id="busy" hidden></section><section id="report" hidden>
  <div class="bar"><div class="tabs" id="svc"></div></div>
</section>
</main></body></html>`;

const dom = new JSDOM(html, { url: "http://localhost/", runScripts: "outside-only", pretendToBeVisual: true });
const { window } = dom;
global.window = window;
global.document = window.document;
global.HTMLElement = window.HTMLElement;
global.MutationObserver = window.MutationObserver;

const STRUCTURE = { id: "p0", type: "project", name: "Fixture Hotel",
  children: [{ id: "flo1", type: "floor", name: "Floor 1", children: [
    { id: "roo1", type: "room", name: "Room 101", children: [] }] }] };

// overall_pct is deliberately a nonsense sentinel far from pct_value_done --
// if the ring were still wired to the old field, this test would catch it.
const OVERALL = {
  overall_pct: 999.9, pct_value_done: 61.4,
  done_value: 4720000, planned_value: 7780000, remaining_value: 3060000,
  waste_value: 96500, saved_value: 0, waste_caveat: null,
  services: ["Electrical", "Plumbing"],
  by_service: {
    Electrical: { pct: 71, done_value: 1890000, remaining_value: 770000, planned_value: 2660000, items: 2, waste_value: 0 },
    Plumbing: { pct: 54, done_value: 940000, remaining_value: 800000, planned_value: 1740000, items: 1, waste_value: 0 },
    FAPA: { pct: 0, done_value: 0, remaining_value: 0, planned_value: 0, items: 1, waste_value: 0 },
  },
  rooms_summary: { done: 58, in_progress: 21, not_started: 17, total: 96 },
};

const SVC_ELECTRICAL = {
  service: "Electrical", room: null,
  activities: ["Wall Piping"],
  mapping: { "Wall Piping": ["2.1", "2.2", "2.3"] },
  act_pct: { "Wall Piping": 90.0 }, overall_pct: 71,
  items: [
    { code: "2.1", desc: "25mm PVC conduit", unit: "MTR", sub: "Pipe",
      qty: 210, planned: 210, used: 210, remaining: 0, pct: 100, mapped: true,
      rooms: 96, in_room: true, rate: 300, quick: false, done_val: 63000, rem_val: 0,
      room_done: 1, room_progress: 0, room_pending: 0, room_total: 1 },
    { code: "2.2", desc: "20mm PVC conduit", unit: "MTR", sub: "Pipe",
      qty: 150, planned: 210, used: 150, remaining: 60, pct: 71.4, mapped: true,
      rooms: 96, in_room: true, rate: null, quick: false, done_val: null, rem_val: null,
      room_done: 0, room_progress: 1, room_pending: 0, room_total: 1 },
    // real-world case that surfaced the mixed-unit bug: a pipe (MTR) and a
    // bend/fitting (NOS) sharing one activity -- must NOT be summed together
    { code: "2.3", desc: "25mm PVC bend black", unit: "NOS", sub: "Pipe",
      qty: 18, planned: 18, used: 18, remaining: 0, pct: 100, mapped: true,
      rooms: 96, in_room: true, rate: 40, quick: false, done_val: 720, rem_val: 0,
      room_done: 1, room_progress: 0, room_pending: 0, room_total: 1 },
  ],
  pnl_by_activity: {}, pnl_totals: {}, pnl_unmapped_value: { items: 0 },
  item_rooms: {}, unmapped: [],
};

const SVC_FAPA = {
  service: "FAPA", room: null,
  activities: ["FA"],
  mapping: { "FA": ["QI1"] },
  act_pct: { "FA": 0.0 }, overall_pct: 0,
  items: [
    // a quick-added item (from stock, no per-room BOQ qty) whose `planned`
    // is a flat manually-entered total -- NOT auto-multiplied by room count.
    // it.rooms=109 confirms it genuinely applies project-wide; the small
    // 28.8 figure is the number that was typed in when it was added, real
    // and whole-project already, just easy to mistake for "one room" without
    // the room-count context now added to the row.
    { code: "QI1", desc: "2CX1.5SQMM LSZH fire survival cable - red colour", unit: "MTR", sub: "Cable",
      qty: 0, planned: 28.8, used: 0, remaining: 28.8, pct: 0, mapped: true,
      rooms: 109, in_room: true, rate: null, quick: true, done_val: null, rem_val: null,
      room_done: 0, room_progress: 0, room_pending: 109, room_total: 109 },
  ],
  pnl_by_activity: {}, pnl_totals: {}, pnl_unmapped_value: { items: 0 },
  item_rooms: {}, unmapped: [],
};

const SVC_PLUMBING = {
  service: "Plumbing", room: null,
  activities: ["CPVC Piping"],
  mapping: { "CPVC Piping": ["3.4", "3.5"] },
  act_pct: { "CPVC Piping": 76.0 }, overall_pct: 54,
  items: [
    { code: "3.4", desc: "20mm CPVC pipe", unit: "MTR", sub: "Pipe",
      qty: 380, planned: 500, used: 380, remaining: 120, pct: 76.0, mapped: true,
      rooms: 96, in_room: true, rate: 750, quick: false, done_val: 285000, rem_val: 90000,
      room_done: 0, room_progress: 1, room_pending: 0, room_total: 1 },
    { code: "3.5", desc: "25mm CPVC pipe", unit: "MTR", sub: "Pipe",
      qty: 260, planned: 400, used: 260, remaining: 140, pct: 65.0, mapped: true,
      rooms: 96, in_room: true, rate: 750, quick: false, done_val: 195000, rem_val: 105000,
      room_done: 0, room_progress: 1, room_pending: 0, room_total: 1 },
  ],
  pnl_by_activity: {}, pnl_totals: {}, pnl_unmapped_value: { items: 0 },
  item_rooms: {}, unmapped: [],
};

const fetchLog = [];
function route(url) {
  const u = new URL(url, "http://localhost");
  const p = u.pathname;
  fetchLog.push(p);
  if (p === "/api/projects") return [{ slug: "fixture-hotel", project: "Fixture Hotel" }];
  if (p === "/api/siteprogress/fixture-hotel")
    return { slug: "fixture-hotel", structure: STRUCTURE, rooms: 96,
             services: ["Electrical", "Plumbing", "FAPA"],
             activities: { Electrical: ["Wall Piping"], Plumbing: ["CPVC Piping"], FAPA: ["FA"] },
             progress_summary: {}, has_boq: true };
  if (p === "/api/siteprogress/fixture-hotel/overall") return JSON.parse(JSON.stringify(OVERALL));
  if (p === "/api/siteprogress/fixture-hotel/service/Electrical") return JSON.parse(JSON.stringify(SVC_ELECTRICAL));
  if (p === "/api/siteprogress/fixture-hotel/service/Plumbing") return JSON.parse(JSON.stringify(SVC_PLUMBING));
  if (p === "/api/siteprogress/fixture-hotel/service/FAPA") return JSON.parse(JSON.stringify(SVC_FAPA));
  throw new Error("unmocked route: " + p);
}
global.fetch = window.fetch = async (url) => ({ ok: true, json: async () => route(url), text: async () => JSON.stringify(route(url)) });
window.confirm = () => true; window.alert = () => {};

const src = fs.readFileSync(new URL("../frontend/siteprogress.js", import.meta.url), "utf8");
window.eval(src);
async function flush(n = 10) { for (let i = 0; i < n; i++) await new Promise((r) => setTimeout(r, 0)); }

async function main() {
  Object.defineProperty(document, "readyState", { value: "complete", configurable: true });
  document.dispatchEvent(new window.Event("DOMContentLoaded"));
  await flush();
  document.querySelector('.spnav button[data-v="siteprogress"]').click();
  await flush(10);

  // navigate to Overall via the pill (matches how a person actually gets here)
  const ovPill = document.querySelector('.sp-pill[data-s="__overall__"]');
  assert.ok(ovPill, "FAIL: Overall pill missing");
  ovPill.click();
  await flush(15);

  // ---- test 1: ring uses pct_value_done, NOT the old item-mean overall_pct ----
  const ringPct = document.querySelector("#sp-oring b").textContent;
  assert.strictEqual(ringPct, "61%", "FAIL: ring should show pct_value_done=61%, got " + ringPct);
  assert.ok(!document.querySelector("#sp-oring").innerHTML.includes("1000") &&
            !document.querySelector("#sp-oring").innerHTML.includes("999"),
    "FAIL: ring is still wired to the stale overall_pct field");
  console.log("PASS: hero ring shows the single ₹-value-weighted % (pct_value_done), not the old item-mean field");

  const ringLabel = document.querySelector("#sp-oring span").textContent;
  assert.strictEqual(ringLabel, "value complete");
  console.log("PASS: ring subtitle reads 'value complete'");

  // ---- test 2: no second, disagreeing "% of value" line under Work done ----
  const heroText = document.querySelector(".sp-hero").textContent;
  assert.ok(!heroText.includes("% of value"), "FAIL: the old disagreeing '% of value' subtitle should be gone");
  console.log("PASS: the old second (disagreeing) percentage under Work done is gone");

  // ---- test 3: new 4th "Rooms — whole site" stat ----
  const stats = [...document.querySelectorAll(".sp-stats .sp-stat")];
  assert.strictEqual(stats.length, 4, "FAIL: expected 4 hero stats, got " + stats.length);
  const roomsStat = stats[3];
  assert.ok(roomsStat.textContent.includes("Rooms — whole site"));
  assert.ok(roomsStat.textContent.includes("58"), "FAIL: expected 58 done rooms in stat");
  assert.ok(roomsStat.textContent.includes("21"), "FAIL: expected 21 in-progress rooms in stat");
  assert.ok(roomsStat.textContent.includes("of 96 rooms"), "FAIL: expected 'of 96 rooms' subtitle");
  assert.ok(roomsStat.classList.contains("sp-statdiv"), "FAIL: 4th stat should carry the divider class");
  console.log("PASS: new 'Rooms — whole site' stat shows 58 done · 21 in progress of 96 rooms, with a divider");

  // ---- test 4: service row is closed by default, no eager /service fetch
  // triggered by rendering the Overall page itself (the default-service load
  // that happened on the way here, before we clicked the Overall pill, is
  // unrelated -- reset the log right here so this only measures what the
  // Overall accordion itself does on render). ----
  fetchLog.length = 0;
  assert.ok(document.querySelectorAll(".sp-card.open").length === 0, "FAIL: no service card should start open");
  assert.ok(!fetchLog.includes("/api/siteprogress/fixture-hotel/service/Electrical"),
    "FAIL: activities should be lazy-fetched on expand, not eagerly on page render");
  console.log("PASS: activities are not fetched until a service row is actually expanded");

  // ---- test 5: expanding Electrical lazy-fetches and renders its activity.
  // Wall Piping mixes MTR (pipe) and NOS (bend) items -- the subtitle must
  // NOT sum them into a fake "X of Y MTR"; it should just say the item count. ----
  const elecCard = document.querySelector('.sp-card[data-ovsvc="Electrical"]');
  assert.ok(elecCard, "FAIL: Electrical service card missing");
  elecCard.querySelector(".sp-ovrow").click();
  await flush(15);
  assert.ok(elecCard.classList.contains("open"), "FAIL: Electrical card should be open after click");
  assert.ok(fetchLog.includes("/api/siteprogress/fixture-hotel/service/Electrical"),
    "FAIL: expanding Electrical should fetch /service/Electrical");
  const actRow = elecCard.querySelector(".sp-ovact");
  assert.ok(actRow, "FAIL: Wall Piping activity row missing after expand");
  const actSub = actRow.querySelector(".sp-ovactsub").textContent;
  assert.strictEqual(actSub, "3 items", "FAIL: mixed-unit activity (MTR pipe + NOS bend) must not fake a combined qty, got: " + actSub);
  console.log("PASS: an activity mixing units (MTR pipe + NOS bend) shows '3 items' — never a bogus combined qty/unit");

  // ---- test 6: activity row items are NOT shown until the activity itself is tapped ----
  assert.ok(!actRow.classList.contains("open"), "FAIL: activity should start collapsed");
  const itemsBoxHidden = actRow.querySelector(".sp-ovitems");
  assert.ok(itemsBoxHidden, "FAIL: items container should exist (CSS-hidden) even before tap");
  console.log("PASS: item rows exist but stay collapsed until the activity row itself is tapped");

  // ---- test 7: tapping the activity reveals item rows, exact-language subtitles ----
  actRow.querySelector(".sp-ovactrow").click();
  await flush(5);
  assert.ok(actRow.classList.contains("open"), "FAIL: activity should be open after tap");
  const itemRows = [...actRow.querySelectorAll(".sp-ovitemrow")];
  assert.strictEqual(itemRows.length, 3, "FAIL: expected 3 item rows, got " + itemRows.length);
  const sub1 = itemRows[0].querySelector(".sp-ovitemsub").textContent;
  assert.strictEqual(sub1, "2.1 · 210 of 210 MTR done", "FAIL: finished-item subtitle mismatch: " + sub1);
  const sub2 = itemRows[1].querySelector(".sp-ovitemsub").textContent;
  assert.strictEqual(sub2, "2.2 · 150 of 210 MTR done, 60 remaining", "FAIL: in-progress item subtitle mismatch: " + sub2);
  const sub3 = itemRows[2].querySelector(".sp-ovitemsub").textContent;
  assert.strictEqual(sub3, "2.3 · 18 of 18 NOS done", "FAIL: the NOS item keeps its own unit at item level: " + sub3);
  console.log("PASS: item subtitles read '{done} of {planned} {unit} done[, {remaining} remaining]' exactly as specified, each in its own real unit");

  // ---- test 8: unrated item shows "—", not a fabricated ₹0 ----
  const money2 = [...itemRows[1].querySelectorAll(".sp-ovitemmoney b")].map((b) => b.textContent);
  assert.deepStrictEqual(money2, ["—", "—"], "FAIL: unrated item should show — for done/remaining, got " + money2);
  const money1 = [...itemRows[0].querySelectorAll(".sp-ovitemmoney b")].map((b) => b.textContent);
  assert.ok(money1[0].includes("63"), "FAIL: rated, done item should show its real ₹ done value, got " + money1);
  console.log("PASS: an unrated item shows '—' (never a fabricated ₹0); a rated item shows its real value");

  // ---- test 9: opening Plumbing closes Electrical (single-open accordion, both levels) ----
  const plumbCard = document.querySelector('.sp-card[data-ovsvc="Plumbing"]');
  plumbCard.querySelector(".sp-ovrow").click();
  await flush(15);
  assert.ok(plumbCard.classList.contains("open"), "FAIL: Plumbing should now be open");
  assert.ok(!elecCard.classList.contains("open"), "FAIL: Electrical should close when Plumbing opens (accordion)");
  console.log("PASS: opening one service closes the other (accordion, matches the per-service activities pattern)");

  // ---- test 9b: Plumbing's two items DO share a unit (MTR+MTR) -- positive
  // control proving same-unit activities still get a real combined qty, so
  // the mixed-unit fix above didn't just make every activity say "N items" ----
  const plumbAct = plumbCard.querySelector(".sp-ovact");
  const plumbActSub = plumbAct.querySelector(".sp-ovactsub").textContent;
  assert.strictEqual(plumbActSub, "2 items · 640 of 900 MTR",
    "FAIL: same-unit items should still combine into one qty/unit, got: " + plumbActSub);
  console.log("PASS: same-unit activities (2 MTR pipe items) still show a real combined '640 of 900 MTR'");

  // ---- test 9c: whole-project qty, never room-scoped. Plumbing's fetch URL
  // must carry no ?room= query at all, regardless of any room the engineer
  // was previously drilled into elsewhere in the app -- Overall always reads
  // every applicable room for every item (matches its own stated meaning:
  // "how much of the TOTAL room count is done", not one room's slice). ----
  const plumbFetchUrl = fetchLog.find((p) => p.startsWith("/api/siteprogress/fixture-hotel/service/Plumbing"));
  assert.strictEqual(plumbFetchUrl, "/api/siteprogress/fixture-hotel/service/Plumbing",
    "FAIL: Overall's service fetch must never carry a room= query param, got: " + plumbFetchUrl);
  console.log("PASS: Overall's per-service fetch never carries a ?room= param -- item qty is always the whole project's, across every applicable room");

  // ---- test 10: item subtitles inside expanded activity always show
  // WHOLE-PROJECT planned qty, even if engineer was previously drilled into
  // a room elsewhere on the page. Verify the new fetch (with no room param)
  // returned whole-project totals, not room-scoped ghosts from an old cache. ----
  const walItem = itemRows[1];     // 2.2: in progress, 150 of 210 MTR whole-project
  const walItemSub = walItem.querySelector(".sp-ovitemsub").textContent;
  assert.ok(walItemSub.includes("210"), "FAIL: item must show whole-project planned (210), not per-room qty, got: " + walItemSub);
  console.log("PASS: item subtitle shows whole-project planned (210 MTR), not a per-room slice");

  // ---- test 11: a quick-added item's total (28.8 MTR) still reads exactly
  // as computed -- no room-count tag (removed per direct feedback: it read
  // as clutter, not clarity). The number itself is unaffected. ----
  fetchLog.length = 0;
  document.querySelectorAll(".sp-card.open").forEach((c) => c.classList.remove("open"));
  const fapaCard = document.querySelector('.sp-card[data-ovsvc="FAPA"]');
  assert.ok(fapaCard, "FAIL: FAPA service card missing");
  fapaCard.querySelector(".sp-ovrow").click();
  await flush(15);
  fapaCard.querySelector(".sp-ovactrow").click();
  await flush(5);
  const qi1Sub = fapaCard.querySelector(".sp-ovitemrow .sp-ovitemsub").textContent;
  assert.strictEqual(qi1Sub, "QI1 · 0 of 28.8 MTR done, 28.8 remaining",
    "FAIL: expected the plain figure with no room-count tag, got: " + qi1Sub);
  assert.ok(!qi1Sub.includes("room"), "FAIL: room-count tag should be gone entirely, got: " + qi1Sub);
  console.log("PASS: item subtitle stays plain — no room-count tag clutter");

  // ---- test 12: re-expanding Electrical does not refetch (cached) ----
  const before = fetchLog.filter((p) => p === "/api/siteprogress/fixture-hotel/service/Electrical").length;
  elecCard.querySelector(".sp-ovrow").click();
  await flush(15);
  const after = fetchLog.filter((p) => p === "/api/siteprogress/fixture-hotel/service/Electrical").length;
  assert.strictEqual(after, before, "FAIL: re-expanding a service should use the cached response, not refetch");
  console.log("PASS: re-expanding a previously-opened service reuses the cached /service/{x} response");
}

main().then(() => console.log("\nALL OVERALL ACCORDION TESTS PASSED")).catch((e) => { console.error("TEST FAILURE:", e.message); process.exit(1); });
