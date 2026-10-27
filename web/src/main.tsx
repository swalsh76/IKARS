import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { TerminalContext } from "./app/hooks";
import { startTerminal } from "./app/terminal";
import { App } from "./screens/App";
import "./styles.css";

const root = document.getElementById("root");
if (root) {
  void startTerminal().then((terminal) => {
    createRoot(root).render(
      <StrictMode>
        <TerminalContext value={terminal}>
          <App />
        </TerminalContext>
      </StrictMode>,
    );
  });
}
