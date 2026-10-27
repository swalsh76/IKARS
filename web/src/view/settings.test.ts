import { describe, expect, it } from "vitest";
import type { SettingsView } from "../api/contracts";
import { changedCount, changes, formOf, keyLines, keyReady, restartNote } from "./settings";

const VIEW: SettingsView = {
  first_run: false,
  skin: "system",
  quiet_start: "22:00",
  quiet_end: "07:00",
  throttle_minutes: 10,
  digest_minutes: 60,
  tax_time_zone: "",
  tax_zone_effective: "America/Denver",
  backup_share: "",
  watched_max_markets: 100,
  read_share_percent: 25,
  storage_budget_gb: 200,
  port: 47821,
  session_days: 30,
  restart_needed: [],
  key: { id_set: true, path_set: true, file_found: true, owner_only: true, environment: "production" },
  autostart_installed: true,
};

describe("the settings form", () => {
  it("sends only what changed, numbers as numbers and text trimmed", () => {
    const form = { ...formOf(VIEW), quiet_start: " 23:00 ", port: "48000" };
    expect(changes(form, VIEW)).toEqual({ change: { quiet_start: "23:00", port: 48000 } });
    expect(changedCount(form, VIEW)).toBe(2);
    expect(changes(formOf(VIEW), VIEW)).toEqual({ change: {} });
  });

  it("says when a number is not a whole number", () => {
    expect(changes({ ...formOf(VIEW), session_days: "3.5" }, VIEW)).toEqual({ problem: "session days must be a whole number" });
    expect(changedCount({ ...formOf(VIEW), port: "" }, VIEW)).toBe(1);
  });
});

describe("the key", () => {
  it("is yes or no only, and says what is missing", () => {
    expect(keyLines(VIEW.key).map((l) => l.text)).toEqual(["set", "set · file found · readable by you only", "Production (read-only key)"]);
    expect(keyReady(VIEW.key)).toBe(true);
    const missing = { id_set: false, path_set: false, file_found: false, owner_only: null, environment: "demo" };
    expect(keyLines(missing).map((l) => l.text)).toEqual(["not set", "the path's variable is not set", "Demo (read-only key)"]);
    expect(keyReady(missing)).toBe(false);
    expect(keyLines({ ...VIEW.key, file_found: false })[1]?.text).toBe("the variable is set but no key file is there");
    expect(keyLines({ ...VIEW.key, owner_only: false })[1]).toMatchObject({ ok: false, text: "set · file found · other accounts can read it" });
    expect(keyLines({ ...VIEW.key, owner_only: null })[1]?.text).toBe("set · file found");
  });
});

describe("the restart note", () => {
  it("names what waits for a restart", () => {
    expect(restartNote(VIEW)).toBeNull();
    expect(restartNote({ ...VIEW, restart_needed: ["port"] })).toBe("the port applies after an engine restart.");
    expect(restartNote({ ...VIEW, restart_needed: ["tax_time_zone", "backup_share", "other"] })).toBe(
      "the tax zone, the share, other apply after an engine restart.",
    );
  });
});
