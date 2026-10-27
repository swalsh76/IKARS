// The load test (run with `pnpm load`, at the console): see playwright.load.config.ts.

import { expect, test } from "@playwright/test";
import { signInUrl } from "./demo";

const STREAM_S = 40;

test("update to screen stays within 33 ms (p99) while 100 markets stream at 10 Hz", async ({ page }) => {
  await page.goto(await signInUrl());
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Markets" }).click();
  await expect(page.getByTestId("market-count")).toHaveText("100 found");
  await page.waitForTimeout(STREAM_S * 1000);
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Health" }).click();
  const card = page.getByRole("region", { name: "Update to screen" });
  await expect(card).toContainText("p99");
  console.log(`\nUpdate to screen: ${((await card.textContent()) ?? "").replace(/\s+/g, " ").trim()}\n`);
  const samples = Number(/last (\d+) screen updates/.exec((await card.textContent()) ?? "")?.[1]);
  expect(samples).toBeGreaterThan(300); // about 10 screen updates a second: enough for a p99
  // the card's own level, which compares the exact p99 with the target (not the rounded text)
  await expect(card.locator(".state")).toHaveText("OK");
});
