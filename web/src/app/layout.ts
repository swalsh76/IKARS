import type { LayoutWindow } from "../api/contracts";

/** The windows of a layout (M1.8b-2). Every IKARS window answers "where are you?" on a channel
 * shared by the engine's own pages (one origin, one browser profile); the window that saves asks,
 * waits a moment for the answers and sends them all, itself first, to the engine.
 *
 * Opening one: ikars open gives each window its place in the address (``#...&at=x,y,w,h``) and the
 * window moves itself there, since Edge applies its own window switches to its first window only.
 * A move onto another monitor needs Edge's window-management permission, asked for when saving. */

export const CHANNEL = "ikars-windows";
export const ANSWER_WAIT_MS = 300;
// the engine's limits (settings_api.SIZES): a window smaller than these is saved at them
const MIN = { width: 200, height: 150 };

type Ask = { kind: "ask"; id: string };
type Answer = { kind: "here"; id: string; from: string; window: LayoutWindow };

// a screen's address in the characters the engine takes (its settings_api.SCREEN): any other is
// %-encoded, as the address means the same either way (a search for * is /markets?q=%2A)
const OUTSIDE = /[^A-Za-z0-9/?&=%._~+-]/g;
const encoded = (ch: string) => {
  const plain = encodeURIComponent(ch);
  return plain !== ch ? plain : `%${ch.charCodeAt(0).toString(16).toUpperCase().padStart(2, "0")}`;
};

// this window: the one that asks also answers (its own listener hears the ask) and must not count
// itself twice
const SELF = crypto.randomUUID();

/** This window: its screen (the address in the terminal) and its place, in Windows' screen
 * coordinates, so any monitor. */
export function here(view: Pick<Window, "location" | "screenX" | "screenY" | "outerWidth" | "outerHeight"> = window): LayoutWindow {
  return {
    screen: (view.location.pathname + view.location.search).replace(OUTSIDE, encoded),
    x: Math.round(view.screenX),
    y: Math.round(view.screenY),
    width: Math.max(MIN.width, Math.round(view.outerWidth)),
    height: Math.max(MIN.height, Math.round(view.outerHeight)),
  };
}

export type Channel = Pick<BroadcastChannel, "postMessage" | "close"> & {
  onmessage: ((event: MessageEvent) => void) | null;
};

const open = (): Channel => new BroadcastChannel(CHANNEL);

/** Answer other windows' asks for as long as this window is open; returns the stop. */
export function answerAsks(channel: Channel = open(), where: () => LayoutWindow = here, self: string = SELF): () => void {
  channel.onmessage = (event: MessageEvent) => {
    const ask = event.data as Partial<Ask> | null;
    if (ask?.kind === "ask" && typeof ask.id === "string") {
      channel.postMessage({ kind: "here", id: ask.id, from: self, window: where() } satisfies Answer);
    }
  };
  return () => channel.close();
}

/** Every open IKARS window, this one first. */
export function gather(
  channel: Channel = open(),
  where: () => LayoutWindow = here,
  wait: (ms: number) => Promise<void> = (ms) => new Promise((done) => setTimeout(done, ms)),
  self: string = SELF,
): Promise<LayoutWindow[]> {
  const id = crypto.randomUUID();
  const found: LayoutWindow[] = [where()];
  channel.onmessage = (event: MessageEvent) => {
    const answer = event.data as Partial<Answer> | null;
    if (answer?.kind === "here" && answer.id === id && answer.from !== self && answer.window) found.push(answer.window);
  };
  channel.postMessage({ kind: "ask", id } satisfies Ask);
  return wait(ANSWER_WAIT_MS).then(() => {
    channel.close();
    return found;
  });
}

type Placeable = Pick<Window, "moveTo" | "resizeTo">;

/** Moves this window to the place its address names (``at=x,y,w,h`` after the ``#``), if any;
 * true when it did. Read before signing in, which clears the address's ``#`` part. */
export function placeFrom(hash: string, view: Placeable = window): boolean {
  const at = new URLSearchParams(hash.slice(1)).get("at");
  const numbers = at?.split(",").map(Number) ?? [];
  if (numbers.length !== 4 || !numbers.every(Number.isInteger)) return false;
  const [x = 0, y = 0, width = 0, height = 0] = numbers;
  view.moveTo(x, y); // onto its monitor first, then sized there (a monitor may scale differently)
  view.resizeTo(width, height);
  view.moveTo(x, y);
  return true;
}

/** Asks Edge, once, to let IKARS's windows sit on any monitor (the window-management permission;
 * Edge remembers the answer). False when it is refused or Edge has no such permission. */
export async function allowPlacement(view: object = window): Promise<boolean> {
  const ask = (view as { getScreenDetails?: () => Promise<unknown> }).getScreenDetails;
  if (typeof ask !== "function") return false;
  try {
    await ask.call(view);
    return true;
  } catch {
    return false;
  }
}

export const NEW_WINDOW_OFFSET = 30; // pixels right and down, so the new window does not hide this one

export type Opener = Pick<Window, "open" | "location" | "screenX" | "screenY" | "innerWidth" | "innerHeight"> & {
  screen: Pick<Screen, "availWidth" | "availHeight">;
};

/** Another IKARS window on this screen, a little offset and the same size, less the offset when this
 * one fills the screen (so it never hides this one whole) (M1.8c). It shares Edge's profile, so it
 * is signed in already; it answers layout asks like any other window. */
export function openNewWindow(view: Opener = window): void {
  const room = (inner: number, avail: number) => Math.min(inner, avail - 2 * NEW_WINDOW_OFFSET);
  const features = [
    "popup",
    `left=${view.screenX + NEW_WINDOW_OFFSET}`,
    `top=${view.screenY + NEW_WINDOW_OFFSET}`,
    `width=${room(view.innerWidth, view.screen.availWidth)}`,
    `height=${room(view.innerHeight, view.screen.availHeight)}`,
  ].join(",");
  // not "noopener": with it Edge may ignore the size and place and open a tab instead (merge review
  // 1); the link back is cut once it is open, so nothing ties the windows together but the engine
  const other = view.open(view.location.pathname + view.location.search, "_blank", features);
  if (other) other.opener = null;
}
