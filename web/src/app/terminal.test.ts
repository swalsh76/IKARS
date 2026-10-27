import { describe, expect, it, vi } from "vitest";
import { ApiFailure } from "../api/client";
import type { Navigator } from "../nav/route";
import { startTerminal } from "./terminal";

const navigator: Navigator = { current: () => ({ screen: "overview" }), go: vi.fn(), replace: vi.fn(), listen: () => () => undefined };
const quiet = {
  fetch: vi.fn(() => new Promise<Response>(() => undefined)) as unknown as typeof fetch,
  setTimeout: vi.fn(() => 0),
  clearTimeout: vi.fn(),
  random: () => 0.5,
};
const hubTimers = { setInterval: vi.fn(() => 0), clearInterval: vi.fn(), setTimeout: vi.fn(() => 0), clearTimeout: vi.fn() };

describe("starting the terminal", () => {
  it("signs in, loads the first values, then streams after the last alert loaded", async () => {
    const order: string[] = [];
    const answers: Record<string, unknown> = {
      "/v1/health": { at_utc: "x" },
      "/v1/portfolio": { cash_e6: 1 },
      "/v1/alerts?limit=200": { items: [{ line: 4, priority: "high" }, { line: 9, priority: "high" }] },
    };
    const get = vi.fn(async (path: string) => {
      order.push(path);
      return answers[path];
    });
    const terminal = await startTerminal({
      signIn: async () => {
        order.push("sign-in");
        return true;
      },
      get: get as never,
      hub: hubTimers,
      stream: quiet,
      navigator,
    });
    expect(order[0]).toBe("sign-in");
    expect(terminal.hub.get("health")).toEqual({ at_utc: "x" });
    expect(terminal.hub.get("alerts").map((a) => a.line)).toEqual([9, 4]);
    expect(terminal.stream.lastAlert).toBe(9);
    expect(quiet.fetch).toHaveBeenCalledWith("/v1/stream?after_alert=9", expect.anything());
    terminal.stop();
  });

  it("says signed out, or retrying, and still opens the stream", async () => {
    for (const [kind, state] of [
      ["signed-out", "signed-out"],
      ["unreachable", "retrying"],
    ] as const) {
      const terminal = await startTerminal({
        signIn: async () => {
          throw new ApiFailure(kind, "no");
        },
        hub: hubTimers,
        stream: quiet,
        navigator,
      });
      expect(terminal.hub.get("connection")).toBe(state); // shown at once; the stream's own state follows
      terminal.stop();
    }
  });

  it("loads the first values once a stream opens when the first try failed (merge review 4)", async () => {
    let up = false;
    const get = vi.fn(async (path: string) => {
      if (!up) throw new ApiFailure("unreachable", "starting");
      return path.startsWith("/v1/alerts") ? { items: [{ line: 3, priority: "high" }] } : { at_utc: "y" };
    });
    const terminal = await startTerminal({ signIn: async () => false, get: get as never, hub: hubTimers, stream: quiet, navigator });
    expect(terminal.hub.get("alerts")).toEqual([]);
    up = true;
    // a stream opens, as the stream reader reports it
    (terminal.stream as unknown as { handlers: { onMessage: (m: unknown) => void } }).handlers.onMessage({
      event: "opened",
      data: { stream_id: "s", coalesce_ms: 100 },
    });
    const asked = get.mock.calls.length;
    (terminal.stream as unknown as { handlers: { onMessage: (m: unknown) => void } }).handlers.onMessage({
      event: "opened",
      data: { stream_id: "s", coalesce_ms: 100 },
    }); // a second opening while the first load runs: not a second load
    expect(get.mock.calls.length).toBe(asked);
    await new Promise((r) => setTimeout(r, 0));
    terminal.hub.flush();
    expect(terminal.hub.get("alerts").map((a) => a.line)).toEqual([3]);
    expect(terminal.stream.lastAlert).toBe(3);
    terminal.stop();
  });

  it("lets a programming error through", async () => {
    await expect(
      startTerminal({
        signIn: async () => {
          throw new TypeError("a bug");
        },
        hub: hubTimers,
        stream: quiet,
        navigator,
      }),
    ).rejects.toThrow("a bug");
  });
});
