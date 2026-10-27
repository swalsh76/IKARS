import { defineConfig } from "@playwright/test";

// The load test (PLAN D20 J): 100 made-up markets moving at 10 Hz through the engine's real API
// server, the Markets screen open in a visible Edge window, and the update-to-screen meter read from
// the Health screen. Run it at the console (`pnpm load`), not over Remote Desktop: RDP draws in
// software and slows every frame. Target: p99 <= 33 ms.
process.env.IKARS_DEMO_MARKETS = "100";
process.env.IKARS_DEMO_HZ = "10";

export default defineConfig({
  testDir: "e2e",
  testMatch: "load.spec.ts",
  globalSetup: "./e2e/demo-server.ts",
  timeout: 120_000,
  retries: 0,
  reporter: [["list"]],
  // IKARS_LOAD_HEADLESS=1 checks the test itself runs (not a measurement: no real drawing)
  use: { channel: "msedge", headless: process.env.IKARS_LOAD_HEADLESS === "1", viewport: { width: 1600, height: 1000 }, trace: "off", screenshot: "off", video: "off" },
});
