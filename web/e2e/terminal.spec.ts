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
    // a policy refusal, or any error a library logs (AG Grid's missing modules are errors in the console)
    if (m.type() === "error" || /Content Security Policy|Refused to/i.test(m.text())) refused.push(m.text());
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
  await page.goto(`${base()}/alerts`);
  await expect(page.getByRole("region", { name: "high alerts" })).toContainText("a made-up alert");
  await expect(page.getByRole("heading", { name: "high (1)" })).toBeVisible();
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

test("the status strip stays in view in a small window", async ({ page }) => {
  await page.setViewportSize({ width: 1000, height: 600 });
  await page.goto(await signInUrl());
  await expect(page.getByTestId("cash")).toBeVisible();
  await page.mouse.wheel(0, 2_000);
  await expect(page.getByLabel("Engine status")).toBeInViewport();
});

test("markets: the grid fills, a search narrows it and a row opens the market", async ({ page }) => {
  const refused = watchPolicy(page);
  await page.goto(await signInUrl());
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Markets" }).click();
  await expect(page.getByTestId("market-count")).toHaveText("30 found");
  await expect(page.locator(".ag-row").first()).toBeVisible();
  await page.locator("#market-category").selectOption("Weather");
  await expect(page).toHaveURL(/category=Weather/);
  await expect(page.getByTestId("market-count")).toHaveText("10 found");
  await expect(page.locator("#market-category option")).toHaveCount(4); // the others stay listed
  await page.locator("#market-category").selectOption("");
  await page.locator("#market-search").fill("market 12");
  await expect(page.getByTestId("market-count")).toHaveText("1 found");
  await expect(page).toHaveURL(/\/markets\?q=market(\+|%20)12$/);
  await page.getByRole("row", { name: /Made-up market 12/ }).click();
  await expect(page).toHaveURL(/\/market\/KXDEMO-26JAN01-T2$/);
  await expect(page.getByRole("heading", { name: "Made-up market 12" })).toBeVisible();
  expect(refused).toEqual([]);
});

test("market: the live quote, the chart, the book and the linked items", async ({ page }) => {
  const refused = watchPolicy(page);
  await page.goto(await signInUrl());
  await page.goto(`${base()}/market/KXDEMO-26JAN00-T0`); // a watched market (it has a book)
  await expect(page.getByRole("heading", { name: "Made-up market 0" })).toBeVisible();
  await expect(page.getByTestId("bid")).toHaveText(/\d+(\.\d)?¢/);
  await expect(page.getByTestId("price-chart").locator("canvas").first()).toBeVisible();
  await expect(page.getByRole("table", { name: "YES bids" })).toContainText("¢");
  const linked = page.getByRole("region", { name: "Linked" });
  await expect(linked.getByRole("link", { name: "Made-up market 1", exact: true })).toBeVisible();
  await expect(linked).toContainText("None.");
  await expect(page.getByRole("navigation", { name: "Breadcrumb" })).toHaveText(/Overview\s*›\s*Markets\s*›\s*KXDEMO-26JAN00-T0/);
  await page.goto(`${base()}/market/KXDEMO-26JAN02-T9`); // not watched: no book
  await expect(page.getByRole("region", { name: "Order book" })).toContainText("not watched");
  expect(refused).toEqual([]);
});

test("ledger: its state, your records paged, and a ticker filter", async ({ page }) => {
  const refused = watchPolicy(page);
  await page.goto(await signInUrl());
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Ledger" }).click();
  await expect(page.getByRole("region", { name: "Ledger status" })).toContainText("chain verified");
  const records = page.getByRole("table", { name: "Records" });
  await expect(records.locator("tbody tr")).toHaveCount(3);
  await expect(records).toContainText("deposit");
  await expect(records).toContainText("+$100.00");
  await expect(page.getByRole("button", { name: "Older" })).toBeDisabled();
  await page.locator("#ledger-ticker").fill("KXNONE");
  await page.locator("#ledger-ticker").press("Enter");
  await expect(page.getByRole("region", { name: "Ledger records" })).toContainText("No records.");
  expect(refused).toEqual([]);
});

test("the meter measures update to screen while markets stream", async ({ page }) => {
  await page.goto(await signInUrl());
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Health" }).click();
  await expect(page.getByRole("region", { name: "Update to screen" })).toContainText("No updates yet");
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Markets" }).click();
  await expect(page.getByTestId("market-count")).toHaveText("30 found");
  await page.waitForTimeout(3_000);
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Health" }).click();
  await expect(page.getByRole("region", { name: "Update to screen" })).toContainText(/p99 \d+ ms/);
});

