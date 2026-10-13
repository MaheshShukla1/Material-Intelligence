// The Annexure groups table lives in a scrolling dialog. The app-wide `th{position:sticky;top:var(--hdr)}` stuck its header 64px down and covered
// the first rows, so billing.css must neutralise it for .bl-gt, and the dialog must be the fixed-header/footer kind.
import css from "css";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
const __dirname = path.dirname(fileURLToPath(import.meta.url));
let pass = 0, fail = 0;
const ok = (c, m) => { if (c) pass++; else { fail++; console.error("FAIL:", m); } };
const rules = css.parse(fs.readFileSync(path.join(__dirname, "billing.css"), "utf8")).stylesheet.rules.filter((r) => r.type === "rule");
const decl = (sel, p) => { const hits = rules.filter((r) => r.selectors.map((s) => s.trim()).includes(sel)); const d = hits.flatMap((h) => h.declarations || []).filter((x) => x.property === p).pop(); return d ? d.value : null; };
ok(decl(".bl-gt th", "position") === "static", ".bl-gt th is not sticky inside the dialog");
const js = fs.readFileSync(path.join(__dirname, "billing.js"), "utf8");
const fn = js.slice(js.indexOf("function editGroups(x)"), js.indexOf("// ------------------------------------------------------- Work Orders"));
ok(/classList\.add\("bl-dlcard"\)/.test(fn), "the groups dialog keeps its header and Save in view (bl-dlcard)");
ok(/min\(1000px,96vw\)/.test(fn), "the groups dialog is wide enough for the description column");
console.log(`groups css: ${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
