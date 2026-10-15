#!/usr/bin/env node
/**
 * U8's lexicon CI gate: scans every front-end source file for the banned tone-lexicon terms
 * `present/lexicon.ts::BANNED` defines (never re-declared here — one list). Comments are
 * stripped first (a source file's own prose *about* the rule, like this file and
 * `present/lexicon.ts` itself, would otherwise trip its own gate) so only code -- string
 * literals, JSX text, template strings -- is scanned. `RANKING_SMELL` hits are reported as
 * warnings (flagged, not hard-banned, per the module's own comment) and never fail the build.
 *
 * Run: node scripts/lexicon_check.mjs   (wired as `npm run lexicon`)
 */
import { readFileSync } from "node:fs";
import { readdirSync, statSync } from "node:fs";
import { join, extname } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = fileURLToPath(new URL("../src", import.meta.url));

// Mirrors present/lexicon.ts exactly -- re-declared rather than imported since this script
// runs under plain Node, not through Vite/tsc, and importing a .ts module needs a loader.
const BANNED = [
  "waste", "wasted", "wasteful", "wasting",
  "burn rate", "burning money",
  "lazy", "sloppy", "incompetent",
];
const RANKING_SMELL = [
  "worst performer", "bottom of the", "ranked last", "least productive",
];

const SKIP_FILES = new Set([
  "present/lexicon.ts",   // defines the banned terms themselves
  "data/api-types.ts",    // generated, pure type shapes -- never hand-edited
]);
const SKIP_SUFFIXES = [".test.ts", ".test.tsx"]; // fixtures asserting the scanner catches these

function stripComments(text) {
  return text
    .replace(/\/\*[\s\S]*?\*\//g, " ") // block comments
    .replace(/\/\/[^\n]*/g, " ");      // line comments (fine here: no // inside a URL string
                                        // in this codebase's own source that would matter --
                                        // a false-negative there is not a lexicon-gate concern)
}

function scan(text) {
  const hay = text.toLowerCase();
  const hits = [];
  for (const term of BANNED) {
    let i = hay.indexOf(term);
    while (i !== -1) {
      hits.push({ term, kind: "banned", index: i });
      i = hay.indexOf(term, i + 1);
    }
  }
  for (const term of RANKING_SMELL) {
    const i = hay.indexOf(term);
    if (i !== -1) hits.push({ term, kind: "ranking", index: i });
  }
  return hits;
}

function lineOf(text, index) {
  return text.slice(0, index).split("\n").length;
}

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    if (name === "node_modules" || name === "dist") continue;
    const full = join(dir, name);
    const st = statSync(full);
    if (st.isDirectory()) {
      walk(full, out);
    } else if (extname(name) === ".ts" || extname(name) === ".tsx") {
      out.push(full);
    }
  }
  return out;
}

const files = walk(ROOT);
let failed = false;
const warnings = [];

for (const file of files) {
  const rel = file.slice(ROOT.length + 1);
  if (SKIP_FILES.has(rel) || SKIP_SUFFIXES.some((suf) => rel.endsWith(suf))) continue;
  const raw = readFileSync(file, "utf8");
  const code = stripComments(raw);
  for (const hit of scan(code)) {
    const line = lineOf(code, hit.index);
    if (hit.kind === "banned") {
      failed = true;
      console.error(`lexicon violation: "${hit.term}" in ${rel}:${line}`);
    } else {
      warnings.push(`ranking-smell: "${hit.term}" in ${rel}:${line}`);
    }
  }
}

for (const w of warnings) console.warn(w);

if (failed) {
  console.error(`\nlexicon gate FAILED (${files.length} files scanned).`);
  process.exit(1);
}
console.log(`lexicon gate passed (${files.length} files scanned, ${warnings.length} ranking-smell warning(s)).`);
