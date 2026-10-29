// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { browserNavigator, crumbs, href, parse, title, type Route } from "./route";

describe("addresses", () => {
  it.each<[string, string, Route]>([
    ["/", "", { screen: "overview" }],
    ["/overview", "", { screen: "overview" }],
    ["/health", "", { screen: "health" }],
    ["/ledger", "", { screen: "ledger" }],
    ["/alerts", "", { screen: "alerts" }],
    ["/settings", "", { screen: "settings" }],
    ["/welcome", "", { screen: "welcome" }],
    ["/update", "?to=0.1.3", { screen: "update", to: "0.1.3" }],
    ["/markets", "?q=rain%20nyc", { screen: "markets", query: "rain nyc" }],
    ["/markets", "", { screen: "markets", query: "" }],
    ["/markets", "?q=rain&category=Climate", { screen: "markets", query: "rain", category: "Climate" }],
    ["/market/KXDEMO-26JAN01-T50", "", { screen: "market", ticker: "KXDEMO-26JAN01-T50" }],
    ["/market/A%2FB", "", { screen: "market", ticker: "A/B" }],
    ["/recommendation/momentum-KXA-1", "", { screen: "recommendation", id: "momentum-KXA-1" }],
    ["/health/extra", "", { screen: "missing", path: "/health/extra" }],
    ["/nothing", "", { screen: "missing", path: "/nothing" }],
    ["/market/%E0%A4%A", "", { screen: "missing", path: "/market/%E0%A4%A" }],
  ])("%s%s", (pathname, search, route) => {
    expect(parse(pathname, search)).toEqual(route);
  });

  it("round-trip through href", () => {
    for (const route of [
      { screen: "overview" },
      { screen: "markets", query: "a b" },
      { screen: "markets", query: "" },
      { screen: "markets", query: "", category: "Climate & Weather" },
      { screen: "market", ticker: "A/B" },
      { screen: "recommendation", id: "momentum-KXA-1" },
      { screen: "ledger" },
    ] as Route[]) {
      const url = new URL(href(route), "http://127.0.0.1");
      expect(parse(url.pathname, url.search)).toEqual(route);
    }
    expect(href({ screen: "missing", path: "/x" })).toBe("/x");
  });
});

describe("breadcrumbs and titles", () => {
  it("start at the Overview", () => {
    expect(crumbs({ screen: "overview" })).toEqual([{ label: "Overview", href: null }]);
    expect(crumbs({ screen: "health" })).toEqual([
      { label: "Overview", href: "/overview" },
      { label: "Health", href: null },
    ]);
    expect(crumbs({ screen: "market", ticker: "KXA" }).map((c) => c.label)).toEqual(["Overview", "Markets", "KXA"]);
    expect(crumbs({ screen: "missing", path: "/x" }).at(-1)?.label).toBe("Not found");
    expect(title({ screen: "health" })).toBe("Health · IKARS");
    // a screen's own name, once it has loaded (M1.12d)
    expect(crumbs({ screen: "recommendation", id: "r1" }).at(-1)?.label).toBe("Recommendation");
    expect(crumbs({ screen: "recommendation", id: "r1" }, "Jamal Murray: 2+ rebounds").at(-1)).toEqual({ label: "Recommendation: Jamal Murray: 2+ rebounds", href: null });
    expect(title({ screen: "recommendation", id: "r1" }, "KXA")).toBe("Recommendation: KXA · IKARS");
  });
});

describe("the browser navigator", () => {
  it("moves with pushState, follows back and forward, and skips a move to where it is", () => {
    window.history.replaceState(null, "", "/overview");
    const navigator = browserNavigator(window);
    const heard = vi.fn();
    const stop = navigator.listen(heard);
    navigator.go("/health");
    expect(navigator.current()).toEqual({ screen: "health" });
    navigator.go("/health");
    expect(heard).toHaveBeenCalledTimes(1);
    window.history.replaceState(null, "", "/alerts");
    window.dispatchEvent(new PopStateEvent("popstate"));
    expect(navigator.current()).toEqual({ screen: "alerts" });
    expect(heard).toHaveBeenCalledTimes(2);
    const entries = window.history.length;
    navigator.replace("/markets?q=rain");
    expect(navigator.current()).toEqual({ screen: "markets", query: "rain" });
    expect(window.history.length).toBe(entries); // no new history entry
    navigator.replace("/markets?q=rain");
    expect(heard).toHaveBeenCalledTimes(3);
    stop();
    navigator.go("/ledger");
    expect(heard).toHaveBeenCalledTimes(3);
  });

  it("knows when this window has somewhere to go back to, and a screen's name lasts until a move (M1.12d)", () => {
    window.history.replaceState(null, "", "/overview");
    const back = vi.spyOn(window.history, "back").mockImplementation(() => undefined);
    const navigator = browserNavigator(window);
    expect(navigator.canBack()).toBe(false);
    navigator.back();
    expect(back).not.toHaveBeenCalled(); // the screen it opened on: Back would leave IKARS
    navigator.go("/recommendation/r1");
    expect(navigator.canBack()).toBe(true);
    navigator.replace("/recommendation/r2"); // a replace keeps the depth
    expect(navigator.canBack()).toBe(true);
    navigator.back();
    expect(back).toHaveBeenCalledTimes(1);
    window.history.replaceState({ depth: 0 }, "", "/overview"); // where the browser's back lands
    window.dispatchEvent(new PopStateEvent("popstate"));
    expect(navigator.canBack()).toBe(false);

    const heard = vi.fn();
    navigator.listen(heard);
    navigator.name("Made-up market");
    navigator.name("Made-up market"); // the same again tells no one
    expect(navigator.named()).toBe("Made-up market");
    expect(heard).toHaveBeenCalledTimes(1);
    navigator.go("/health");
    expect(navigator.named()).toBeNull();
    back.mockRestore();
  });
});
