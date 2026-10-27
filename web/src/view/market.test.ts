import { describe, expect, it } from "vitest";
import type { Candle, LedgerRecordView, MarketView } from "../api/contracts";
import { drawingCents, e6 } from "../money";
import { bookRows, categories, chartPoints, emptyChart, marketRow, recordRow } from "./market";

const MARKET: MarketView = {
  ticker: "KXDEMO-26JAN01-T50",
  title: "Made-up market",
  event_ticker: "KXDEMO-26JAN01",
  series_ticker: "KXDEMO",
  category: "Demo",
  status: "active",
  close_time_utc: "2026-01-01T19:00:00Z",
  volume_24h_e2: 125_050,
  yes_bid_e6: 410_000,
  yes_ask_e6: 430_000,
  last_price_e6: null,
  watched: true,
};

function candle(minute: number, close: number | null): Candle {
  return {
    end_utc: `2026-01-01T12:${String(minute).padStart(2, "0")}:00Z`,
    open_e6: close,
    high_e6: close,
    low_e6: close,
    close_e6: close,
    bid_close_e6: null,
    ask_close_e6: null,
    volume_e2: 0,
  };
}

describe("market rows", () => {
  it("format the engine's market, and a streamed quote wins", () => {
    expect(marketRow(MARKET)).toMatchObject({ bid: "41¢", ask: "43¢", last: "–", volume: "1,250.5", watched: "yes", category: "Demo" });
    const quote = { ticker: MARKET.ticker, yes_bid_e6: 425_000, yes_ask_e6: null, last_price_e6: 420_000, received_ns: 1 };
    expect(marketRow(MARKET, quote)).toMatchObject({ bid: "42.5¢", ask: "43¢", last: "42¢" });
    expect(marketRow({ ...MARKET, category: "", close_time_utc: null, watched: false })).toMatchObject({ category: "Other", closes: "–", watched: "" });
  });

  it("list the categories once each, sorted", () => {
    expect(categories([MARKET, { ...MARKET, category: "" }, { ...MARKET, category: "Climate" }, MARKET])).toEqual(["Climate", "Demo", "Other"]);
  });
});

describe("the order book", () => {
  it("shows both sides' bids, best first, to a depth", () => {
    const level = (p: number, n: number) => ({ price_e6: p, count_e2: n });
    const rows = bookRows({ ticker: "T", ready: true, yes: [level(410_000, 1_250), level(400_000, 300)], no: [level(560_000, 300)] }, 1);
    expect(rows).toEqual({ yes: [{ price: "41¢", count: "12.5" }], no: [{ price: "56¢", count: "3" }] });
  });
});

describe("the chart", () => {
  it("draws traded closes only, in seconds and cents", () => {
    expect(chartPoints([candle(1, 410_000), candle(2, null), candle(3, 425_000)])).toEqual([
      { time: Date.UTC(2026, 0, 1, 12, 1) / 1000, value: 41 },
      { time: Date.UTC(2026, 0, 1, 12, 3) / 1000, value: 42.5 },
    ]);
    expect(drawingCents(e6(1))).toBe(0.0001);
  });

  it("says why it is empty", () => {
    expect(emptyChart([])).toMatch(/no price history/);
    expect(emptyChart([candle(1, null)])).toBe("Nothing traded in the last day.");
    expect(emptyChart([candle(1, 410_000)])).toBeNull();
  });
});

describe("ledger records", () => {
  it("show when, what and the cash, signed", () => {
    const record: LedgerRecordView = {
      seq: 7,
      kind: "fill",
      time_utc: "2026-01-01T12:00:00.000000000Z",
      time_local: "2026-01-01T05:00:00.000000000-07:00",
      source: "fill",
      ticker: MARKET.ticker,
      cash_e6: -1_518_000,
      fields: {},
      context: {},
      note: null,
    };
    expect(recordRow(record)).toEqual({ seq: 7, when: "2026-01-01 05:00", kind: "fill", cash: "-$1.52", note: "" });
    expect(recordRow({ ...record, cash_e6: null, note: "made up" })).toMatchObject({ cash: "", note: "made up" });
  });
});
