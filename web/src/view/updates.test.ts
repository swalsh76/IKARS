import { describe, expect, it } from "vitest";
import type { UpdateView } from "../api/contracts";
import { canApply, updateParts, updatedTo } from "./updates";

const UP: UpdateView = { installed: true, current: "0.1.0", available: null, state: "up to date", checked_at_utc: "2026-10-27T12:00:00+00:00", problem: null };

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
