// Settings and the first-run wizard (M1.8b-1), against the made-up-data server.

import { expect, test, type Page } from "@playwright/test";
import { startDemo, type Demo } from "./demo-server";

// Each group runs against a demo server of its own: the settings it saves (a skin, a port) must
// never meet another spec's (the hotkeys test switches the skin), which CI's parallel run showed.
async function signInTo(demo: Demo): Promise<string> {
  const answer = await fetch(`http://127.0.0.1:${demo.port}/v1/session/code`, {
    method: "POST",
    headers: { Authorization: `Bearer ${demo.token}` },
  });
  if (!answer.ok) throw new Error(`the demo server refused a sign-in code: ${answer.status}`);
  return ((await answer.json()) as { url: string }).url;
}

function watchPolicy(page: Page): string[] {
  const refused: string[] = [];
  page.on("console", (m) => {
    if (m.type() === "error" || /Content Security Policy|Refused to/i.test(m.text())) refused.push(m.text());
  });
  page.on("pageerror", (e) => refused.push(`page error: ${e.message}`));
  return refused;
}

test.describe("settings", () => {
  let demo: Demo;
  test.beforeAll(async () => {
    demo = await startDemo();
  });
  test.afterAll(() => demo.stop());

test("a skin is applied, saved and kept across a reload; a change notes its restart", async ({ page }) => {
  const refused = watchPolicy(page);
  await page.goto(await signInTo(demo));
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Settings" }).click();
  await expect(page.getByRole("region", { name: "Kalshi key" })).toContainText("IKARS_KALSHI_KEY_ID");
  await page.locator("#setting-skin").selectOption("midnight");
  const skin = () => page.evaluate(() => document.documentElement.getAttribute("data-theme"));
  expect(await skin()).toBe("midnight");
  await page.reload();
  await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible();
  await expect.poll(skin).toBe("midnight"); // loaded from the engine's settings
  await page.locator("#setting-port").fill("48000");
  await page.locator("#setting-quiet_start").fill("23:00");
  await expect(page.getByTestId("settings-note")).toContainText("2 changes not saved");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByTestId("settings-note")).toContainText("Saved");
  await expect(page.getByTestId("settings-note")).toContainText("the port applies after an engine restart");
  await page.locator("#setting-session_days").fill("3.5");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByTestId("settings-note")).toContainText("session days must be a whole number");
  expect(refused).toEqual([]);
});

test("the settings screen exists at its address", async ({ page }) => {
  await page.goto(await signInTo(demo));
  await page.goto(`http://127.0.0.1:${demo.port}/settings`);
  await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible();
});
});

test.describe("the first-run wizard", () => {
  let demo: Demo;
  test.beforeAll(async () => {
    demo = await startDemo(["--first-run"]);
  });
  test.afterAll(() => demo.stop());

  test("opens when there are no settings, saves the zone and quiet hours, and ends on the Overview", async ({ page }) => {
    const refused = watchPolicy(page);
    await page.goto(await signInTo(demo));
    await expect(page).toHaveURL(/\/welcome$/);
    await expect(page.getByRole("region", { name: "Kalshi key" })).toContainText("IKARS_KALSHI_KEY_ID");
    await page.getByRole("button", { name: "Continue" }).click();
    await expect(page.getByRole("region", { name: "Engine and feed" })).toContainText("connected");
    await page.getByRole("button", { name: "Continue" }).click();
    await page.getByRole("button", { name: "Skip for now" }).click();
    await page.locator("#wizard-quiet-start").fill("21:30");
    await page.getByRole("button", { name: "Save and continue" }).click();
    await expect(page.getByRole("region", { name: "Done" })).toContainText("quiet hours 21:30 to 07:00");
    await page.getByRole("button", { name: "Start at sign-in" }).click();
    await expect(page.getByRole("region", { name: "Done" })).toContainText("Start at sign-in installed");
    await page.getByRole("button", { name: "Open the Overview" }).click();
    await expect(page.getByRole("heading", { name: "Portfolio and exposure" })).toBeVisible();
    await page.reload(); // settings exist now: no wizard
    await expect(page).toHaveURL(/\/overview$/);
    expect(refused).toEqual([]);
  });
});

test.describe("layouts and the support bundle", () => {
  let demo: Demo;
  test.beforeAll(async () => {
    demo = await startDemo();
  });
  test.afterAll(() => demo.stop());

  test("the open windows are saved as a layout, chosen for the start and deleted", async ({ page, context }) => {
    const refused = watchPolicy(page);
    await page.goto(await signInTo(demo));
    const second = await context.newPage(); // the same browser profile, as Edge's app windows are
    await second.goto(`http://127.0.0.1:${demo.port}/ledger`);
    await expect(second.getByRole("heading", { name: "Ledger" }).first()).toBeVisible();
    await page.goto(`http://127.0.0.1:${demo.port}/settings`);
    const note = page.getByTestId("layout-note");
    await page.getByRole("button", { name: "Save the open windows" }).click();
    await expect(note).toContainText("Name the layout first");
    await page.getByLabel("Layout name").fill("Trading");
    await page.getByRole("button", { name: "Save the open windows" }).click();
    await expect(note).toContainText("Saved Trading (2 windows: Settings, Ledger)");
    await expect(page.getByRole("radio", { name: "Trading" })).toBeChecked();
    await page.getByRole("radio", { name: "One window" }).click(); // checked once the engine saves it
    await expect(note).toContainText("ikars open opens one window");
    await expect(page.getByRole("radio", { name: "One window" })).toBeChecked();
    await page.reload();
    await expect(page.getByRole("radio", { name: "One window" })).toBeChecked(); // kept by the engine
    await page.getByRole("button", { name: "Delete Trading" }).click();
    await expect(note).toContainText("Deleted Trading");
    await expect(page.getByRole("radio", { name: "Trading" })).toHaveCount(0);
    expect(refused).toEqual([]);
  });

  test("a support bundle is made from the Health screen", async ({ page }) => {
    const refused = watchPolicy(page);
    await page.goto(await signInTo(demo));
    await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Health" }).click();
    await page.getByRole("button", { name: "Make a support bundle" }).click();
    await expect(page.getByTestId("support-note")).toContainText("saved as support", { timeout: 30_000 });
    await expect(page.getByTestId("support-note")).toContainText("never sent");
    // the program's version (M1.9b): the demo server is a development copy, with no updates
    await expect(page.getByRole("region", { name: "Program" })).toContainText("Development copy");
    expect(refused).toEqual([]);
  });

  test("the IKARS Update window draws on its own, and says a development copy cannot update", async ({ page }) => {
    const refused = watchPolicy(page);
    await page.goto(await signInTo(demo));
    await page.goto(`http://127.0.0.1:${demo.port}/update?to=0.1.3`);
    const window = page.getByRole("main", { name: "IKARS Update" });
    await expect(window.getByRole("heading", { name: "IKARS cannot update here" })).toBeVisible();
    await expect(window).toContainText("development copy");
    await expect(page.getByRole("navigation", { name: "Main" })).toHaveCount(0); // no terminal frame
    expect(refused).toEqual([]);
  });
});
