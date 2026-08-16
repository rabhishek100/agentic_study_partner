#!/usr/bin/env node
/**
 * Enforces the Mugensei design system contract (docs/mugensei-design-system.md).
 *
 * The contract is only real if it cannot be violated silently. This scans the
 * component tree for the four things the system forbids — raw colour, derived
 * colour, off-scale spacing, and off-scale radii/icon sizes — and compares the
 * result against a committed baseline.
 *
 * The baseline exists because the migration is staged: ~190 violations were
 * already in the tree when the contract landed, and they are removed by the
 * stage that owns them, not all at once. The check fails on anything NEW, and
 * on any baseline entry that has been fixed but not removed from the baseline
 * (so the count only ever ratchets down).
 *
 *   npm run lint:tokens            check against the baseline
 *   npm run lint:tokens -- --write rewrite the baseline (only when reducing it)
 */

import { readFileSync, writeFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = fileURLToPath(new URL("..", import.meta.url));
const BASELINE = join(ROOT, "scripts", "design-tokens.baseline.json");
const SCAN_DIRS = ["app", "components", "hooks", "lib"];

/** Tailwind's named palettes. The system has roles instead. */
const PALETTE =
  "slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|" +
  "teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose";
const UTIL = "bg|text|border|ring|fill|stroke|from|to|via|shadow|outline|decoration|divide|accent";

const RULES = [
  {
    id: "palette-colour",
    // `bg-emerald-500` — a route picking its own colour.
    re: new RegExp(`\\b(?:${UTIL})-(?:${PALETTE})-\\d{2,3}(?:/\\d+)?\\b`, "g"),
    why: "named palette colour; use a semantic role",
  },
  {
    id: "raw-hex",
    re: /#[0-9a-fA-F]{6}\b/g,
    why: "raw hex; use a semantic role",
  },
  {
    id: "derived-colour",
    // `bg-card/30`, `border-primary/25` — an ad-hoc opacity variant.
    re: /\b(?:bg|text|border|ring|fill|stroke|divide|outline)-[a-z][a-z-]*\/\d+\b/g,
    why: "opacity variant; if the tone is needed, it is a role",
  },
  {
    id: "colour-mix",
    re: /color-mix\(/g,
    why: "derived colour; if the tone is needed, it is a role",
  },
];

/** 4px foundation: 4, 8, 12, 16, 24, 32, 48, 64 — expressed in Tailwind steps. */
const SPACING_STEPS = new Set(["1", "2", "3", "4", "6", "8", "12", "16"]);
const SPACING_RE = /\b(?:gap|gap-x|gap-y|p|px|py|pt|pr|pb|pl|m|mx|my|mt|mr|mb|ml|space-x|space-y)-(\d+(?:\.\d+)?)\b/g;

/** menu 6, control 8, panel 10, floating 14, pill full. */
const RADIUS_OK = new Set(["none", "sm", "md", "lg", "xl", "2xl", "full", ""]);
// No trailing \b: it cannot match after the `]` of an arbitrary value like
// `rounded-[7px]`, which is exactly the case this rule exists to catch.
const RADIUS_RE = /\brounded(?:-[a-z]+)?-(\[[^\]]+\]|[a-z0-9]+)/g;

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (name === "node_modules" || name.startsWith(".")) continue;
    if (statSync(full).isDirectory()) walk(full, out);
    else if (/\.tsx?$/.test(full)) out.push(full);
  }
  return out;
}

function scan() {
  const found = [];
  for (const dir of SCAN_DIRS) {
    let files;
    try {
      files = walk(join(ROOT, dir));
    } catch {
      continue;
    }
    for (const file of files) {
      const rel = relative(ROOT, file);
      const text = readFileSync(file, "utf8");
      text.split("\n").forEach((line, i) => {
        for (const rule of RULES) {
          rule.re.lastIndex = 0;
          for (const m of line.matchAll(rule.re)) {
            found.push({ file: rel, line: i + 1, rule: rule.id, match: m[0], why: rule.why });
          }
        }
        for (const m of line.matchAll(SPACING_RE)) {
          if (!SPACING_STEPS.has(m[1])) {
            found.push({
              file: rel, line: i + 1, rule: "off-scale-spacing", match: m[0],
              why: "not on the 4px foundation (4/8/12/16/24/32/48/64)",
            });
          }
        }
        for (const m of line.matchAll(RADIUS_RE)) {
          if (!RADIUS_OK.has(m[1])) {
            found.push({
              file: rel, line: i + 1, rule: "off-scale-radius", match: m[0],
              why: "not on the radius scale (6/8/10/14/full)",
            });
          }
        }
      });
    }
  }
  return found;
}

const key = (v) => `${v.file}:${v.rule}:${v.match}`;
const violations = scan();
const counts = new Map();
for (const v of violations) counts.set(key(v), (counts.get(key(v)) ?? 0) + 1);

if (process.argv.includes("--write")) {
  const next = Object.fromEntries([...counts.entries()].sort(([a], [b]) => a.localeCompare(b)));
  writeFileSync(BASELINE, JSON.stringify(next, null, 2) + "\n");
  console.log(`baseline written: ${counts.size} entries, ${violations.length} violations`);
  process.exit(0);
}

let baseline = {};
try {
  baseline = JSON.parse(readFileSync(BASELINE, "utf8"));
} catch {
  console.error(`No baseline at ${relative(ROOT, BASELINE)}. Run: npm run lint:tokens -- --write`);
  process.exit(1);
}

const added = [];
const reduced = [];
for (const [k, n] of counts) {
  const allowed = baseline[k] ?? 0;
  if (n > allowed) added.push({ k, n, allowed });
}
for (const [k, allowed] of Object.entries(baseline)) {
  const n = counts.get(k) ?? 0;
  if (n < allowed) reduced.push({ k, n, allowed });
}

if (added.length) {
  console.error("\nNew design-system violations:\n");
  for (const { k, n, allowed } of added) console.error(`  ${k}  (${allowed} allowed, ${n} found)`);
  console.error(
    "\nUse a semantic role from docs/mugensei-design-system.md, or if this is a\n" +
      "deliberate reduction elsewhere, run: npm run lint:tokens -- --write\n",
  );
  process.exit(1);
}

if (reduced.length) {
  console.error("\nBaseline is stale — these were fixed but are still listed:\n");
  for (const { k, n, allowed } of reduced) console.error(`  ${k}  (${allowed} listed, ${n} found)`);
  console.error("\nRatchet it down: npm run lint:tokens -- --write\n");
  process.exit(1);
}

console.log(`design tokens ok — ${violations.length} baselined violations, 0 new`);
