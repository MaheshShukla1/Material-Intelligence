// jsdom test for the Measurement chip + sheet of a length item (Mtr / RMT) in Site Progress, and the zone link in the Zones dialog.
//
// Reported need (site engineers, Hyatt / Thoth): the client signs a measurement sheet that says WHERE the cable was laid (From, To, drum reading
// start / end, total). A length of cable is not always inside one room -- a feeder belongs to the whole project, a riser tray to a whole level --
// so the entry is not tied to a zone. Rules pinned here (the real siteprogress.js against a mocked server):
//   * a Mtr / RMT item row has a chip: "Add measurement" until the first entry, then "N entries · X of Y Mtr" (amber while part of the installed
//     quantity has no entry, green when all of it has); a Nos / Sq.Mtr item gets no chip; a drilled-down single room gets none;
//   * the chip opens a small sheet: Where (whole project / whole level / a zone), From, To, drum readings or a typed length, live length;
//   * "Add & next" posts the entry to the chosen place, shows it, and starts the next with From = this To and Start = this End;
//   * the place is remembered for the next entry; a closed place says why and cannot be added to; nothing is posted for it;
//   * Cancel asks for a reason, keeps the entry on screen struck through; Restore brings it back; a refused request says why, nothing changes;
//   * after every change the item row's chip is refreshed from the server's numbers;
//   * in the Zones dialog a zone with entries links to the sheet and its number box is read-only; a zone without entries is exactly what it
//     was; if the entries cannot be fetched the dialog has no links.
import { JSDOM } from "jsdom";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
let pass = 0, fail = 0;
function ok(cond, msg) { if (cond) pass++; else { fail++; console.error("FAIL:", msg); } }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const click = (w, el) => el.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));

const Z1 = "z1", Z2 = "z2";
const CABLE = "G.1", DET = "N.1";
const PROJ = "~project", LVL = "~area:L3";

const STRUCTURE = { kind: "mall", id: "p0", type: "project", name: "Thoth",
  children: [{ id: "lv1", type: "level", name: "L3", children: [Z1, Z2].map((id, i) => ({ id, type: "room", name: `Zone ${i + 1}`, children: [] })) }] };

async function boot({ runsFail = false, refuse = null, prompts = [], closed = {} } = {}) {
  const html = `<!doctype html><html><body><header class="top"><div class="brand"><p id="ctx"></p></div><div class="topact"></div></header><main></main></body></html>`;
  const dom = new JSDOM(html, { url: "https://example.test/", runScripts: "dangerously", pretendToBeVisual: true });
  const w = dom.window, d = w.document;
  const posts = [], gets = [], prompted = [];
  let n = 0;
  // server state: entries per place, and what each place holds
  const places = {
    [PROJ]: { kind: "project", label: "Whole project (feeder / main route)", group: "", plan: 600, runs: [], base: 0 },
    [LVL]: { kind: "area", label: "L3 · whole level", group: "Whole level", plan: 600, runs: [], base: 0 },
    [Z1]: { kind: "zone", label: "Zone 1", group: "L3", plan: 300, runs: [], base: 0 },
    [Z2]: { kind: "zone", label: "Zone 2", group: "L3", plan: 300, runs: [], base: 0 },
  };
  const act = (p) => p.runs.filter((r) => !r.cancelled).reduce((a, r) => a + r.qty, 0);
  const done = (p) => p.base + act(p);
  const scope = (k) => {
    const p = places[k], a = act(p), why = closed[k] || "";
    return { key: k, kind: p.kind, label: p.label, group: p.group, plan: p.plan, done: done(p), allowed: !why, why, writable: true, wwhy: "",
      runs: p.runs.map((r) => ({ ...r })), active: a, other: Math.round((done(p) - a) * 1e4) / 1e4 };
  };
  const summary = () => {
    const all = Object.values(places), nn = all.reduce((a, p) => a + p.runs.filter((r) => !r.cancelled).length, 0);
    const installed = done(places[PROJ]) + done(places[LVL]) + done(places[Z1]) + done(places[Z2]) - done(places[LVL]) * 0;
    return { n: nn, backed: all.reduce((a, p) => a + Math.min(act(p), done(p)), 0), done: installed };
  };
  const cableItem = () => {
    const s = summary();
    const it = { code: CABLE, desc: "11 kV cable", unit: "Mtr", sub: "Other", acts: ["Cabling"], qty: 600, planned: 600, used: s.done, remaining: 600 - s.done,
      pct: s.done / 6, mapped: true, rooms: 2, in_room: true, rate: 40, quick: false, done_val: 0, rem_val: 0,
      room_done: 0, room_progress: 0, room_pending: 2, room_total: 2, planned_override: false, has_room_groups: false };
    if (s.n) it.meas = s;
    return it;
  };
  const detItem = () => ({ code: DET, desc: "Smoke detector", unit: "Nos", sub: "Other", acts: ["Cabling"], qty: 6, planned: 6, used: 0, remaining: 6, pct: 0, mapped: true,
    rooms: 2, in_room: true, rate: 900, quick: false, done_val: 0, rem_val: 0, room_done: 0, room_progress: 0, room_pending: 2, room_total: 2, planned_override: false, has_room_groups: false });
  const serviceBody = () => ({ service: "ELV", room: null, activities: ["Cabling"], mapping: { Cabling: [CABLE, DET] }, act_pct: { Cabling: 0 }, overall_pct: 0,
    items: [cableItem(), detItem()], pnl_by_activity: {}, pnl_totals: {}, pnl_unmapped_value: { items: 0 }, item_rooms: {}, item_room_qty: {},
    item_progress: {}, unmapped: [], labour_only: {}, labour_pct: {}, labour_suggested: {} });
  w.prompt = (msg) => { prompted.push(msg); return prompts.length ? prompts.shift() : ""; };
  const findRun = (id) => { for (const [k, p] of Object.entries(places)) { const r = p.runs.find((x) => x.id === id); if (r) return [k, p, r]; } return []; };
  const keyOf = (b) => (b.scope === "project" ? PROJ : b.scope === "area" ? "~area:" + b.area : b.room);
  w.fetch = (url, opts) => {
    const u = String(url);
    let status = 200, body = {};
    const post = opts && opts.method === "POST";
    if (post) posts.push({ url: u, body: JSON.parse(opts.body) }); else gets.push(u);
    if (u === "/api/projects") body = [{ slug: "thoth-mall", project: "Thoth", runs: 1, latest_run: "r1" }];
    else if (u === "/api/siteprogress/thoth-mall") body = { structure: STRUCTURE, has_boq: true, services: ["ELV"] };
    else if (u.startsWith("/api/siteprogress/thoth-mall/item-room-plan")) {
      const code = new URL(u, "https://x.test").searchParams.get("item_code");
      body = code === CABLE ? { unit: "Mtr", rooms: { [Z1]: { planned: 300, frac: done(places[Z1]) / 300 }, [Z2]: { planned: 300, frac: done(places[Z2]) / 300 } } }
                            : { unit: "Nos", rooms: { [Z1]: { planned: 3, frac: 0 }, [Z2]: { planned: 3, frac: 0 } } };
    } else if (post && u.includes("/progress/runs/cancel")) {
      const b = JSON.parse(opts.body), [k, p, r] = findRun(b.run_id);
      if (refuse) { status = 400; body = { detail: refuse }; }
      else { r.cancelled = b.cancelled; r.reason = b.reason || ""; body = { svc: serviceBody(), zone: scope(k), summary: summary() }; }
    } else if (post && u.includes("/progress/runs")) {
      const b = JSON.parse(opts.body), k = keyOf(b);
      if (refuse) { status = 400; body = { detail: refuse }; }
      else {
        const qty = b.start != null ? Math.abs(b.start - b.end) : b.qty;
        places[k].runs.push({ id: "run" + (++n), from: b.from, to: b.to, start: b.start ?? null, end: b.end ?? null, qty, remarks: b.remarks || "", at: "2026-10-20T10:00:0" + n, cancelled: false, reason: "" });
        body = { svc: serviceBody(), zone: scope(k), summary: summary() };
      }
    } else if (u.startsWith("/api/siteprogress/thoth-mall/progress/runs")) {
      if (runsFail) { status = 500; body = { detail: "boom" }; }
      else body = { unit: "Mtr", runs_ok: true, scopes: Object.keys(places).map(scope), summary: summary(),
                    rooms: Object.fromEntries([Z1, Z2].filter((k) => places[k].runs.length).map((k) => [k, scope(k)])) };
    } else if (u.startsWith("/api/siteprogress/thoth-mall/service/") || u.includes("/mark-rooms-done") || u.includes("/progress/rooms")) body = serviceBody();
    else if (u.startsWith("/api/siteprogress/thoth-mall/pnl/")) body = { done_value: 0, remaining_value: 0 };
    else if (u.startsWith("/api/siteprogress/thoth-mall/realistic/")) body = { items: [], shortages: 0 };
    return Promise.resolve({ ok: status < 400, status, json: async () => body, text: async () => JSON.stringify(body) });
  };
  w.eval(fs.readFileSync(path.join(__dirname, "siteprogress.js"), "utf8"));
  await sleep(0); await sleep(0);
  click(w, [...d.querySelectorAll(".spnav button")].find((b) => b.dataset.v === "siteprogress"));
  await sleep(60);
  return { w, d, posts, gets, places, prompted };
}
const chip = (d, code) => d.querySelector(`[data-measedit="${code}"]`);
async function openSheet(ctx, code = CABLE) {
  click(ctx.w, chip(ctx.d, code));
  await sleep(60);
  return ctx.d.querySelector("#sp-modal");
}
const set = (w, el, v) => { el.value = String(v); el.dispatchEvent(new w.Event("input", { bubbles: true })); };
const pick = (w, el, v) => { el.value = v; el.dispatchEvent(new w.Event("change", { bubbles: true })); };
const q = (m, sel) => m.querySelector(sel);
const entryPosts = (ctx) => ctx.posts.filter((p) => /\/progress\/runs$/.test(p.url) || /\/progress\/runs\?/.test(p.url));
const txt = (el) => el.textContent.replace(/\s+/g, " ").trim();

async function main() {
  // ================= 1. the chip
  let ctx = await boot();
  ok(!!chip(ctx.d, CABLE), "the length item (Mtr) has a measurement chip");
  ok(!chip(ctx.d, DET), "a Nos item has none");
  ok(txt(chip(ctx.d, CABLE)) === "📏 Add measurement", `before the first entry the chip invites one, got "${txt(chip(ctx.d, CABLE))}"`);
  ok(!chip(ctx.d, CABLE).classList.contains("warn") && !chip(ctx.d, CABLE).classList.contains("on"), "the empty chip is neutral");

  // ================= 2. the reported scenario: a FEEDER for the whole project, drum readings, Add & next
  let m = await openSheet(ctx);
  ok(!!m && !m.classList.contains("sp-zones"), "the small measurement sheet opened (not the wide Zones dialog)");
  ok(ctx.gets.some((u) => u.includes("/progress/runs?service=ELV&item_code=G.1")), "opening the sheet asks for the item's entries");
  ok(/Measurement · G\.1/.test(txt(m.querySelector("h2"))), "titled with the item code");
  ok(/No measurement yet/.test(txt(m)), "an empty item says so");
  const where = q(m, '[data-m="where"]');
  ok(where.value === PROJ, `the place starts at the whole project when nothing was measured yet, got ${where.value}`);
  const labels = [...where.querySelectorAll("option")].map((o) => o.textContent);
  ok(labels[0].startsWith("Whole project") && labels.includes("L3 · whole level") && labels.includes("Zone 1") && labels.includes("Zone 2"), `all three kinds of place are offered, got ${JSON.stringify(labels)}`);
  ok([...where.querySelectorAll("optgroup")].length === 3, "places are grouped (not in one place / whole level / L3)");
  const addBtn = () => q(m, '[data-madd="next"]');
  ok(addBtn().disabled, "Add is off until From, To and readings are filled in");
  set(ctx.w, q(m, '[data-m="from"]'), "Transformer");
  set(ctx.w, q(m, '[data-m="to"]'), "MDB");
  set(ctx.w, q(m, '[data-m="start"]'), 1000);
  ok(q(m, "[data-mtotal]").textContent === "—", "one reading alone has no length");
  set(ctx.w, q(m, '[data-m="end"]'), 858);
  ok(q(m, "[data-mtotal]").textContent === "142 Mtr", `length = |1000 - 858| = 142 Mtr, got ${q(m, "[data-mtotal]").textContent}`);
  ok(!addBtn().disabled, "Add is on once From, To and both readings are valid");
  ctx.posts.length = 0;
  click(ctx.w, addBtn()); await sleep(80);
  let ep = entryPosts(ctx);
  ok(ep.length === 1 && JSON.stringify(ep[0].body) === JSON.stringify({ service: "ELV", item_code: CABLE, from: "Transformer", to: "MDB", remarks: "", scope: "project", start: 1000, end: 858 }),
     `posted the feeder exactly, got ${JSON.stringify(ep[0] && ep[0].body)}`);
  ok(ctx.posts.every((p) => !p.url.includes("/mark-rooms-done")), "an entry does not go through Mark ticked as done");
  ok(/Transformer → MDB/.test(txt(q(m, "[data-mslist]"))) && /1000 → 858/.test(txt(q(m, "[data-mslist]"))) && /142 Mtr/.test(txt(q(m, "[data-mslist]"))), "the entry is listed with its readings");
  ok(/Whole project/.test(txt(q(m, "[data-mslist]"))), "and says where it is");
  ok(/142 of 142 Mtr/.test(txt(q(m, "[data-mscov]"))) && q(m, ".sp-mscov").classList.contains("ok"), `the strip says all installed quantity is backed, got "${txt(q(m, "[data-mscov]"))}"`);
  ok(q(m, '[data-m="from"]').value === "MDB" && q(m, '[data-m="start"]').value === "858", "Add & next: From = this To, Start = this End");
  ok(q(m, '[data-m="to"]').value === "" && q(m, '[data-m="end"]').value === "", "To and End are empty for the next entry");
  ok(q(m, '[data-m="where"]').value === PROJ, "the place is kept for the next entry");
  const rowChip = txt(chip(ctx.d, CABLE));
  ok(rowChip === "📏 1 entry · 142 of 142 Mtr" && chip(ctx.d, CABLE).classList.contains("on"), `the item row's chip is refreshed and green, got "${rowChip}"`);

  // typed length ("Direct length"), then plain Add which clears From too
  click(ctx.w, q(m, '[data-mmode="qty"]'));
  ok(!q(m, '[data-m="start"]') && !!q(m, '[data-m="qty"]'), "Direct length swaps the two readings for one length box");
  set(ctx.w, q(m, '[data-m="from"]'), "MDB");
  set(ctx.w, q(m, '[data-m="to"]'), "DB-1");
  set(ctx.w, q(m, '[data-m="qty"]'), "58.5");
  set(ctx.w, q(m, '[data-m="remarks"]'), "in trunking");
  ctx.posts.length = 0;
  click(ctx.w, q(m, '[data-madd="one"]')); await sleep(80);
  ep = entryPosts(ctx);
  ok(ep.length === 1 && ep[0].body.qty === 58.5 && ep[0].body.start === undefined && ep[0].body.remarks === "in trunking", `a typed length posts qty and no readings, got ${JSON.stringify(ep[0] && ep[0].body)}`);
  ok(q(m, '[data-m="from"]').value === "" && q(m, '[data-m="qty"]').value === "", "Add (not next) clears the form");
  ok(txt(chip(ctx.d, CABLE)) === "📏 2 entries · 200.5 of 200.5 Mtr", `two entries counted, got "${txt(chip(ctx.d, CABLE))}"`);

  // ================= 3. cancel / restore
  ctx.prompted.length = 0;
  const w2 = { v: "wrong drum" };
  ctx.w.prompt = (msg) => { ctx.prompted.push(msg); return w2.v; };
  ctx.posts.length = 0;
  click(ctx.w, [...m.querySelectorAll("[data-mtoggle]")].find((b) => b.closest(".sp-runrow").textContent.includes("Transformer"))); await sleep(80);
  ok(ctx.prompted.length === 1 && /cancelled/.test(ctx.prompted[0]), "Cancel asks for a reason");
  const cp = ctx.posts.filter((p) => p.url.includes("/runs/cancel"));
  ok(cp.length === 1 && cp[0].body.cancelled === true && cp[0].body.reason === "wrong drum" && cp[0].body.scope === "project", `cancel posted with the reason and place, got ${JSON.stringify(cp[0] && cp[0].body)}`);
  const struck = [...m.querySelectorAll(".sp-runrow.x")];
  ok(struck.length === 1 && /Cancelled: wrong drum/.test(txt(struck[0])) && /Restore/.test(txt(struck[0])), "the cancelled entry stays on screen, struck through, with its reason and a Restore button");
  ok(txt(chip(ctx.d, CABLE)) === "📏 1 entry · 58.5 of 58.5 Mtr", `the chip drops the cancelled entry, got "${txt(chip(ctx.d, CABLE))}"`);
  ctx.prompted.length = 0;
  click(ctx.w, struck[0].querySelector("[data-mtoggle]")); await sleep(80);
  ok(ctx.prompted.length === 0, "Restore does not ask for a reason");
  ok(m.querySelectorAll(".sp-runrow.x").length === 0 && txt(chip(ctx.d, CABLE)) === "📏 2 entries · 200.5 of 200.5 Mtr", "restored: back in the count");

  // ================= 4. a whole level and a zone, the place is chosen from the list
  ctx = await boot();
  m = await openSheet(ctx);
  pick(ctx.w, q(m, '[data-m="where"]'), LVL);
  set(ctx.w, q(m, '[data-m="from"]'), "Riser");
  set(ctx.w, q(m, '[data-m="to"]'), "L3 end");
  set(ctx.w, q(m, '[data-m="start"]'), 0);
  set(ctx.w, q(m, '[data-m="end"]'), 150);
  ctx.posts.length = 0;
  click(ctx.w, q(m, '[data-madd="one"]')); await sleep(80);
  ep = entryPosts(ctx);
  ok(ep.length === 1 && ep[0].body.scope === "area" && ep[0].body.area === "L3" && ep[0].body.room === undefined, `a whole-level entry posts scope area + the level, got ${JSON.stringify(ep[0] && ep[0].body)}`);
  ok(/L3 · whole level/.test(txt(q(m, "[data-mslist]"))), "listed under its level");
  pick(ctx.w, q(m, '[data-m="where"]'), Z2);
  set(ctx.w, q(m, '[data-m="from"]'), "FACP-3");
  set(ctx.w, q(m, '[data-m="to"]'), "Loop A");
  set(ctx.w, q(m, '[data-m="qty"]') || (click(ctx.w, q(m, '[data-mmode="qty"]')), q(m, '[data-m="qty"]')), 40);
  ctx.posts.length = 0;
  click(ctx.w, q(m, '[data-madd="one"]')); await sleep(80);
  ep = entryPosts(ctx);
  ok(ep.length === 1 && ep[0].body.scope === "zone" && ep[0].body.room === Z2 && ep[0].body.qty === 40, `a zone entry posts scope zone + the room, got ${JSON.stringify(ep[0] && ep[0].body)}`);
  ok(/Zone 2/.test(txt(q(m, "[data-mslist]"))), "listed under its zone");
  ok(txt(chip(ctx.d, CABLE)).startsWith("📏 2 entries"), `two entries in two places, got "${txt(chip(ctx.d, CABLE))}"`);

  // ================= 5. a closed place says why and cannot be added to
  ctx = await boot({ closed: { [Z1]: "This item is measured for the whole project. Add it there." } });
  m = await openSheet(ctx);
  pick(ctx.w, q(m, '[data-m="where"]'), Z1);
  ok(/Zone 1 \(closed\)/.test(q(m, '[data-m="where"]').selectedOptions[0].textContent), "a closed place is marked in the list");
  ok(!q(m, "[data-mswhy]").hidden && /measured for the whole project/.test(q(m, "[data-mswhy]").textContent), "and the reason is shown");
  set(ctx.w, q(m, '[data-m="from"]'), "a"); set(ctx.w, q(m, '[data-m="to"]'), "b"); set(ctx.w, q(m, '[data-m="start"]'), 0); set(ctx.w, q(m, '[data-m="end"]'), 5);
  ok(q(m, '[data-madd="next"]').disabled && q(m, '[data-madd="one"]').disabled, "Add is off for a closed place");
  pick(ctx.w, q(m, '[data-m="where"]'), PROJ);
  ok(q(m, "[data-mswhy]").hidden && !q(m, '[data-madd="next"]').disabled, "back on an open place: no warning, Add on (the typed values were kept)");

  // ================= 6. a refused entry says why and changes nothing
  ctx = await boot({ refuse: "this item is planned at 600; 590 is recorded, so 20 more does not fit. Raise the plan first." });
  m = await openSheet(ctx);
  set(ctx.w, q(m, '[data-m="from"]'), "a"); set(ctx.w, q(m, '[data-m="to"]'), "b"); set(ctx.w, q(m, '[data-m="start"]'), 0); set(ctx.w, q(m, '[data-m="end"]'), 20);
  click(ctx.w, q(m, '[data-madd="next"]')); await sleep(80);
  ok(/Raise the plan first/.test(txt(ctx.d.querySelector("#sp-toast"))), "the server's reason is shown");
  ok(q(m, '[data-m="from"]').value === "a" && q(m, '[data-m="end"]').value === "20", "the typed values are kept so nothing is lost");
  ok(q(m, ".sp-runlist") === null && txt(chip(ctx.d, CABLE)) === "📏 Add measurement", "no entry shown, chip unchanged");

  // ================= 7. nothing is required: the item row, the chip and the Zones dialog work without a single entry
  ctx = await boot();
  click(ctx.w, ctx.d.querySelector(`[data-roomsedit="${CABLE}"]`)); await sleep(80);
  let z = ctx.d.querySelector("#sp-modal");
  ok(!!z && z.classList.contains("sp-zones"), "the Zones dialog still opens");
  ok(!z.querySelector("[data-meas]") && !z.querySelector("input[data-roomqty]").readOnly, "zones without entries: no link, number boxes are editable, as before");

  // ================= 8. a zone WITH entries links to the sheet, its number is read-only
  ctx = await boot();
  m = await openSheet(ctx);
  pick(ctx.w, q(m, '[data-m="where"]'), Z1);
  set(ctx.w, q(m, '[data-m="from"]'), "FACP-3"); set(ctx.w, q(m, '[data-m="to"]'), "Loop A"); click(ctx.w, q(m, '[data-mmode="qty"]')); set(ctx.w, q(m, '[data-m="qty"]'), 70);
  click(ctx.w, q(m, '[data-madd="one"]')); await sleep(80);
  click(ctx.w, ctx.d.querySelector("#sp-modal-x"));
  click(ctx.w, ctx.d.querySelector(`[data-roomsedit="${CABLE}"]`)); await sleep(100);
  z = ctx.d.querySelector("#sp-modal");
  const zl = z.querySelector(`[data-meas="${Z1}"]`);
  ok(!!zl && /1 entry/.test(zl.textContent), `the zone with an entry links to its measurements, got ${zl && zl.textContent}`);
  ok(z.querySelector(`input[data-roomqty="${Z1}"]`).readOnly, "its number box is read-only (the quantity is the sum of the entries)");
  ok(!z.querySelector(`[data-meas="${Z2}"]`) && !z.querySelector(`input[data-roomqty="${Z2}"]`).readOnly, "the other zone is untouched");
  click(ctx.w, zl); await sleep(100);
  const m2 = ctx.d.querySelector("#sp-modal");
  ok(!m2.classList.contains("sp-zones") && q(m2, '[data-m="where"]').value === Z1, "the link opens the sheet already on that zone");

  // ================= 9. if the entries cannot be fetched, the Zones dialog is what it was
  ctx = await boot({ runsFail: true });
  click(ctx.w, ctx.d.querySelector(`[data-roomsedit="${CABLE}"]`)); await sleep(100);
  z = ctx.d.querySelector("#sp-modal");
  ok(!!z && !z.querySelector("[data-meas]") && !z.querySelector("input[data-roomqty]").readOnly, "no entries fetched: no links, nothing read-only");
  click(ctx.w, ctx.d.querySelector("#sp-modal-x"));
  click(ctx.w, chip(ctx.d, CABLE)); await sleep(80);
  ok(!ctx.d.querySelector("#sp-modal") && /Could not open/.test(txt(ctx.d.querySelector("#sp-toast"))), "the chip says it could not open, and no half-sheet appears");

  // ================= 10. the global hook Billing uses
  ctx = await boot();
  ok(typeof ctx.w.MIMeasure === "object" && typeof ctx.w.MIMeasure.open === "function", "window.MIMeasure.open exists for the Billing screen");
  let changed = null;
  await ctx.w.MIMeasure.open({ slug: "thoth-mall", service: "ELV", code: CABLE, desc: "11 kV cable", unit: "Mtr", room: "", onChange: async (svc) => { changed = svc; } });
  m = ctx.d.querySelector("#sp-modal");
  ok(!!m && /Measurement · G\.1/.test(txt(m.querySelector("h2"))), "it opens the same sheet from outside Site Progress");
  set(ctx.w, q(m, '[data-m="from"]'), "a"); set(ctx.w, q(m, '[data-m="to"]'), "b"); set(ctx.w, q(m, '[data-m="start"]'), 0); set(ctx.w, q(m, '[data-m="end"]'), 9);
  click(ctx.w, q(m, '[data-madd="one"]')); await sleep(80);
  ok(changed && Array.isArray(changed.items), "and tells the caller after every change, with the new service view");

  console.log(`${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
}
main();
