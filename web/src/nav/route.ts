// One navigator over real addresses (R9; BUILD-LOG 1.8w, behaviour 1): every screen can be linked
// to, and back and forward are the browser's own. A screen that is left lets go of its requests
// and subscriptions in its effect's cleanup (behaviour 2).

export type Route =
  | { screen: "overview" }
  | { screen: "health" }
  | { screen: "markets"; query: string; category?: string }
  | { screen: "market"; ticker: string }
  | { screen: "recommendation"; id: string }
  | { screen: "ledger" }
  | { screen: "alerts" }
  | { screen: "settings" }
  | { screen: "welcome" }
  | { screen: "update"; to: string }
  | { screen: "missing"; path: string };

export interface Crumb {
  label: string;
  href: string | null; // null: the screen shown now
}

const TITLES: Record<Exclude<Route["screen"], "missing">, string> = {
  overview: "Overview",
  health: "Health",
  markets: "Markets",
  market: "Market",
  recommendation: "Recommendation",
  ledger: "Ledger",
  alerts: "Alerts",
  settings: "Settings",
  welcome: "Set up IKARS",
  update: "IKARS Update",
};

/** The route an address names. */
export function parse(pathname: string, search = ""): Route {
  let parts: string[];
  try {
    parts = pathname.split("/").filter(Boolean).map(decodeURIComponent);
  } catch {
    return { screen: "missing", path: pathname }; // a malformed %-escape: no screen (merge review 2)
  }
  const [first, second] = parts;
  if (parts.length === 0 || (first === "overview" && parts.length === 1)) return { screen: "overview" };
  if (parts.length === 1 && (first === "health" || first === "ledger" || first === "alerts" || first === "settings" || first === "welcome")) {
    return { screen: first };
  }
  if (parts.length === 1 && first === "markets") {
    const params = new URLSearchParams(search);
    const category = params.get("category");
    return category ? { screen: "markets", query: params.get("q") ?? "", category } : { screen: "markets", query: params.get("q") ?? "" };
  }
  if (parts.length === 2 && first === "market" && second) return { screen: "market", ticker: second };
  if (parts.length === 2 && first === "recommendation" && second) return { screen: "recommendation", id: second };
  if (parts.length === 1 && first === "update") return { screen: "update", to: new URLSearchParams(search).get("to") ?? "" };
  return { screen: "missing", path: pathname };
}

/** The address of a route. */
export function href(route: Route): string {
  switch (route.screen) {
    case "overview":
      return "/overview";
    case "markets": {
      const params = new URLSearchParams();
      if (route.query) params.set("q", route.query);
      if (route.category) params.set("category", route.category);
      const text = params.toString();
      return text ? `/markets?${text}` : "/markets";
    }
    case "market":
      return `/market/${encodeURIComponent(route.ticker)}`;
    case "recommendation":
      return `/recommendation/${encodeURIComponent(route.id)}`;
    case "update":
      return `/update?to=${encodeURIComponent(route.to)}`;
    case "missing":
      return route.path;
    default:
      return `/${route.screen}`;
  }
}

/** Overview first, then where the route sits under it. ``name``: the screen's own name for its last
 * crumb, once it has loaded (a recommendation's market). */
export function crumbs(route: Route, name: string | null = null): Crumb[] {
  if (route.screen === "overview") return [{ label: "Overview", href: null }];
  const trail: Crumb[] = [{ label: "Overview", href: "/overview" }];
  if (route.screen === "missing") return [...trail, { label: "Not found", href: null }];
  if (route.screen === "market") {
    return [...trail, { label: "Markets", href: "/markets" }, { label: route.ticker, href: null }];
  }
  const label = TITLES[route.screen];
  return [...trail, { label: name ? `${label}: ${name}` : label, href: null }];
}

/** The page title for a route. */
export function title(route: Route, name: string | null = null): string {
  const last = crumbs(route, name).at(-1)?.label ?? "Overview";
  return `${last} · IKARS`;
}

export interface Navigator {
  current(): Route;
  go(to: string): void;
  /** Moves without a new history entry (a search box as the owner types). */
  replace(to: string): void;
  /** Back one screen in this window (Alt+Left); nothing when it has none to go back to. */
  back(): void;
  /** Whether this window has a screen of its own to go back to (the app window shows no Back of its own). */
  canBack(): boolean;
  /** Names the screen shown now, for its last crumb and the window title; a move clears it. */
  name(label: string | null): void;
  named(): string | null;
  listen(listener: () => void): () => void;
}

/** How many of this window's moves lie behind its history entry (0: the screen it opened on). */
function depthOf(state: unknown): number {
  const depth = typeof state === "object" && state !== null ? (state as { depth?: unknown }).depth : undefined;
  return typeof depth === "number" ? depth : 0;
}

/** The navigator over the browser's history (pushState, popstate). */
export function browserNavigator(win: Window = window): Navigator {
  const listeners = new Set<() => void>();
  let route = parse(win.location.pathname, win.location.search);
  let depth = depthOf(win.history.state);
  let label: string | null = null;
  const tell = () => {
    for (const listener of listeners) listener();
  };
  const changed = () => {
    route = parse(win.location.pathname, win.location.search);
    depth = depthOf(win.history.state);
    label = null;
    tell();
  };
  win.addEventListener("popstate", changed);
  return {
    current: () => route,
    go(to: string) {
      if (to === win.location.pathname + win.location.search) return;
      win.history.pushState({ depth: depth + 1 }, "", to);
      changed();
    },
    replace(to: string) {
      if (to === win.location.pathname + win.location.search) return;
      win.history.replaceState({ depth }, "", to);
      changed();
    },
    back() {
      if (depth > 0) win.history.back();
    },
    canBack: () => depth > 0,
    name(next) {
      if (next === label) return;
      label = next;
      tell();
    },
    named: () => label,
    listen(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
}
