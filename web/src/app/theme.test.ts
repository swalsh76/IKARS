// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { THEME_EVENT, applyTheme, currentTheme, showsLight, toggled } from "./theme";

describe("the theme", () => {
  it("is set on the page, Windows' when none, and announced", () => {
    const heard = vi.fn();
    document.addEventListener(THEME_EVENT, heard);
    applyTheme("light");
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
    expect(currentTheme()).toBe("light");
    applyTheme("system");
    expect(document.documentElement.hasAttribute("data-theme")).toBe(false);
    expect(currentTheme()).toBe("system");
    expect(heard).toHaveBeenCalledTimes(2);
    document.removeEventListener(THEME_EVENT, heard);
  });

  it("switches to the other of light and dark from what shows now", () => {
    expect(showsLight("system", true)).toBe(true);
    expect(showsLight("dark", true)).toBe(false);
    expect(toggled("system", true)).toBe("dark");
    expect(toggled("system", false)).toBe("light");
    expect(toggled("light", false)).toBe("dark");
    expect(toggled("dark", true)).toBe("light");
  });
});
