import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, resolve } from "node:path";

import { describe, expect, it } from "vitest";

// vitest runs from the package root; `import.meta.url` is a served URL here,
// not a file one.
const ROOT = resolve(process.cwd());

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    if (name === "node_modules" || name.startsWith(".")) continue;
    const full = join(dir, name);
    if (statSync(full).isDirectory()) walk(full, out);
    else if (/\.tsx$/.test(full)) out.push(full);
  }
  return out;
}

/** A `className` on an `<Input>` or `<Textarea>`, however it is written. */
const FIELD = /<(?:Input|Textarea)\b[^>]*?className=(?:"([^"]*)"|\{cn\(\s*"([^"]*)")/gs;

/**
 * Anything that lands under 16px: the type scale's two sub-base steps, and the
 * side-chat container scale, which bottoms out at 11.5px in a narrow window.
 */
const BELOW_BASE = /\b(?:text-xs|text-eyebrow|side-chat-ui)\b/;

/**
 * iOS Safari zooms the page in when a field smaller than 16px takes focus, and
 * does not zoom back out when it loses it: the reader is left on a viewport
 * wider than the window, scrolling sideways through a layout that fitted a
 * moment earlier. `ui/input` and `ui/textarea` carry `text-base md:text-sm`
 * for exactly this reason — but a call site that overrides the size undoes it,
 * and three composers did.
 *
 * A source check rather than a rendered one because the property belongs to
 * every field there will ever be, not to the five that exist today.
 */
describe("the 16px floor at compact", () => {
  it("is carried by every field that overrides the primitive's size", () => {
    const offenders: string[] = [];

    for (const dir of ["app", "components"]) {
      for (const file of walk(join(ROOT, dir))) {
        const source = readFileSync(file, "utf8");
        for (const match of source.matchAll(FIELD)) {
          const classes = match[1] ?? match[2] ?? "";
          if (!BELOW_BASE.test(classes)) continue;
          if (classes.includes("max-md:text-base")) continue;
          offenders.push(`${file.slice(ROOT.length)}: ${classes}`);
        }
      }
    }

    expect(offenders).toEqual([]);
  });
});
