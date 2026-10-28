import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiFailure, change, get, signIn } from "./client";

const health = {
  at_utc: "2026-01-01T00:00:00Z",
  feed: { connected: true, seconds_since_last_frame: 0.2, open_gaps: 0, books_ready: 1, watched_markets: 1, trading_active: true },
  storage_state: "ok",
  ledger: { verified: true, reconciled_exact: true, records: 1, errors: 0 },
  backup: { configured: true, hours_since: 1, last_error: null },
  clock_status: "ok",
  clock_offset_ms: 1.5,
  gpu: null,
  api_streams: 0,
  port_fallback: false,
  setup_reason: null,
  plugins: {},
  detail: {},
};

function reply(status: number, body?: unknown): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => vi.unstubAllGlobals());

describe("get", () => {
  it("returns a reply that matches its contract, with the cookie and no cache", async () => {
    const fetch = vi.fn().mockResolvedValue(reply(200, health));
    vi.stubGlobal("fetch", fetch);
    expect(await get("/v1/health", "HealthView")).toEqual(health);
    const [path, init] = fetch.mock.calls[0] as [string, RequestInit];
    expect(path).toBe("/v1/health");
    expect(init.credentials).toBe("same-origin");
    expect(init.cache).toBe("no-store");
  });

  it("refuses a reply that does not match", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(reply(200, { ...health, made_up: 1 })));
    await expect(get("/v1/health", "HealthView")).rejects.toMatchObject({ kind: "contract" });
  });

  it("says signed out on a 401 and keeps the engine's words", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(reply(401, { error: "unauthorised", detail: "sign in with: ikars open" })));
    await expect(get("/v1/health", "HealthView")).rejects.toMatchObject({
      kind: "signed-out",
      status: 401,
      code: "unauthorised",
      message: "sign in with: ikars open",
    });
  });

  it("turns a reply it cannot read into a failure it can show (merge review 1)", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("<html>", { status: 200 })));
    await expect(get("/v1/health", "HealthView")).rejects.toMatchObject({ kind: "contract" });
    const slow = { ok: true, status: 200, json: () => Promise.reject(new DOMException("t", "TimeoutError")) } as unknown as Response;
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(slow));
    await expect(get("/v1/health", "HealthView")).rejects.toMatchObject({ kind: "timeout" });
    const leaving = new AbortController();
    leaving.abort();
    const gone = { ok: true, status: 200, json: () => Promise.reject(new DOMException("a", "AbortError")) } as unknown as Response;
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(gone));
    await expect(get("/v1/health", "HealthView", { signal: leaving.signal })).rejects.not.toBeInstanceOf(ApiFailure);
  });

  it("calls other failures refused, even without the engine's JSON", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("busy", { status: 503 })));
    await expect(get("/v1/health", "HealthView")).rejects.toMatchObject({ kind: "refused", status: 503 });
  });

  it("tells a timeout from an engine that cannot be reached", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((_path: string, init: RequestInit) => new Promise((_resolve, fail) => init.signal?.addEventListener("abort", () => fail(new Error("aborted"))))),
    );
    await expect(get("/v1/health", "HealthView", { timeoutMs: 5 })).rejects.toMatchObject({ kind: "timeout" });
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    await expect(get("/v1/health", "HealthView")).rejects.toMatchObject({ kind: "unreachable" });
  });

  it("lets a screen that leaves cancel without a failure to show", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((_path: string, init: RequestInit) => new Promise((_resolve, fail) => init.signal?.addEventListener("abort", () => fail(new DOMException("aborted", "AbortError"))))),
    );
    const leaving = new AbortController();
    const asked = get("/v1/health", "HealthView", { signal: leaving.signal });
    leaving.abort();
    await expect(asked).rejects.not.toBeInstanceOf(ApiFailure);
  });
});

describe("change", () => {
  it("sends JSON and checks the answer", async () => {
    const fetch = vi.fn().mockResolvedValue(reply(200, { quotes: ["KXA"], books: [] }));
    vi.stubGlobal("fetch", fetch);
    expect(await change("/v1/stream/s/subscriptions", "Subscriptions", { quotes: ["KXA"], books: [] })).toEqual({ quotes: ["KXA"], books: [] });
    const init = fetch.mock.calls[0]?.[1] as RequestInit;
    expect(init.method).toBe("PUT");
    expect(init.body).toBe(JSON.stringify({ quotes: ["KXA"], books: [] }));
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(reply(403, { error: "forbidden", detail: "no" })));
    await expect(change("/v1/x", "Subscriptions", {})).rejects.toMatchObject({ kind: "refused", code: "forbidden" });
  });
});

describe("signIn", () => {
  function place(hash: string) {
    const history = { state: null, replaceState: vi.fn() } as unknown as History;
    const location = { hash, pathname: "/overview", search: "?x=1" } as Location;
    return { history, location };
  }

  it("spends the code once and wipes it from the address first", async () => {
    const fetch = vi.fn().mockResolvedValue(reply(204));
    vi.stubGlobal("fetch", fetch);
    const { history, location } = place("#code=abc");
    expect(await signIn(location, history)).toBe(true);
    expect(history.replaceState).toHaveBeenCalledWith(null, "", "/overview?x=1");
    const [path, init] = fetch.mock.calls[0] as [string, RequestInit];
    expect(path).toBe("/v1/session");
    expect(init.method).toBe("POST");
    expect(init.body).toBe(JSON.stringify({ code: "abc" }));
  });

  it("does nothing without a code, and reports a spent one", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(reply(401, { error: "unauthorised", detail: "the code was used, expired or unknown" })));
    expect(await signIn(place("").location, place("").history)).toBe(false);
    const { history, location } = place("#code=old");
    await expect(signIn(location, history)).rejects.toMatchObject({ kind: "signed-out" });
  });
});
