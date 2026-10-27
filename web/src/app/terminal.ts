// The terminal's parts, made once: the hub, the stream and the navigator (src/main.tsx starts them).

import { ApiFailure, change, get, signIn } from "../api/client";
import { Hub, type HubDeps } from "../api/hub";
import { EventStream, type StreamDeps } from "../api/stream";
import { browserNavigator, type Navigator } from "../nav/route";

export interface Terminal {
  hub: Hub;
  stream: EventStream;
  navigator: Navigator;
  stop(): void;
}

export interface TerminalDeps {
  signIn: () => Promise<boolean>;
  get: typeof get;
  hub?: Partial<HubDeps>;
  stream?: StreamDeps;
  navigator?: Navigator;
}

const timers = {
  setInterval: (fn: () => void, ms: number) => setInterval(fn, ms),
  clearInterval: (handle: unknown) => clearInterval(handle as ReturnType<typeof setInterval>),
  setTimeout: (fn: () => void, ms: number) => setTimeout(fn, ms),
};

/** Signs in (when the address carries a code), loads what the screens show first, then opens the
 * stream after the last alert loaded, so nothing between the two is missed. */
export async function startTerminal(deps: Partial<TerminalDeps> = {}): Promise<Terminal> {
  const fetchJson = deps.get ?? get;
  const hub = new Hub({
    ...timers,
    subscribe: (id, quotes, books) =>
      change(`/v1/stream/${encodeURIComponent(id)}/subscriptions`, "Subscriptions", { quotes, books }),
    ...deps.hub,
  });
  const stream = new EventStream(
    { onMessage: (m) => hub.handle(m), onState: (s) => hub.connection(s) },
    deps.stream,
  );
  const navigator = deps.navigator ?? browserNavigator();
  hub.start();
  try {
    await (deps.signIn ?? signIn)();
    const [health, portfolio, alerts] = await Promise.all([
      fetchJson("/v1/health", "HealthView"),
      fetchJson("/v1/portfolio", "PortfolioView"),
      fetchJson("/v1/alerts?limit=200", "AlertPage"),
    ]);
    hub.set("health", health);
    hub.set("portfolio", portfolio);
    hub.addAlerts(alerts.items);
    stream.lastAlert = alerts.items.reduce((last, a) => Math.max(last, a.line), 0);
  } catch (error) {
    // the stream reports the state from here (signed out, or the engine not answering yet)
    if (!(error instanceof ApiFailure)) throw error;
    hub.connection(error.kind === "signed-out" ? "signed-out" : "retrying");
  }
  hub.flush();
  stream.start();
  return {
    hub,
    stream,
    navigator,
    stop() {
      stream.stop();
      hub.stop();
    },
  };
}
