import { defineConfig } from "@playwright/test";

// Whole screens against the engine's real API server on made-up data (`demo-api`), in the
// installed Microsoft Edge (no browser download). Run `pnpm build` first: the server serves dist.
export default defineConfig({
  testDir: "e2e",
  testIgnore: "load.spec.ts", // the load test runs on its own: pnpm load
  globalSetup: "./e2e/demo-server.ts",
  timeout: 30_000,
  retries: 0,
  reporter: [["list"]],
  use: { channel: "msedge", trace: "off", screenshot: "off", video: "off" },
});
