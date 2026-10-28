import { describe, expect, it } from "vitest";
import type { UpdateView } from "../api/contracts";
import { canApply, clock, downloadText, steps, updateParts, updatedTo } from "./updates";

const UP: UpdateView = { installed: true, current: "0.1.0", available: null, state: "up to date", checked_at_utc: "2026-10-27T12:00:00+00:00", problem: null, progress: null, size_bytes: null, events: [] };

describe("the program's updates", () => {
  it("say the version, then what is new", () => {
    expect(updateParts(UP)).toEqual({ version: "Version 0.1.0", status: "Up to date · checked 2026-10-27 12:00 UTC" });
    expect(updateParts({ ...UP, checked_at_utc: null }).status).toBe("Up to date");
    expect(updateParts({ ...UP, state: "ready", available: "0.1.1" }).status).toBe("0.1.1 is downloaded and ready to install.");
    expect(updateParts({ ...UP, state: "checking" }).status).toContain("Checking");
    expect(updateParts({ ...UP, state: "failed", problem: "offline" }).status).toContain("failed: offline");
    expect(updateParts({ ...UP, installed: false, current: null, state: "not installed" }).version).toBe("Development copy");
  });

  it("offer Restart to update only when a release is downloaded, and know when it is running", () => {
    expect(canApply(UP)).toBe(false);
    expect(canApply({ ...UP, state: "ready", available: "0.1.1" })).toBe(true);
    expect(canApply({ ...UP, installed: false, state: "ready", available: "0.1.1" })).toBe(false);
    expect(updatedTo(UP, "0.1.1")).toBe(false);
    expect(updatedTo({ ...UP, current: "0.1.1" }, "0.1.1")).toBe(true);
  });
});

describe("the Update window", () => {
  it("marks each step done, under way or still to come", () => {
    expect(steps("download").map((s) => s.mark)).toEqual(["now", "next", "next", "next", "next"]);
    expect(steps("install").map((s) => s.mark)).toEqual(["done", "done", "now", "next", "next"]);
    expect(steps("done").every((s) => s.mark === "done")).toBe(true);
    expect(steps("failed", "start").map((s) => s.mark)).toEqual(["done", "done", "done", "now", "next"]);
    expect(steps("install")[2]?.label).toBe("Install");
  });

  it("says the download and the time plainly", () => {
    expect(downloadText({ ...UP, progress: 41, size_bytes: 64_500_000 })).toBe("41% of 64.5 MB");
    expect(downloadText({ ...UP, progress: null, size_bytes: null })).toBe("starting");
    expect(updateParts({ ...UP, state: "downloading", available: "0.1.3", progress: 5, size_bytes: 4_200_000 }).status).toBe(
      "Downloading 0.1.3: 5% of 4.2 MB",
    );
    expect(clock(74.9)).toBe("1:14");
    expect(clock(-3)).toBe("0:00");
  });
});
