// The update-to-screen meter (PLAN D20 J as amended; BUILD-LOG 1.8w, behaviour 6). D20 J: from the
// terminal **applying** a change (the hub handing it to the screens) to the change being on the
// display, measured to the frame *after* the one that shows it (two animation frames: an upper bound),
// with the page's own work per update (from the flush to the first frame) beside it.
// Target: update to screen p99 <= 33 ms while 100 markets stream at 10 Hz.
//
// For information only, the whole way from the engine receiving the change (its received_ns, on this
// machine's clock) to the screen: it adds the engine's and the hub's 10 Hz batching (up to 100 ms
// each, by design: PLAN D3), so it is not held to the 33 ms target.

export const KEPT = 10_000; // the latest updates the percentiles are taken over (about 15 s at 100 markets, 10 Hz)

/** The latest ``size`` samples, in a ring. */
export class Window {
  private readonly values: Float64Array;
  private next = 0;
  private filled = 0;

  constructor(size = KEPT) {
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

  /** The ``p``th percentile (0..100) of the samples kept, nearest rank; null when there are none. */
  percentile(p: number): number | null {
    if (this.filled === 0) return null;
    const sorted = Array.from(this.values.subarray(0, this.filled)).sort((a, b) => a - b);
    const rank = Math.min(sorted.length - 1, Math.max(0, Math.ceil((p / 100) * sorted.length) - 1));
    return sorted[rank] ?? null;
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
}

const browser: MeterDeps = {
  frame: (callback) => void requestAnimationFrame(() => callback()),
  wallMs: () => Date.now(),
  perfMs: () => performance.now(),
};

export class Meter {
  readonly latency = new Window(); // one sample per flush that carried a quote
  readonly work = new Window();
  readonly fromEngine = new Window(); // one sample per quote

  constructor(private readonly deps: MeterDeps = browser) {}

  /** A flush is handing these updates to the screens now (the engine's received_ns of each). */
  flushed(receivedNs: readonly number[]): void {
    if (receivedNs.length === 0) return;
    const applied = this.deps.perfMs();
    this.deps.frame(() => {
      const work = this.deps.perfMs() - applied; // the render, up to the frame that shows it
      this.deps.frame(() => {
        // the frame after the one that shows it
        this.latency.add(this.deps.perfMs() - applied);
        this.work.add(work);
        const shown = this.deps.wallMs();
        for (const received of receivedNs) this.fromEngine.add(Math.max(0, shown - received / 1e6));
      });
    });
  }

  reading(): Reading {
    return {
      samples: this.latency.count,
      p50: this.latency.percentile(50),
      p99: this.latency.percentile(99),
      workP50: this.work.percentile(50),
      workP99: this.work.percentile(99),
      engineP50: this.fromEngine.percentile(50),
      engineP99: this.fromEngine.percentile(99),
    };
  }
}
