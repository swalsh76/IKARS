import { describe, expect, it } from "vitest";
import type { RecommendationView, ScoreCard } from "../api/contracts";
import { checkRows, countdown, figures, headline, inputText, orderText, recommendationRow, ruleName, scoreRows } from "./recommendations";

const AT = Date.parse("2026-01-01T00:00:00Z");

function rec(more: Partial<RecommendationView> = {}): RecommendationView {
  return {
    id: "momentum-KXA-1",
    ticker: "KXA",
    event_ticker: "KXDEMO",
    side: "yes",
    summary: "made up: up 5c in 5 min",
    at_utc: "2026-01-01T00:00:00Z",
    strategy: "momentum",
    version: "0d648804a13a",
    state: "active",
    superseded_by: null,
    entry_price_e6: 460_000,
    count_e2: 1_000,
    expires_utc: "2026-01-01T00:30:00Z",
    pnl_e6: 40_000,
    inputs: ["market:KXA", "trade:t-1"],
    marks: [],
    title: "Made-up market",
    source: "momentum",
    outlay_e6: 4_780_000,
    return_e6: 8_368,
    per_day_e6: 401_664,
    response: null,
    horizons_s: [1800, 3600],
    ...more,
  };
}

describe("a recommendation's row", () => {
  it("formats the engine's figures, never computing them", () => {
    expect(recommendationRow(rec(), AT + 90_000)).toEqual({
      id: "momentum-KXA-1",
      market: "Made-up market",
      why: "made up: up 5c in 5 min",
      action: "Buy YES @ $0.46",
      size: "10",
      pnl: "+$0.04",
      pnlLevel: "gain",
      perDay: "+40.2%",
      strategy: "momentum 0d6488",
      expires: "28 m 30 s",
      answer: "",
    });
    const bare = recommendationRow(rec({ title: "", pnl_e6: null, per_day_e6: null, response: "taken", state: "expired", side: "no" }), AT);
    expect([bare.market, bare.pnl, bare.pnlLevel, bare.perDay, bare.expires, bare.answer, bare.action]).toEqual([
      "KXA",
      "—",
      "unpriced",
      "—",
      "expired",
      "taken",
      "Buy NO @ $0.46",
    ]);
    expect(recommendationRow(rec({ pnl_e6: -360_000 }), AT).pnlLevel).toBe("loss");
    expect(recommendationRow(rec({ pnl_e6: 0 }), AT).pnlLevel).toBe("flat");
  });

  it("counts down to its expiry", () => {
    const end = "2026-01-01T02:05:00Z";
    expect(countdown(end, AT)).toBe("2 h 5 m");
    expect(countdown(end, Date.parse("2026-01-01T02:04:15Z"))).toBe("45 s");
    expect(countdown(end, Date.parse(end))).toBe("expired");
    expect(countdown("not a time", AT)).toBe("");
  });

  it("names its order, rule and headline", () => {
    expect(headline(rec())).toBe("Buy YES 10 @ $0.46");
    expect(orderText(rec())).toBe("KXA YES 10 @ 46¢");
    expect(ruleName(rec())).toBe("momentum 0d6488");
    expect(inputText("market:KXA")).toBe("market · KXA");
    expect(inputText("plain")).toBe("plain");
  });
});

describe("the drill-down", () => {
  it("shows the figures, the worst case as the whole outlay", () => {
    expect(figures(rec()).map((f) => [f.value, f.level])).toEqual([
      ["+$0.04", "gain"],
      ["+40.2%", "gain"],
      ["$4.78", undefined],
      ["-$4.78", "loss"],
    ]);
    expect(figures(rec({ pnl_e6: null, per_day_e6: null, outlay_e6: null })).map((f) => f.value)).toEqual(["—", "—", "—", "—"]);
  });

  it("lists the checks made and those to come", () => {
    const marks = [
      { horizon_s: 1800, at_utc: "2026-01-01T00:30:00Z", yes_bid_e6: 500_000, yes_ask_e6: 510_000, score_e6: 40_000, at_settlement: false },
      { horizon_s: 7200, at_utc: "2026-01-01T02:00:00Z", yes_bid_e6: null, yes_ask_e6: null, score_e6: 5_220_000, at_settlement: true },
    ];
    const rows = checkRows(rec({ marks }), "UTC");
    expect(rows.map((c) => [c.horizon, c.bid, c.ask, c.score, c.level, c.how])).toEqual([
      ["30 min", "50¢", "51¢", "+$0.04", "gain", "sold at the bid"],
      ["1 h", "", "", "", "unpriced", ""],
      ["2 h", "none", "none", "+$5.22", "gain", "held to settlement"],
    ]);
    expect(rows[1]?.when).toBe("not yet");
    const first = { horizon_s: 1800, at_utc: "2026-01-01T00:30:00Z", yes_bid_e6: null, yes_ask_e6: 510_000, score_e6: null, at_settlement: false };
    const unscored = checkRows(rec({ marks: [first] }));
    expect([unscored[0]?.score, unscored[0]?.how]).toEqual(["—", "no price then"]);
  });
});

describe("the scorecard", () => {
  it("shows each rule's checks after costs", () => {
    const card: ScoreCard = {
      since_utc: "2025-12-02T00:00:00Z",
      at_utc: "2026-01-01T00:00:00Z",
      lines: [
        { strategy: "momentum", version: "0d648804a13a", horizon_s: 1800, checks: 237, scored: 106, above_zero: 47, mean_e6: -42_000, median_e6: -28_000 },
        { strategy: "momentum", version: "0d648804a13a", horizon_s: 3600, checks: 32, scored: 0, above_zero: 0, mean_e6: null, median_e6: null },
      ],
    };
    expect(scoreRows(card)).toEqual([
      { rule: "momentum 0d6488", horizon: "30 min", scored: "106 of 237", above: "47 (44%)", mean: "-$0.04", meanLevel: "loss", median: "-$0.03" },
      { rule: "momentum 0d6488", horizon: "1 h", scored: "0 of 32", above: "—", mean: "—", meanLevel: "unpriced", median: "—" },
    ]);
  });
});
