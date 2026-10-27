import { describe, expect, it, vi } from "vitest";
import type { LayoutWindow } from "../api/contracts";
import { answerAsks, gather, here, type Channel } from "./layout";

/** Channels joined like BroadcastChannel: a message reaches every other one, never its sender. */
function bus(): () => Channel & { closed: boolean } {
  const all: (Channel & { closed: boolean })[] = [];
  return () => {
    const channel: Channel & { closed: boolean } = {
      onmessage: null,
      closed: false,
      postMessage(data: unknown) {
        for (const other of all) if (other !== channel && !other.closed) other.onmessage?.({ data } as MessageEvent);
      },
      close() {
        channel.closed = true;
      },
    };
    all.push(channel);
    return channel;
  };
}

const at = (screen: string, x: number): LayoutWindow => ({ screen, x, y: 0, width: 1280, height: 800 });

describe("a window's place", () => {
  it("is its screen and its place on any monitor, in whole pixels and at least the engine's minimum", () => {
    const view = { location: { pathname: "/markets", search: "?q=fed" }, screenX: -1920.4, screenY: 10.6, outerWidth: 120, outerHeight: 900 };
    expect(here(view as unknown as Window)).toEqual({ screen: "/markets?q=fed", x: -1920, y: 11, width: 200, height: 900 });
  });
});

describe("gathering the open windows", () => {
  it("asks every other window and lists itself first", async () => {
    const open = bus();
    const stops = [
      answerAsks(open(), () => at("/ledger", 0), "ledger window"),
      answerAsks(open(), () => at("/alerts", 1920), "alerts window"),
      answerAsks(open(), () => at("/overview", -1920), "this window"), // its own listener
    ];
    const asking = open();
    const found = await gather(asking, () => at("/overview", -1920), () => Promise.resolve(), "this window");
    expect(found.map((w) => w.screen)).toEqual(["/overview", "/ledger", "/alerts"]);
    expect(asking.closed).toBe(true);
    stops.forEach((stop) => stop());
  });

  it("ignores answers to another ask and anything that is not an answer", async () => {
    const open = bus();
    const stranger = open();
    const asking = open();
    const pending = gather(asking, () => at("/overview", 0), async () => {
      stranger.postMessage({ kind: "here", id: "someone else's", window: at("/ledger", 0) });
      stranger.postMessage(null);
      stranger.postMessage("junk");
    });
    expect((await pending).map((w) => w.screen)).toEqual(["/overview"]);
  });

  it("answers only asks, and stops when told", () => {
    const open = bus();
    const listener = open();
    const where = vi.fn(() => at("/ledger", 0));
    const stop = answerAsks(listener, where);
    const other = open();
    other.postMessage({ kind: "here", id: "x" });
    other.postMessage({ kind: "ask" });
    expect(where).not.toHaveBeenCalled();
    other.postMessage({ kind: "ask", id: "x" });
    expect(where).toHaveBeenCalledOnce();
    stop();
    expect(listener.closed).toBe(true);
  });
});
