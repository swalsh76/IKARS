// What the Markets and Market screens show, from the engine's contracts: formatted, never computed.

import type { BookView, Candle, LedgerRecordView, MarketView, QuoteUpdate } from "../api/contracts";
import { cents, contracts, dollars, drawingCents, e2, e6 } from "../money";
import { local } from "./health";

export interface MarketRow {
  ticker: string;
  title: string;
  category: string;
  status: string;
  bid: string;
  ask: string;
  last: string;
  volume: string;
  closes: string;
  watched: string;
}

const price = (value: number | null | undefined): string => (value === null || value === undefined ? "–" : cents(e6(value)));

/** One grid row: the engine's market with its latest streamed quote. A streamed quote wins for every
 * field, an empty side (null) included: the snapshot's older price is not one you can trade at
 * (M1.8a-web-2a merge review 1). */
export function marketRow(m: MarketView, quote?: QuoteUpdate): MarketRow {
  const now = quote ?? m;
  return {
    ticker: m.ticker,
    title: m.title,
    category: m.category || "Other",
    status: m.status,
    bid: price(now.yes_bid_e6),
    ask: price(now.yes_ask_e6),
    last: price(now.last_price_e6),
    volume: contracts(e2(m.volume_24h_e2)),
    closes: m.close_time_utc ? local(m.close_time_utc) : "–",
    watched: m.watched ? "yes" : "",
  };
}

const NO_QUOTE = {}; // the key for "no streamed quote yet"
const rows = new WeakMap<MarketView, WeakMap<object, MarketRow>>();

/** ``marketRow``, the same object again for the same market and quote objects: the grid redraws only
 * rows whose data changed (M1.8a-web-2a merge review 4). Held weakly, so old rows are let go. */
export function cachedRow(m: MarketView, quote?: QuoteUpdate): MarketRow {
  let byQuote = rows.get(m);
  if (!byQuote) rows.set(m, (byQuote = new WeakMap()));
  const key = quote ?? NO_QUOTE;
  let row = byQuote.get(key);
  if (!row) byQuote.set(key, (row = marketRow(m, quote)));
  return row;
}

/** The categories in a page of markets, sorted, for the filter. */
export function categories(markets: readonly MarketView[]): string[] {
  return [...new Set(markets.map((m) => m.category || "Other"))].sort((a, b) => a.localeCompare(b));
}

export interface BookRow {
  price: string;
  count: string;
}

/** Both sides' bids, best first, at most ``depth`` each (Kalshi's books are bids on both sides). */
export function bookRows(book: BookView, depth = 10): { yes: BookRow[]; no: BookRow[] } {
  const side = (levels: BookView["yes"]) =>
    levels.slice(0, depth).map((l) => ({ price: cents(e6(l.price_e6)), count: contracts(e2(l.count_e2)) }));
  return { yes: side(book.yes), no: side(book.no) };
}

export interface ChartPoint {
  time: number; // seconds since the epoch (UTC), as the chart wants it
  value: number; // cents, for drawing only
}

/** The traded closes to draw; a period with no trade is skipped (a gap, not a made-up price). */
/** A market is open for trading (Kalshi's REST and stream names). */
export function isOpen(status: string): boolean {
  return status === "active" || status === "open";
}

export function chartPoints(candles: readonly Candle[]): ChartPoint[] {
  return candles
    .filter((c): c is Candle & { close_e6: number } => c.close_e6 !== null)
    .map((c) => ({ time: Math.floor(Date.parse(c.end_utc) / 1000), value: drawingCents(e6(c.close_e6)) }));
}

/** Why a chart has nothing to draw, in words. */
export function emptyChart(candles: readonly Candle[]): string | null {
  if (candles.length === 0) return "Kalshi has no price history for this market in the last day.";
  if (chartPoints(candles).length === 0) return "Nothing traded in the last day.";
  return null;
}

export interface RecordRow {
  seq: number;
  when: string;
  kind: string;
  cash: string;
  note: string;
}

/** Your ledger records for a market (local only), with times as everywhere else (merge review 9). */
export function recordRow(r: LedgerRecordView, zone?: string): RecordRow {
  return {
    seq: r.seq,
    when: local(r.time_utc, zone),
    kind: r.kind,
    cash: r.cash_e6 === null ? "" : dollars(e6(r.cash_e6), { signed: true }),
    note: r.note ?? "",
  };
}
