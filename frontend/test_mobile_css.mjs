// Real structural test for the mobile touch-target pass on siteprogress.css
// and style.css. jsdom can't reliably evaluate (hover:none)/(pointer:coarse)
// media features (there's no real touch/mouse capability in a Node process
// to detect), so this proves what CAN be proven mechanically instead:
//   1. Both stylesheets are still syntactically valid CSS after the edit.
//   2. Every base (non-media) rule for every touched selector is BYTE
//      IDENTICAL to the original file -- the actual proof that desktop is
//      untouched, not just an assumption from "I only added new blocks".
//   3. Each new touch media rule contains exactly the property values
//      intended, not typos or the wrong selector.
//   4. The .sp-brow grid-column coordination is self-consistent: the touch
//      rule's remove/arrow tracks are wide enough for the bumped .sp-fx
//      button size, in both the wide and narrow (max-width:720px) shapes.
import css from "css";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const UPLOADS = "/mnt/user-data/uploads";
const FRONTEND = path.resolve(__dirname, "../frontend");

let pass = 0, fail = 0;
function ok(cond, msg) {
  if (cond) pass++;
  else { fail++; console.error("FAIL:", msg); }
}

function parseOrThrow(filePath, label) {
  const text = fs.readFileSync(filePath, "utf8");
  try {
    const ast = css.parse(text, { silent: false });
    ok(true, `${label} parses as valid CSS`);
    return ast.stylesheet.rules;
  } catch (e) {
    ok(false, `${label} failed to parse: ${e.message}`);
    return [];
  }
}

function declMap(rule) {
  const m = {};
  for (const d of rule.declarations || []) {
    if (d.type === "declaration") m[d.property] = d.value;
  }
  return m;
}

function findRule(rules, selector) {
  return rules.find((r) => r.type === "rule" && r.selectors && r.selectors.includes(selector));
}

function findMediaRule(rules, mediaSubstr, selector) {
  const media = rules.filter((r) => r.type === "media" && r.media.replace(/\s+/g, "") === mediaSubstr.replace(/\s+/g, ""));
  for (const m of media) {
    const hit = findRule(m.rules, selector);
    if (hit) return hit;
  }
  return null;
}

// ---------------------------------------------------------- parse both versions
const newSpRules = parseOrThrow(path.join(FRONTEND, "siteprogress.css"), "frontend/siteprogress.css (new)");
const oldSpRules = parseOrThrow(path.join(UPLOADS, "siteprogress.css"), "uploads/siteprogress.css (old)");
const newStyleRules = parseOrThrow(path.join(FRONTEND, "style.css"), "frontend/style.css (new)");
const oldStyleRules = parseOrThrow(path.join(UPLOADS, "style.css"), "uploads/style.css (old)");

// ---------------------------------------------------- base rules byte-identical
// The exact selectors touched by this pass -- prove their BASE (non-media)
// declarations are untouched, so desktop rendering cannot have changed.
const touchedSelectors = [".sp-treeact", ".sp-ic", ".sp-fx", ".sp-addchip",
                          ".sp-linkchip", ".sp-suggpick", ".sp-brow"];
for (const sel of touchedSelectors) {
  const oldR = findRule(oldSpRules, sel);
  const newR = findRule(newSpRules, sel);
  ok(oldR && newR, `${sel}: base rule exists in both old and new`);
  if (oldR && newR) {
    ok(JSON.stringify(declMap(oldR)) === JSON.stringify(declMap(newR)),
      `${sel}: base (desktop) declarations byte-identical to before -- got ${JSON.stringify(declMap(newR))} vs old ${JSON.stringify(declMap(oldR))}`);
  }
}

// the pre-existing 720px .sp-brow rule must also be untouched
const old720 = findMediaRule(oldSpRules, "(max-width:720px)", ".sp-brow");
const new720 = findMediaRule(newSpRules, "(max-width:720px)", ".sp-brow");
ok(old720 && new720 && JSON.stringify(declMap(old720)) === JSON.stringify(declMap(new720)),
  "pre-existing @media(max-width:720px) .sp-brow rule is untouched");

// ------------------------------------------------------------- new touch rules
const treeact = findMediaRule(newSpRules, "(hover:none),(pointer:coarse)", ".sp-treeact");
ok(treeact && declMap(treeact).opacity === "1", ".sp-treeact touch rule sets opacity:1");

const ic = findMediaRule(newSpRules, "(hover:none),(pointer:coarse)", ".sp-ic");
ok(ic, ".sp-ic has a touch rule");
if (ic) {
  const d = declMap(ic);
  ok(parseInt(d["min-width"]) >= 34 && parseInt(d["min-height"]) >= 34,
    `.sp-ic touch min box >=34px, got ${d["min-width"]}/${d["min-height"]}`);
}

const fx = findMediaRule(newSpRules, "(hover:none),(pointer:coarse)", ".sp-fx");
ok(fx, ".sp-fx has a touch rule");
let fxTouchSize = 0;
if (fx) {
  const d = declMap(fx);
  fxTouchSize = parseInt(d.width);
  ok(fxTouchSize >= 38 && parseInt(d.height) === fxTouchSize,
    `.sp-fx touch size is a square >=38px, got ${d.width}/${d.height}`);
}

const addchip = findMediaRule(newSpRules, "(hover:none),(pointer:coarse)", ".sp-addchip");
ok(addchip, ".sp-addchip has a touch rule");
if (addchip) {
  const d = declMap(addchip);
  ok(parseInt(d.width) >= 38 && parseInt(d.height) >= 38,
    `.sp-addchip touch size >=38px, got ${d.width}/${d.height}`);
}

const linkchip = findMediaRule(newSpRules, "(hover:none),(pointer:coarse)", ".sp-linkchip");
const suggpick = findMediaRule(newSpRules, "(hover:none),(pointer:coarse)", ".sp-suggpick");
ok(linkchip && suggpick, ".sp-linkchip and .sp-suggpick both have a touch rule");

// -------------------------------------------------- grid-track coordination
// The whole point: the remove/arrow tracks the bumped .sp-fx sits in must
// be wide enough to hold it, in BOTH the wide-touch and narrow-touch shapes.
function lastTwoTracks(cols) {
  const parts = cols.trim().split(/\s+/);
  return parts.slice(-2).map((p) => parseInt(p));
}

const brownWideTouch = findMediaRule(newSpRules, "(hover:none),(pointer:coarse)", ".sp-brow");
ok(brownWideTouch, ".sp-brow has a wide-touch rule (non-width-gated)");
if (brownWideTouch && fxTouchSize) {
  const [remove, arrow] = lastTwoTracks(declMap(brownWideTouch)["grid-template-columns"]);
  ok(remove >= fxTouchSize && arrow >= fxTouchSize,
    `wide-touch .sp-brow remove/arrow tracks (${remove}/${arrow}px) fit the ${fxTouchSize}px .sp-fx button`);
}

const brownNarrowTouch = findMediaRule(newSpRules,
  "(max-width:720px) and (hover:none),(max-width:720px) and (pointer:coarse)", ".sp-brow");
ok(brownNarrowTouch, ".sp-brow has a narrow+touch rule");
if (brownNarrowTouch && fxTouchSize) {
  const [remove, arrow] = lastTwoTracks(declMap(brownNarrowTouch)["grid-template-columns"]);
  ok(remove >= fxTouchSize && arrow >= fxTouchSize,
    `narrow-touch .sp-brow remove/arrow tracks (${remove}/${arrow}px) fit the ${fxTouchSize}px .sp-fx button`);
  // grid-template-areas must NOT be redeclared here -- it must still
  // inherit from the 720px rule (stacked layout), proven by this rule
  // only setting grid-template-columns, nothing else.
  ok(Object.keys(declMap(brownNarrowTouch)).length === 1 &&
    "grid-template-columns" in declMap(brownNarrowTouch),
    "narrow-touch .sp-brow rule sets ONLY grid-template-columns (leaves grid-template-areas to the 720px rule)");
}

// -------------------------------------------------------------- source order
// cascade correctness: the wide-touch rule must appear BEFORE the 720px
// rule, and the narrow-touch rule must appear AFTER it, in the raw file --
// otherwise a narrow+touch phone (the common case) would get the WRONG
// grid-template-columns (the wide-touch one winning over the stacked one).
const rawCss = fs.readFileSync(path.join(FRONTEND, "siteprogress.css"), "utf8");
const idxWideTouch = rawCss.indexOf("grid-template-columns:1fr 80px 168px 90px 40px 40px");
const idx720 = rawCss.indexOf("@media(max-width:720px){.sp-brow{grid-template-columns:1fr 56px 26px 30px");
const idxNarrowTouch = rawCss.indexOf("grid-template-columns:1fr 56px 40px 40px");
ok(idxWideTouch > -1 && idx720 > -1 && idxNarrowTouch > -1, "all three .sp-brow rules found in source");
ok(idxWideTouch < idx720, "wide-touch .sp-brow rule appears BEFORE the 720px rule (so 720px wins when both match)");
ok(idx720 < idxNarrowTouch, "narrow-touch .sp-brow rule appears AFTER the 720px rule (so it wins on top of it)");

// ------------------------------------------------------------- style.css
const oldTablewrap = findRule(oldStyleRules, ".tablewrap");
const newTablewrap = findRule(newStyleRules, ".tablewrap");
ok(oldTablewrap && newTablewrap && JSON.stringify(declMap(oldTablewrap)) === JSON.stringify(declMap(newTablewrap)),
  ".tablewrap base (desktop) rule is untouched");

const tablewrapMobile = findMediaRule(newStyleRules, "(max-width:760px)", ".tablewrap");
ok(tablewrapMobile && declMap(tablewrapMobile)["overflow-x"] === "auto",
  ".tablewrap gets overflow-x:auto inside the existing 760px breakpoint");
const tableMinWidth = findMediaRule(newStyleRules, "(max-width:760px)", ".tablewrap table");
ok(tableMinWidth && parseInt(declMap(tableMinWidth)["min-width"]) >= 500,
  ".tablewrap table gets a real min-width inside the same breakpoint, so it scrolls instead of crushing");

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
