// The update-to-screen meter (PLAN D20 J as amended; BUILD-LOG 1.8w, behaviour 6). D20 J: from the
// terminal **applying** a change (the hub handing it to the screens) to the change being on the
// display, measured to the frame *after* the one that shows it (two animation frames: an upper bound),
// with the page's own work per update (from the flush to the first frame) beside it.
// Target: update to screen p99 <= 33 ms while 100 markets stream at 10 Hz.
//
// For information only, the whole way from the engine receiving the change (its received_ns, on this
// machine's clock) to the screen: it adds the engine's and the hub's 10 Hz batching (up to 100 ms
// each, by design: PLAN D3), so it is not held to the 33 ms target.
//
// One measurement at a time, none while the window is hidden, and none during which it was hidden or
// shown: the browser holds animation frames meanwhile, and they would land with made-up times
// (M1.8a-web-2b merge review 1 and the per-commit review after it).

export const SCREEN_KEPT = 1_000; // screen updates (about 10 a second): about the last 100 s
export const ENGINE_KEPT = 10_000; // quotes (hundreds a second at 100 markets): about the last 15 s

/** The latest ``size`` samples, in a ring. */
export class Window {
  private readonly values: Float64Array;
  private next = 0;
  private filled = 0;

  constructor(size: number) {
    this.values = new Float64Array(size);
  }

  add(value: number): void {
    this.values[this.next] = value;
    this.next = (this.next + 1) % this.values.length;
    this.filled = Math.min(this.filled + 1, this.values.length);
  }

  get count(): number {
    return this.filled;
  }

  /** Nearest-rank percentiles (0..100) of the samples kept, from one sort; nulls when there are none. */
  percentiles(...ps: number[]): (number | null)[] {
    if (this.filled === 0) return ps.map(() => null);
    const sorted = this.values.slice(0, this.filled).sort();
    return ps.map((p) => sorted[Math.min(sorted.length - 1, Math.max(0, Math.ceil((p / 100) * sorted.length) - 1))] ?? null);
  }
}

export interface Reading {
  samples: number;
  p50: number | null; // update to screen (D20 J)
  p99: number | null;
  workP50: number | null; // the page's own work per update
  workP99: number | null;
  engineP50: number | null; // from the engine receiving it (information: includes the batching)
  engineP99: number | null;
}

export interface MeterDeps {
  frame: (callback: () => void) => void; // requestAnimationFrame
  wallMs: () => number; // Date.now: the clock the engine's received_ns is on
  perfMs: () => number; // performance.now: for the page's own times
  hidden: () => boolean; // the window is minimised or covered: no frames are drawn
  shownOrHidden: () => number; // how many times the window was hidden or shown so far
}

let visibilityChanges = 0;
if (typeof document !== "undefined") document.addEventListener("visibilitychange", () => (visibilityChanges += 1));

const browser: MeterDeps = {
  frame: (callback) => void requestAnimationFrame(() => callback()),
  wallMs: () => Date.now(),
  perfMs: () => performance.now(),
  hidden: () => document.visibilityState === "hidden",
  shownOrHidden: () => visibilityChanges,
};

export class Meter {
  readonly latency = new Window(SCREEN_KEPT);
  readonly work = new Window(SCREEN_KEPT);
  readonly fromEngine = new Window(ENGINE_KEPT);
  private measuring = false;

  constructor(private readonly deps: MeterDeps = browser) {}

  /** A flush is handing these updates to the screens now (the engine's received_ns of each). */
  flushed(receivedNs: readonly number[]): void {
    if (receivedNs.length === 0 || this.measuring || this.deps.hidden()) return;
    this.measuring = true;
    const applied = this.deps.perfMs();
    const visibility = this.deps.shownOrHidden();
    this.deps.frame(() => {
      const work = this.deps.perfMs() - applied; // the render, up to the frame that shows it
      this.deps.frame(() => {
        this.measuring = false;
        if (this.deps.shownOrHidden() !== visibility) return; // hidden meanwhile: the frames were held
        this.latency.add(this.deps.perfMs() - applied); // the frame after the one that shows it
        this.work.add(work);
        const shown = this.deps.wallMs();
        for (const received of receivedNs) this.fromEngine.add(Math.max(0, shown - received / 1e6));
      });
    });
  }

  reading(): Reading {
    const [p50, p99] = this.latency.percentiles(50, 99);
    const [workP50, workP99] = this.work.percentiles(50, 99);
    const [engineP50, engineP99] = this.fromEngine.percentiles(50, 99);
    return {
      samples: this.latency.count,
      p50: p50 ?? null,
      p99: p99 ?? null,
      workP50: workP50 ?? null,
      workP99: workP99 ?? null,
      engineP50: engineP50 ?? null,
      engineP99: engineP99 ?? null,
    };
  }
}
