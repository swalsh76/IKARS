import { describe, expect, it } from "vitest";
import { ENGINE_KEPT, Meter, SCREEN_KEPT, Window } from "./meter";

describe("the window", () => {
  it("keeps the latest samples and gives nearest-rank percentiles from one sort", () => {
    const window = new Window(4);
    expect(window.percentiles(50, 99)).toEqual([null, null]);
    for (const v of [10, 20, 30, 40, 50]) window.add(v); // 10 falls out
    expect(window.count).toBe(4);
    expect(window.percentiles(50, 99, 0)).toEqual([30, 50, 20]);
    expect([SCREEN_KEPT, ENGINE_KEPT]).toEqual([1_000, 10_000]);
  });
});

describe("the meter", () => {
  function rig() {
    const frames: (() => void)[] = [];
    let wall = 1_000_000;
    let perf = 50;
    let hidden = false;
    let changes = 0;
    const meter = new Meter({
      frame: (callback) => frames.push(callback),
      wallMs: () => wall,
      perfMs: () => perf,
      hidden: () => hidden,
      shownOrHidden: () => changes,
    });
    const nextFrame = (advance: number) => {
      wall += advance;
      perf += advance;
      frames.shift()?.();
    };
    const hide = (h: boolean) => {
      hidden = h;
      changes += 1;
    };
    return { meter, frames, nextFrame, hide };
  }

  it("measures from the flush to the frame after the one that shows the change (D20 J)", () => {
    const { meter, frames, nextFrame } = rig();
    meter.flushed([(1_000_000 - 85) * 1e6, (1_000_000 - 2) * 1e6]); // the engine had them 85 and 2 ms before
    expect(frames).toHaveLength(1);
    nextFrame(4); // the frame that shows it: the page's work is 4 ms
    expect(meter.reading().samples).toBe(0);
    nextFrame(16); // the frame after: 20 ms after the flush
    expect(meter.reading()).toEqual({ samples: 1, p50: 20, p99: 20, workP50: 4, workP99: 4, engineP50: 22, engineP99: 105 });
  });

  it("records nothing for a flush without quotes, and never a negative engine time", () => {
    const { meter, frames, nextFrame } = rig();
    meter.flushed([]);
    expect(frames).toHaveLength(0);
    meter.flushed([(1_000_000 + 100) * 1e6]); // the engine's clock a little ahead
    nextFrame(1);
    nextFrame(1);
    expect(meter.reading().engineP99).toBe(0);
  });

  it("measures one at a time and not while hidden, so held frames never pile up (merge review 1)", () => {
    const { meter, frames, nextFrame, hide } = rig();
    meter.flushed([1]);
    meter.flushed([1]); // one is in flight: skipped
    expect(frames).toHaveLength(1);
    nextFrame(1);
    hide(true); // minimised between the two frames
    nextFrame(300_000);
    expect(meter.reading().samples).toBe(0);
    meter.flushed([1]); // hidden: nothing queued
    expect(frames).toHaveLength(0);
    hide(false);
    meter.flushed([1]);
    expect(frames).toHaveLength(1);
  });

  it("drops a measurement during which the window was hidden and shown again (per-commit review)", () => {
    const { meter, nextFrame, hide } = rig();
    meter.flushed([1]);
    hide(true); // hidden before the first frame: both frames are held
    hide(false); // shown again: both callbacks now run
    nextFrame(300_000);
    nextFrame(16);
    expect(meter.reading().samples).toBe(0);
  });
});
