// React's view of the terminal: values from the hub and the route, each re-rendering only its own
// screen part when it changes.

import { createContext, useCallback, useContext, useEffect, useRef, useState, useSyncExternalStore } from "react";
import type { ContractName, Contracts } from "../api/contracts";
import type { Values } from "../api/hub";
import { ApiFailure, get } from "../api/client";
import type { Route } from "../nav/route";
import type { Terminal } from "./terminal";

export const TerminalContext = createContext<Terminal | null>(null);

export function useTerminal(): Terminal {
  const terminal = useContext(TerminalContext);
  if (!terminal) throw new Error("useTerminal outside the terminal");
  return terminal;
}

export function useValue<K extends keyof Values>(key: K): Values[K] {
  const { hub } = useTerminal();
  // the same function while hub and key stay the same: a re-render does not re-subscribe (merge review 8)
  const subscribe = useCallback((listener: () => void) => hub.listen(key, listener), [hub, key]);
  const read = useCallback(() => hub.get(key), [hub, key]);
  return useSyncExternalStore(subscribe, read);
}

/** The markets a screen shows, live: subscribes their quotes (and books) while the screen is open and
 * re-renders it when any of them changes (one render per hub flush). Returns a change count. */
export function useMarkets(quotes: readonly string[], books: readonly string[] = []): number {
  const { hub } = useTerminal();
  const [changes, setChanges] = useState(0);
  const key = JSON.stringify([quotes, books]);
  const held = useRef<(() => void) | null>(null);
  useEffect(() => {
    // the new list is wanted before the old one is let go: a market in both keeps its value and its
    // subscription (released first, it was dropped and the engine would not resend it: merge review 2)
    const [q, b] = JSON.parse(key) as [string[], string[]];
    const release = hub.want(q, b);
    held.current?.();
    held.current = release;
    const changed = () => setChanges((n) => n + 1);
    const stops = [...q.map((t) => hub.listen(`quote:${t}`, changed)), ...b.map((t) => hub.listen(`book:${t}`, changed))];
    return () => {
      for (const stop of stops) stop();
    };
  }, [hub, key]);
  useEffect(
    () => () => {
      held.current?.(); // the screen is left
      held.current = null;
    },
    [],
  );
  return changes;
}

export function useRoute(): Route {
  const { navigator } = useTerminal();
  return useSyncExternalStore(navigator.listen, navigator.current);
}

/** ``stale``: a refresh failed and ``value`` is the last one that loaded (it is kept, not blanked). */
export type Loaded<T> = { state: "loading" } | { state: "ready"; value: T; stale?: string } | { state: "failed"; message: string };

/** ``GET path`` while the screen shows it; leaving the screen cancels the request (behaviour 2).
 * ``path`` null: nothing to ask yet (it waits on something else). */
export function useSnapshot<K extends ContractName>(path: string | null, name: K, refreshMs = 0): Loaded<Contracts[K]> {
  const [loaded, setLoaded] = useState<Loaded<Contracts[K]>>({ state: "loading" });
  const [asked, setAsked] = useState(path);
  if (path !== asked) {
    // another address (a new search): never the last one's value under it (merge review 3)
    setAsked(path);
    setLoaded({ state: "loading" });
  }
  useEffect(() => {
    if (path === null) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = async () => {
      try {
        setLoaded({ state: "ready", value: await get(path, name, { signal: controller.signal }) });
      } catch (error) {
        if (controller.signal.aborted) return;
        const message = error instanceof ApiFailure ? error.message : "could not load";
        // a refresh that fails keeps the last good value, marked stale (merge review 9)
        setLoaded((last) => (last.state === "ready" ? { state: "ready", value: last.value, stale: message } : { state: "failed", message }));
      }
      if (refreshMs > 0 && !controller.signal.aborted) timer = setTimeout(() => void load(), refreshMs);
    };
    void load();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [path, name, refreshMs]);
  return loaded;
}
