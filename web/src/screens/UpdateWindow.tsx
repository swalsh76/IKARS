import { useEffect, useRef, useState } from "react";
import { ApiFailure, change, get } from "../api/client";
import type { UpdateView } from "../api/contracts";
import { announceReload } from "../app/layout";
import { clock, downloadText, steps, updatedTo, type Phase, type PHASES } from "../view/updates";

const CHECK_WAIT_MS = 600_000; // the download (about 65 MB, less for a delta)
const POLL_MS = 1_000;
const GIVE_UP_MS = 180_000; // from the engine's stop to the new version answering
const CLOSE_AFTER_S = 5; // "Update complete", then a countdown to closing

const now = () => new Date().toISOString().slice(11, 19);
const sleep = (ms: number) => new Promise((done) => setTimeout(done, ms));

/** The IKARS Update window (M1.9b; the owner's approved design): the download with its bar, then
 * each step ticked as it is seen, the elapsed time, and Details (the engine's steps and this
 * window's). The engine is stopped while Velopack installs, so those steps are seen from here:
 * the engine stops answering, then the new version answers; every IKARS window then reloads. */
export function UpdateWindow({ to }: { to: string }) {
  const [phase, setPhase] = useState<Phase>("download");
  const [failedAt, setFailedAt] = useState<(typeof PHASES)[number] | null>(null);
  const [view, setView] = useState<UpdateView | null>(null);
  const [log, setLog] = useState<string[]>([]);
  const [problem, setProblem] = useState("");
  const [since, setSince] = useState<number>(() => Date.now());
  const [nowMs, setNowMs] = useState<number>(() => Date.now()); // the clock, a second at a time
  const started = useRef(false);
  const [closingIn, setClosingIn] = useState<number | null>(null);
  const [stayed, setStayed] = useState(false); // Edge would not let it close: the owner closes it

  useEffect(() => {
    if (closingIn === null) return;
    if (closingIn <= 0) {
      window.close();
      const check = window.setTimeout(() => setStayed(true), 500);
      return () => window.clearTimeout(check);
    }
    const next = window.setTimeout(() => setClosingIn(closingIn - 1), 1_000);
    return () => window.clearTimeout(next);
  }, [closingIn]);

  useEffect(() => {
    const timer = window.setInterval(() => setNowMs(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    if (started.current) return; // once, whatever React does with effects
    started.current = true;
    const note = (text: string) => setLog((lines) => [...lines, `${now()} ${text}`]);
    const fail = (at: (typeof PHASES)[number], text: string) => {
      setFailedAt(at);
      setPhase("failed");
      setProblem(text);
      note(text);
    };
    const words = (error: unknown) => (error instanceof ApiFailure ? error.message : String(error));

    void (async () => {
      let current: UpdateView;
      try {
        current = await get("/v1/update", "UpdateView");
      } catch (error) {
        return fail("download", `the engine did not answer: ${words(error)}`);
      }
      setView(current);
      if (current.installed && current.current === to && current.state !== "ready") {
        setPhase("done"); // opened again after the update: nothing to do, never a fresh start
        setClosingIn(CLOSE_AFTER_S);
        return;
      }
      if (!current.installed) {
        setPhase("cannot");
        setProblem("This copy of IKARS is a development copy: it cannot update itself.");
        return;
      }
      if (current.state !== "ready") {
        note("downloading the new version");
        const polling = window.setInterval(() => {
          get("/v1/update", "UpdateView").then(setView).catch(() => undefined);
        }, POLL_MS / 2);
        try {
          current = await change("/v1/update/check", "UpdateView", {}, { method: "POST", timeoutMs: CHECK_WAIT_MS });
        } catch (error) {
          return fail("download", `the download failed: ${words(error)}`);
        } finally {
          window.clearInterval(polling);
        }
        setView(current);
        if (current.state !== "ready" || current.available === null) {
          return fail("download", current.problem ?? "no newer version was found");
        }
      }
      const target = current.available ?? to;
      setPhase("stop");
      setSince(Date.now());
      let done;
      try {
        done = await change("/v1/update/apply", "Done", {}, { method: "POST" });
      } catch (error) {
        return fail("stop", `the engine refused: ${words(error)}`);
      }
      if (!done.done) return fail("stop", done.detail);
      note(done.detail);
      // seen from here: the engine stops answering, then the new version answers
      const began = Date.now();
      let stopped = false;
      while (Date.now() - began < GIVE_UP_MS) {
        await sleep(POLL_MS);
        try {
          const answer = await get("/v1/update", "UpdateView", { timeoutMs: POLL_MS });
          if (updatedTo(answer, target)) {
            setView(answer);
            note(`IKARS ${target} answers`);
            setPhase("reload");
            announceReload();
            note("every IKARS window reloads");
            setPhase("done");
            setClosingIn(CLOSE_AFTER_S);
            return;
          }
        } catch {
          if (!stopped) {
            stopped = true;
            note("the engine stopped; Velopack is installing");
            setPhase("install");
            setSince(Date.now());
          }
        }
        if (stopped && Date.now() - began > 20_000) setPhase((p) => (p === "install" ? "start" : p));
      }
      fail(stopped ? "start" : "stop", "IKARS has not come back in three minutes: open it from the Start menu");
    })();
  }, [to]);

  const all = [...(view?.events ?? []), ...log].sort();
  const from = view?.current ?? "?";
  const heading =
    phase === "done" ? "Update complete" : phase === "cannot" ? "IKARS cannot update here" : `Updating IKARS ${from} → ${view?.available ?? to}`;
  const elapsed = clock((nowMs - since) / 1000);

  return (
    <main className="update-window" aria-label="IKARS Update">
      <h1>{heading}</h1>
      {phase !== "cannot" && (
        <ol className="update-steps">
          {steps(phase, failedAt).map((step, i) => (
            <li key={step.label} className={step.mark} aria-current={step.mark === "now" ? "step" : undefined}>
              <span className="mark" aria-hidden="true">
                {step.mark === "done" ? "✓" : step.mark === "now" ? "●" : "○"}
              </span>
              {step.label}
              {i === 0 && view && view.size_bytes !== null && <span className="muted small"> · {downloadText(view)}</span>}
              {step.mark === "now" && phase !== "failed" && (
                <div className="bar" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={i === 0 ? (view?.progress ?? 0) : undefined}>
                  <div className={i === 0 ? "fill" : "fill moving"} style={i === 0 ? { width: `${view?.progress ?? 0}%` } : undefined} />
                </div>
              )}
              {step.mark === "now" && i > 0 && phase !== "failed" && <span className="muted small"> {elapsed}</span>}
            </li>
          ))}
        </ol>
      )}
      {problem && <p className={phase === "cannot" ? "muted" : "loss"}>{problem}</p>}
      {phase === "done" && (
        <p className="gain" role="status">
          IKARS {to} is installed and running.{" "}
          {stayed ? "You can close this window." : closingIn !== null && closingIn > 0 ? `This window closes in ${closingIn} s.` : ""}
        </p>
      )}
      {phase === "done" && stayed && (
        <div className="update-actions">
          <button type="button" onClick={() => window.close()}>
            Close
          </button>
        </div>
      )}
      <p className="muted small">Your data folder is never touched. Please leave IKARS's windows open.</p>
      <details>
        <summary>Details</summary>
        <ul className="update-log small">
          {all.map((line, i) => (
            <li key={`${i}:${line}`}>{line}</li>
          ))}
        </ul>
      </details>
    </main>
  );
}
