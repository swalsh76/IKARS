import { describe, expect, it, vi } from "vitest";
import { BACKOFF_BASE_MS, BACKOFF_MAX_MS, EventStream, SILENT_MS, SseParser, message, type StreamDeps, type StreamMessage, type StreamState } from "./stream";

describe("the SSE parser", () => {
  it("joins chunks split anywhere and ends events at a blank line", () => {
    const parser = new SseParser();
    expect(parser.feed("id: 1\nevent: op")).toEqual([]);
    expect(parser.feed('ened\ndata: {"a":\ndata: 1}\r\n\n: comment\nevent: x\n')).toEqual([{ event: "opened", data: '{"a":\n1}' }]);
    expect(parser.feed("data: y\n\n\n")).toEqual([{ event: "x", data: "y" }]);
  });
});

describe("message", () => {
  it("validates the payload and skips events it does not know", () => {
    expect(message({ event: "opened", data: '{"stream_id":"s","coalesce_ms":100}' })).toEqual({
      event: "opened",
      data: { stream_id: "s", coalesce_ms: 100 },
    });
    expect(message({ event: "something-new", data: "{}" })).toBeNull();
    expect(() => message({ event: "opened", data: '{"stream_id":1}' })).toThrow(/StreamOpened/);
  });
});

/** A fake engine: each fetch gets the next scripted answer; a body stays open until closed. */
class Engine {
  urls: string[] = [];
  answers: (() => Response)[] = [];
  bodies: ReadableStreamDefaultController<Uint8Array>[] = [];
  timers: { at: number; fn: () => void; id: number }[] = [];
  now = 0;
  private next = 0;
  readonly deps: StreamDeps = {
    fetch: (async (url: string, init: RequestInit) => {
      this.urls.push(url);
      const answer = this.answers.shift();
      if (!answer) return new Promise(() => undefined);
      const response = answer();
      init.signal?.addEventListener("abort", () => {
        for (const body of this.bodies) {
          try {
            body.error(new DOMException("aborted", "AbortError"));
          } catch {
            // closed already
          }
        }
      });
      return response;
    }) as typeof fetch,
    setTimeout: (fn, ms) => {
      this.next += 1;
      this.timers.push({ at: this.now + ms, fn, id: this.next });
      return this.next;
    },
    clearTimeout: (id) => {
      this.timers = this.timers.filter((t) => t.id !== id);
    },
    random: () => 1,
  };

  open(): Response {
    const body = new ReadableStream<Uint8Array>({ start: (c) => void this.bodies.push(c) });
    return new Response(body, { status: 200 });
  }

  send(text: string): void {
    this.bodies.at(-1)?.enqueue(new TextEncoder().encode(text));
  }

  async advance(ms: number): Promise<void> {
    this.now += ms;
    for (;;) {
      const due = this.timers.filter((t) => t.at <= this.now).sort((a, b) => a.at - b.at)[0];
      if (!due) break;
      this.timers = this.timers.filter((t) => t !== due);
      due.fn();
      await settle();
    }
  }
}

async function settle(): Promise<void> {
  for (let i = 0; i < 20; i += 1) await Promise.resolve();
}

function streamOn(engine: Engine) {
  const messages: StreamMessage[] = [];
  const states: StreamState[] = [];
  const stream = new EventStream({ onMessage: (m) => messages.push(m), onState: (s) => states.push(s) }, engine.deps);
  return { stream, messages, states };
}

const OPENED = 'event: opened\ndata: {"stream_id":"s1","coalesce_ms":100}\n\n';
const ALERT = (line: number) =>
  `event: alert\ndata: {"line":${line},"key":"k","priority":"high","title":"t","text":"x","at_local":"2026-01-01T00:00:00-07:00","outcome":"shown"}\n\n`;

describe("the stream", () => {
  it("opens, hands on validated events and remembers the last alert", async () => {
    const engine = new Engine();
    engine.answers.push(() => engine.open());
    const { stream, messages, states } = streamOn(engine);
    stream.start();
    stream.start(); // once only
    await settle();
    engine.send(OPENED + ALERT(7) + ALERT(5));
    await settle();
    expect(states).toEqual(["connecting", "open"]);
    expect(stream.streamId).toBe("s1");
    expect(messages.map((m) => m.event)).toEqual(["opened", "alert", "alert"]);
    expect(stream.lastAlert).toBe(7);
    expect(engine.urls).toEqual(["/v1/stream"]);
    stream.stop();
  });

  it("resumes after the last alert when the stream ends, and an opened stream resets the backoff", async () => {
    const engine = new Engine();
    engine.answers.push(() => engine.open(), () => engine.open());
    const { stream, states } = streamOn(engine);
    stream.lastAlert = 41;
    stream.start();
    await settle();
    engine.send(OPENED);
    await settle();
    engine.bodies.at(-1)?.close(); // an engine restart ends the stream
    await settle();
    expect(stream.streamId).toBeNull();
    expect(states.at(-1)).toBe("retrying");
    await engine.advance(BACKOFF_BASE_MS);
    expect(engine.urls).toEqual(["/v1/stream?after_alert=41", "/v1/stream?after_alert=41"]);
    engine.send(OPENED);
    await settle();
    expect(stream.delay()).toBe(BACKOFF_BASE_MS); // reset by the opened stream
    stream.stop();
  });

  it("does not reset the backoff on refusals, up to its ceiling", async () => {
    const engine = new Engine();
    for (let i = 0; i < 12; i += 1) engine.answers.push(() => new Response("busy", { status: 503 }));
    const { stream } = streamOn(engine);
    stream.start();
    await settle();
    const waits: number[] = [];
    for (let i = 0; i < 8; i += 1) {
      waits.push(stream.delay());
      await engine.advance(BACKOFF_MAX_MS);
    }
    expect(waits.slice(0, 4)).toEqual([500, 1000, 2000, 4000]);
    expect(waits.at(-1)).toBe(BACKOFF_MAX_MS);
    stream.stop();
  });

  it("says signed out and waits the longest before asking again", async () => {
    const engine = new Engine();
    engine.answers.push(() => new Response(null, { status: 401 }));
    const { stream, states } = streamOn(engine);
    stream.start();
    await settle();
    expect(states).toEqual(["connecting", "signed-out"]);
    await engine.advance(BACKOFF_MAX_MS - 1);
    expect(engine.urls).toHaveLength(1);
    await engine.advance(1);
    expect(engine.urls).toHaveLength(2);
    stream.stop();
  });

  it("drops a stream silent for SILENT_MS and opens another", async () => {
    const engine = new Engine();
    engine.answers.push(() => engine.open(), () => engine.open());
    const { stream, states } = streamOn(engine);
    stream.start();
    await settle();
    engine.send(OPENED);
    await settle();
    await engine.advance(SILENT_MS - 1);
    expect(states.at(-1)).toBe("open");
    await engine.advance(1);
    expect(states.at(-1)).toBe("retrying");
    await engine.advance(BACKOFF_BASE_MS);
    expect(engine.urls).toHaveLength(2);
    stream.stop();
  });

  it("closes on the engine's refusal event and on a payload off its contract", async () => {
    const engine = new Engine();
    engine.answers.push(() => engine.open(), () => engine.open());
    const { stream, messages, states } = streamOn(engine);
    stream.start();
    await settle();
    engine.send('event: error\ndata: {"error":"unavailable","detail":"at most 8 streams at once"}\n\n');
    await settle();
    expect(messages.map((m) => m.event)).toEqual(["error"]);
    expect(states.at(-1)).toBe("retrying");
    await engine.advance(BACKOFF_BASE_MS);
    engine.send('event: quote\ndata: {"ticker":1}\n\n');
    await settle();
    expect(states.at(-1)).toBe("retrying");
    stream.stop();
  });

  it("stops for good, and a network failure is a retry", async () => {
    const engine = new Engine();
    const failing = vi.fn(() => {
      throw new TypeError("Failed to fetch");
    });
    engine.answers.push(failing);
    const { stream, states } = streamOn(engine);
    stream.start();
    await settle();
    expect(states).toEqual(["connecting", "retrying"]);
    stream.stop();
    await engine.advance(BACKOFF_MAX_MS * 2);
    expect(engine.urls).toHaveLength(1);
    expect(stream.streamId).toBeNull();
  });
});
