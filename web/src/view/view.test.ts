import { describe, expect, it } from "vitest";
import type { HealthView, PortfolioView } from "../api/contracts";
import { duration, gapRow, healthCards, local, strip } from "./health";
import { latestAlerts, portfolioCard } from "./portfolio";

const HEALTH: HealthView = {
  at_utc: "2026-01-01T00:00:00Z",
  feed: { connected: true, seconds_since_last_frame: 0.24, open_gaps: 0, books_ready: 100, watched_markets: 100, trading_active: true },
  storage_state: "ok",
  ledger: { verified: true, reconciled_exact: true, records: 183, errors: 0 },
  backup: { configured: true, hours_since: 3, last_error: null },
  clock_status: "ok",
  clock_offset_ms: 12.4,
  gpu: "Made-up GPU",
  api_streams: 1,
  port_fallback: false,
  setup_reason: null,
  plugins: { momentum: "running" },
  detail: {},
};

function cards(h: HealthView) {
  return Object.fromEntries(healthCards(h).map((c) => [c.title, c]));
}

describe("health cards", () => {
  it("say setup mode, why and what to do, when the engine has no usable key (M1.9a)", () => {
    const setup = { ...HEALTH, setup_reason: "IKARS_KALSHI_KEY_ID and IKARS_KALSHI_PRIVATE_KEY_PATH must both be set" };
    expect(cards(setup)["Kalshi feed"]).toMatchObject({ level: "problem", state: "SETUP", big: "Setup mode" });
    expect(cards(setup)["Kalshi feed"]?.lines[0]).toContain("must both be set");
    expect(strip(setup, "open")[0]).toEqual({ label: "Setup mode: no Kalshi key", level: "problem" });
  });

  it("say OK when all is well", () => {
    const c = cards(HEALTH);
    expect(c["Kalshi feed"]).toMatchObject({ level: "ok", state: "LIVE", big: "Live" });
    expect(c["Kalshi feed"]?.lines).toContain("last frame 0.2 s ago");
    expect(c["Clock"]?.big).toBe("+12 ms vs Kalshi");
    expect(c["Ledger"]).toMatchObject({ level: "ok", big: "Reconciled exactly" });
    expect(c["Backups"]?.big).toBe("Last copy 3 h ago");
    expect(c["Plugins"]?.big).toBe("1 running · 0 paused");
    expect(c["Terminal"]?.lines[0]).toBe("on its fixed port");
    expect(c["GPU"]?.big).toBe("Made-up GPU");
  });

  it("raise problems and say what is not known yet", () => {
    const c = cards({
      ...HEALTH,
      feed: { connected: false, seconds_since_last_frame: 125, open_gaps: null, books_ready: null, watched_markets: null, trading_active: false },
      storage_state: "warn",
      ledger: { verified: false, reconciled_exact: null, records: null, errors: null },
      backup: { configured: true, hours_since: 30, last_error: "share unreachable" },
      clock_status: "unmeasured",
      clock_offset_ms: -3,
      gpu: null,
      port_fallback: true,
      plugins: { news: "paused" },
    });
    expect(c["Kalshi feed"]).toMatchObject({ level: "problem", big: "Disconnected" });
    expect(c["Kalshi feed"]?.lines).toEqual(["last frame 2 min ago", "trading is closed", "? open gaps"]);
    expect(c["Order books"]?.level).toBe("unknown");
    expect(c["Ledger"]?.level).toBe("problem");
    expect(c["Ledger"]?.lines[0]).toBe("chain broken: nothing is appended");
    expect(c["Backups"]?.level).toBe("problem");
    expect(c["Storage"]?.level).toBe("watch");
    expect(c["Clock"]).toMatchObject({ level: "unknown", big: "-3 ms vs Kalshi" });
    expect(c["Plugins"]?.level).toBe("watch");
    expect(c["Terminal"]?.level).toBe("problem");
    expect(c["GPU"]).toMatchObject({ level: "unknown", big: "None found" });
  });

  it("cover a fresh engine with nothing measured", () => {
    const c = cards({
      ...HEALTH,
      feed: { connected: null, seconds_since_last_frame: null, open_gaps: null, books_ready: null, watched_markets: null, trading_active: null },
      storage_state: null,
      ledger: { verified: null, reconciled_exact: false, records: 1, errors: 0 },
      backup: { configured: false, hours_since: null, last_error: null },
      clock_status: null,
      clock_offset_ms: null,
      plugins: {},
    });
    expect(c["Kalshi feed"]).toMatchObject({ level: "unknown", big: "Starting", state: "NOT YET" });
    expect(c["Ledger"]).toMatchObject({ level: "watch", big: "Not reconciled" });
    expect(c["Backups"]).toMatchObject({ level: "watch", big: "No share set" });
    expect(c["Storage"]?.level).toBe("unknown");
    expect(c["Clock"]?.big).toBe("Not measured");
    expect(c["Plugins"]?.lines[0]).toMatch(/M1.12/);
    const none = cards({ ...HEALTH, ledger: { verified: null, reconciled_exact: null, records: 0, errors: 0 }, backup: { configured: true, hours_since: null, last_error: null } });
    expect(none["Ledger"]).toMatchObject({ level: "unknown", big: "Not reconciled yet" });
    expect(none["Backups"]).toMatchObject({ level: "unknown", big: "None yet" });
  });
});

describe("the status strip", () => {
  it("leads with the feed, and with the reason when the engine is not reachable", () => {
    expect(strip(HEALTH, "open").map((i) => i.label)).toEqual([
      "Feed live · 0.2 s",
      "Books 100 ready",
      "Clock +12 ms",
      "Ledger reconciled",
      "Backup 3 h ago",
      "Storage ok",
      "GPU: Made-up GPU",
    ]);
    expect(strip(HEALTH, "signed-out")).toEqual([{ label: "Signed out: run ikars open", level: "problem" }]);
    expect(strip(null, "connecting")[0]?.label).toBe("Connecting to the engine");
    expect(strip(null, "open")[0]?.label).toBe("Engine connected");
    expect(strip(HEALTH, "retrying")[0]?.label).toBe("Engine reconnecting");
    const down = strip(
      { ...HEALTH, feed: { ...HEALTH.feed, connected: false, seconds_since_last_frame: null }, ledger: { ...HEALTH.ledger, reconciled_exact: false }, backup: { configured: true, hours_since: null, last_error: "x" }, storage_state: null, clock_offset_ms: null, gpu: null },
      "open",
    );
    expect(down.map((i) => i.label)).toEqual(["Feed down", "Books 100 ready", "Clock not measured", "Ledger not reconciled", "Backup failing", "Storage –", "GPU: none"]);
    const live = strip({ ...HEALTH, feed: { ...HEALTH.feed, seconds_since_last_frame: null }, backup: { configured: false, hours_since: null, last_error: null } }, "open");
    expect(live[0]?.label).toBe("Feed live · –");
    expect(live[4]?.label).toBe("Backups off");
  });

  it("colours each item as its Health card does (merge review 6)", () => {
    const stale = strip({ ...HEALTH, backup: { configured: true, hours_since: 40, last_error: null }, clock_status: "warn" }, "open");
    expect(stale[4]).toEqual({ label: "Backup 40 h ago", level: "problem" });
    expect(stale[2]?.level).toBe("watch");
    const starting = strip({ ...HEALTH, feed: { ...HEALTH.feed, connected: null } }, "open");
    expect(starting[0]).toEqual({ label: "Feed starting", level: "unknown" });
    expect(strip({ ...HEALTH, feed: { ...HEALTH.feed, connected: false } }, "open")[0]?.level).toBe("problem");
  });
});

describe("gaps and times", () => {
  it("say how long and when, in a given zone", () => {
    expect(duration(3_800)).toBe("3.8 s");
    expect(duration(41 * 60_000)).toBe("41 min");
    expect(duration(125 * 60_000)).toBe("2 h 5 min");
    expect(local("2026-10-26T09:12:00Z", "America/Denver")).toBe("Oct 26, 03:12");
    const row = gapRow(
      { id: 1, channel: "trade", reason: "reconnect (connection_lost)", start_utc: "2026-10-26T09:12:00Z", end_utc: "2026-10-26T09:12:04Z", recovery: "backfill", status: "done", detail: "71 trades fetched" },
      "America/Denver",
    );
    expect(row).toEqual({ when: "Oct 26, 03:12", what: "trade: reconnect (connection_lost)", length: "4.0 s", recovered: "done · 71 trades fetched" });
    expect(gapRow({ id: 2, channel: "book", reason: "r", start_utc: "2026-10-26T09:12:05Z", end_utc: "2026-10-26T09:12:00Z", recovery: "x", status: "open", detail: "" }).length).toBe("0.0 s");
  });
});

describe("the portfolio card", () => {
  const portfolio: PortfolioView = {
    cash_e6: 100_000_000,
    cost_e6: 3_036_000,
    unrealised_e6: 72_000,
    positions: [],
    exposure_by_category: { Weather: 1_518_000, "": 518_000, Demo: 1_518_000 },
    exposure_by_event: {},
  };

  it("formats the engine's numbers and draws exposure as shares of the cost held", () => {
    const card = portfolioCard(portfolio);
    expect(card).toMatchObject({ cash: "$100.00", unrealised: "+$0.07", unrealisedLevel: "gain", positions: "0" });
    expect(card.exposure).toEqual([
      { name: "Demo", amount: "$1.52", width: 50 },
      { name: "Weather", amount: "$1.52", width: 50 },
      { name: "Other", amount: "$0.52", width: 17.06 },
    ]);
  });

  it("says when it is not priced, and shows losses and flat", () => {
    expect(portfolioCard({ ...portfolio, unrealised_e6: null })).toMatchObject({ unrealised: "Not priced yet", unrealisedLevel: "unpriced" });
    expect(portfolioCard({ ...portfolio, unrealised_e6: -10_000 }).unrealisedLevel).toBe("loss");
    expect(portfolioCard({ ...portfolio, unrealised_e6: 0 }).unrealisedLevel).toBe("flat");
  });

  it("puts urgent alerts first, then the newest", () => {
    const a = (line: number, priority: string) => ({ line, priority, key: "k", title: "t", text: "x", at_local: "", outcome: "" });
    expect(latestAlerts([a(1, "high"), a(2, "urgent"), a(3, "low"), a(4, "high")]).map((x) => x.line)).toEqual([2, 4, 1]);
  });
});
