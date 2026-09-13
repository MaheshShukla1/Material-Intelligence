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

const STRUCTURE = { id: "p0", type: "project", name: "Thoth Mall", kind: "mall",
  children: [{ id: "lev1", type: "level", name: "Level 1", children: [
    { id: "zon1", type: "room", name: "Zone A", children: [] }] }] };

// 6 items so the search test has something real to filter down from
const ITEMS = [
  { code: "5.5", desc: "Supplying & Erecting automatic emergency light with maintained fixture", unit: "Nos", sub: "Pipe",
    qty: 1, planned: 15, used: 0, remaining: 15, pct: 0, mapped: true, rooms: 21, in_room: true,
    rate: null, quick: false, done_val: null, rem_val: null,
    room_done: 0, room_progress: 0, room_pending: 21, room_total: 21 },
  { code: "15.2.1", desc: "300 MM ID DWC Pipe", unit: "Rmt", sub: "Pipe",
    qty: 100, planned: 100, used: 0, remaining: 100, pct: 0, mapped: true, rooms: 21, in_room: true,
    rate: null, quick: false, done_val: null, rem_val: null,
    room_done: 0, room_progress: 0, room_pending: 21, room_total: 21 },
  { code: "15.2.2", desc: "150 MM ID DWC Pipe", unit: "Rmt", sub: "Pipe",
    qty: 100, planned: 100, used: 0, remaining: 100, pct: 0, mapped: true, rooms: 21, in_room: true,
    rate: null, quick: false, done_val: null, rem_val: null,
    room_done: 0, room_progress: 0, room_pending: 21, room_total: 21 },
  { code: "a#2", desc: "Class F insulation & IE 3 rating shall be considered for", unit: "Nos", sub: "Switch/Socket",
    qty: 5, planned: 5, used: 0, remaining: 5, pct: 0, mapped: true, rooms: 21, in_room: true,
    rate: null, quick: false, done_val: null, rem_val: null,
    room_done: 0, room_progress: 0, room_pending: 21, room_total: 21 },
  { code: "2.3.1", desc: "VFD Control Panel along with DP sensors & compatable with", unit: "Lot", sub: "Switch/Socket",
    qty: 1, planned: 1, used: 0, remaining: 1, pct: 0, mapped: true, rooms: 21, in_room: true,
    rate: null, quick: false, done_val: null, rem_val: null,
    room_done: 0, room_progress: 0, room_pending: 21, room_total: 21 },
  { code: "16.1.1", desc: "250 MM ID Hume Pipe", unit: "Rmt", sub: "Pipe",
    qty: 50, planned: 50, used: 0, remaining: 50, pct: 0, mapped: true, rooms: 21, in_room: true,
    rate: null, quick: false, done_val: null, rem_val: null,
    room_done: 0, room_progress: 0, room_pending: 21, room_total: 21 },
];

function svcState() {
  return {
    service: "Electrical", room: null, activities: ["Wall piping"],
    mapping: { "Wall piping": ["5.5", "15.2.1"] },
    act_pct: { "Wall piping": 0 }, overall_pct: 0,
    items: JSON.parse(JSON.stringify(ITEMS)),
    pnl_by_activity: {}, pnl_totals: {}, pnl_unmapped_value: { items: 0 },
    item_rooms: {}, unmapped: [],
  };
}

let linkPosts = [];
let mappingPosts = [];

function route(url, opts) {
  const u = new URL(url, "http://localhost");
  const method = (opts && opts.method) || "GET";
  const p = u.pathname;
  if (p === "/api/projects") return [{ slug: "fixture-mall", project: "Thoth Mall" }];
  if (p === "/api/siteprogress/fixture-mall")
    return { slug: "fixture-mall", structure: STRUCTURE, rooms: 21,
             services: ["Electrical"], activities: { Electrical: ["Wall piping"] },
             progress_summary: {}, has_boq: true };
  if (p === "/api/siteprogress/fixture-mall/service/Electrical") return svcState();
  if (p === "/api/siteprogress/fixture-mall/pnl/Electrical")
    return { service: "Electrical", room: null, project: { done_value: 0, remaining_value: 0, pct_value_done: 0 },
             by_activity: {}, waste: { available: false }, rated_items: 0, total_items: 6, unmapped_value: { items: 0 } };
  if (p === "/api/siteprogress/fixture-mall/realistic/Electrical") return { service: "Electrical", has_run: false, items: [] };
  if (p === "/api/siteprogress/fixture-mall/links/Electrical") {
    return {
      service: "Electrical", has_run: true, run: "run1",
      stock_names: ["25MM PVC PIPE", "20MM PVC PIPE", "GANG BOX"],
      material_units: { "25MM PVC PIPE": "Rmt", "20MM PVC PIPE": "Rmt", "GANG BOX": "Nos" },
      items: ITEMS.map((it) => ({
        code: it.code, desc: it.desc, unit: it.unit, sub: it.sub,
        linked: [], suggestion: { best: null, confident: false, candidates: [] },
      })),
    };
  }
  if (method === "POST" && p === "/api/siteprogress/fixture-mall/links") {
    const body = JSON.parse(opts.body);
    linkPosts.push(body);
    return { service: body.service, item_code: body.item_code, materials: body.materials };
  }
  if (method === "POST" && p === "/api/siteprogress/fixture-mall/mapping") {
    const body = JSON.parse(opts.body);
    mappingPosts.push(body);
    return svcState();
  }
  throw new Error("unmocked route: " + method + " " + p);
}
global.fetch = window.fetch = async (url, opts) => ({ ok: true, json: async () => route(url, opts), text: async () => JSON.stringify(route(url, opts)) });
window.confirm = () => true; window.alert = () => {};

const src = fs.readFileSync(new URL("../frontend/siteprogress.js", import.meta.url), "utf8");
window.eval(src);
async function flush(n = 12) { for (let i = 0; i < n; i++) await new Promise((r) => setTimeout(r, 0)); }

async function main() {
  Object.defineProperty(document, "readyState", { value: "complete", configurable: true });
  document.dispatchEvent(new window.Event("DOMContentLoaded"));
  await flush();
  document.querySelector('.spnav button[data-v="siteprogress"]').click();
  await flush(20);

  // ================= LINK STOCK BUG =================

  // ---- test 1: typing a material and clicking "Save links" DIRECTLY
  // (never pressing + or Enter) must still save it -- this is the actual
  // bug reported against real Thoth Mall data ----
  linkPosts = [];
  const linkBtn = document.querySelector('[data-linkedit="5.5"]');
  assert.ok(linkBtn, "FAIL: link-stock button for item 5.5 missing");
  linkBtn.click();
  await flush(10);
  let modalEl = document.getElementById("sp-modal");
  assert.ok(modalEl, "FAIL: link modal did not open");
  const input1 = modalEl.querySelector('.sp-linkinput[data-code="5.5"]');
  assert.ok(input1, "FAIL: link input for 5.5 missing");
  input1.value = "25MM PVC PIPE";
  input1.dispatchEvent(new window.Event("input"));
  // deliberately do NOT press Enter or click "+" -- go straight to Save
  document.getElementById("sp-modal-save").click();
  await flush(10);
  assert.strictEqual(linkPosts.length, 1, "FAIL: expected exactly one /links POST");
  assert.deepStrictEqual(linkPosts[0], { service: "Electrical", item_code: "5.5", materials: [{ material: "25MM PVC PIPE", factor: null }] },
    "FAIL: typed-but-uncommitted material was not saved -- this is the exact reported bug");
  console.log("PASS: typing a material and clicking 'Save links' directly (no + or Enter) now saves it correctly");

  // ---- test 2: the normal, already-working flow (press Enter to commit,
  // then Save) still works -- the fix must not break existing behaviour ----
  linkPosts = [];
  linkBtn.click();
  await flush(10);
  modalEl = document.getElementById("sp-modal");
  const input2 = modalEl.querySelector('.sp-linkinput[data-code="5.5"]');
  input2.value = "20MM PVC PIPE";
  input2.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Enter" }));
  document.getElementById("sp-modal-save").click();
  await flush(10);
  assert.strictEqual(linkPosts.length, 1);
  assert.deepStrictEqual(linkPosts[0].materials, [{ material: "20MM PVC PIPE", factor: null }]);
  console.log("PASS: the existing 'press Enter, then Save' flow still works exactly as before (no regression)");

  // ---- test 3: two materials -- one committed via +, one left typed and
  // uncommitted -- both must be saved, and in the right order ----
  linkPosts = [];
  linkBtn.click();
  await flush(10);
  modalEl = document.getElementById("sp-modal");
  const input3 = modalEl.querySelector('.sp-linkinput[data-code="5.5"]');
  input3.value = "25MM PVC PIPE";
  modalEl.querySelector('.sp-addchip[data-add="5.5"]').click();     // committed via +
  input3.value = "20MM PVC PIPE";                                    // left uncommitted
  document.getElementById("sp-modal-save").click();
  await flush(10);
  assert.deepStrictEqual(linkPosts[0].materials,
    [{ material: "25MM PVC PIPE", factor: null }, { material: "20MM PVC PIPE", factor: null }],
    "FAIL: expected both the +-committed and the leftover-typed material, in order");
  console.log("PASS: a +-committed material plus a leftover typed one are both saved together, correctly ordered");

  // ---- test 4: an empty/whitespace-only leftover input must not create a
  // bogus empty-string chip ----
  linkPosts = [];
  linkBtn.click();
  await flush(10);
  modalEl = document.getElementById("sp-modal");
  modalEl.querySelector('.sp-linkinput[data-code="5.5"]').value = "   ";
  document.getElementById("sp-modal-save").click();
  await flush(10);
  assert.deepStrictEqual(linkPosts[0].materials, [], "FAIL: whitespace-only leftover text must not become a fake material");
  console.log("PASS: whitespace-only leftover text is correctly ignored, not saved as a bogus material");

  // ================= CONVERSION FACTOR (unit mismatch) =================

  // ---- test 4b: linking a material whose unit differs from the BOQ item's
  // own unit shows an inline factor input right on the chip -- item 5.5 is
  // "Nos", "25MM PVC PIPE" is "Rmt" per material_units ----
  linkPosts = [];
  linkBtn.click();
  await flush(10);
  modalEl = document.getElementById("sp-modal");
  const input4b = modalEl.querySelector('.sp-linkinput[data-code="5.5"]');
  input4b.value = "25MM PVC PIPE";
  input4b.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Enter" }));
  await flush(5);
  const chip = modalEl.querySelector('.sp-chip');
  assert.ok(chip, "FAIL: chip for the linked material missing");
  assert.ok(chip.classList.contains("warn"), "FAIL: a mismatched-unit chip with no factor yet should show the warn state");
  const factorInput = chip.querySelector(".sp-factorinput");
  assert.ok(factorInput, "FAIL: expected an inline factor input on a mismatched-unit chip (Nos vs Rmt)");
  assert.ok(chip.textContent.includes("Rmt") && chip.textContent.includes("Nos"),
    "FAIL: expected the chip to show both units (Rmt / Nos) so the engineer knows what to enter, got: " + chip.textContent);
  console.log("PASS: linking a material with a different unit (Rmt vs item's Nos) shows an inline factor input, chip flagged");

  // ---- test 4c: a material whose unit already matches gets no factor
  // input at all -- "GANG BOX" is Nos, item 5.5 is also Nos ----
  const inputGB = modalEl.querySelector('.sp-linkinput[data-code="5.5"]');
  inputGB.value = "GANG BOX";
  inputGB.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Enter" }));
  await flush(5);
  const chips = [...modalEl.querySelectorAll(".sp-chip")];
  const gangChip = chips.find((c) => c.textContent.includes("GANG BOX"));
  assert.ok(gangChip, "FAIL: GANG BOX chip missing");
  assert.ok(!gangChip.querySelector(".sp-factorinput"), "FAIL: a same-unit material should show no factor input at all");
  assert.ok(!gangChip.classList.contains("warn"), "FAIL: a same-unit chip should not be flagged");
  console.log("PASS: a material whose unit already matches (Nos = Nos) shows no factor input, no warning");

  // ---- test 4d: entering a factor commits it into what gets saved ----
  // re-query fresh -- test 4c's renderChips() call replaced the DOM,
  // so the `factorInput`/`chip` references captured in test 4b are stale
  const pipeChipBefore = [...modalEl.querySelectorAll(".sp-chip")].find((c) => c.textContent.includes("25MM PVC PIPE"));
  const factorInput2 = pipeChipBefore.querySelector(".sp-factorinput");
  assert.ok(factorInput2, "FAIL: factor input should still be present after adding a second chip");
  factorInput2.value = "3";
  factorInput2.dispatchEvent(new window.Event("change"));
  await flush(5);
  const pipeChipAfter = [...modalEl.querySelectorAll(".sp-chip")].find((c) => c.textContent.includes("25MM PVC PIPE"));
  assert.ok(pipeChipAfter && !pipeChipAfter.classList.contains("warn"), "FAIL: chip should drop the warn state once a factor is entered");
  linkPosts = [];
  document.getElementById("sp-modal-save").click();
  await flush(10);
  const saved = linkPosts[0].materials;
  const pipeEntry = saved.find((m) => m.material === "25MM PVC PIPE");
  const boxEntry = saved.find((m) => m.material === "GANG BOX");
  assert.strictEqual(pipeEntry.factor, 3, "FAIL: expected the entered factor (3) to be saved for the mismatched material");
  assert.strictEqual(boxEntry.factor, null, "FAIL: a same-unit material should save with factor:null (safe 1:1 default, not a fabricated number)");
  console.log("PASS: an entered factor (3 Rmt per Nos) is saved; the same-unit material saves with factor:null");

  // ================= ADD BOQ ITEMS SEARCH =================

  // ---- test 5: the search box exists and the full list renders initially ----
  const addBtn = document.querySelector('[data-map="Wall piping"]');
  assert.ok(addBtn, "FAIL: '+ Add BOQ items' button missing");
  addBtn.click();
  await flush(10);
  modalEl = document.getElementById("sp-modal");
  const searchInput = document.getElementById("sp-mapsearch");
  assert.ok(searchInput, "FAIL: search input missing from the Add BOQ items modal -- the actual reported gap");
  assert.strictEqual(document.querySelectorAll("#sp-maprows input[data-c]").length, 6);
  console.log("PASS: 'Add BOQ items' modal now has a search box, full list shows initially");

  // ---- test 6: typing a filter narrows the visible items ----
  searchInput.value = "DWC";
  searchInput.dispatchEvent(new window.Event("input"));
  await flush(5);
  let visible = [...document.querySelectorAll("#sp-maprows input[data-c]")].map((c) => c.dataset.c);
  assert.deepStrictEqual(visible.sort(), ["15.2.1", "15.2.2"].sort(), "FAIL: 'DWC' should narrow to the two DWC pipe items");
  console.log("PASS: typing 'DWC' narrows the list to exactly the matching items");

  // ---- test 7: search matches on code too, not just description ----
  searchInput.value = "16.1.1";
  searchInput.dispatchEvent(new window.Event("input"));
  await flush(5);
  visible = [...document.querySelectorAll("#sp-maprows input[data-c]")].map((c) => c.dataset.c);
  assert.deepStrictEqual(visible, ["16.1.1"]);
  console.log("PASS: search also matches on item code, not just description");

  // ---- test 8: no-match state renders a clear empty message, not a blank list ----
  searchInput.value = "zzz-nonexistent";
  searchInput.dispatchEvent(new window.Event("input"));
  await flush(5);
  assert.strictEqual(document.querySelectorAll("#sp-maprows input[data-c]").length, 0);
  assert.ok(document.querySelector("#sp-maprows .sp-empty"), "FAIL: expected a 'no items match' message");
  console.log("PASS: a filter with no matches shows a clear empty state, not a blank panel");

  // ---- test 9: THE critical one -- check an item, filter it out of view,
  // then Save -- it must still be included. A DOM query at save time would
  // silently drop it since its checkbox no longer exists once filtered out. ----
  searchInput.value = "";
  searchInput.dispatchEvent(new window.Event("input"));
  await flush(5);
  document.querySelector('#sp-maprows input[data-c="16.1.1"]').checked = true;
  document.querySelector('#sp-maprows input[data-c="16.1.1"]').dispatchEvent(new window.Event("change"));
  searchInput.value = "DWC";   // filters 16.1.1 out of the rendered list entirely
  searchInput.dispatchEvent(new window.Event("input"));
  await flush(5);
  assert.strictEqual(document.querySelector('#sp-maprows input[data-c="16.1.1"]'), null,
    "sanity: 16.1.1's checkbox should really be gone from the DOM right now");
  mappingPosts = [];
  document.getElementById("sp-modal-save").click();
  await flush(10);
  assert.strictEqual(mappingPosts.length, 1);
  assert.ok(mappingPosts[0].codes.includes("16.1.1"),
    "FAIL: an item checked then filtered out of view was dropped from the save -- exactly the bug a DOM-query-at-save-time would cause");
  assert.ok(mappingPosts[0].codes.includes("15.2.1"), "FAIL: pre-existing mapped item 5.5's sibling should also still be included");
  console.log("PASS: an item checked, then hidden by a later search filter, is still correctly included on Save");
}

main().then(() => console.log("\nALL LINK-STOCK + SEARCH BUGFIX TESTS PASSED")).catch((e) => { console.error("TEST FAILURE:", e.message, e.stack); process.exit(1); });
