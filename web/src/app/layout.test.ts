import { describe, expect, it, vi } from "vitest";
import type { LayoutWindow } from "../api/contracts";
import { allowPlacement, answerAsks, gather, here, openNewWindow, placeFrom, type Channel, type Opener } from "./layout";

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

describe("a screen's address", () => {
  it("is sent in the characters the engine takes, meaning the same", () => {
    const view = { location: { pathname: "/markets", search: "?q=*(x)!'" }, screenX: 0, screenY: 0, outerWidth: 800, outerHeight: 600 };
    expect(here(view as unknown as Window).screen).toBe("/markets?q=%2A%28x%29%21%27");
    const other = { ...view, location: { pathname: "/markets", search: "?q=a,b:c@d" } };
    expect(here(other as unknown as Window).screen).toBe("/markets?q=a%2Cb%3Ac%40d");
  });
});

describe("placing a layout's window", () => {
  it("moves and sizes itself from the address, and leaves a plain address alone", () => {
    const view = { moveTo: vi.fn(), resizeTo: vi.fn() };
    expect(placeFrom("#code=c&at=-1920,10,1600,900", view)).toBe(true);
    expect(view.moveTo).toHaveBeenCalledWith(-1920, 10);
    expect(view.resizeTo).toHaveBeenCalledWith(1600, 900);
    for (const hash of ["", "#code=c", "#at=1,2,3", "#at=1,2,3,x", "#at=1.5,2,3,4"]) {
      expect(placeFrom(hash, { moveTo: vi.fn(), resizeTo: vi.fn() })).toBe(false);
    }
  });

  it("asks for other monitors where Edge can, and says when it cannot", async () => {
    expect(await allowPlacement({ getScreenDetails: () => Promise.resolve({}) })).toBe(true);
    expect(await allowPlacement({ getScreenDetails: () => Promise.reject(new Error("refused")) })).toBe(false);
    expect(await allowPlacement({})).toBe(false);
  });
});

describe("a new window", () => {
  const stub = (other: { opener: unknown } | null, inner = { innerWidth: 1280, innerHeight: 760 }) => {
    const open = vi.fn(() => other);
    const view = {
      open,
      location: { pathname: "/market/KXDEMO", search: "?tab=book" },
      screenX: -1920,
      screenY: 10,
      ...inner,
      screen: { availWidth: 1920, availHeight: 1040 },
    } as unknown as Opener; // a stub of exactly Opener's fields
    return { open, view };
  };

  it("opens this screen as a popup the same size, a little offset, and cuts the link back", () => {
    const other = { opener: "this window" };
    const { open, view } = stub(other);
    openNewWindow(view);
    expect(open).toHaveBeenCalledWith("/market/KXDEMO?tab=book", "_blank", "popup,left=-1890,top=40,width=1280,height=760");
    expect(other.opener).toBeNull();
  });

  it("is smaller than a window that fills the screen, so it never hides it whole", () => {
    const { open, view } = stub(null, { innerWidth: 1920, innerHeight: 1040 });
    openNewWindow(view); // a refused popup (null) is no error
    expect(open).toHaveBeenCalledWith("/market/KXDEMO?tab=book", "_blank", "popup,left=-1890,top=40,width=1860,height=980");
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
