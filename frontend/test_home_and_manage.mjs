// jsdom test — real DOM interactions against the actual index.html/app.js/
// style.css files (no internal functions called directly except where the
// underlying pipeline (show/report rendering) is genuinely out of scope for
// this change and is spied on instead of re-mocked wholesale).
import { JSDOM } from "jsdom";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "..");

let pass = 0, fail = 0;
function ok(cond, msg) {
  if (cond) { pass++; }
  else { fail++; console.error("FAIL:", msg); }
}

function projectsFixture() {
  return [
    { project: "Hyatt Hotel", slug: "hyatt-hotel", runs: 3, latest_run: "run-h3" },
    { project: "Thoth Mall", slug: "thoth-mall", runs: 1, latest_run: "run-t1" },
  ];
}
function runsFixture() {
  return [
    { run_id: "run-h3", project_slug: "hyatt-hotel", filename: "google-sheet.xlsx",
      created: "2026-08-16T17:09:00+00:00", stats: { materials: 385 } },
    { run_id: "run-h2", project_slug: "hyatt-hotel", filename: "google-sheet.xlsx",
      created: "2026-08-16T15:26:00+00:00", stats: { materials: 385 } },
    { run_id: "run-h1", project_slug: "hyatt-hotel", filename: "google-sheet.xlsx",
      created: "2026-08-16T14:07:00+00:00", stats: { materials: 385 } },
    { run_id: "run-t1", project_slug: "thoth-mall", filename: "thoth_mall_stock.xlsx",
      created: "2026-08-16T14:44:00+00:00", stats: { materials: 244 } },
  ];
}

async function buildDom({ projects = [], runs = [] } = {}) {
  let html = fs.readFileSync(path.join(ROOT, "index.html"), "utf8");
  // strip the two <script src> tags — we eval the real source manually so we
  // control fetch/timing precisely, same code either way.
  html = html.replace(/<script src="app\.js"><\/script>\s*/, "")
             .replace(/<script src="siteprogress\.js"><\/script>\s*/, "");

  const dom = new JSDOM(html, {
    url: "https://example.test/",
    runScripts: "dangerously",
    pretendToBeVisual: true,
  });
  const { window } = dom;

  const calls = [];
  window.fetch = async (url) => {
    calls.push(url);
    const u = String(url);
    const json = (body, okFlag = true) => ({
      ok: okFlag, status: okFlag ? 200 : 404,
      json: async () => body, text: async () => JSON.stringify(body),
    });
    if (u === "/api/projects") return json(projects);
    if (u === "/api/runs") return json(runs);
    if (u.startsWith("/api/subcategories/")) return json({ all: [], by_service: {} });
    if (u.startsWith("/api/forecast/")) return json([]);
    if (u.startsWith("/api/run/")) {
      const id = u.split("/").pop();
      return json({
        meta: {
          project: "Hyatt Hotel", filename: "google-sheet.xlsx", source: "register",
          stats: { asof: "2026-08-16", materials: 385 }, lead_time: 14,
          issues: [], mapping: null, leadtime: {},
        },
        summary: { counts: {}, overdue_orders: 0, idle_lines: 0, services: [] },
      });
    }
    return json({}, false);
  };
  window.localStorage.clear();

  const appSrc = fs.readFileSync(path.join(ROOT, "app.js"), "utf8");
  window.eval(appSrc);
  // let the boot-time loadProjects()/restoreLast() promises settle
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));
  return { dom, window, calls };
}

function click(el) {
  el.dispatchEvent(new el.ownerDocument.defaultView.MouseEvent("click", { bubbles: true }));
}

// ------------------------------------------------------------ A. home toggle
async function testHomeToggleDefault() {
  const { window } = await buildDom();
  const d = window.document;
  ok(d.getElementById("paneUpload").hidden === false, "paneUpload visible by default");
  ok(d.getElementById("paneSheet").hidden === true, "paneSheet hidden by default");
  ok(d.getElementById("segUpload").classList.contains("on"), "segUpload starts active");
  ok(d.getElementById("recent").hidden === true, "recent list hidden with no projects");
}

async function testHomeToggleSwitch() {
  const { window } = await buildDom();
  const d = window.document;
  click(d.getElementById("segSheet"));
  ok(d.getElementById("paneSheet").hidden === false, "paneSheet visible after clicking segSheet");
  ok(d.getElementById("paneUpload").hidden === true, "paneUpload hidden after clicking segSheet");
  ok(d.getElementById("segSheet").classList.contains("on"), "segSheet becomes active");
  ok(d.getElementById("segUpload").classList.contains("on") === false, "segUpload loses active");
  ok(d.getElementById("segSheet").getAttribute("aria-selected") === "true", "aria-selected true on segSheet");
  click(d.getElementById("segUpload"));
  ok(d.getElementById("paneUpload").hidden === false, "paneUpload visible again after clicking segUpload");
  ok(d.getElementById("paneSheet").hidden === true, "paneSheet hidden again");
}

async function testPickHomeTriggersFileInput() {
  const { window } = await buildDom();
  const d = window.document;
  let clicked = false;
  d.getElementById("file").click = () => { clicked = true; };
  click(d.getElementById("pickHome"));
  ok(clicked, "clicking Choose file (home card) opens the hidden file input");
}

// ------------------------------------------------------- B. recent projects
async function testRecentProjectsRender() {
  const { window } = await buildDom({ projects: projectsFixture(), runs: runsFixture() });
  const d = window.document;
  ok(d.getElementById("recent").hidden === false, "recent list visible with projects present");
  const cards = [...d.querySelectorAll("#recentlist .rp")];
  ok(cards.length === 2, `expected 2 recent-project cards, got ${cards.length}`);
  const hyatt = cards.find((c) => c.dataset.run === "run-h3");
  ok(!!hyatt, "Hyatt card keyed to its latest_run");
  ok(hyatt.querySelector(".rp-name").textContent === "Hyatt Hotel", "Hyatt card shows project name");
  ok(/3 runs/.test(hyatt.querySelector(".rp-meta").textContent), "Hyatt card shows run count");
  ok(/385 materials/.test(hyatt.querySelector(".rp-meta").textContent), "Hyatt card shows material count from /api/runs");
  const thoth = cards.find((c) => c.dataset.run === "run-t1");
  ok(!!thoth && /1 run\b/.test(thoth.querySelector(".rp-meta").textContent), "Thoth card shows singular 'run'");
}

async function testRecentProjectClickOpensProject() {
  const { window } = await buildDom({ projects: projectsFixture(), runs: runsFixture() });
  const d = window.document;
  let calledWith = null;
  const original = window.switchProject;
  window.switchProject = (id) => { calledWith = id; };
  const thoth = [...d.querySelectorAll("#recentlist .rp")].find((c) => c.dataset.run === "run-t1");
  click(thoth);
  ok(calledWith === "run-t1", `clicking Thoth card called switchProject('run-t1'), got ${calledWith}`);
  window.switchProject = original;
}

async function testRecentProjectKeyboardOpens() {
  const { window } = await buildDom({ projects: projectsFixture(), runs: runsFixture() });
  const d = window.document;
  let calledWith = null;
  window.switchProject = (id) => { calledWith = id; };
  const hyatt = [...d.querySelectorAll("#recentlist .rp")].find((c) => c.dataset.run === "run-h3");
  hyatt.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  ok(calledWith === "run-h3", "Enter key on a focused card opens that project");
}

// -------------------------------------------------- C. header stays clean
async function testHeaderCleanOnHomeThenShownOnReport() {
  const { window } = await buildDom({ projects: projectsFixture(), runs: runsFixture() });
  const d = window.document;
  ok(d.getElementById("pick").hidden === true, "header Upload hidden on Home");
  ok(d.getElementById("sync").hidden === true, "header Sync hidden on Home");
  ok(d.getElementById("leadwrap").hidden === true, "header Lead time hidden on Home");

  await window.show("run-h3", {
    project: "Hyatt Hotel", filename: "google-sheet.xlsx", source: "register",
    stats: { asof: "2026-08-16", materials: 385 }, lead_time: 14,
    issues: [], mapping: null, leadtime: {},
  }, { counts: {}, overdue_orders: 0, idle_lines: 0, services: [] });

  ok(d.getElementById("pick").hidden === false, "header Upload shown once a report is open");
  ok(d.getElementById("sync").hidden === false, "header Sync shown once a report is open");
  ok(d.getElementById("leadwrap").hidden === false, "header Lead time shown once a report is open");
  ok(d.getElementById("report").hidden === false, "report visible after show()");
  ok(d.getElementById("drop").hidden === true, "drop hidden after show()");

  window.goHome();
  ok(d.getElementById("pick").hidden === true, "header Upload hidden again after goHome()");
  ok(d.getElementById("sync").hidden === true, "header Sync hidden again after goHome()");
  ok(d.getElementById("leadwrap").hidden === true, "header Lead time hidden again after goHome()");
  ok(d.getElementById("drop").hidden === false, "drop visible again after goHome()");
  ok(d.getElementById("report").hidden === true, "report hidden again after goHome()");
}

// -------------------------------------------------- D. manage uploads modal
async function testManageUploadsCollapsedAndBadge() {
  const { window } = await buildDom({ projects: projectsFixture(), runs: runsFixture() });
  const d = window.document;
  await window.openManager();

  const hyattGroup = d.querySelector('.mproj[data-slug="hyatt-hotel"]');
  ok(!!hyattGroup, "Hyatt group rendered");
  const topLevelRuns = [...hyattGroup.querySelectorAll(".mrun")].filter((el) => !el.closest(".molder"));
  ok(topLevelRuns.length === 1, `only the current upload shown before expanding, got ${topLevelRuns.length}`);
  const badge = hyattGroup.querySelector(".mrun-badge");
  ok(!!badge && badge.textContent === "Current", "current upload carries the Current badge");
  const showMore = hyattGroup.querySelector(".mshowmore");
  ok(!!showMore && /Show 2 older uploads/.test(showMore.textContent), "show-more offers the other 2 uploads");
  ok(hyattGroup.querySelector(".molder").hidden === true, "older uploads collapsed by default");

  click(showMore);
  ok(hyattGroup.querySelector(".molder").hidden === false, "older uploads expand on click");
  ok(hyattGroup.querySelectorAll(".molder .mrun").length === 2, "both older uploads render when expanded");
  ok(/Hide older uploads/.test(showMore.textContent), "show-more label flips to Hide");

  click(showMore);
  ok(hyattGroup.querySelector(".molder").hidden === true, "older uploads collapse again on second click");

  const thothGroup = d.querySelector('.mproj[data-slug="thoth-mall"]');
  ok(!!thothGroup && !thothGroup.querySelector(".mshowmore"),
    "single-upload project (Thoth) has no show-more toggle");
}

async function testManageUploadsDangerZone() {
  const { window } = await buildDom({ projects: projectsFixture(), runs: runsFixture() });
  const d = window.document;
  await window.openManager();

  ok(d.querySelectorAll(".mproj-hd button").length === 0,
    "Delete-project button no longer sits inline in the project header");
  const danger = d.querySelector(".mdanger");
  ok(!!danger, "danger zone footer rendered");
  const dangerBtns = [...danger.querySelectorAll("[data-project]")];
  ok(dangerBtns.length === 2, `danger zone lists one delete button per project, got ${dangerBtns.length}`);
  ok(dangerBtns.some((b) => b.dataset.project === "hyatt-hotel"), "Hyatt delete button present");
  ok(dangerBtns.some((b) => b.dataset.project === "thoth-mall"), "Thoth delete button present");
}

async function testManageUploadsConfirmFlow() {
  const { window } = await buildDom({ projects: projectsFixture(), runs: runsFixture() });
  const d = window.document;
  await window.openManager();

  const runDelBtn = d.querySelector('.mproj[data-slug="hyatt-hotel"] [data-run]');
  click(runDelBtn);
  ok(d.getElementById("confirm").hidden === false, "confirm dialog opens for a single-upload delete");
  ok(/Delete this upload\?/.test(d.getElementById("ctitle").textContent), "confirm title matches run deletion");
  window.closeConfirm();

  const projDelBtn = d.querySelector('.mdanger [data-project="thoth-mall"]');
  click(projDelBtn);
  ok(d.getElementById("confirm").hidden === false, "confirm dialog opens for a project delete");
  ok(/Delete "Thoth Mall"/.test(d.getElementById("ctitle").textContent), "confirm title names the right project");
  window.closeConfirm();
}

async function testManageUploadsEmptyState() {
  const { window } = await buildDom({ projects: [], runs: [] });
  const d = window.document;
  await window.openManager();
  ok(/Nothing uploaded yet/.test(d.getElementById("mlist").textContent), "empty state message shown");
  ok(!d.querySelector(".mdanger"), "no danger zone when there is nothing to delete");
}

// End-to-end: click a real recent-project card (nothing stubbed) and confirm
// the existing report pipeline (switchProject -> show -> load) still runs
// clean through it, with the header settling into the "report open" state.
async function testFullOpenFlowFromRecentCard() {
  const { window } = await buildDom({ projects: projectsFixture(), runs: runsFixture() });
  const d = window.document;
  const thoth = [...d.querySelectorAll("#recentlist .rp")].find((c) => c.dataset.run === "run-t1");
  click(thoth);
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));
  ok(d.getElementById("report").hidden === false, "report opens end-to-end from a recent-project click");
  ok(d.getElementById("drop").hidden === true, "drop hides end-to-end from a recent-project click");
  ok(d.getElementById("pick").hidden === false, "header controls unhide end-to-end too");
}

async function testCopyReflectsInventoryNotSkip() {
  const { window } = await buildDom();
  const d = window.document;
  const headText = d.querySelector(".home-head p").textContent;
  const cardText = d.querySelector(".homecard .ds").textContent;
  ok(/feed the Inventory tab/.test(headText), "home subtitle says Safety/PPE/Tools feed Inventory, not skipped");
  ok(!/Safety, PPE, tools and item master tabs are skipped/.test(headText), "stale 'skipped' claim removed from subtitle");
  ok(/go to the\s*Inventory tab/.test(cardText.replace(/\s+/g, " ")), "dropzone subtext says Safety/PPE/Tools go to Inventory");
  ok(/item master sheets are skipped automatically/.test(cardText), "only item master sheets are claimed skipped");
}

const tests = [
  testHomeToggleDefault, testHomeToggleSwitch, testPickHomeTriggersFileInput,
  testRecentProjectsRender, testRecentProjectClickOpensProject, testRecentProjectKeyboardOpens,
  testHeaderCleanOnHomeThenShownOnReport,
  testManageUploadsCollapsedAndBadge, testManageUploadsDangerZone,
  testManageUploadsConfirmFlow, testManageUploadsEmptyState,
  testFullOpenFlowFromRecentCard, testCopyReflectsInventoryNotSkip,
];

(async () => {
  for (const t of tests) {
    try { await t(); } catch (e) { fail++; console.error("THROW in", t.name, e); }
  }
  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
})();
