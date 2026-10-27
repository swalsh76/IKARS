import { describe, expect, it } from "vitest";
import type { AlertView } from "../api/contracts";
import { meterCard } from "./health";
import { alertGroups, ledgerLines } from "./records";

const alert = (line: number, priority: string, at = "2026-01-01T05:00:00-07:00"): AlertView => ({
  line,
  priority,
  key: "k",
  title: `t${line}`,
  text: "x",
  at_local: at,
  outcome: "shown",
});

describe("alert groups", () => {
  it("put urgent first, then high, then the rest by name, each newest first", () => {
    const groups = alertGroups([alert(1, "low"), alert(2, "urgent"), alert(3, "high"), alert(4, "urgent"), alert(5, "digest")], "America/Denver");
    expect(groups.map((g) => g.priority)).toEqual(["urgent", "high", "digest", "low"]);
    expect(groups[0]?.alerts.map((a) => a.line)).toEqual([4, 2]);
    expect(groups[0]?.alerts[0]).toMatchObject({ when: "Jan 1, 05:00", title: "t4" });
    expect(alertGroups([alert(9, "high", "not a time")])[0]?.alerts[0]?.when).toBe("not a time");
  });
});

describe("the ledger's state", () => {
  it("says it in words", () => {
    const status = { records: { fill: 3, deposit: 1 }, head_seq: 4, verified: true, reconciled_exact: true, tax_zone: "America/Denver" };
    expect(ledgerLines(status)).toEqual(["chain verified", "reconciles exactly with Kalshi", "4 records: 1 deposit · 3 fill", "tax zone America/Denver"]);
    expect(ledgerLines({ ...status, records: {}, head_seq: 0, verified: false, reconciled_exact: false })).toEqual([
      "chain broken: nothing is appended",
      "does not reconcile with Kalshi",
      "0 records",
      "tax zone America/Denver",
    ]);
    expect(ledgerLines({ ...status, verified: null, reconciled_exact: null }).slice(0, 2)).toEqual(["chain not checked yet", "not reconciled yet"]);
  });
});

describe("the meter's card", () => {
  it("is OK within the target, watch beyond it, and says when nothing is measured", () => {
    const none = { samples: 0, p50: null, p99: null, workP50: null, workP99: null, engineP50: null, engineP99: null };
    expect(meterCard(none)).toMatchObject({ level: "unknown", big: "No updates yet" });
    const ok = meterCard({ samples: 1000, p50: 12, p99: 30.4, workP50: 1, workP99: 2.25, engineP50: 110, engineP99: 190 });
    expect(ok).toMatchObject({ level: "ok", big: "p50 12 ms · p99 30 ms" });
    expect(ok.lines).toContain("the page's own work p99 2.3 ms");
    expect(ok.lines).toContain("from the engine receiving it: p50 110 ms · p99 190 ms (with the 10 Hz batching on both sides)");
    const slow = meterCard({ ...none, samples: 10, p50: 20, p99: 48 });
    expect(slow).toMatchObject({ level: "watch" });
    expect(slow.lines).toContain("the page's own work p99 –");
  });
});
