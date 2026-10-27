import type { LayoutWindow } from "../api/contracts";

/** The windows of a layout (M1.8b-2). Every IKARS window answers "where are you?" on a channel
 * shared by the engine's own pages (one origin, one browser profile); the window that saves asks,
 * waits a moment for the answers and sends them all, itself first, to the engine. */

export const CHANNEL = "ikars-windows";
export const ANSWER_WAIT_MS = 300;
// the engine's limits (settings_api.SIZES): a window smaller than these is saved at them
const MIN = { width: 200, height: 150 };

type Ask = { kind: "ask"; id: string };
type Answer = { kind: "here"; id: string; from: string; window: LayoutWindow };

// this window: the one that asks also answers (its own listener hears the ask) and must not count
// itself twice
const SELF = crypto.randomUUID();

/** This window: its screen (the address in the terminal) and its place, in Windows' screen
 * coordinates, so any monitor. */
export function here(view: Pick<Window, "location" | "screenX" | "screenY" | "outerWidth" | "outerHeight"> = window): LayoutWindow {
  return {
    screen: view.location.pathname + view.location.search,
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
