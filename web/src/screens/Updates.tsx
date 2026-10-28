import { useEffect, useState } from "react";
import { ApiFailure, change, get } from "../api/client";
import type { UpdateView } from "../api/contracts";
import { canApply, updateLine } from "../view/updates";

const CHECK_WAIT_MS = 600_000; // a check downloads a new release (about 65 MB)

/** The program's version and updates, on Health and Settings (M1.9b; installed only on a click). */
export function Updates() {
  const [view, setView] = useState<UpdateView | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    get("/v1/update", "UpdateView", { signal: controller.signal })
      .then(setView)
      .catch((error: unknown) => {
        if (!controller.signal.aborted) setNote(error instanceof ApiFailure ? error.message : "could not read the version");
      });
    return () => controller.abort();
  }, []);

  const failed = (error: unknown) => setNote(error instanceof ApiFailure ? error.message : "not done");
  const check = () => {
    setBusy(true);
    setNote("Checking for updates…");
    change("/v1/update/check", "UpdateView", {}, { method: "POST", timeoutMs: CHECK_WAIT_MS })
      .then((v) => {
        setView(v);
        setNote("");
      })
      .catch(failed)
      .finally(() => setBusy(false));
  };
  const apply = () => {
    setBusy(true);
    change("/v1/update/apply", "Done", {}, { method: "POST" })
      .then((done) => setNote(done.done ? `${done.detail}; this window reconnects when it is back.` : done.detail))
      .catch(failed)
      .finally(() => setBusy(false));
  };

  return (
    <div className="updates">
      <span className="small" data-testid="update-line">
        {view ? updateLine(view) : note || "Reading the version…"}
      </span>{" "}
      {view?.installed && (
        <button type="button" className="link" onClick={check} disabled={busy}>
          Check now
        </button>
      )}{" "}
      {view && canApply(view) && (
        <button type="button" className="link" onClick={apply} disabled={busy}>
          Restart to update to {view.available}
        </button>
      )}
      {view && note && <div className="muted small">{note}</div>}
    </div>
  );
}
