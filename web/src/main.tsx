import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { TerminalContext } from "./app/hooks";
import { answerAsks } from "./app/layout";
import { startTerminal } from "./app/terminal";
import { App } from "./screens/App";
import "./styles.css";

const root = document.getElementById("root");
if (root) {
  startTerminal()
    .then((terminal) => {
      answerAsks(); // another window saving a layout asks where this one is (M1.8b-2)
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
