// The Overview's portfolio card from the engine's PortfolioView: formatted, never computed (the
// engine does the money; PLAN D1 as amended).

import type { AlertView, PortfolioView } from "../api/contracts";
import { dollars, e6, share } from "../money";

export interface Exposure {
  name: string;
  amount: string;
  width: number; // percent of the cost held, for the bar only
}

export interface PortfolioCard {
  cash: string;
  unrealised: string;
  unrealisedLevel: "gain" | "loss" | "flat" | "unpriced";
  positions: string;
  exposure: Exposure[];
}

export function portfolioCard(p: PortfolioView): PortfolioCard {
  const unrealised = p.unrealised_e6;
  const exposure = Object.entries(p.exposure_by_category)
    .sort(([an, a], [bn, b]) => (a === b ? an.localeCompare(bn) : a > b ? -1 : 1)) // compared, never subtracted
    .map(([name, amount]) => ({ name: name || "Other", amount: dollars(e6(amount)), width: share(e6(amount), e6(p.cost_e6)) }));
  return {
    cash: dollars(e6(p.cash_e6)),
    unrealised: unrealised === null ? "Not priced yet" : dollars(e6(unrealised), { signed: true }),
    unrealisedLevel: unrealised === null ? "unpriced" : unrealised > 0 ? "gain" : unrealised < 0 ? "loss" : "flat",
    positions: String(p.positions.length),
    exposure,
  };
}

/** The Overview's alert lines: the newest few, highest priority first. */
export function latestAlerts(alerts: readonly AlertView[], count = 3): AlertView[] {
  const rank: Record<string, number> = { urgent: 0, high: 1 };
  return [...alerts]
    .sort((a, b) => (rank[a.priority] ?? 2) - (rank[b.priority] ?? 2) || b.line - a.line)
    .slice(0, count);
}
