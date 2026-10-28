import { describe, expect, it } from "vitest";
import type { UpdateView } from "../api/contracts";
import { canApply, updateLine } from "./updates";

const UP: UpdateView = { installed: true, current: "0.1.0", available: null, state: "up to date", checked_at_utc: "2026-10-27T12:00:00+00:00", problem: null };

describe("the program's updates", () => {
  it("say the version and what is new", () => {
    expect(updateLine(UP)).toBe("version 0.1.0 · up to date (checked 2026-10-27 12:00 UTC)");
    expect(updateLine({ ...UP, checked_at_utc: null })).toBe("version 0.1.0 · up to date");
    expect(updateLine({ ...UP, state: "ready", available: "0.1.1" })).toBe("version 0.1.0 · 0.1.1 is downloaded and ready");
    expect(updateLine({ ...UP, state: "checking" })).toContain("checking");
    expect(updateLine({ ...UP, state: "failed", problem: "offline" })).toContain("failed: offline");
    expect(updateLine({ ...UP, installed: false, current: null, state: "not installed" })).toContain("a development copy");
  });

  it("offer Restart to update only when a release is downloaded", () => {
    expect(canApply(UP)).toBe(false);
    expect(canApply({ ...UP, state: "ready", available: "0.1.1" })).toBe(true);
    expect(canApply({ ...UP, installed: false, state: "ready", available: "0.1.1" })).toBe(false);
  });
});
