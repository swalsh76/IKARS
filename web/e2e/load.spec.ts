// The load test (run with `pnpm load`, at the console): see playwright.load.config.ts.

import { expect, test } from "@playwright/test";

const STREAM_S = 40;

test("update to screen stays within 33 ms (p99) while 100 markets stream at 10 Hz", async ({ page }) => {
  const base = `http://127.0.0.1:${process.env.IKARS_DEMO_PORT ?? "0"}`;
  const answer = await fetch(`${base}/v1/session/code`, {
    method: "POST",
    headers: { Authorization: `Bearer ${process.env.IKARS_DEMO_TOKEN ?? ""}` },
  });
  await page.goto(((await answer.json()) as { url: string }).url);
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Markets" }).click();
  await expect(page.getByTestId("market-count")).toHaveText("100 found");
  await page.waitForTimeout(STREAM_S * 1000);
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Health" }).click();
  const card = page.getByRole("region", { name: "Update to screen" });
  await expect(card).toContainText("p99");
  const text = (await card.textContent()) ?? "";
  console.log(`\nUpdate to screen: ${text.replace(/\s+/g, " ").trim()}\n`);
  const p99 = Number(/p99 (\d+) ms/.exec(text)?.[1]);
  const samples = Number(/last (\d+) screen updates/.exec(text)?.[1]);
  expect(samples).toBeGreaterThan(300); // about 10 screen updates a second: enough for a p99
  expect(p99).toBeLessThanOrEqual(33);
});
