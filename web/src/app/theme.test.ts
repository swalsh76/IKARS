// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { THEMES, THEME_EVENT, applyTheme, currentTheme, isTheme, showsLight, toggled } from "./theme";

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

  it("knows its skins, light or dark", () => {
    expect(THEMES).toEqual(["system", "dark", "light", "contrast", "midnight", "paper"]);
    expect(isTheme("midnight")).toBe(true);
    expect(isTheme("neon")).toBe(false);
    expect(showsLight("paper", false)).toBe(true);
    expect(showsLight("contrast", true)).toBe(false);
    expect(toggled("midnight", true)).toBe("light");
    expect(toggled("paper", false)).toBe("dark");
    applyTheme("contrast");
    expect(currentTheme()).toBe("contrast");
    document.documentElement.setAttribute("data-theme", "neon");
    expect(currentTheme()).toBe("system");
    applyTheme("system");
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
