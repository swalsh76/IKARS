import { useEffect, useState } from "react";
import { ApiFailure, change, get } from "../api/client";
import type { UpdateView } from "../api/contracts";
import { canApply, updateParts, updatedTo } from "../view/updates";

const CHECK_WAIT_MS = 600_000; // a check downloads a new release (about 65 MB)
const POLL_MS = 2_000; // while updating: is the new version answering yet?
const GIVE_UP_MS = 180_000;

/** The program's version and updates, on Health and Settings (M1.9b; installed only on a click).
 * While an update installs it says so plainly, and reloads this window into the new version. */
export function Updates() {
  const [view, setView] = useState<UpdateView | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState<"" | "checking" | "updating">("");
  const [target, setTarget] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    get("/v1/update", "UpdateView", { signal: controller.signal })
      .then(setView)
      .catch((error: unknown) => {
        if (!controller.signal.aborted) setNote(error instanceof ApiFailure ? error.message : "could not read the version");
      });
    return () => controller.abort();
  }, []);

  // updating: ask until the new version answers, then reload into it (its own terminal files)
  useEffect(() => {
    if (target === null) return;
    const began = Date.now();
    const timer = window.setInterval(() => {
      if (Date.now() - began > GIVE_UP_MS) {
        window.clearInterval(timer);
        setNote("IKARS has not come back yet: open it from the Start menu.");
        return;
      }
      get("/v1/update", "UpdateView", { timeoutMs: POLL_MS })
        .then((v) => {
          if (updatedTo(v, target)) window.location.reload();
        })
        .catch(() => undefined); // the engine is stopped or starting: ask again
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [target]);

  const failed = (error: unknown) => setNote(error instanceof ApiFailure ? error.message : "not done");
  const check = () => {
    setBusy("checking");
    setNote("Checking GitHub for a newer version, and downloading it if there is one (about 65 MB)…");
    change("/v1/update/check", "UpdateView", {}, { method: "POST", timeoutMs: CHECK_WAIT_MS })
      .then((v) => {
        setView(v);
        setNote("");
      })
      .catch(failed)
      .finally(() => setBusy(""));
  };
  const apply = () => {
    if (!view?.available) return;
    const wanted = view.available;
    setBusy("updating");
    change("/v1/update/apply", "Done", {}, { method: "POST" })
      .then((done) => {
        if (!done.done) {
          setBusy("");
          setNote(done.detail);
          return;
        }
        setTarget(wanted);
      })
      .catch((error: unknown) => {
        setBusy("");
        failed(error);
      });
  };

  if (busy === "updating") {
    return (
      <div className="updates updating" role="status" data-testid="update-progress">
        <div className="update-version">Updating to {view?.available ?? "the new version"}…</div>
        <ol className="small">
          <li>The engine stops.</li>
          <li>The new version installs (the data folder is never touched).</li>
          <li>IKARS starts again and this window reloads by itself.</li>
        </ol>
        <div className="muted small">{note || "About half a minute. Please leave this window open."}</div>
      </div>
    );
  }
  const parts = view ? updateParts(view) : null;
  return (
    <div className="updates">
      <div className="update-version" data-testid="update-line">
        {parts ? parts.version : note || "Reading the version…"}
      </div>
      {parts && <div className="muted small">{busy === "checking" ? note : parts.status}</div>}
      {view?.installed && (
        <div className="update-actions">
          <button type="button" onClick={check} disabled={busy !== ""}>
            {busy === "checking" ? "Checking…" : "Check now"}
          </button>
          {canApply(view) && (
            <button type="button" className="primary" onClick={apply} disabled={busy !== ""}>
              Restart to update to {view.available}
            </button>
          )}
        </div>
      )}
      {view && busy === "" && note && <div className="muted small">{note}</div>}
    </div>
  );
}
