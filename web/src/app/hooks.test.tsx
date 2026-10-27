// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Hub } from "../api/hub";
import { Meter } from "../api/meter";
import type { Route } from "../nav/route";
import { TerminalContext, useMarkets, useMeterReading, useQuoteChanges, useRoute, useSnapshot, useTerminal, useValue } from "./hooks";
import { Guarded } from "../screens/Guarded";
import type { Terminal } from "./terminal";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;

function fakeTerminal(): { terminal: Terminal; setRoute: (r: Route) => void } {
  const hub = new Hub({ setInterval: () => 0, clearInterval: () => undefined, setTimeout: () => 0, clearTimeout: () => undefined, subscribe: async () => undefined });
  let route: Route = { screen: "overview" };
  const listeners = new Set<() => void>();
  const terminal = {
    hub,
    meter: new Meter({ frame: (f) => f(), wallMs: () => 1_000, perfMs: () => 0, hidden: () => false, shownOrHidden: () => 0 }),
    stream: {} as Terminal["stream"],
    navigator: {
      current: () => route,
      go: () => undefined,
      replace: () => undefined,
      listen: (l: () => void) => {
        listeners.add(l);
        return () => listeners.delete(l);
      },
    },
    stop: () => undefined,
  } as Terminal;
  return {
    terminal,
    setRoute: (r) => {
      route = r;
      for (const l of listeners) l();
    },
  };
}

async function show(terminal: Terminal, node: React.ReactNode): Promise<void> {
  await act(async () => {
    root.render(<TerminalContext value={terminal}>{node}</TerminalContext>);
  });
}

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("hub values and the route", () => {
  it("re-render when the hub flushes and when the route moves", async () => {
    const { terminal, setRoute } = fakeTerminal();
    function View() {
      const connection = useValue("connection");
      const route = useRoute();
      return <p>{`${connection} ${route.screen}`}</p>;
    }
    await show(terminal, <View />);
    expect(container.textContent).toBe("connecting overview");
    await act(async () => {
      terminal.hub.connection("open");
      terminal.hub.flush();
      setRoute({ screen: "health" });
    });
    expect(container.textContent).toBe("open health");
  });

  it("refuse to work outside the terminal", async () => {
    function Lost() {
      useTerminal();
      return null;
    }
    const error = vi.spyOn(console, "error").mockImplementation(() => undefined);
    await expect(act(async () => root.render(<Lost />))).rejects.toThrow("outside the terminal");
    error.mockRestore();
  });
});

describe("useSnapshot", () => {
  const page = { items: [] };

  it("loads, says when it failed, and refreshes while shown", async () => {
    vi.useFakeTimers();
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ error: "unavailable", detail: "busy" }), { status: 503 }))
      .mockResolvedValue(new Response(JSON.stringify(page), { status: 200 }));
    vi.stubGlobal("fetch", fetch);
    const { terminal } = fakeTerminal();
    function View() {
      const gaps = useSnapshot("/v1/gaps", "GapPage", 1_000);
      return <p>{gaps.state === "failed" ? gaps.message : gaps.state}</p>;
    }
    await show(terminal, <View />);
    expect(container.textContent).toBe("busy");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });
    expect(container.textContent).toBe("ready");
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it("keeps the last good value when a refresh fails, marked stale (merge review 9)", async () => {
    vi.useFakeTimers();
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(page), { status: 200 }))
      .mockResolvedValue(new Response(JSON.stringify({ error: "unavailable", detail: "busy" }), { status: 503 }));
    vi.stubGlobal("fetch", fetch);
    const { terminal } = fakeTerminal();
    function View() {
      const gaps = useSnapshot("/v1/gaps", "GapPage", 1_000);
      return <p>{gaps.state === "ready" ? `${gaps.value.items.length} ${gaps.stale ?? "fresh"}` : gaps.state}</p>;
    }
    await show(terminal, <View />);
    expect(container.textContent).toBe("0 fresh");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });
    expect(container.textContent).toBe("0 busy");
  });

  it("cancels its request when the screen is left", async () => {
    let signal: AbortSignal | undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn((_path: string, init: RequestInit) => {
        signal = init.signal ?? undefined;
        return new Promise((_resolve, fail) => signal?.addEventListener("abort", () => fail(new DOMException("aborted", "AbortError"))));
      }),
    );
    const { terminal } = fakeTerminal();
    function View() {
      return <p>{useSnapshot("/v1/gaps", "GapPage").state}</p>;
    }
    await show(terminal, <View />);
    expect(container.textContent).toBe("loading");
    await act(async () => root.render(<TerminalContext value={terminal}>{null}</TerminalContext>));
    expect(signal?.aborted).toBe(true);
  });

  it("names a failure that is not the engine's", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("odd")));
    const { terminal } = fakeTerminal();
    function View() {
      const gaps = useSnapshot("/v1/gaps", "GapPage");
      return <p>{gaps.state === "failed" ? gaps.message : gaps.state}</p>;
    }
    await show(terminal, <View />);
    expect(container.textContent).toBe("the engine cannot be reached");
  });
});

describe("useMarkets", () => {
  it("subscribes while shown, re-renders on its markets' changes and lets go when left", async () => {
    const { terminal } = fakeTerminal();
    const want = vi.spyOn(terminal.hub, "want");
    function View() {
      const changes = useMarkets(["KXA"], ["KXA"]);
      return <p>{`${changes} ${terminal.hub.get("quote:KXA")?.yes_bid_e6 ?? "-"}`}</p>;
    }
    await show(terminal, <View />);
    expect(want).toHaveBeenCalledWith(["KXA"], ["KXA"]);
    expect(container.textContent).toBe("0 -");
    await act(async () => {
      terminal.hub.handle({ event: "quote", data: { ticker: "KXA", yes_bid_e6: 7, yes_ask_e6: null, last_price_e6: null, received_ns: 1 } });
      terminal.hub.handle({ event: "book", data: { ticker: "KXA", ready: true, yes: [], no: [] } });
      terminal.hub.flush();
    });
    expect(container.textContent).toBe("2 7");
    await act(async () => root.render(<TerminalContext value={terminal}>{null}</TerminalContext>));
    expect(terminal.hub.get("quote:KXA")).toBeUndefined(); // released: its value goes too
  });
});

describe("the meter's reading", () => {
  it("is read again on its timer", async () => {
    vi.useFakeTimers();
    const { terminal } = fakeTerminal();
    function View() {
      return <p>{useMeterReading(500).samples}</p>;
    }
    await show(terminal, <View />);
    expect(container.textContent).toBe("0");
    terminal.meter.flushed([990 * 1e6]);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(500);
    });
    expect(container.textContent).toBe("1");
  });
});

describe("useQuoteChanges", () => {
  it("hands a flush's changed quotes over at once, without re-rendering, and lets go when left", async () => {
    const { terminal } = fakeTerminal();
    const want = vi.spyOn(terminal.hub, "want");
    const batches: string[][] = [];
    let renders = 0;
    function View() {
      renders += 1;
      useQuoteChanges(["KXA", "KXB"], (changed) => batches.push(changed));
      return null;
    }
    await show(terminal, <View />);
    expect(want).toHaveBeenCalledWith(["KXA", "KXB"]);
    const before = renders;
    const quote = (ticker: string) => ({ event: "quote" as const, data: { ticker, yes_bid_e6: 1, yes_ask_e6: 2, last_price_e6: null, received_ns: 1 } });
    await act(async () => {
      terminal.hub.handle(quote("KXA"));
      terminal.hub.handle(quote("KXB"));
      terminal.hub.flush();
      await Promise.resolve();
    });
    expect(batches).toEqual([["KXA", "KXB"]]); // one call for the flush
    expect(renders).toBe(before); // and no render
    terminal.hub.handle(quote("KXA"));
    terminal.hub.flush(); // a batch is queued...
    // ...and the screen closes before it runs (a synchronous act: the effects end at once)
    act(() => root.render(<TerminalContext value={terminal}>{null}</TerminalContext>));
    await Promise.resolve();
    expect(batches).toHaveLength(1); // the queued batch was dropped
    expect(terminal.hub.get("quote:KXA")).toBeUndefined(); // released
  });
});

describe("a changing list of markets", () => {
  it("keeps a market in both lists live (merge review 2)", async () => {
    const { terminal } = fakeTerminal();
    function View({ books }: { books: string[] }) {
      useMarkets(["KXA"], books);
      return <p>{terminal.hub.get("quote:KXA")?.yes_bid_e6 ?? "-"}</p>;
    }
    await show(terminal, <View books={[]} />);
    await act(async () => {
      terminal.hub.handle({ event: "quote", data: { ticker: "KXA", yes_bid_e6: 7, yes_ask_e6: null, last_price_e6: null, received_ns: 1 } });
      terminal.hub.flush();
    });
    await show(terminal, <View books={["KXA"]} />); // the market was found to be watched
    expect(container.textContent).toBe("7");
  });
});

describe("a snapshot whose address changes", () => {
  it("never shows the last address's value under the new one (merge review 3)", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ items: [] }), { status: 200 }))
      .mockResolvedValue(new Response(JSON.stringify({ error: "unavailable", detail: "busy" }), { status: 503 }));
    vi.stubGlobal("fetch", fetch);
    const { terminal } = fakeTerminal();
    function View({ path }: { path: string }) {
      const page = useSnapshot(path, "GapPage");
      return <p>{page.state === "failed" ? page.message : page.state}</p>;
    }
    await show(terminal, <View path="/v1/gaps?a" />);
    expect(container.textContent).toBe("ready");
    await show(terminal, <View path="/v1/gaps?b" />);
    expect(container.textContent).toBe("busy");
  });
});

describe("a snapshot with nothing to ask yet", () => {
  it("waits without a request", async () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    const { terminal } = fakeTerminal();
    function View() {
      return <p>{useSnapshot(null, "GapPage").state}</p>;
    }
    await show(terminal, <View />);
    expect(container.textContent).toBe("loading");
    expect(fetch).not.toHaveBeenCalled();
  });
});

describe("a guarded part", () => {
  it("says it could not be drawn and leaves the rest working (merge review 3)", async () => {
    const { terminal } = fakeTerminal();
    function Broken(): never {
      throw new Error("not an exact integer amount");
    }
    const error = vi.spyOn(console, "error").mockImplementation(() => undefined);
    await show(
      terminal,
      <>
        <Guarded title="Portfolio and exposure">
          <Broken />
        </Guarded>
        <p>still here</p>
      </>,
    );
    error.mockRestore();
    expect(container.textContent).toContain("This could not be drawn: not an exact integer amount");
    expect(container.textContent).toContain("still here");
  });
});

