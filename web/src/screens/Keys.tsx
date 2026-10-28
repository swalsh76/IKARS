import { useEffect, useRef, useState } from "react";
import { useTerminal } from "../app/hooks";
import { HOTKEYS, actionFor, isControl, takesText } from "../app/keys";
import { change } from "../api/client";
import { applyTheme, currentTheme, toggled } from "../app/theme";

export const LINKED_EVENT = "ikars:linked"; // X: the Market screen shows or hides its Linked panel
export const BOOK_ID = "order-book"; // B: the Market screen's order book
export const RESPOND_EVENT = "ikars:respond"; // T, I, D: the Recommendation screen's answer
export const COPY_EVENT = "ikars:copy"; // C: the Recommendation screen copies its order

/** The hotkeys, for the whole terminal, and the list `?` shows (a dialog; Esc or a click outside closes). */
export function Hotkeys() {
  const { navigator } = useTerminal();
  const [open, setOpen] = useState(false);
  const shown = useRef(false);
  useEffect(() => {
    shown.current = open;
  }, [open]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const action = actionFor({
        key: event.key,
        ctrlKey: event.ctrlKey,
        shiftKey: event.shiftKey,
        altKey: event.altKey,
        metaKey: event.metaKey,
        typing: takesText(document.activeElement),
        onControl: isControl(document.activeElement),
      });
      if (action === null) return;
      if (action.kind === "close" && !shown.current) return; // Esc is the box's or the browser's then
      event.preventDefault(); // Edge's own Ctrl+K and Ctrl+1..5 are not wanted in the terminal
      switch (action.kind) {
        case "go":
          navigator.go(action.to);
          break;
        case "search": {
          const box = document.getElementById("search") as HTMLInputElement | null;
          box?.focus();
          box?.select();
          break;
        }
        case "theme": {
          const skin = toggled(currentTheme(), window.matchMedia("(prefers-color-scheme: light)").matches);
          applyTheme(skin);
          change("/v1/settings", "SettingsView", { skin }).catch(() => undefined); // remembered when it can be
          break;
        }
        case "help":
          setOpen(true);
          break;
        case "close":
          setOpen(false);
          break;
        case "book": {
          const book = document.getElementById(BOOK_ID);
          book?.scrollIntoView({ block: "center" });
          book?.focus();
          break;
        }
        case "linked":
          window.dispatchEvent(new CustomEvent(LINKED_EVENT));
          break;
        case "respond":
          window.dispatchEvent(new CustomEvent(RESPOND_EVENT, { detail: action.answer }));
          break;
        case "copy":
          window.dispatchEvent(new CustomEvent(COPY_EVENT));
          break;
        case "alert":
          navigator.go("/alerts");
          break;
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [navigator]);

  if (!open) return null;
  return (
    <div className="overlay" onClick={() => setOpen(false)}>
      <div role="dialog" aria-modal="true" aria-label="Hotkeys" className="card dialog" onClick={(e) => e.stopPropagation()}>
        <h2>Hotkeys</h2>
        <table>
          <tbody>
            {HOTKEYS.map((h) => (
              <tr key={h.keys}>
                <td className="nowrap">
                  <kbd>{h.keys}</kbd>
                </td>
                <td className={h.arrives ? "muted" : ""}>
                  {h.does}
                  {h.arrives && ` (arrives in ${h.arrives})`}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
