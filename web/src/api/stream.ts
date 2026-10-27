// The engine's event stream, read with fetch (PLAN D3 as amended: not EventSource, so the rules
// below are ours). The rules carried over from M1.8a (BUILD-LOG 1.8w, behaviour 4):
// - a reconnect resumes after the last alert seen (?after_alert=);
// - the backoff grows on every failure and only a stream that opened resets it (a refusal does not);
// - a stream silent for SILENT_MS is dropped and reopened (the engine sends health every 5 s);
// - "signed out" (401) is its own state: the owner signs in again with ikars open.
// Every event is validated against its contract before it is handed on.

import { checked } from "./client";
import { STREAM_EVENTS, type Contracts, type StreamEvent } from "./contracts";

export const SILENT_MS = 20_000;
export const BACKOFF_BASE_MS = 500;
export const BACKOFF_MAX_MS = 30_000;

export type StreamMessage = {
  [E in StreamEvent]: { event: E; data: Contracts[(typeof STREAM_EVENTS)[E]] };
}[StreamEvent];

export type StreamState = "connecting" | "open" | "retrying" | "signed-out";

interface RawEvent {
  event: string;
  data: string;
}

/** Server-Sent Events, fed in chunks as they arrive (lines end in \n; an event ends at a blank line). */
export class SseParser {
  private buffer = "";
  private event = "message";
  private data: string[] = [];

  feed(chunk: string): RawEvent[] {
    this.buffer += chunk;
    const out: RawEvent[] = [];
    let newline: number;
    while ((newline = this.buffer.indexOf("\n")) !== -1) {
      const line = this.buffer.slice(0, newline).replace(/\r$/, "");
      this.buffer = this.buffer.slice(newline + 1);
      if (line === "") {
        if (this.data.length) out.push({ event: this.event, data: this.data.join("\n") });
        this.event = "message";
        this.data = [];
      } else if (line.startsWith("event:")) {
        this.event = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        this.data.push(line.slice(5).replace(/^ /, ""));
      } // id: and comments are not needed: alerts resume by line
    }
    return out;
  }
}

/** A raw event as its contract (throws ApiFailure "contract" when it is not). */
export function message(raw: RawEvent): StreamMessage | null {
  if (!(raw.event in STREAM_EVENTS)) return null; // an event this terminal does not know: skipped
  const event = raw.event as StreamEvent;
  const data: unknown = JSON.parse(raw.data);
  return { event, data: checked(STREAM_EVENTS[event], data) } as StreamMessage;
}

export interface StreamDeps {
  fetch: typeof fetch;
  setTimeout: (fn: () => void, ms: number) => unknown;
  clearTimeout: (handle: unknown) => void;
  random: () => number;
}

const browser: StreamDeps = {
  fetch: (...args) => fetch(...args),
  setTimeout: (fn, ms) => setTimeout(fn, ms),
  clearTimeout: (handle) => clearTimeout(handle as ReturnType<typeof setTimeout>),
  random: Math.random,
};

export interface StreamHandlers {
  onMessage(message: StreamMessage): void;
  onState(state: StreamState): void;
}

export class EventStream {
  /** The last alert line seen: a reconnect resumes after it. */
  lastAlert: number | null = null;
  /** The open stream's id (for its subscriptions), or null between streams. */
  streamId: string | null = null;
  private failures = 0;
  private stopped = true;
  private controller: AbortController | null = null;
  private retry: unknown = null;

  constructor(
    private readonly handlers: StreamHandlers,
    private readonly deps: StreamDeps = browser,
  ) {}

  start(): void {
    if (!this.stopped) return;
    this.stopped = false;
    void this.connect();
  }

  stop(): void {
    this.stopped = true;
    this.controller?.abort();
    if (this.retry !== null) this.deps.clearTimeout(this.retry);
    this.retry = null;
    this.streamId = null;
  }

  /** The wait before the next attempt: doubling from BACKOFF_BASE_MS to BACKOFF_MAX_MS, with jitter. */
  delay(): number {
    const ceiling = Math.min(BACKOFF_MAX_MS, BACKOFF_BASE_MS * 2 ** Math.max(0, this.failures - 1));
    return Math.round(ceiling / 2 + (this.deps.random() * ceiling) / 2);
  }

  private schedule(state: StreamState): void {
    this.streamId = null;
    if (this.stopped) return;
    this.failures += 1;
    this.handlers.onState(state);
    this.retry = this.deps.setTimeout(() => {
      this.retry = null;
      void this.connect();
    }, state === "signed-out" ? BACKOFF_MAX_MS : this.delay());
  }

  private async connect(): Promise<void> {
    if (this.stopped) return;
    this.handlers.onState("connecting");
    const controller = new AbortController();
    this.controller = controller;
    let silence: unknown = null;
    const quiet = () => {
      if (silence !== null) this.deps.clearTimeout(silence);
      silence = this.deps.setTimeout(() => controller.abort(), SILENT_MS);
    };
    const query = this.lastAlert === null ? "" : `?after_alert=${this.lastAlert}`;
    try {
      quiet();
      const response = await this.deps.fetch(`/v1/stream${query}`, {
        credentials: "same-origin",
        cache: "no-store",
        signal: controller.signal,
      });
      if (response.status === 401) return this.schedule("signed-out");
      if (!response.ok || !response.body) return this.schedule("retrying"); // a refusal: no reset
      const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
      const parser = new SseParser();
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        quiet();
        for (const raw of parser.feed(value)) {
          const parsed = message(raw);
          if (parsed === null) continue;
          if (parsed.event === "opened") {
            this.streamId = parsed.data.stream_id;
            this.failures = 0; // only an opened stream resets the backoff
            this.handlers.onState("open");
          } else if (parsed.event === "alert") {
            this.lastAlert = Math.max(this.lastAlert ?? 0, parsed.data.line);
          }
          this.handlers.onMessage(parsed);
          if (parsed.event === "error") controller.abort(); // refused (too many at once): retry later
        }
      }
      this.schedule("retrying"); // the stream ended (an engine restart, or silence)
    } catch {
      this.schedule("retrying"); // network gone, silence, or a contract failure in an event
    } finally {
      if (silence !== null) this.deps.clearTimeout(silence);
    }
  }
}
