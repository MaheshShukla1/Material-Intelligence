// jsdom test for the labour-only activity feature in siteprogress.js: an
// activity with no BOQ material (Zari work, core-cutting, chasing,
// testing...) can be tracked by a direct % slider instead of items. Same
// harness pattern as test_service_switch_parallel.mjs -- real siteprogress.js
// loaded via window.eval(), a mocked fetch, real DOM assertions.
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

// The real _service_view_core() response shape, with four activities
// covering every state this feature can be in:
//   Wire Pulling  -- normal, item-tracked, has items
//   Metal Box     -- empty, NOT a labour keyword -> no suggestion link
//   Zari Work     -- empty, IS a labour keyword -> suggestion link shown
//   DB Testing    -- already toggled labour_only, with a recorded 42%
function serviceBody() {
  return {
    service: "Electrical", room: null,
    activities: ["Wire Pulling", "Metal Box", "Zari Work", "DB Testing"],
    mapping: { "Wire Pulling": ["E.1"], "Metal Box": [], "Zari Work": [], "DB Testing": [] },
    act_pct: { "Wire Pulling": 50, "Metal Box": null, "Zari Work": null, "DB Testing": 42 },
    overall_pct: 46,
    items: [{ code: "E.1", desc: "wire", unit: "MTR", sub: "Other", acts: ["Wire Pulling"],
             qty: 10, planned: 10, used: 5, remaining: 5, pct: 50, mapped: true,
             rooms: 1, in_room: true, rate: 100, quick: false, done_val: 500,
             rem_val: 500, room_done: 0, room_progress: 1, room_pending: 0,
             room_total: 1, planned_override: false, has_room_groups: false }],
    pnl_by_activity: {}, pnl_totals: {}, pnl_unmapped_value: { items: 0 },
    item_rooms: {}, item_room_qty: {}, unmapped: [],
    labour_only: { "Wire Pulling": false, "Metal Box": false, "Zari Work": false, "DB Testing": true },
    labour_pct: { "DB Testing": 42 },
    labour_suggested: { "Wire Pulling": false, "Metal Box": false, "Zari Work": true, "DB Testing": false },
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

  const posts = [];   // every POST body, for asserting what got saved
  window.fetch = (url, opts) => {
    const u = String(url);
    if (opts && opts.method === "POST") {
      posts.push({ url: u, body: JSON.parse(opts.body) });
    }
    let body = {};
    if (u === "/api/projects") {
      body = [{ slug: "hyatt-hotel", project: "Hyatt Hotel", runs: 1, latest_run: "r1" }];
    } else if (u === "/api/siteprogress/hyatt-hotel") {
      body = { structure: { kind: "hotel" }, has_boq: true, services: ["Electrical"] };
    } else if (u.startsWith("/api/siteprogress/hyatt-hotel/service/") ||
               u.startsWith("/api/siteprogress/hyatt-hotel/activity-labour") ||
               u.startsWith("/api/siteprogress/hyatt-hotel/progress/activity")) {
      body = serviceBody();
    } else if (u.startsWith("/api/siteprogress/hyatt-hotel/pnl/")) {
      body = { done_value: 0, remaining_value: 0 };
    } else if (u.startsWith("/api/siteprogress/hyatt-hotel/realistic/")) {
      body = { items: [], shortages: 0 };
    }
    return Promise.resolve({ ok: true, status: 200, json: async () => body, text: async () => JSON.stringify(body) });
  };

  window.eval(fs.readFileSync(path.join(__dirname, "siteprogress.js"), "utf8"));
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));

  const navBtn = [...d.querySelectorAll(".spnav button")].find((b) => b.dataset.v === "siteprogress");
  ok(!!navBtn, "Site Progress nav button was injected");
  navBtn.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 60));

  const byName = (n) => [...d.querySelectorAll(".sp-card[data-a]")].find((c) => c.dataset.a === n);
  ok(byName("Wire Pulling") && byName("Metal Box") && byName("Zari Work") && byName("DB Testing"),
    "all 4 activity cards rendered");

  // -------------------------------------------------- suggestion link
  const zari = byName("Zari Work");
  ok(!!zari, "Zari Work card found");
  const zariSuggestLink = zari && zari.querySelector("[data-labouron]");
  ok(!!zariSuggestLink, "Zari Work (empty + labour keyword) shows the 'track as labour-only' link");

  const metalBox = byName("Metal Box");
  const metalBoxSuggestLink = metalBox && metalBox.querySelector("[data-labouron]");
  ok(!metalBoxSuggestLink, "Metal Box (empty, NOT a labour keyword) does NOT show the suggestion link");

  const wirePulling = byName("Wire Pulling");
  const wirePullingSuggestLink = wirePulling && wirePulling.querySelector("[data-labouron]");
  ok(!wirePullingSuggestLink, "Wire Pulling (has items) does NOT show the suggestion link");

  // -------------------------------------------------- toggled-on card shape
  const dbTesting = byName("DB Testing");
  ok(!!dbTesting, "DB Testing card found");
  const badge = dbTesting && dbTesting.querySelector("[data-labouroff]");
  ok(!!badge, "DB Testing (labour_only=true) shows the revert badge");
  ok(badge && badge.textContent.trim() === "labour only", "badge reads 'labour only'");
  const slider = dbTesting && dbTesting.querySelector(".sp-labourrange");
  ok(!!slider, "DB Testing shows a progress slider");
  ok(slider && slider.value === "42", `slider initial value reflects act_pct (42), got ${slider && slider.value}`);
  const addBtn = dbTesting && dbTesting.querySelector("[data-map]");
  ok(!addBtn, "DB Testing does NOT show '+ Add BOQ items' while labour-only is on");

  // -------------------------------------------------- click to toggle on
  posts.length = 0;
  zariSuggestLink.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 20));
  const toggleOnPost = posts.find((p) => p.url.includes("/activity-labour"));
  ok(!!toggleOnPost, "clicking the suggestion link posted to /activity-labour");
  ok(toggleOnPost && toggleOnPost.body.activity === "Zari Work" && toggleOnPost.body.on === true,
    `toggle-on posted the right body, got ${JSON.stringify(toggleOnPost && toggleOnPost.body)}`);

  // -------------------------------------------------- drag the slider
  posts.length = 0;
  const dbTesting2 = byName("DB Testing");   // re-query: the click above re-rendered the list
  const slider2 = dbTesting2.querySelector(".sp-labourrange");
  slider2.value = "77";
  slider2.dispatchEvent(new window.Event("input", { bubbles: true }));
  const pctLabel = dbTesting2.querySelector("[data-labourpctval]");
  ok(pctLabel && pctLabel.textContent === "77%", "dragging updates the visible % label locally, before saving");
  ok(posts.length === 0, "dragging (input event) does not save yet -- only release (change) does");
  slider2.dispatchEvent(new window.Event("change", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 20));
  const savePost = posts.find((p) => p.url.includes("/progress/activity"));
  ok(!!savePost, "releasing the slider posted to /progress/activity");
  ok(savePost && savePost.body.activity === "DB Testing" && Math.abs(savePost.body.frac - 0.77) < 1e-9,
    `save posted the right frac, got ${JSON.stringify(savePost && savePost.body)}`);

  // -------------------------------------------------- click badge to revert
  posts.length = 0;
  const dbTesting3 = byName("DB Testing");
  const badge3 = dbTesting3.querySelector("[data-labouroff]");
  badge3.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 20));
  const toggleOffPost = posts.find((p) => p.url.includes("/activity-labour"));
  ok(!!toggleOffPost, "clicking the badge posted to /activity-labour");
  ok(toggleOffPost && toggleOffPost.body.activity === "DB Testing" && toggleOffPost.body.on === false,
    `toggle-off posted the right body, got ${JSON.stringify(toggleOffPost && toggleOffPost.body)}`);

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
}

main();
