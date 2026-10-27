// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Hub } from "../api/hub";
import type { Route } from "../nav/route";
import { TerminalContext, useRoute, useSnapshot, useTerminal, useValue } from "./hooks";
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
    stream: {} as Terminal["stream"],
    navigator: {
      current: () => route,
      go: () => undefined,
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

