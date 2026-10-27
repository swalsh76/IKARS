import { useEffect, useState } from "react";
import { ApiFailure, change, get } from "../api/client";
import type { LayoutsView } from "../api/contracts";
import { gather } from "../app/layout";
import { layoutLine } from "../view/settings";

/** Saved layouts in Settings (M1.8b-2): save the open windows, choose the one ikars open restores,
 * delete one. */
export function Layouts() {
  const [view, setView] = useState<LayoutsView | null>(null);
  const [name, setName] = useState("");
  const [note, setNote] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    get("/v1/layouts", "LayoutsView", { signal: controller.signal })
      .then(setView)
      .catch((error: unknown) => {
        if (!controller.signal.aborted) setNote(error instanceof ApiFailure ? error.message : "could not load the layouts");
      });
    return () => controller.abort();
  }, []);

  const failed = (error: unknown) => setNote(error instanceof ApiFailure ? error.message : "not saved");
  const save = () => {
    const wanted = name.trim();
    if (!wanted) return setNote("Name the layout first.");
    setNote("Asking the open windows where they are…");
    gather()
      .then((windows) => change("/v1/layouts", "LayoutsView", { name: wanted, windows }))
      .then((v) => {
        setView(v);
        setName("");
        const saved = v.layouts.find((l) => l.name.toLowerCase() === wanted.toLowerCase());
        setNote(`Saved ${wanted} (${saved ? layoutLine(saved) : "its windows"}); ikars open restores it.`);
      })
      .catch(failed);
  };
  const startWith = (layout: string) => {
    change("/v1/layouts/start", "LayoutsView", { name: layout })
      .then((v) => {
        setView(v);
        setNote(layout ? `ikars open restores ${layout}.` : "ikars open opens one window.");
      })
      .catch(failed);
  };
  const remove = (layout: string) => {
    change(`/v1/layouts/${encodeURIComponent(layout)}`, "LayoutsView", {}, { method: "DELETE" })
      .then((v) => {
        setView(v);
        setNote(`Deleted ${layout}.`);
      })
      .catch(failed);
  };

  return (
    <div className="layouts">
      {view && (
        <ul>
          {view.layouts.map((layout) => (
            <li key={layout.name}>
              <label>
                <input type="radio" name="start-layout" checked={view.start === layout.name} onChange={() => startWith(layout.name)} /> {layout.name}
              </label>{" "}
              <span className="muted small">{layoutLine(layout)}</span>{" "}
              <button type="button" className="link" onClick={() => remove(layout.name)} aria-label={`Delete ${layout.name}`}>
                Delete
              </button>
            </li>
          ))}
          <li>
            <label>
              <input type="radio" name="start-layout" checked={view.start === ""} onChange={() => startWith("")} /> One window
            </label>{" "}
            <span className="muted small">where Edge puts it</span>
          </li>
        </ul>
      )}
      <div className="layout-save">
        <input id="layout-name" aria-label="Layout name" placeholder="Name, like Trading" maxLength={40} value={name} onChange={(e) => setName(e.target.value)} />
        <button type="button" className="link" onClick={save}>
          Save the open windows
        </button>
      </div>
      <p className="muted small" data-testid="layout-note">
        {note || "The chosen one opens with ikars open; ikars open --layout NAME opens another."}
      </p>
    </div>
  );
}
