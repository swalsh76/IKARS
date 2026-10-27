// Whole screens in Edge against the engine's real API server on made-up data (demo-api).

import { expect, test, type Page } from "@playwright/test";

const base = () => `http://127.0.0.1:${process.env.IKARS_DEMO_PORT ?? "0"}`;

/** A fresh one-time sign-in address, as ikars open gets it (the bearer token is demo-api's own). */
async function signInUrl(): Promise<string> {
  const answer = await fetch(`${base()}/v1/session/code`, {
    method: "POST",
    headers: { Authorization: `Bearer ${process.env.IKARS_DEMO_TOKEN ?? ""}` },
  });
  return ((await answer.json()) as { url: string }).url;
}

/** Every Content Security Policy refusal the page reports (there must be none). */
function watchPolicy(page: Page): string[] {
  const refused: string[] = [];
  page.on("console", (m) => {
    if (/Content Security Policy|Refused to/i.test(m.text())) refused.push(m.text());
  });
  page.on("pageerror", (e) => refused.push(`page error: ${e.message}`));
  return refused;
}

test("signs in with the one-time code and shows the Overview", async ({ page }) => {
  const refused = watchPolicy(page);
  await page.goto(await signInUrl());
  await expect(page.getByRole("heading", { name: "Portfolio and exposure" })).toBeVisible();
  expect(page.url()).not.toContain("code="); // removed from the address bar at once
  await expect(page.getByTestId("cash")).toHaveText("$300.00");
  await expect(page.getByTestId("positions")).toHaveText("0");
  await expect(page.getByRole("region", { name: "Top recommendations" })).toContainText("Arrives in M1.12");
  await expect(page.getByLabel("Engine status")).toContainText("Feed live");
  await expect(page.locator("#alerts").locator("..")).toContainText("made up");
  await expect(page).toHaveTitle("Overview · IKARS");
  expect(refused).toEqual([]);
});

test("drills into Health and back, with real addresses, and stays signed in on reload", async ({ page }) => {
  const refused = watchPolicy(page);
  await page.goto(await signInUrl());
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Health" }).click();
  await expect(page).toHaveURL(/\/health$/);
  await expect(page.getByRole("region", { name: "Kalshi feed" })).toContainText("LIVE");
  await expect(page.getByRole("region", { name: "Ledger" })).toContainText("Reconciled exactly");
  await expect(page.getByRole("heading", { name: "Gap log (last 7 days)" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Breadcrumb" })).toHaveText(/Overview\s*›\s*Health/);
  await page.goBack();
  await expect(page.getByRole("heading", { name: "Portfolio and exposure" })).toBeVisible();
  await page.goForward();
  await expect(page).toHaveURL(/\/health$/);
  await page.reload(); // the cookie, not the spent code, signs it in
  await expect(page.getByRole("region", { name: "Kalshi feed" })).toContainText("LIVE");
  await page.getByText("The engine's whole report").click();
  await expect(page.locator("pre")).toContainText('"demo": true');
  expect(refused).toEqual([]);
});

test("a screen address opens directly, and an unknown one says so", async ({ page }) => {
  await page.goto(await signInUrl());
  await page.goto(`${base()}/markets`);
  await expect(page.getByRole("region", { name: "Markets" })).toContainText("Arrives in M1.8a-web-2a");
  await page.goto(`${base()}/no/such/screen`);
  await expect(page.getByRole("heading", { name: "Not found" })).toBeVisible();
});

test("without a sign-in it says how to sign in", async ({ page }) => {
  await page.goto(base());
  await expect(page.getByLabel("Engine status")).toContainText("Signed out: run ikars open");
});

test("a spent code does not sign in", async ({ page, browser }) => {
  const url = await signInUrl();
  await page.goto(url);
  await expect(page.getByTestId("cash")).toBeVisible();
  const other = await browser.newContext(); // no cookie: only the code could sign it in
  const second = await other.newPage();
  await second.goto(url);
  await expect(second.getByLabel("Engine status")).toContainText("Signed out");
  await other.close();
});
