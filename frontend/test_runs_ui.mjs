// jsdom test for the Runs panel of the Zones dialog (length items: Mtr / RMT).
//
// Reported need (site engineer, Hyatt): the client signs a measurement sheet that says WHERE the cable was laid (From, To, drum reading
// start / end, total), not only how much. Rules pinned here:
//   * a zone of a Mtr / RMT item gets a "+ Runs" link; a Nos / Sq.Mtr item gets none and never asks for runs;
//   * the panel takes From, To and either the drum readings (total = |start - end|, shown live) or a typed length;
//   * "Add & next run" posts the run, shows it, and starts the next one with From = this To and Start = this End;
//   * the zone's number box then shows the quantity that came from the runs and cannot be typed over;
//   * Cancel asks for a reason, keeps the run on screen struck through; Restore brings it back;
//   * a refused run (server 400) says why and leaves everything as it was;
//   * if the runs cannot be fetched, the dialog is exactly what it was (no Runs links).
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

function item(code, unit, planned, desc) {
  return { code, desc, unit, sub: "Other", acts: ["Cabling"], qty: planned, planned, used: 0, remaining: planned,
    pct: 0, mapped: true, rooms: 2, in_room: true, rate: 40, quick: false, done_val: 0, rem_val: 0,
    room_done: 0, room_progress: 0, room_pending: 2, room_total: 2, planned_override: false, has_room_groups: false };
}
function serviceBody() {
  return { service: "ELV", room: null, activities: ["Cabling"], mapping: { Cabling: [CABLE, DET] }, act_pct: { Cabling: 0 }, overall_pct: 0,
    items: [item(CABLE, "Mtr", 600, "2C x 1.5 sq mm FA cable"), item(DET, "Nos", 6, "Smoke detector")],
    pnl_by_activity: {}, pnl_totals: {}, pnl_unmapped_value: { items: 0 }, item_rooms: {}, item_room_qty: {},
    item_progress: {}, unmapped: [], labour_only: {}, labour_pct: {}, labour_suggested: {} };
}
const STRUCTURE = { kind: "mall", id: "p0", type: "project", name: "Hyatt",
  children: [{ id: "lv1", type: "level", name: "L3", children: [Z1, Z2].map((id, i) => ({ id, type: "room", name: `Zone ${i + 1}`, children: [] })) }] };

async function boot({ runsFail = false, preset = {}, refuse = null, prompts = [] } = {}) {
  const html = `<!doctype html><html><body><header class="top"><div class="brand"><p id="ctx"></p></div><div class="topact"></div></header><main></main></body></html>`;
  const dom = new JSDOM(html, { url: "https://example.test/", runScripts: "dangerously", pretendToBeVisual: true });
  const w = dom.window, d = w.document;
  const posts = [], gets = [];
  let n = 0;
  const zones = { [Z1]: { runs: [], done: 0 }, [Z2]: { runs: [], done: 0 }, ...preset };
  const view = (z) => ({ runs: z.runs.map((r) => ({ ...r })), active: z.runs.filter((r) => !r.cancelled).reduce((a, r) => a + r.qty, 0), done: z.done,
                         other: Math.round((z.done - z.runs.filter((r) => !r.cancelled).reduce((a, r) => a + r.qty, 0)) * 1e4) / 1e4 });
  w.prompt = (msg) => { gets.push("PROMPT " + msg); return prompts.length ? prompts.shift() : ""; };
  w.fetch = (url, opts) => {
    const u = String(url);
    let status = 200, body = {};
    if (opts && opts.method === "POST") posts.push({ url: u, body: JSON.parse(opts.body) }); else gets.push(u);
    if (u === "/api/projects") body = [{ slug: "hyatt-hotel", project: "Hyatt", runs: 1, latest_run: "r1" }];
    else if (u === "/api/siteprogress/hyatt-hotel") body = { structure: STRUCTURE, has_boq: true, services: ["ELV"] };
    else if (u.startsWith("/api/siteprogress/hyatt-hotel/item-room-plan")) {
      const code = new URL(u, "https://x.test").searchParams.get("item_code");
      body = code === CABLE ? { unit: "Mtr", rooms: { [Z1]: { planned: 300, frac: zones[Z1].done / 300 }, [Z2]: { planned: 300, frac: zones[Z2].done / 300 } } }
                            : { unit: "Nos", rooms: { [Z1]: { planned: 3, frac: 0 }, [Z2]: { planned: 3, frac: 0 } } };
    } else if (u.startsWith("/api/siteprogress/hyatt-hotel/progress/runs/cancel") && opts && opts.method === "POST") {
      const b = JSON.parse(opts.body), z = zones[b.room], r = z.runs.find((x) => x.id === b.run_id);
      if (refuse) { status = 400; body = { detail: refuse }; }
      else { r.cancelled = b.cancelled; r.reason = b.reason || ""; z.done += b.cancelled ? -r.qty : r.qty; body = { svc: serviceBody(), zone: view(z) }; }
    } else if (u.startsWith("/api/siteprogress/hyatt-hotel/progress/runs") && opts && opts.method === "POST") {
      const b = JSON.parse(opts.body), z = zones[b.room];
      if (refuse) { status = 400; body = { detail: refuse }; }
      else {
        const qty = b.start != null ? Math.abs(b.start - b.end) : b.qty;
        z.runs.push({ id: "run" + (++n), from: b.from, to: b.to, start: b.start ?? null, end: b.end ?? null, qty, remarks: b.remarks || "", cancelled: false, reason: "" });
        z.done += qty; body = { svc: serviceBody(), zone: view(z) };
      }
    } else if (u.startsWith("/api/siteprogress/hyatt-hotel/progress/runs")) {
      if (runsFail) { status = 500; body = { detail: "boom" }; }
      else body = { unit: "Mtr", runs_ok: true, rooms: Object.fromEntries(Object.entries(zones).filter(([, z]) => z.runs.length).map(([k, z]) => [k, view(z)])) };
    } else if (u.startsWith("/api/siteprogress/hyatt-hotel/service/") || u.includes("/mark-rooms-done") || u.includes("/progress/rooms")) body = serviceBody();
    else if (u.startsWith("/api/siteprogress/hyatt-hotel/pnl/")) body = { done_value: 0, remaining_value: 0 };
    else if (u.startsWith("/api/siteprogress/hyatt-hotel/realistic/")) body = { items: [], shortages: 0 };
    return Promise.resolve({ ok: status < 400, status, json: async () => body, text: async () => JSON.stringify(body) });
  };
  w.eval(fs.readFileSync(path.join(__dirname, "siteprogress.js"), "utf8"));
  await sleep(0); await sleep(0);
  click(w, [...d.querySelectorAll(".spnav button")].find((b) => b.dataset.v === "siteprogress"));
  await sleep(60);
  return { w, d, posts, gets, zones };
}
async function openZones(ctx, code) {
  click(ctx.w, ctx.d.querySelector(`[data-roomsedit="${code}"]`));
  await sleep(60);
  return ctx.d.querySelector("#sp-modal");
}
const set = (w, el, v) => { el.value = String(v); el.dispatchEvent(new w.Event("input", { bubbles: true })); };
const q = (m, rid, sel) => m.querySelector(`[data-runpanel="${rid}"] ${sel}`);
const runPosts = (ctx) => ctx.posts.filter((p) => /\/progress\/runs$/.test(p.url));

async function main() {
  // ================= 1. the reported scenario: FA cable, drum readings, Add & next
  let ctx = await boot();
  let m = await openZones(ctx, CABLE);
  ok(!!m && m.classList.contains("sp-zones"), "Zones dialog opened for the FA cable");
  ok(ctx.gets.some((u) => u.includes("/progress/runs?service=ELV&item_code=G.1")), "a length item asks for its runs");
  const link = m.querySelector(`[data-runs="${Z1}"]`);
  ok(!!link && link.textContent === "+ Runs", `Zone 1 has a "+ Runs" link, got ${link && link.textContent}`);
  ok(m.querySelector(`[data-runpanel="${Z1}"]`).hidden, "the panel is closed until asked for");
  click(ctx.w, link);
  const panel = m.querySelector(`[data-runpanel="${Z1}"]`);
  ok(!panel.hidden && link.getAttribute("aria-expanded") === "true", "clicking Runs opens the panel");
  ok(/No runs yet/.test(panel.textContent), "an empty zone says there are no runs yet");
  const addBtn = () => q(m, Z1, '[data-runadd="next"]');
  ok(addBtn().disabled, "Add is off until From, To and readings are filled in");

  set(ctx.w, q(m, Z1, '[data-r="from"]'), "FACP-3");
  set(ctx.w, q(m, Z1, '[data-r="to"]'), "Corridor Loop A");
  set(ctx.w, q(m, Z1, '[data-r="start"]'), 1000);
  ok(q(m, Z1, "[data-runtotal]").textContent === "—", "one reading alone has no total");
  set(ctx.w, q(m, Z1, '[data-r="end"]'), 858);
  ok(q(m, Z1, "[data-runtotal]").textContent === "142 Mtr", `total = |1000 - 858| = 142 Mtr, got ${q(m, Z1, "[data-runtotal]").textContent}`);
  ok(!addBtn().disabled, "Add is on once From, To and both readings are valid");

  ctx.posts.length = 0;
  click(ctx.w, addBtn()); await sleep(60);
  let pr = runPosts(ctx);
  ok(pr.length === 1 && JSON.stringify(pr[0].body) === JSON.stringify({ service: "ELV", item_code: CABLE, room: Z1, from: "FACP-3", to: "Corridor Loop A", remarks: "", start: 1000, end: 858 }),
     `posted the run exactly, got ${JSON.stringify(pr[0] && pr[0].body)}`);
  ok(ctx.posts.every((p) => !p.url.includes("/mark-rooms-done") && !p.url.includes("/progress/rooms")), "a run does not go through Mark ticked as done");
  ok(/FACP-3 → Corridor Loop A/.test(panel.textContent) && /1000 → 858/.test(panel.textContent) && /142 Mtr/.test(panel.textContent), `the run is listed with its readings, got "${panel.textContent.replace(/\s+/g, " ").slice(0, 160)}"`);
  ok(link.textContent === "Runs (1)", `the link counts the runs, got ${link.textContent}`);
  const box = m.querySelector(`input[data-roomqty="${Z1}"]`);
  ok(box.value === "142", `the zone's number box shows the runs' quantity, got ${box.value}`);
  ok(box.readOnly, "the number box cannot be typed over once the zone has runs");
  ok(m.querySelector(`input[data-room="${Z1}"]`).checked === false, "adding a run leaves the zone unticked");
  ok(q(m, Z1, '[data-r="from"]').value === "Corridor Loop A", "Add & next: From of the next run = To of this one");
  ok(q(m, Z1, '[data-r="start"]').value === "858", "Add & next: Start of the next run = End of this one");
  ok(q(m, Z1, '[data-r="to"]').value === "" && q(m, Z1, '[data-r="end"]').value === "", "To and End are empty for the next run");

  // second run; "Add run" (not next) clears From too
  set(ctx.w, q(m, Z1, '[data-r="to"]'), "Rooms 301-306");
  set(ctx.w, q(m, Z1, '[data-r="end"]'), 762);
  set(ctx.w, q(m, Z1, '[data-r="remarks"]'), "in conduit");
  ctx.posts.length = 0;
  click(ctx.w, q(m, Z1, '[data-runadd="one"]')); await sleep(60);
  pr = runPosts(ctx);
  ok(pr.length === 1 && pr[0].body.from === "Corridor Loop A" && pr[0].body.start === 858 && pr[0].body.end === 762 && pr[0].body.remarks === "in conduit", `second run posted, got ${JSON.stringify(pr[0] && pr[0].body)}`);
  ok(box.value === "238", `box = 142 + 96 = 238, got ${box.value}`);
  ok(link.textContent === "Runs (2)", "two runs counted");
  ok(q(m, Z1, '[data-r="from"]').value === "" && q(m, Z1, '[data-r="start"]').value === "", "plain Add clears the form");
  ok(/in conduit/.test(panel.textContent), "remarks are shown under the run");
  ok(ctx.gets.some((u) => u.includes("/realistic/")), "the page behind is refreshed after a run");

  // ================= 2. direct qty (no drum mark)
  click(ctx.w, q(m, Z1, '[data-rmode="qty"]'));
  ok(!q(m, Z1, '[data-r="start"]') && !!q(m, Z1, '[data-r="qty"]'), "Direct qty replaces the two readings by one length box");
  set(ctx.w, q(m, Z1, '[data-r="from"]'), "Rack-1"); set(ctx.w, q(m, Z1, '[data-r="to"]'), "Rack-2"); set(ctx.w, q(m, Z1, '[data-r="qty"]'), "12.5");
  ok(q(m, Z1, "[data-runtotal]").textContent === "12.5 Mtr", "a typed length is its own total");
  ctx.posts.length = 0;
  click(ctx.w, q(m, Z1, '[data-runadd="one"]')); await sleep(60);
  pr = runPosts(ctx);
  ok(pr.length === 1 && pr[0].body.qty === 12.5 && !("start" in pr[0].body) && !("end" in pr[0].body), `typed length posts qty only, got ${JSON.stringify(pr[0] && pr[0].body)}`);
  ok(box.value === "250.5", `box = 238 + 12.5, got ${box.value}`);

  // ================= 3. cancel keeps the run on screen, struck through; restore brings it back
  const rows = () => [...panel.querySelectorAll(".sp-runrow")];
  ok(rows().length === 3, "three runs listed");
  const w2 = await boot({ prompts: ["wrong room"] });
  let m2 = await openZones(w2, CABLE);
  click(w2.w, m2.querySelector(`[data-runs="${Z1}"]`));
  set(w2.w, q(m2, Z1, '[data-r="from"]'), "A"); set(w2.w, q(m2, Z1, '[data-r="to"]'), "B"); set(w2.w, q(m2, Z1, '[data-r="qty"]') || q(m2, Z1, '[data-r="start"]'), 0);
  click(w2.w, q(m2, Z1, '[data-rmode="qty"]'));
  set(w2.w, q(m2, Z1, '[data-r="from"]'), "A"); set(w2.w, q(m2, Z1, '[data-r="to"]'), "B"); set(w2.w, q(m2, Z1, '[data-r="qty"]'), 40);
  click(w2.w, q(m2, Z1, '[data-runadd="one"]')); await sleep(60);
  const box2 = m2.querySelector(`input[data-roomqty="${Z1}"]`), p2 = m2.querySelector(`[data-runpanel="${Z1}"]`);
  ok(box2.value === "40", "setup: one 40 m run");
  w2.posts.length = 0;
  click(w2.w, p2.querySelector("[data-runtoggle]")); await sleep(60);
  ok(w2.gets.some((g) => g.startsWith("PROMPT")), "cancelling asks for a reason");
  const cp = w2.posts.find((p) => p.url.includes("/runs/cancel"));
  ok(cp && cp.body.cancelled === true && cp.body.reason === "wrong room" && cp.body.room === Z1 && cp.body.run_id === "run1", `cancel posted with the reason, got ${JSON.stringify(cp && cp.body)}`);
  ok(p2.querySelector(".sp-runrow.x") !== null && /Cancelled: wrong room/.test(p2.textContent), "the cancelled run stays listed, struck through, with its reason");
  ok(box2.value === "0", `the zone quantity goes back to 0, got ${box2.value}`);
  ok(box2.readOnly === false && m2.querySelector(`[data-runs="${Z1}"]`).textContent === "+ Runs", "with no active run the number box is editable again");
  ok(p2.querySelector("[data-runtoggle]").textContent === "Restore", "the button now says Restore");
  w2.posts.length = 0;
  click(w2.w, p2.querySelector("[data-runtoggle]")); await sleep(60);
  const rp = w2.posts.find((p) => p.url.includes("/runs/cancel"));
  ok(rp && rp.body.cancelled === false, "Restore posts cancelled: false");
  ok(box2.value === "40" && !p2.querySelector(".sp-runrow.x"), "restored: quantity back, no longer struck through");

  // a prompt dismissed with Cancel does nothing
  const w3 = await boot({ prompts: [null] });
  let m3 = await openZones(w3, CABLE);
  click(w3.w, m3.querySelector(`[data-runs="${Z1}"]`));
  click(w3.w, q(m3, Z1, '[data-rmode="qty"]'));
  set(w3.w, q(m3, Z1, '[data-r="from"]'), "A"); set(w3.w, q(m3, Z1, '[data-r="to"]'), "B"); set(w3.w, q(m3, Z1, '[data-r="qty"]'), 10);
  click(w3.w, q(m3, Z1, '[data-runadd="one"]')); await sleep(60);
  w3.posts.length = 0;
  click(w3.w, m3.querySelector("[data-runtoggle]")); await sleep(60);
  ok(!w3.posts.some((p) => p.url.includes("/runs/cancel")), "dismissing the reason box cancels nothing");

  // ================= 4. a refused run says why and changes nothing
  const w4 = await boot({ refuse: "this zone is planned at 300; 290 is recorded, so 40 more does not fit. Raise the plan first." });
  let m4 = await openZones(w4, CABLE);
  click(w4.w, m4.querySelector(`[data-runs="${Z1}"]`));
  click(w4.w, q(m4, Z1, '[data-rmode="qty"]'));
  set(w4.w, q(m4, Z1, '[data-r="from"]'), "A"); set(w4.w, q(m4, Z1, '[data-r="to"]'), "B"); set(w4.w, q(m4, Z1, '[data-r="qty"]'), 40);
  click(w4.w, q(m4, Z1, '[data-runadd="one"]')); await sleep(60);
  ok(/Raise the plan first/.test(w4.d.querySelector("#sp-toast, .toast, #toast")?.textContent || w4.d.body.textContent), "the server's reason is shown");
  ok(m4.querySelector(`input[data-roomqty="${Z1}"]`).value === "0" && /No runs yet/.test(m4.querySelector(`[data-runpanel="${Z1}"]`).textContent), "nothing changed on screen");
  ok(q(m4, Z1, '[data-r="qty"]').value === "40", "what was typed is still there to correct");

  // ================= 5. zones that already have runs open with them
  const w5 = await boot({ preset: { [Z2]: { done: 30, runs: [{ id: "x1", from: "P", to: "Q", start: null, end: null, qty: 20, remarks: "", cancelled: false, reason: "" }] } } });
  let m5 = await openZones(w5, CABLE);
  ok(m5.querySelector(`[data-runs="${Z2}"]`).textContent === "Runs (1)", "a zone with runs says so");
  ok(m5.querySelector(`input[data-roomqty="${Z2}"]`).readOnly, "its number box is read-only");
  click(w5.w, m5.querySelector(`[data-runs="${Z2}"]`));
  ok(/P → Q/.test(m5.querySelector(`[data-runpanel="${Z2}"]`).textContent), "its runs are listed");
  ok(/Recorded without run detail:\s*10 Mtr/.test(m5.querySelector(`[data-runpanel="${Z2}"]`).textContent.replace(/\s+/g, " ")), "the 10 m no run explains is said, not hidden");

  // ================= 6. other items and failures leave the dialog as it was
  const w6 = await boot();
  let m6 = await openZones(w6, DET);
  ok(!w6.gets.some((u) => u.includes("/progress/runs")), "a Nos item never asks for runs");
  ok(!m6.querySelector("[data-runs]") && !m6.querySelector(".sp-runpanel"), "a Nos item has no Runs links");
  const w7 = await boot({ runsFail: true });
  let m7 = await openZones(w7, CABLE);
  ok(!!m7 && !m7.querySelector("[data-runs]"), "if the runs cannot be fetched there are no Runs links");
  ok(!!m7.querySelector(`input[data-roomqty="${Z1}"]`) && !m7.querySelector(`input[data-roomqty="${Z1}"]`).readOnly, "and the zone's number box works exactly as before");

  console.log(`${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
}
main().catch((e) => { console.error("CRASH", e); process.exit(2); });
