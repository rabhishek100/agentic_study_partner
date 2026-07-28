// Copy the pdf.js worker next to the app so it is served from our own origin.
//
// It must match the installed pdfjs-dist exactly — react-pdf raises a hard
// version-mismatch error otherwise — so it is copied at install time rather
// than vendored into git, where it would silently drift on the next upgrade.
// A CDN would work too, but the app serves private documents and should not
// hand page rendering to a third-party origin.

import { copyFileSync, existsSync, mkdirSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";

const require = createRequire(import.meta.url);

try {
  const pdfjs = dirname(require.resolve("pdfjs-dist/package.json"));
  const source = join(pdfjs, "build", "pdf.worker.min.mjs");
  if (!existsSync(source)) {
    throw new Error(`worker not found at ${source}`);
  }
  mkdirSync("public", { recursive: true });
  copyFileSync(source, join("public", "pdf.worker.min.mjs"));
  console.log("copied pdf.js worker into public/");
} catch (error) {
  // A missing worker breaks the viewer, not the build; say so loudly rather
  // than failing an unrelated install.
  console.warn(`could not copy the pdf.js worker: ${error.message}`);
}
