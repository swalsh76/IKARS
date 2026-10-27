// What the Ledger and Alerts screens show: formatted from the engine's contracts, never computed.

import type { AlertView, LedgerStatusView } from "../api/contracts";
import { local } from "./health";

export interface AlertGroup {
  priority: string;
  alerts: { line: number; when: string; title: string; text: string; outcome: string }[];
}

const ORDER = ["urgent", "high"];

/** Alerts by priority (urgent, then high, then the rest by name), each newest first. */
export function alertGroups(alerts: readonly AlertView[], zone?: string): AlertGroup[] {
  const groups = new Map<string, AlertView[]>();
  for (const alert of alerts) {
    const list = groups.get(alert.priority) ?? [];
    list.push(alert);
    groups.set(alert.priority, list);
  }
  const rank = (p: string) => (ORDER.includes(p) ? ORDER.indexOf(p) : ORDER.length);
  return [...groups.entries()]
    .sort(([a], [b]) => rank(a) - rank(b) || a.localeCompare(b))
    .map(([priority, list]) => ({
      priority,
      alerts: [...list]
        .sort((a, b) => b.line - a.line)
        .map((a) => ({ line: a.line, when: whenOf(a.at_local, zone), title: a.title, text: a.text, outcome: a.outcome })),
    }));
}

function whenOf(at: string, zone?: string): string {
  return Number.isNaN(Date.parse(at)) ? at : local(at, zone);
}

/** The ledger's state in words. */
export function ledgerLines(s: LedgerStatusView): string[] {
  const kinds = Object.entries(s.records)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([kind, n]) => `${n} ${kind}`)
    .join(" · ");
  return [
    s.verified === null ? "chain not checked yet" : s.verified ? "chain verified" : "chain broken: nothing is appended",
    s.reconciled_exact === null ? "not reconciled yet" : s.reconciled_exact ? "reconciles exactly with Kalshi" : "does not reconcile with Kalshi",
    `${s.head_seq} records${kinds ? `: ${kinds}` : ""}`,
    `tax zone ${s.tax_zone}`,
  ];
}
