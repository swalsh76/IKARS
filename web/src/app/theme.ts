// The theme (PLAN G16, M1.8b-1): dark, light, or Windows' own. Applied as `data-theme` on the page (none:
// follow Windows); the stylesheet's tokens follow it, and parts drawn from the tokens by script (the
// chart) hear the THEME_EVENT and repaint. It is saved with the engine's settings (M1.8b-1).

export type Theme = "system" | "dark" | "light";
export const THEMES: readonly Theme[] = ["system", "dark", "light"];
export const THEME_EVENT = "ikars:theme";

export function applyTheme(theme: Theme, root: HTMLElement = document.documentElement): void {
  if (theme === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", theme);
  root.dispatchEvent(new CustomEvent(THEME_EVENT, { bubbles: true, detail: theme }));
}

export function currentTheme(root: HTMLElement = document.documentElement): Theme {
  const set = root.getAttribute("data-theme");
  return set === "dark" || set === "light" ? set : "system";
}

/** Whether the page shows light now (the theme set, or Windows' when it follows Windows). */
export function showsLight(theme: Theme, windowsLight: boolean): boolean {
  return theme === "light" || (theme === "system" && windowsLight);
}

/** Ctrl+Shift+L: the other of light and dark from what shows now (a fixed choice, no longer Windows'). */
export function toggled(theme: Theme, windowsLight: boolean): Theme {
  return showsLight(theme, windowsLight) ? "dark" : "light";
}
