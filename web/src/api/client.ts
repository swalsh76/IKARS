// The engine's API, as the page calls it (PLAN D3 as amended). The session is the HttpOnly cookie,
// which the browser sends by itself; nothing here ever sees a credential. Every reply is validated
// against its contract (unknown fields refused) before the screens get it.

import type { ContractName, Contracts } from "./contracts";
import { validators } from "./validate";

export const REQUEST_TIMEOUT_MS = 10_000;

/** What went wrong, in the words a screen shows. A timeout is its own kind: the engine may be busy,
 * not gone (it is never taken for a restart). */
export type Failure = "signed-out" | "refused" | "timeout" | "unreachable" | "contract";

export class ApiFailure extends Error {
  constructor(
    readonly kind: Failure,
    message: string,
    readonly status = 0,
    readonly code = "",
  ) {
    super(message);
  }
}

/** ``value`` as contract ``name``, or a "contract" failure saying where it differs. */
export function checked<K extends ContractName>(name: K, value: unknown): Contracts[K] {
  const validate = validators[name];
  if (!validate(value)) {
    const where = validate.errors?.[0];
    throw new ApiFailure("contract", `${name} does not match the contract: ${where?.instancePath || "/"} ${where?.message ?? ""}`.trim());
  }
  return value as Contracts[K];
}

export interface RequestOptions {
  signal?: AbortSignal;
  timeoutMs?: number;
  method?: "GET" | "PUT" | "POST" | "DELETE";
  body?: unknown;
}

async function send(path: string, options: RequestOptions): Promise<Response> {
  const timeout = AbortSignal.timeout(options.timeoutMs ?? REQUEST_TIMEOUT_MS);
  const signal = options.signal ? AbortSignal.any([options.signal, timeout]) : timeout;
  const init: RequestInit = {
    method: options.method ?? "GET",
    credentials: "same-origin",
    cache: "no-store",
    signal,
  };
  if (options.body !== undefined) {
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(options.body);
  }
  try {
    return await fetch(path, init);
  } catch (error) {
    if (options.signal?.aborted) throw error; // the screen let go: not a failure to show
    if (timeout.aborted) throw new ApiFailure("timeout", "the engine did not answer in time");
    throw new ApiFailure("unreachable", "the engine cannot be reached");
  }
}

async function failure(response: Response): Promise<ApiFailure> {
  let code = "";
  let detail = `the engine answered ${response.status}`;
  try {
    const error = checked("ApiError", await response.json());
    code = error.error;
    detail = error.detail;
  } catch {
    // not the engine's JSON error: keep the status
  }
  const kind: Failure = response.status === 401 ? "signed-out" : "refused";
  return new ApiFailure(kind, detail, response.status, code);
}

/** The reply's JSON as contract ``name``. Reading the body can fail too (a timeout part way, or not
 * JSON): always an ApiFailure, never a raw error a screen cannot show (M1.8a-web-1b merge review). */
async function body<K extends ContractName>(response: Response, name: K, options: RequestOptions): Promise<Contracts[K]> {
  let value: unknown;
  try {
    value = await response.json();
  } catch (error) {
    if (options.signal?.aborted) throw error; // the screen let go
    if (error instanceof DOMException && (error.name === "TimeoutError" || error.name === "AbortError")) {
      throw new ApiFailure("timeout", "the engine did not finish its answer in time");
    }
    throw new ApiFailure("contract", `${name}: the engine's answer is not JSON`);
  }
  return checked(name, value);
}

/** ``GET path`` as contract ``name``. */
export async function get<K extends ContractName>(path: string, name: K, options: RequestOptions = {}): Promise<Contracts[K]> {
  const response = await send(path, options);
  if (!response.ok) throw await failure(response);
  return body(response, name, options);
}

/** A change (``PUT``/``POST``) answered with contract ``name``. */
export async function change<K extends ContractName>(
  path: string,
  name: K,
  content: unknown,
  options: RequestOptions = {},
): Promise<Contracts[K]> {
  const response = await send(path, { ...options, method: options.method ?? "PUT", body: content });
  if (!response.ok) throw await failure(response);
  return body(response, name, options);
}

/** Trades the one-time code in the address's ``#code=`` for the session cookie, and removes the code
 * from the address bar at once (it works once, for 30 s). True when a code was spent. */
export async function signIn(location: Location = window.location, history: History = window.history): Promise<boolean> {
  const code = new URLSearchParams(location.hash.slice(1)).get("code");
  if (!code) return false;
  history.replaceState(history.state, "", location.pathname + location.search);
  const response = await send("/v1/session", { method: "POST", body: { code } });
  if (response.status !== 204) throw await failure(response);
  return true;
}
