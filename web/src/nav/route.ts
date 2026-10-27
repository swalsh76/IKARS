// One navigator over real addresses (R9; BUILD-LOG 1.8w, behaviour 1): every screen can be linked
// to, and back and forward are the browser's own. A screen that is left lets go of its requests
// and subscriptions in its effect's cleanup (behaviour 2).

export type Route =
  | { screen: "overview" }
  | { screen: "health" }
  | { screen: "markets"; query: string; category?: string }
  | { screen: "market"; ticker: string }
  | { screen: "ledger" }
  | { screen: "alerts" }
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
  ledger: "Ledger",
  alerts: "Alerts",
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
  if (parts.length === 1 && (first === "health" || first === "ledger" || first === "alerts")) return { screen: first };
  if (parts.length === 1 && first === "markets") {
    const params = new URLSearchParams(search);
    const category = params.get("category");
    return category ? { screen: "markets", query: params.get("q") ?? "", category } : { screen: "markets", query: params.get("q") ?? "" };
  }
  if (parts.length === 2 && first === "market" && second) return { screen: "market", ticker: second };
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
    case "missing":
      return route.path;
    default:
      return `/${route.screen}`;
  }
}

/** Overview first, then where the route sits under it. */
export function crumbs(route: Route): Crumb[] {
  if (route.screen === "overview") return [{ label: "Overview", href: null }];
  const trail: Crumb[] = [{ label: "Overview", href: "/overview" }];
  if (route.screen === "missing") return [...trail, { label: "Not found", href: null }];
  if (route.screen === "market") {
    return [...trail, { label: "Markets", href: "/markets" }, { label: route.ticker, href: null }];
  }
  return [...trail, { label: TITLES[route.screen], href: null }];
}

/** The page title for a route. */
export function title(route: Route): string {
  const last = crumbs(route).at(-1)?.label ?? "Overview";
  return `${last} · IKARS`;
}

export interface Navigator {
  current(): Route;
  go(to: string): void;
  /** Moves without a new history entry (a search box as the owner types). */
  replace(to: string): void;
  listen(listener: () => void): () => void;
}

/** The navigator over the browser's history (pushState, popstate). */
export function browserNavigator(win: Window = window): Navigator {
  const listeners = new Set<() => void>();
  let route = parse(win.location.pathname, win.location.search);
  const changed = () => {
    route = parse(win.location.pathname, win.location.search);
    for (const listener of listeners) listener();
  };
  win.addEventListener("popstate", changed);
  return {
    current: () => route,
    go(to: string) {
      if (to === win.location.pathname + win.location.search) return;
      win.history.pushState(null, "", to);
      changed();
    },
    replace(to: string) {
      if (to === win.location.pathname + win.location.search) return;
      win.history.replaceState(null, "", to);
      changed();
    },
    listen(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
}
