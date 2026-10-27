// jsdom test: a BOQ line's name must be readable in full from the item row and from the Zones dialog heading.
//
// Reported (site engineer, Point Wiring, switch/socket lines): the row showed "One light point controlled by one single way 6 A switch.wi..."
// and the Zones dialog "...switch.with average p...", so two lines that differ only at the end could not be told apart without
// opening something. Cause: short() cut every description at 58 characters (70 in the Zones dialog); the server always sent the full text.
// Pinned here:
//   * the row shows the whole description, with no "…" and no cut;
//   * the Zones dialog heading shows the whole description too;
//   * the existing clean-up of a leading "Supply, Installation ... of" is unchanged;
//   * two lines that share the first 70 characters are distinguishable in the row;
//   * nothing else moves: the code, the tags, the qty box and the planned figure are still on the row.
import { JSDOM } from "jsdom";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
let pass = 0, fail = 0;
function ok(cond, msg) { if (cond) pass++; else { fail++; console.error("FAIL:", msg); } }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const click = (w, el) => el.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));

const A = "1.1.1", B = "1.1.2", C = "1.1.3";
const SHARED = "One light point controlled by one single way 6 A switch with average point wiring length of ";
const DESC = {
  [A]: SHARED + "10 m in 1.5 sqmm FRLS copper wire laid in concealed 20 mm PVC conduit, complete with switch box and cover plate",
  [B]: SHARED + "15 m in 1.5 sqmm FRLS copper wire laid in concealed 20 mm PVC conduit, complete with switch box and cover plate",
  [C]: "Supply, Installation, Testing and Commissioning of One light point controlled by SP MCB, cost includes rate of all fittings",
};
const CLEANED_C = "One light point controlled by SP MCB, cost includes rate of all fittings";

function item(code) {
  return { code, desc: DESC[code], unit: "Nos", sub: "Switch/Socket", acts: ["Point Wiring"], qty: 25, planned: 50, used: 0, remaining: 50,
    pct: 0, mapped: true, rooms: 2, in_room: true, rate: 40, quick: false, done_val: 0, rem_val: 0,
    room_done: 0, room_progress: 0, room_pending: 2, room_total: 2, planned_override: false, has_room_groups: false };
}
function serviceBody() {
  return { service: "Electrical", room: null, activities: ["Point Wiring"], mapping: { "Point Wiring": [A, B, C] }, act_pct: { "Point Wiring": 0 }, overall_pct: 0,
    items: [A, B, C].map(item), pnl_by_activity: {}, pnl_totals: {}, pnl_unmapped_value: { items: 0 }, item_rooms: {}, item_room_qty: {},
    item_progress: {}, unmapped: [], labour_only: {}, labour_pct: {}, labour_suggested: {} };
}
const Z = ["z1", "z2"];
const STRUCTURE = { kind: "mall", id: "p0", type: "project", name: "Sample Mall",
  children: [{ id: "lv1", type: "level", name: "B3", children: Z.map((id, i) => ({ id, type: "room", name: `Zone ${i + 1}`, children: [] })) }] };

async function boot() {
  const html = `<!doctype html><html><body><header class="top"><div class="brand"><p id="ctx"></p></div><div class="topact"></div></header><main></main></body></html>`;
  const dom = new JSDOM(html, { url: "https://example.test/", runScripts: "dangerously", pretendToBeVisual: true });
  const w = dom.window, d = w.document;
  w.fetch = (url, opts) => {
    const u = String(url); let body = {};
    if (u === "/api/projects") body = [{ slug: "sample-mall", project: "Sample Mall", runs: 1, latest_run: "r1" }];
    else if (u === "/api/siteprogress/sample-mall") body = { structure: STRUCTURE, has_boq: true, services: ["Electrical"] };
    else if (u.startsWith("/api/siteprogress/sample-mall/item-room-plan")) body = { unit: "Nos", rooms: Object.fromEntries(Z.map((z) => [z, { planned: 25, frac: 0 }])) };
    else if (u.startsWith("/api/siteprogress/sample-mall/progress/runs")) body = { unit: "Nos", runs_ok: false, rooms: {} };
    else if (u.startsWith("/api/siteprogress/sample-mall/service/")) body = serviceBody();
    else if (u.startsWith("/api/siteprogress/sample-mall/pnl/")) body = { done_value: 0, remaining_value: 0 };
    else if (u.startsWith("/api/siteprogress/sample-mall/realistic/")) body = { items: [], shortages: 0 };
    return Promise.resolve({ ok: true, status: 200, json: async () => body, text: async () => JSON.stringify(body) });
  };
  w.eval(fs.readFileSync(path.join(__dirname, "siteprogress.js"), "utf8"));
  await sleep(0); await sleep(0);
  click(w, [...d.querySelectorAll(".spnav button")].find((b) => b.dataset.v === "siteprogress"));
  await sleep(80);
  return { w, d };
}
const norm = (s) => String(s || "").replace(/\s+/g, " ").trim();

async function main() {
  const { w, d } = await boot();
  const rowOf = (code) => d.querySelector(`.sp-brow[data-code="${code}"]`);
  const nameOf = (code) => { const n = rowOf(code).querySelector(".sp-bname"); const c = n.querySelector(".code"); return norm(n.firstChild === c ? [...n.childNodes].filter((x) => x.nodeType === 3).map((x) => x.textContent).join("") : n.textContent); };

  // ---- 1. the reported rows: the whole description is on the row
  ok(!!rowOf(A) && !!rowOf(B) && !!rowOf(C), "the three BOQ rows are drawn");
  ok(nameOf(A) === DESC[A], `row 1.1.1 shows the whole description, got "${nameOf(A)}"`);
  ok(nameOf(B) === DESC[B], `row 1.1.2 shows the whole description, got "${nameOf(B)}"`);
  ok(!rowOf(A).querySelector(".sp-bname").textContent.includes("…"), "no ellipsis on row 1.1.1");
  ok(!rowOf(B).querySelector(".sp-bname").textContent.includes("…"), "no ellipsis on row 1.1.2");
  ok(DESC[A].length > 100, "fixture is genuinely longer than the old 58 / 70 character cuts");

  // ---- 2. the two lines that only differ at the end can be told apart on the rows
  ok(DESC[A].slice(0, 70) === DESC[B].slice(0, 70), "fixture: the two lines share their first 70 characters (the old cut made them identical)");
  ok(nameOf(A) !== nameOf(B) && /10 m/.test(nameOf(A)) && /15 m/.test(nameOf(B)), "the rows differ: one says 10 m, the other 15 m");

  // ---- 3. the existing clean-up of a leading "Supply, Installation ... of" is untouched
  ok(nameOf(C) === CLEANED_C, `a leading "Supply, Installation, Testing and Commissioning of" is still removed, got "${nameOf(C)}"`);

  // ---- 4. nothing else on the row moved
  const r = rowOf(A);
  ok(r.querySelector(".code").textContent === A, "the item code is still on the row");
  ok(!!r.querySelector(".sp-tag") && r.querySelector(".sp-tag").textContent === "Switch/Socket", "the sub-category tag is still there");
  ok(!!r.querySelector(`input[data-qty="${A}"]`), "the quantity box is still there");
  ok(!!r.querySelector(`[data-planned="${A}"]`), "the planned figure is still there");
  ok(!!r.querySelector(`[data-roomsedit="${A}"]`), "the zones chip is still there");

  // ---- 5. the Zones dialog heading shows the whole description
  click(w, r.querySelector(`[data-roomsedit="${A}"]`));
  await sleep(80);
  const m = d.querySelector("#sp-modal");
  ok(!!m && m.classList.contains("sp-zones"), "the Zones dialog opened");
  const dlgText = norm(m.textContent);
  ok(dlgText.includes(DESC[A]), `the Zones dialog shows the whole description, got "${dlgText.slice(0, 220)}"`);
  // (the dialog's own help text has a "Whole …" in it, so only the part that is the item's name is checked)
  ok(!dlgText.slice(0, dlgText.indexOf(" — tick the")).includes("…"), "no ellipsis in the item's name in the Zones dialog");
  ok(/tick the .* you're updating/.test(dlgText), "the instruction after the name is still there");

  console.log(`${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
}
main().catch((e) => { console.error(e); process.exit(1); });
