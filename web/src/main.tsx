import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { TerminalContext } from "./app/hooks";
import { answerAsks, placeFrom } from "./app/layout";
import { startTerminal } from "./app/terminal";
import { App } from "./screens/App";
import "./styles.css";

const root = document.getElementById("root");
if (root) {
  placeFrom(window.location.hash); // a layout's window: to its place, before signing in clears it
  startTerminal()
    .then((terminal) => {
      // another window saving a layout asks where this one is (M1.8b-2); the Update window is
      // none of them, and must not reload on its own announcement (the owner's 0.1.4 update)
      if (window.location.pathname !== "/update") answerAsks();
      createRoot(root).render(
        <StrictMode>
          <TerminalContext value={terminal}>
            <App />
          </TerminalContext>
        </StrictMode>,
      );
    })
    .catch((error: unknown) => {
      // never a blank window (M1.8a-web-1b merge review 1): say so, as text
      const note = document.createElement("p");
      note.className = "fatal";
      note.textContent = `IKARS could not start: ${error instanceof Error ? error.message : String(error)}. Reopen it with ikars open.`;
      root.replaceChildren(note);
    });
}
