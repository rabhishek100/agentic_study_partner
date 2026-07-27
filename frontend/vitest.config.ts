import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

/**
 * No `@vitejs/plugin-react`: it pulls a Babel 8 peer that conflicts with the
 * Babel 7 the shadcn CLI depends on. Tests do not need Fast Refresh, and
 * Vitest's own transform already compiles TSX with the automatic JSX runtime.
 */
export default defineConfig({
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./", import.meta.url)),
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./tests/setup.ts"],
    include: ["tests/**/*.test.ts", "tests/**/*.test.tsx"],
  },
});
