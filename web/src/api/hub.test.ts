import { describe, expect, it, vi } from "vitest";
import type { AlertView, HealthView } from "./contracts";
import { ALERTS_PER_PRIORITY, FLUSH_MS, Hub, RETRY_MS, mergeAlerts, type HubDeps } from "./hub";

function alert(line: number, priority = "high"): AlertView {
  return { line, priority, key: `k${line}`, title: "t", text: "x", at_local: "2026-01-01T00:00:00-07:00", outcome: "shown" };
}

function rig(subscribe: HubDeps["subscribe"] = vi.fn().mockResolvedValue(undefined)) {
  const timeouts: (() => void)[] = [];
  const deps: HubDeps = {
    setInterval: vi.fn(() => 1),
    clearInterval: vi.fn(),
    setTimeout: (fn) => {
      timeouts.push(fn);
      return timeouts.length;
    },
    clearTimeout: vi.fn(),
    subscribe,
  };
  return { hub: new Hub(deps), deps, timeouts };
}

async function settle(): Promise<void> {
  for (let i = 0; i < 10; i += 1) await Promise.resolve();
}

describe("the hub's values", () => {
  it("hands changes to the screens only at a flush, the last value winning", () => {
    const { hub, deps } = rig();
    hub.start();
    hub.start();
    expect(deps.setInterval).toHaveBeenCalledTimes(1);
    expect(deps.setInterval).toHaveBeenCalledWith(expect.any(Function), FLUSH_MS);
    const heard = vi.fn();
    const stop = hub.listen("quote:KXA", heard);
    hub.handle({ event: "quote", data: { ticker: "KXA", yes_bid_e6: 1, yes_ask_e6: 2, last_price_e6: null, received_ns: 1 } });
    hub.handle({ event: "quote", data: { ticker: "KXA", yes_bid_e6: 3, yes_ask_e6: 4, last_price_e6: null, received_ns: 2 } });
    expect(hub.get("quote:KXA")).toBeUndefined();
    hub.flush();
    expect(heard).toHaveBeenCalledTimes(1);
    expect(hub.get("quote:KXA")?.yes_bid_e6).toBe(3);
    hub.flush(); // nothing new: nobody told
    expect(heard).toHaveBeenCalledTimes(1);
    stop();
    hub.stop();
    hub.stop();
    expect(deps.clearInterval).toHaveBeenCalledTimes(1);
  });

  it("keeps each kind of event in its place", () => {
    const { hub } = rig();
    const book = { ticker: "KXA", ready: true, yes: [], no: [] };
    const portfolio = { cash_e6: 1, cost_e6: 0, unrealised_e6: null, positions: [], exposure_by_category: {}, exposure_by_event: {} };
    hub.handle({ event: "book", data: book });
    hub.handle({ event: "portfolio", data: portfolio });
    hub.handle({ event: "health", data: { at_utc: "x" } as HealthView });
    hub.handle({ event: "alert", data: alert(1) });
    hub.handle({ event: "error", data: { error: "unavailable", detail: "busy" } });
    hub.connection("retrying");
    hub.flush();
    expect(hub.get("book:KXA")).toBe(book);
    expect(hub.get("portfolio")).toBe(portfolio);
    expect(hub.get("health")?.at_utc).toBe("x");
    expect(hub.get("alerts").map((a) => a.line)).toEqual([1]);
    expect(hub.get("connection")).toBe("retrying");
  });

  it("adds alerts to those waiting for the flush, not only to those shown", () => {
    const { hub } = rig();
    hub.addAlerts([alert(1)]);
    hub.addAlerts([alert(2)]);
    hub.flush();
    expect(hub.get("alerts").map((a) => a.line)).toEqual([2, 1]);
  });
});

describe("the meter's view of a flush", () => {
  it("is told the received times of the quotes handed on, and nothing for other values", () => {
    const flushed = vi.fn();
    const metered = new Hub({ ...rig().deps, flushed });
    metered.listen("quote:KXA", () => undefined);
    metered.listen("quote:KXB", () => undefined);
    metered.listen("book:KXA", () => undefined);
    metered.handle({ event: "quote", data: { ticker: "KXA", yes_bid_e6: 1, yes_ask_e6: 2, last_price_e6: null, received_ns: 5 } });
    metered.handle({ event: "quote", data: { ticker: "KXB", yes_bid_e6: 1, yes_ask_e6: 2, last_price_e6: null, received_ns: null } });
    metered.handle({ event: "book", data: { ticker: "KXA", ready: true, yes: [], no: [] } });
    metered.handle({ event: "quote", data: { ticker: "KXC", yes_bid_e6: 1, yes_ask_e6: 2, last_price_e6: null, received_ns: 9 } }); // shown nowhere
    metered.flush();
    expect(flushed).toHaveBeenCalledWith([5]);
  });
});

describe("mergeAlerts", () => {
  it("keeps each line once, newest first, at most the cap per priority", () => {
    const many = Array.from({ length: ALERTS_PER_PRIORITY + 5 }, (_, i) => alert(i + 1));
    const merged = mergeAlerts([alert(3, "urgent")], [...many, alert(3, "urgent"), alert(1000, "urgent")]);
    expect(merged.filter((a) => a.priority === "high")).toHaveLength(ALERTS_PER_PRIORITY);
    expect(merged[0]?.line).toBe(1000);
    expect(new Set(merged.map((a) => a.line)).size).toBe(merged.length);
  });
});

describe("subscriptions", () => {
  it("send the union only when it changes, and only to an open stream", async () => {
    const subscribe = vi.fn().mockResolvedValue(undefined);
    const { hub } = rig(subscribe);
    const first = hub.want(["KXB", "KXA"], ["KXA"]);
    await settle();
    expect(subscribe).not.toHaveBeenCalled(); // no stream yet
    hub.handle({ event: "opened", data: { stream_id: "s1", coalesce_ms: 100 } });
    await settle();
    expect(subscribe).toHaveBeenLastCalledWith("s1", ["KXA", "KXB"], ["KXA"]);
    const second = hub.want(["KXA"]); // already in the union
    await settle();
    expect(subscribe).toHaveBeenCalledTimes(1);
    first();
    first(); // releasing twice changes nothing
    await settle();
    expect(subscribe).toHaveBeenLastCalledWith("s1", ["KXA"], []);
    second();
    await settle();
    expect(subscribe).toHaveBeenLastCalledWith("s1", [], []);
  });

  it("sends one change at a time and catches up after it", async () => {
    let finish: () => void = () => undefined;
    const subscribe = vi.fn(() => new Promise<void>((resolve) => (finish = resolve)));
    const { hub } = rig(subscribe);
    hub.handle({ event: "opened", data: { stream_id: "s1", coalesce_ms: 100 } });
    hub.want(["KXA"]);
    await settle();
    hub.want(["KXB"]);
    await settle();
    expect(subscribe).toHaveBeenCalledTimes(1);
    finish();
    await settle();
    expect(subscribe).toHaveBeenLastCalledWith("s1", ["KXA", "KXB"], []);
  });

  it("sends to a stream that opened while a change was in flight", async () => {
    let finish: () => void = () => undefined;
    const subscribe = vi.fn((stream: string) => (stream === "s1" ? new Promise<void>((resolve) => (finish = resolve)) : Promise.resolve()));
    const { hub } = rig(subscribe);
    hub.handle({ event: "opened", data: { stream_id: "s1", coalesce_ms: 100 } });
    hub.want(["KXA"]);
    await settle();
    hub.connection("retrying"); // the engine restarted mid-change
    hub.handle({ event: "opened", data: { stream_id: "s2", coalesce_ms: 100 } });
    await settle();
    expect(subscribe).toHaveBeenCalledTimes(1); // one in flight
    finish();
    await settle();
    expect(subscribe).toHaveBeenLastCalledWith("s2", ["KXA"], []);
  });

  it("forgets a market's values when no screen shows it (merge review 7)", async () => {
    const { hub } = rig();
    const release = hub.want(["KXA"], ["KXA"]);
    hub.handle({ event: "quote", data: { ticker: "KXA", yes_bid_e6: 1, yes_ask_e6: 2, last_price_e6: null, received_ns: 1 } });
    hub.handle({ event: "book", data: { ticker: "KXA", ready: true, yes: [], no: [] } });
    hub.flush();
    expect(hub.get("quote:KXA")).toBeDefined();
    release();
    expect(hub.get("quote:KXA")).toBeUndefined();
    expect(hub.get("book:KXA")).toBeUndefined();
  });

  it("keeps one retry waiting however often a change fails, and stop cancels it (merge review 9)", async () => {
    const subscribe = vi.fn().mockRejectedValue(new Error("busy"));
    const { hub, deps, timeouts } = rig(subscribe);
    hub.handle({ event: "opened", data: { stream_id: "s1", coalesce_ms: 100 } });
    hub.want(["KXA"]);
    await settle();
    hub.want(["KXB"]);
    await settle();
    expect(timeouts).toHaveLength(1);
    hub.stop();
    expect(deps.clearTimeout).toHaveBeenCalledWith(1);
  });

  it("retries a failed change, and sends again to a new stream", async () => {
    const subscribe = vi.fn().mockRejectedValueOnce(new Error("busy")).mockResolvedValue(undefined);
    const { hub, timeouts } = rig(subscribe);
    hub.handle({ event: "opened", data: { stream_id: "s1", coalesce_ms: 100 } });
    hub.want(["KXA"]);
    await settle();
    expect(timeouts).toHaveLength(1);
    timeouts[0]?.();
    await settle();
    expect(subscribe).toHaveBeenCalledTimes(2);
    hub.connection("retrying");
    hub.handle({ event: "opened", data: { stream_id: "s2", coalesce_ms: 100 } });
    await settle();
    expect(subscribe).toHaveBeenLastCalledWith("s2", ["KXA"], []);
    expect(RETRY_MS).toBeGreaterThan(0);
  });
});
