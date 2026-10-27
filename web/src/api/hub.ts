// The update hub (BUILD-LOG 1.8w, behaviour 3): what the stream says is kept here and handed to the
// screens about ten times a second, however fast it arrives; a screen asks for the markets it shows,
// and the stream's subscriptions change only when the union of every screen's markets changes (one
// change in flight at a time; a failed change is retried). Screens read it through React's
// useSyncExternalStore, so a value keeps its identity until it changes.

import type { AlertView, BookView, HealthView, PortfolioView, QuoteUpdate } from "./contracts";
import type { StreamMessage, StreamState } from "./stream";

export const FLUSH_MS = 100;
export const RETRY_MS = 1_000;
export const ALERTS_PER_PRIORITY = 200;

export type Key = "health" | "portfolio" | "alerts" | "connection" | `quote:${string}` | `book:${string}`;

export interface Values {
  health: HealthView | null;
  portfolio: PortfolioView | null;
  alerts: readonly AlertView[];
  connection: StreamState;
}

export interface HubDeps {
  setInterval: (fn: () => void, ms: number) => unknown;
  clearInterval: (handle: unknown) => void;
  setTimeout: (fn: () => void, ms: number) => unknown;
  clearTimeout: (handle: unknown) => void;
  /** Sends the stream's subscriptions (PUT /v1/stream/{id}/subscriptions). */
  subscribe: (streamId: string, quotes: string[], books: string[]) => Promise<unknown>;
  /** Told at each flush the engine's received_ns of the quotes handed to the screens (the meter). */
  flushed?: (receivedNs: readonly number[]) => void;
}

/** Newest first by line, at most ``ALERTS_PER_PRIORITY`` of each priority, each line once. */
export function mergeAlerts(current: readonly AlertView[], added: readonly AlertView[]): AlertView[] {
  const byLine = new Map(current.map((a) => [a.line, a]));
  for (const alert of added) byLine.set(alert.line, alert);
  const kept: AlertView[] = [];
  const counts = new Map<string, number>();
  for (const alert of [...byLine.values()].sort((a, b) => b.line - a.line)) {
    const count = counts.get(alert.priority) ?? 0;
    if (count < ALERTS_PER_PRIORITY) {
      kept.push(alert);
      counts.set(alert.priority, count + 1);
    }
  }
  return kept;
}

export class Hub {
  private readonly current = new Map<Key, unknown>([
    ["health", null],
    ["portfolio", null],
    ["alerts", []],
    ["connection", "connecting"],
  ]);
  private readonly pending = new Map<Key, unknown>();
  private readonly listeners = new Map<Key, Set<() => void>>();
  private readonly wanted = { quotes: new Map<string, number>(), books: new Map<string, number>() };
  private sent = "";
  private sending = false;
  private timer: unknown = null;
  private retry: unknown = null; // at most one retry of a failed change waits (merge review 9)
  private streamId: string | null = null;

  constructor(private readonly deps: HubDeps) {}

  start(): void {
    this.timer ??= this.deps.setInterval(() => this.flush(), FLUSH_MS);
  }

  stop(): void {
    if (this.timer !== null) this.deps.clearInterval(this.timer);
    if (this.retry !== null) this.deps.clearTimeout(this.retry);
    this.timer = this.retry = null;
  }

  // --- values ---------------------------------------------------------------------------------

  get<K extends keyof Values>(key: K): Values[K];
  get(key: `quote:${string}`): QuoteUpdate | undefined;
  get(key: `book:${string}`): BookView | undefined;
  get(key: Key): unknown {
    return this.current.get(key);
  }

  /** Changes waiting for the next flush (the last value of each key wins). */
  set(key: Key, value: unknown): void {
    this.pending.set(key, value);
  }

  listen(key: Key, listener: () => void): () => void {
    let set = this.listeners.get(key);
    if (!set) this.listeners.set(key, (set = new Set()));
    set.add(listener);
    return () => set.delete(listener);
  }

  /** Hands every waiting change to the screens at once. */
  flush(): void {
    if (!this.pending.size) return;
    const changed = [...this.pending.entries()];
    this.pending.clear();
    if (this.deps.flushed) {
      const received: number[] = [];
      for (const [key, value] of changed) {
        // only quotes a screen shows: one no screen listens to changes nothing on it (merge review 2)
        if (!this.listeners.get(key)?.size) continue;
        const at = key.startsWith("quote:") ? (value as QuoteUpdate).received_ns : null;
        if (typeof at === "number") received.push(at);
      }
      this.deps.flushed(received);
    }
    for (const [key, value] of changed) this.current.set(key, value);
    for (const [key] of changed) for (const listener of this.listeners.get(key) ?? []) listener();
  }

  /** One message from the stream. */
  handle(message: StreamMessage): void {
    switch (message.event) {
      case "opened":
        this.streamId = message.data.stream_id;
        this.sent = ""; // a new stream has no subscriptions yet
        void this.sync();
        break;
      case "quote":
        this.set(`quote:${message.data.ticker}`, message.data);
        break;
      case "book":
        this.set(`book:${message.data.ticker}`, message.data);
        break;
      case "portfolio":
        this.set("portfolio", message.data);
        break;
      case "health":
        this.set("health", message.data);
        break;
      case "alert":
        this.addAlerts([message.data]);
        break;
      case "error":
        break;
    }
  }

  addAlerts(alerts: readonly AlertView[]): void {
    const base = (this.pending.get("alerts") as AlertView[] | undefined) ?? this.get("alerts");
    this.set("alerts", mergeAlerts(base, alerts));
  }

  connection(state: StreamState): void {
    if (state !== "open") this.streamId = null;
    this.set("connection", state);
  }

  // --- subscriptions --------------------------------------------------------------------------

  /** A screen shows these markets' quotes and books until it calls the function returned. */
  want(quotes: readonly string[], books: readonly string[] = []): () => void {
    const add = (map: Map<string, number>, kind: "quote" | "book", tickers: readonly string[], by: number) => {
      for (const ticker of tickers) {
        const count = (map.get(ticker) ?? 0) + by;
        if (count > 0) {
          map.set(ticker, count);
        } else {
          // no screen shows it now: its value goes too, so a later screen never sees it stale
          // (merge review 7)
          map.delete(ticker);
          this.current.delete(`${kind}:${ticker}`);
          this.pending.delete(`${kind}:${ticker}`);
        }
      }
    };
    add(this.wanted.quotes, "quote", quotes, 1);
    add(this.wanted.books, "book", books, 1);
    void this.sync();
    let released = false;
    return () => {
      if (released) return;
      released = true;
      add(this.wanted.quotes, "quote", quotes, -1);
      add(this.wanted.books, "book", books, -1);
      void this.sync();
    };
  }

  private union(): { quotes: string[]; books: string[]; key: string } {
    const quotes = [...this.wanted.quotes.keys()].sort();
    const books = [...this.wanted.books.keys()].sort();
    return { quotes, books, key: JSON.stringify([quotes, books]) };
  }

  /** Sends the union when it differs from what the stream has; one at a time, retried on failure. */
  async sync(): Promise<void> {
    if (this.sending || this.streamId === null) return;
    const { quotes, books, key } = this.union();
    if (key === this.sent) return;
    this.sending = true;
    const stream = this.streamId;
    let sent = false;
    try {
      await this.deps.subscribe(stream, quotes, books);
      if (this.streamId === stream) this.sent = key;
      sent = true;
    } catch {
      this.retry ??= this.deps.setTimeout(() => {
        this.retry = null;
        void this.sync();
      }, RETRY_MS);
    } finally {
      this.sending = false;
    }
    // a screen changed its markets, or a new stream opened, while this one was in flight: catch up
    // (does nothing when the stream already has the union)
    if (sent) void this.sync();
  }
}
