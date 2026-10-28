// What the Overview's recommendation list, the Recommendation screen and the scorecard show (M1.12c):
// formatted from the engine's contracts, never computed (the ranking and every amount are the engine's).

import type { MarkView, RecommendationView, ScoreCard } from "../api/contracts";
import { cents, contracts, dollars, e2, e6, percent } from "../money";
import { local } from "./health";

export type Level = "gain" | "loss" | "flat" | "unpriced";

export const ANSWERS = ["taken", "ignored", "dismissed"] as const;
export type Answer = (typeof ANSWERS)[number];

export interface RecommendationRow {
  id: string;
  market: string;
  why: string;
  action: string;
  size: string;
  pnl: string;
  pnlLevel: Level;
  perDay: string;
  strategy: string;
  expires: string;
  answer: string;
}

function level(value: number | null): Level {
  return value === null ? "unpriced" : value > 0 ? "gain" : value < 0 ? "loss" : "flat";
}

/** ``12 m 30 s``, ``2 h 5 m``, ``expired`` (to ``now``, in milliseconds). */
export function countdown(utc: string, now: number): string {
  const left = Math.floor((Date.parse(utc) - now) / 1_000);
  if (Number.isNaN(left)) return "";
  if (left <= 0) return "expired";
  const h = Math.floor(left / 3_600);
  const m = Math.floor((left % 3_600) / 60);
  const s = left % 60;
  return h > 0 ? `${h} h ${m} m` : m > 0 ? `${m} m ${s} s` : `${s} s`;
}

function shortRule(strategy: string, version: string): string {
  return `${strategy} ${version.slice(0, 6)}`;
}

/** The rule and the start of its registered version: ``momentum 0d6488``. */
export function ruleName(r: RecommendationView): string {
  return shortRule(r.strategy, r.version);
}

function buy(r: RecommendationView, size: boolean): string {
  const count = size ? ` ${contracts(e2(r.count_e2))}` : "";
  return `Buy ${r.side.toUpperCase()}${count} @ ${dollars(e6(r.entry_price_e6))}`;
}

/** ``Buy YES @ $0.46``. */
export function action(r: RecommendationView): string {
  return buy(r, false);
}

/** ``Buy YES 10 @ $0.46``. */
export function headline(r: RecommendationView): string {
  return buy(r, true);
}

export function recommendationRow(r: RecommendationView, now: number): RecommendationRow {
  return {
    id: r.id,
    market: r.title || r.ticker,
    why: r.summary,
    action: action(r),
    size: contracts(e2(r.count_e2)),
    pnl: r.pnl_e6 === null ? "—" : dollars(e6(r.pnl_e6), { signed: true }),
    pnlLevel: level(r.pnl_e6),
    perDay: r.per_day_e6 === null ? "—" : percent(r.per_day_e6, { signed: true }),
    strategy: ruleName(r),
    expires: r.state === "active" ? countdown(r.expires_utc, now) : r.state,
    answer: r.response ?? "",
  };
}

/** What Copy order puts on the clipboard, to place it on Kalshi by hand (recommend-only, D6). */
export function orderText(r: RecommendationView): string {
  return `${r.ticker} ${r.side.toUpperCase()} ${contracts(e2(r.count_e2))} @ ${cents(e6(r.entry_price_e6))}`;
}

export interface Figure {
  label: string;
  value: string;
  level?: Level;
}

/** The headline figures: P&L at the target, return per day, the worst case. */
export function figures(r: RecommendationView): Figure[] {
  return [
    { label: "P&L at target, after fees", value: r.pnl_e6 === null ? "—" : dollars(e6(r.pnl_e6), { signed: true }), level: level(r.pnl_e6) },
    { label: "Return per day", value: r.per_day_e6 === null ? "—" : percent(r.per_day_e6, { signed: true }), level: level(r.per_day_e6) },
    { label: "Laid out", value: r.outlay_e6 === null ? "—" : dollars(e6(r.outlay_e6)) },
    { label: "Worst case", value: r.outlay_e6 === null ? "—" : `-${dollars(e6(r.outlay_e6))}`, level: r.outlay_e6 === null ? "unpriced" : "loss" },
  ];
}

export interface CheckRow {
  horizon: string;
  when: string;
  bid: string;
  ask: string;
  score: string;
  level: Level;
  how: string;
}

function horizon(seconds: number): string {
  return seconds % 3_600 === 0 ? `${seconds / 3_600} h` : `${Math.round(seconds / 60)} min`;
}

/** The checks made so far, and those still to come. */
export function checkRows(r: RecommendationView, zone?: string): CheckRow[] {
  const made = new Map<number, MarkView>(r.marks.map((m) => [m.horizon_s, m]));
  const all = [...new Set([...r.horizons_s, ...made.keys()])].sort((a, b) => a - b);
  return all.map((h) => {
    const m = made.get(h);
    if (!m) return { horizon: horizon(h), when: "not yet", bid: "", ask: "", score: "", level: "unpriced", how: "" };
    return {
      horizon: horizon(h),
      when: local(m.at_utc, zone),
      bid: m.yes_bid_e6 === null ? "none" : cents(e6(m.yes_bid_e6)),
      ask: m.yes_ask_e6 === null ? "none" : cents(e6(m.yes_ask_e6)),
      score: m.score_e6 === null ? "—" : dollars(e6(m.score_e6), { signed: true }),
      level: level(m.score_e6),
      how: m.at_settlement ? "held to settlement" : m.score_e6 === null ? "no price then" : "sold at the bid",
    };
  });
}

/** An input as words: ``market:KX…`` is the market; the rest by kind. */
export function inputText(input: string): string {
  const at = input.indexOf(":");
  if (at < 0) return input;
  return `${input.slice(0, at)} · ${input.slice(at + 1)}`;
}

export interface ScoreRow {
  rule: string;
  horizon: string;
  scored: string;
  above: string;
  mean: string;
  meanLevel: Level;
  median: string;
}

/** One line per strategy version and check. */
export function scoreRows(card: ScoreCard): ScoreRow[] {
  return card.lines.map((l) => ({
    rule: shortRule(l.strategy, l.version),
    horizon: horizon(l.horizon_s),
    scored: `${l.scored} of ${l.checks}`,
    above: l.scored ? `${l.above_zero} (${Math.round((100 * l.above_zero) / l.scored)}%)` : "—",
    mean: l.mean_e6 === null ? "—" : dollars(e6(l.mean_e6), { signed: true }),
    meanLevel: level(l.mean_e6),
    median: l.median_e6 === null ? "—" : dollars(e6(l.median_e6), { signed: true }),
  }));
}
