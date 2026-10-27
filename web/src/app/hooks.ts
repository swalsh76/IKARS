// React's view of the terminal: values from the hub and the route, each re-rendering only its own
// screen part when it changes.

import { createContext, useContext, useEffect, useState, useSyncExternalStore } from "react";
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
  return useSyncExternalStore(
    (listener) => hub.listen(key, listener),
    () => hub.get(key),
  );
}

export function useRoute(): Route {
  const { navigator } = useTerminal();
  return useSyncExternalStore(navigator.listen, navigator.current);
}

export type Loaded<T> = { state: "loading" } | { state: "ready"; value: T } | { state: "failed"; message: string };

/** ``GET path`` while the screen shows it; leaving the screen cancels the request (behaviour 2). */
export function useSnapshot<K extends ContractName>(path: string, name: K, refreshMs = 0): Loaded<Contracts[K]> {
  const [loaded, setLoaded] = useState<Loaded<Contracts[K]>>({ state: "loading" });
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = async () => {
      try {
        setLoaded({ state: "ready", value: await get(path, name, { signal: controller.signal }) });
      } catch (error) {
        if (controller.signal.aborted) return;
        setLoaded({ state: "failed", message: error instanceof ApiFailure ? error.message : "could not load" });
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
