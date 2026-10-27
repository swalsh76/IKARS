/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The engine serves the built files (PLAN D3 as amended) under its strict Content Security Policy:
// no inline script, so no module-preload polyfill injected into the page.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
    modulePreload: { polyfill: false },
    sourcemap: false,
  },
  test: {
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
    environment: "node",
    coverage: {
      provider: "v8",
      include: ["src/**/*.ts", "src/**/*.tsx"],
      // thin screens are covered by Playwright (as decided for M1.8a); generated files by their check
      exclude: ["src/**/*.test.*", "src/screens/**", "src/main.tsx", "src/api/contracts.ts", "src/api/validators.*"],
      thresholds: { lines: 90, functions: 90, branches: 90, statements: 90 },
      reporter: ["text-summary", "text"],
    },
  },
});
