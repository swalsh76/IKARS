// Skins (PLAN G16 as amended: built-in colour palettes, chosen in Settings; M1.8b-1). Applied as
// `data-theme` on the page (none: follow Windows, Dark or Light); the stylesheet's tokens follow it,
// and parts drawn from the tokens by script (the chart) hear the THEME_EVENT and repaint. The choice
// is saved with the engine's settings (`terminal.skin`). Colours only: the CSP is unchanged.

export type Theme = "system" | "dark" | "light" | "contrast" | "midnight" | "paper";

export interface Skin {
  id: Theme;
  label: string;
  light: boolean | null; // null: Windows decides
}

export const SKINS: readonly Skin[] = [
  { id: "system", label: "Follow Windows (Dark or Light)", light: null },
  { id: "dark", label: "Dark", light: false },
  { id: "light", label: "Light", light: true },
  { id: "contrast", label: "High contrast", light: false },
  { id: "midnight", label: "Midnight", light: false },
  { id: "paper", label: "Paper", light: true },
];
export const THEMES: readonly Theme[] = SKINS.map((s) => s.id);
export const THEME_EVENT = "ikars:theme";

export function isTheme(value: string): value is Theme {
  return (THEMES as readonly string[]).includes(value);
}

export function applyTheme(theme: Theme, root: HTMLElement = document.documentElement): void {
  if (theme === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", theme);
  root.dispatchEvent(new CustomEvent(THEME_EVENT, { bubbles: true, detail: theme }));
}

export function currentTheme(root: HTMLElement = document.documentElement): Theme {
  const set = root.getAttribute("data-theme") ?? "system";
  return isTheme(set) ? set : "system";
}

/** Whether the page shows a light skin now (Windows' choice when following Windows). */
export function showsLight(theme: Theme, windowsLight: boolean): boolean {
  return SKINS.find((s) => s.id === theme)?.light ?? windowsLight;
}

/** Ctrl+Shift+L: Light from any dark skin, Dark from any light one (a fixed choice from then on). */
export function toggled(theme: Theme, windowsLight: boolean): Theme {
  return showsLight(theme, windowsLight) ? "dark" : "light";
}
