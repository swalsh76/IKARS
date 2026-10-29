// Whole screens in Edge against the engine's real API server on made-up data (demo-api).

import { expect, test, type Page } from "@playwright/test";
import { base, signInUrl } from "./demo";

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
  await expect(page.getByTestId("active")).toHaveText("6 active"); // made-up, live from the stream
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
  // the grid's rows change in place as quotes stream (no React render: transactions)
  const firstRow = page.locator('.ag-row[row-index="0"]').first(); // drawn: the grid draws only what shows
  const before = await firstRow.textContent();
  await expect.poll(() => firstRow.textContent(), { timeout: 10_000 }).not.toBe(before);
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

test("hotkeys: the screens, the list, the theme and the Linked panel", async ({ page }) => {
  const refused = watchPolicy(page);
  await page.goto(await signInUrl());
  await expect(page.getByTestId("cash")).toBeVisible();
  await page.keyboard.press("Control+2");
  await expect(page).toHaveURL(/\/markets$/);
  await page.locator("body").click({ position: { x: 5, y: 5 } }); // out of the search box
  await page.keyboard.press("Shift+Slash"); // ?
  await expect(page.getByRole("dialog", { name: "Hotkeys" })).toContainText("Copy its order");
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog", { name: "Hotkeys" })).toHaveCount(0);
  const theme = () => page.evaluate(() => document.documentElement.getAttribute("data-theme"));
  await page.keyboard.press("Control+Shift+L");
  const first = await theme();
  expect(["light", "dark"]).toContain(first);
  await page.keyboard.press("Control+Shift+L");
  expect(await theme()).toBe(first === "light" ? "dark" : "light");
  await page.goto(`${base()}/market/KXDEMO-26JAN00-T0`);
  await expect(page.getByRole("region", { name: "Linked" })).toBeVisible();
  await page.keyboard.press("x");
  await expect(page.getByRole("region", { name: "Linked" })).toBeHidden();
  expect(refused).toEqual([]);
});


test("New window opens this screen in another window, already signed in (M1.8c)", async ({ page }) => {
  const refused = watchPolicy(page);
  await page.goto(await signInUrl());
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Ledger" }).click();
  const opened = page.waitForEvent("popup");
  await page.getByRole("button", { name: "+ New window" }).click();
  const other = await opened;
  await expect(other).toHaveURL(/\/ledger$/); // no sign-in code: the shared profile's cookie
  await expect(other.getByRole("region", { name: "Ledger status" })).toContainText("chain verified");
  // a popup window (no tabs or toolbar), not a tab, and not tied back to this window
  expect(await other.evaluate(() => [window.toolbar.visible, window.opener === null])).toEqual([false, true]);
  expect(refused).toEqual([]);
});

test("recommendations: the ranked list, the drill-down, an answer by hotkey and Copy order (M1.12c)", async ({ page, context }) => {
  const refused = watchPolicy(page);
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.goto(await signInUrl());
  const list = page.getByRole("table", { name: "Top recommendations" });
  await expect(list.getByRole("row")).toHaveCount(7); // the heading and six
  const perDay = (await list.locator("tbody tr td:nth-child(5)").allTextContents()).map((s) => Number(s.replace(/[%,+]/g, "")));
  expect(perDay).toEqual([...perDay].sort((x, y) => y - x)); // ranked by return per day
  await expect(page.getByRole("table", { name: "Scorecard" })).toContainText("momentum 0d6488");
  const back = page.getByRole("button", { name: "Back" });
  await expect(back).toBeDisabled(); // the screen the window opened on
  await list.getByRole("row").nth(1).locator("td").nth(4).click(); // a click anywhere on the row (M1.12d)
  await expect(page).toHaveURL(/\/recommendation\/momentum-/);
  await expect(page.getByTestId("headline")).toHaveText(/^Buy YES 10 @ \$0\.\d\d$/);
  await expect(page.getByRole("navigation", { name: "Breadcrumb" })).toHaveText(/Overview\s*›\s*Recommendation: \S/); // named
  await expect(page.getByRole("table", { name: "Checks" })).toContainText("not yet");
  await expect(page.getByRole("region", { name: "Provenance" })).toContainText("registered 0d648804a13a");
  await page.keyboard.press("t");
  await expect(page.getByTestId("note")).toHaveText("Marked taken.");
  await expect(page.getByRole("button", { name: "Mark taken" })).toHaveAttribute("aria-pressed", "true");
  await page.keyboard.press("t"); // the same answer again takes it back
  await expect(page.getByTestId("note")).toHaveText("Answer taken back.");
  await page.keyboard.press("c");
  await expect(page.getByTestId("note")).toHaveText(/^Copied: KXDEMO-\S+ YES 10 @ \d+¢$/);
  await back.click();
  await expect(page.getByTestId("active")).toHaveText("6 active");
  await expect(back).toBeDisabled();

  // on the Overview: Down highlights a row, C and T act on it, Enter opens it (M1.12d)
  const rows = list.locator("tbody tr");
  await page.keyboard.press("ArrowDown");
  await expect(rows.nth(0)).toHaveClass(/\bon\b/);
  await page.keyboard.press("c");
  await expect(page.getByTestId("list-note")).toHaveText(/^Copied: KXDEMO-\S+ YES 10 @ \d+¢$/);
  expect(await page.evaluate(() => navigator.clipboard.readText())).toMatch(/^KXDEMO-\S+ YES 10 @ \d+¢$/);
  await page.keyboard.press("t");
  await expect(page.getByTestId("list-note")).toHaveText(/: Marked taken\.$/);
  await expect(rows.nth(0)).toContainText("taken");
  await page.keyboard.press("t"); // taken back
  await expect(page.getByTestId("list-note")).toHaveText(/: Answer taken back\.$/);
  await page.keyboard.press("ArrowDown");
  await expect(rows.nth(1)).toHaveClass(/\bon\b/);
  const second = await rows.nth(1).getByRole("link").getAttribute("href");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(new RegExp(`${second}$`));
  expect(refused).toEqual([]);
});
