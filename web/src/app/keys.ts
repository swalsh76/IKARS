// The hotkeys (the approved mock-up's list; PLAN G16, M1.8b-1). A key press becomes an action here, as a
// plain function, so the list is tested without a browser; the shell carries the actions out.

export type Action =
  | { kind: "go"; to: string }
  | { kind: "search" }
  | { kind: "theme" }
  | { kind: "help" }
  | { kind: "close" }
  | { kind: "book" }
  | { kind: "linked" }
  | { kind: "respond"; answer: "taken" | "ignored" | "dismissed" }
  | { kind: "copy" }
  | { kind: "move"; by: 1 | -1 }
  | { kind: "alert" };

export interface Press {
  key: string;
  ctrlKey: boolean;
  shiftKey: boolean;
  altKey: boolean;
  metaKey: boolean;
  /** The press is in a text box (search, ledger filter): single keys belong to the box then. */
  typing: boolean;
  /** The focus is on a link, a button or another control: Enter activates it, so Enter is not ours. */
  onControl?: boolean;
}

export const SCREENS = ["/overview", "/markets", "/ledger", "/alerts", "/health"] as const;

export interface Hotkey {
  keys: string;
  does: string;
  arrives?: string; // the slice that brings it, when its feature is not built yet
}

/** The list `?` shows, in the mock-up's order. */
export const HOTKEYS: readonly Hotkey[] = [
  { keys: "Ctrl+K", does: "Search markets" },
  { keys: "Ctrl+1 … Ctrl+5", does: "Overview, Markets, Ledger, Alerts, Health" },
  { keys: "Up / Down", does: "Highlight a recommendation (Overview)" },
  { keys: "Enter", does: "Open the highlighted recommendation; with none, the newest urgent alert" },
  { keys: "Alt+Left / Alt+Right", does: "Back and forward (or the Back button)" },
  { keys: "X", does: "Show or hide the Linked panel (Market)" },
  { keys: "B", does: "The order book (Market)" },
  { keys: "T / I / D", does: "Mark the recommendation taken, ignored or dismissed (the highlighted one on the Overview)" },
  { keys: "C", does: "Copy its order (ticker, side, size, price) for Kalshi (the highlighted one on the Overview)" },
  { keys: "Ctrl+Shift+L", does: "Light or dark theme" },
  { keys: "?", does: "This list" },
  { keys: "Esc", does: "Close this list" },
];

/** The action a key press asks for, or null (the browser keeps it). Alt+Left/Right are the browser's own. */
export function actionFor(p: Press): Action | null {
  if (p.metaKey || p.altKey) return null;
  if (p.ctrlKey && !p.shiftKey) {
    if (p.key.toLowerCase() === "k") return { kind: "search" };
    const screen = SCREENS[Number(p.key) - 1];
    return /^[1-5]$/.test(p.key) && screen ? { kind: "go", to: screen } : null;
  }
  if (p.ctrlKey && p.shiftKey) return p.key.toLowerCase() === "l" ? { kind: "theme" } : null;
  if (p.key === "Escape") return { kind: "close" };
  if (p.typing) return null;
  switch (p.key) {
    case "?":
      return { kind: "help" };
    case "Enter":
      return p.onControl ? null : { kind: "alert" };
    case "b":
    case "B":
      return { kind: "book" };
    case "x":
    case "X":
      return { kind: "linked" };
    case "t":
    case "T":
      return { kind: "respond", answer: "taken" };
    case "i":
    case "I":
      return { kind: "respond", answer: "ignored" };
    case "d":
    case "D":
      return { kind: "respond", answer: "dismissed" };
    case "c":
    case "C":
      return { kind: "copy" };
    case "ArrowDown":
      return { kind: "move", by: 1 };
    case "ArrowUp":
      return { kind: "move", by: -1 };
    default:
      return null;
  }
}

/** Whether an element is a control Enter activates (a link, a button, or anything focusable). */
export function isControl(element: Element | null): boolean {
  if (!element || element.tagName === "BODY") return false;
  const tag = element.tagName;
  const role = element.getAttribute?.("role");
  return tag === "A" || tag === "BUTTON" || tag === "SUMMARY" || role === "button" || role === "link" || role === "row" || (element as HTMLElement).tabIndex >= 0;
}

/** Whether an element takes typed text (its single-key presses are not hotkeys). */
export function takesText(element: Element | null): boolean {
  if (!element) return false;
  const tag = element.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || (element as HTMLElement).isContentEditable === true;
}
