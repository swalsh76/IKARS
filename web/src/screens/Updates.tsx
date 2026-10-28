import { useEffect, useState } from "react";
import { ApiFailure, change, get } from "../api/client";
import type { UpdateView } from "../api/contracts";
import { openUpdateWindow } from "../app/layout";
import { canApply, updateParts } from "../view/updates";

const CHECK_WAIT_MS = 600_000; // a check downloads a new release (about 65 MB)
const POLL_MS = 500; // while checking: the download's progress

/** The program's version and updates, on Health and Settings (M1.9b; installed only on a click).
 * Restart to update opens the IKARS Update window, which shows the whole update. */
export function Updates() {
  const [view, setView] = useState<UpdateView | null>(null);
  const [note, setNote] = useState("");
  const [checking, setChecking] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    get("/v1/update", "UpdateView", { signal: controller.signal })
      .then(setView)
      .catch((error: unknown) => {
        if (!controller.signal.aborted) setNote(error instanceof ApiFailure ? error.message : "could not read the version");
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!checking) return;
    const timer = window.setInterval(() => {
      get("/v1/update", "UpdateView").then(setView).catch(() => undefined);
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [checking]);

  const check = () => {
    setChecking(true);
    setNote("");
    change("/v1/update/check", "UpdateView", {}, { method: "POST", timeoutMs: CHECK_WAIT_MS })
      .then(setView)
      .catch((error: unknown) => setNote(error instanceof ApiFailure ? error.message : "the check failed"))
      .finally(() => setChecking(false));
  };

  const parts = view ? updateParts(view) : null;
  const downloading = view?.state === "downloading";
  return (
    <div className="updates">
      <div className="update-version" data-testid="update-line">
        {parts ? parts.version : note || "Reading the version…"}
      </div>
      {parts && <div className="muted small">{checking && !downloading ? "Checking GitHub for a newer version…" : parts.status}</div>}
      {downloading && (
        <div className="bar" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={view.progress ?? 0}>
          <div className="fill" style={{ width: `${view.progress ?? 0}%` }} />
        </div>
      )}
      {view?.installed && (
        <div className="update-actions">
          <button type="button" onClick={check} disabled={checking}>
            {checking ? "Checking…" : "Check now"}
          </button>
          {canApply(view) && view.available && (
            <button type="button" className="primary" onClick={() => view.available && openUpdateWindow(view.available)} disabled={checking}>
              Restart to update to {view.available}
            </button>
          )}
        </div>
      )}
      {view && note && <div className="muted small">{note}</div>}
    </div>
  );
}
