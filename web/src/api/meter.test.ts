import { describe, expect, it } from "vitest";
import { KEPT, Meter, Window } from "./meter";

describe("the window", () => {
  it("keeps the latest samples and gives nearest-rank percentiles", () => {
    const window = new Window(4);
    expect(window.percentile(50)).toBeNull();
    for (const v of [10, 20, 30, 40, 50]) window.add(v); // 10 falls out
    expect(window.count).toBe(4);
    expect(window.percentile(50)).toBe(30);
    expect(window.percentile(99)).toBe(50);
    expect(window.percentile(0)).toBe(20);
    expect(new Window().percentile(99)).toBeNull();
    expect(KEPT).toBe(10_000);
  });
});

describe("the meter", () => {
  function rig() {
    const frames: (() => void)[] = [];
    let wall = 1_000_000;
    let perf = 50;
    const meter = new Meter({
      frame: (callback) => frames.push(callback),
      wallMs: () => wall,
      perfMs: () => perf,
    });
    const nextFrame = (advance: number) => {
      wall += advance;
      perf += advance;
      frames.shift()?.();
    };
    return { meter, frames, nextFrame };
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
});
