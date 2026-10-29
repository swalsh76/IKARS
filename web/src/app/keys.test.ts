import { describe, expect, it } from "vitest";
import { HOTKEYS, actionFor, isControl, takesText, type Press } from "./keys";

const press = (key: string, more: Partial<Press> = {}): Press => ({
  key,
  ctrlKey: false,
  shiftKey: false,
  altKey: false,
  metaKey: false,
  typing: false,
  ...more,
});

describe("hotkeys", () => {
  it("map the mock-up's keys to actions", () => {
    expect(actionFor(press("k", { ctrlKey: true }))).toEqual({ kind: "search" });
    expect(actionFor(press("K", { ctrlKey: true }))).toEqual({ kind: "search" });
    expect(actionFor(press("1", { ctrlKey: true }))).toEqual({ kind: "go", to: "/overview" });
    expect(actionFor(press("5", { ctrlKey: true }))).toEqual({ kind: "go", to: "/health" });
    expect(actionFor(press("L", { ctrlKey: true, shiftKey: true }))).toEqual({ kind: "theme" });
    expect(actionFor(press("?"))).toEqual({ kind: "help" });
    expect(actionFor(press("Escape"))).toEqual({ kind: "close" });
    expect(actionFor(press("Enter"))).toEqual({ kind: "alert" });
    expect(actionFor(press("b"))).toEqual({ kind: "book" });
    expect(actionFor(press("X"))).toEqual({ kind: "linked" });
    expect(actionFor(press("t"))).toEqual({ kind: "respond", answer: "taken" });
    expect(actionFor(press("I"))).toEqual({ kind: "respond", answer: "ignored" });
    expect(actionFor(press("d"))).toEqual({ kind: "respond", answer: "dismissed" });
    expect(actionFor(press("C"))).toEqual({ kind: "copy" });
    expect(actionFor(press("ArrowDown"))).toEqual({ kind: "move", by: 1 });
    expect(actionFor(press("ArrowUp"))).toEqual({ kind: "move", by: -1 });
    expect(actionFor(press("ArrowDown", { typing: true }))).toBeNull(); // a text box keeps its arrows
    expect(actionFor(press("ArrowUp", { altKey: true }))).toBeNull();
    for (const key of ["t", "i", "d", "c"]) expect(actionFor(press(key, { typing: true }))).toBeNull();
    expect(HOTKEYS.filter((h) => h.arrives)).toEqual([]); // M1.12c brought the last ones
  });

  it("leave single keys to a text box, but not Ctrl combinations or Esc", () => {
    for (const key of ["?", "Enter", "b", "x"]) expect(actionFor(press(key, { typing: true }))).toBeNull();
    expect(actionFor(press("k", { ctrlKey: true, typing: true }))).toEqual({ kind: "search" });
    expect(actionFor(press("Escape", { typing: true }))).toEqual({ kind: "close" });
  });

  it("leave everything else to the browser", () => {
    expect(actionFor(press("ArrowLeft", { altKey: true }))).toBeNull(); // Edge's back
    expect(actionFor(press("6", { ctrlKey: true }))).toBeNull();
    expect(actionFor(press("c", { ctrlKey: true }))).toBeNull(); // copy
    expect(actionFor(press("p", { ctrlKey: true, shiftKey: true }))).toBeNull();
    expect(actionFor(press("k", { metaKey: true }))).toBeNull();
    expect(actionFor(press("q"))).toBeNull();
  });

  it("leave Enter to a focused link or button (per-commit review)", () => {
    expect(actionFor(press("Enter", { onControl: true }))).toBeNull();
    const el = (tagName: string, more: object = {}) => ({ tagName, tabIndex: -1, getAttribute: () => null, ...more }) as unknown as Element;
    expect(["A", "BUTTON", "SUMMARY"].map((t) => isControl(el(t)))).toEqual([true, true, true]);
    expect(isControl(el("DIV", { getAttribute: () => "row" }))).toBe(true);
    expect(isControl(el("SECTION", { tabIndex: 0 }))).toBe(true);
    expect(isControl(el("SECTION"))).toBe(false);
    expect(isControl(el("BODY", { tabIndex: 0 }))).toBe(false);
    expect(isControl(null)).toBe(false);
  });

  it("know a text box", () => {
    const tag = (tagName: string, more: object = {}) => ({ tagName, ...more }) as unknown as Element;
    expect(["INPUT", "TEXTAREA", "SELECT"].map((t) => takesText(tag(t)))).toEqual([true, true, true]);
    expect(takesText(tag("DIV", { isContentEditable: true }))).toBe(true);
    expect(takesText(tag("BUTTON"))).toBe(false);
    expect(takesText(null)).toBe(false);
  });
});
