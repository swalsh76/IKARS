import { useEffect, useState } from "react";
import { ApiFailure, change, get } from "../api/client";
import type { SettingsView } from "../api/contracts";
import { useTerminal, useValue } from "../app/hooks";
import { keyLines, keyReady } from "../view/settings";
import { shareLine } from "./Settings";

const STEPS = ["Kalshi key", "Engine and feed", "Backups", "Tax zone and quiet hours", "Done"] as const;

/** The first-run wizard (the approved mock-up, docs/design/mockups/Wizard.html): opens when there are
 * no settings yet; each step checks what it can and says what to do. Nothing is sent anywhere. */
export function Welcome() {
  const { navigator } = useTerminal();
  const health = useValue("health");
  const [step, setStep] = useState(0);
  const [view, setView] = useState<SettingsView | null>(null);
  const [share, setShare] = useState("");
  const [shareNote, setShareNote] = useState("");
  const [zone, setZone] = useState("");
  const [quiet, setQuiet] = useState({ start: "22:00", end: "07:00" });
  const [note, setNote] = useState("");

  const load = () =>
    get("/v1/settings", "SettingsView")
      .then((v) => {
        setView(v);
        setShare(v.backup_share);
        setZone(v.tax_time_zone);
        setQuiet({ start: v.quiet_start, end: v.quiet_end });
      })
      .catch((error: unknown) => setNote(error instanceof ApiFailure ? error.message : "could not load the settings"));
  useEffect(() => {
    void load();
  }, []);

  const failed = (error: unknown) => setNote(error instanceof ApiFailure ? error.message : "not saved");
  const next = () => {
    setNote("");
    setStep((s) => Math.min(s + 1, STEPS.length - 1));
  };

  if (!view) return <p className="muted">{note || "Loading…"}</p>;

  const testShare = () =>
    change("/v1/settings/share-check", "ShareCheckView", { share }, { method: "POST" })
      .then((c) => setShareNote(shareLine(c)))
      .catch(failed);
  // the share is set up first and saved only once IKARS's folder and marker are there, so a share
  // that cannot be used is never left as the backup target (M1.8b-1 merge review 3)
  const saveShare = () =>
    change("/v1/settings/share-init", "ShareCheckView", { share: share.trim() }, { method: "POST" })
      .then(async (c) => {
        setShareNote(shareLine(c));
        if (!c.marker) return;
        await change("/v1/settings", "SettingsView", { backup_share: share.trim() });
        next();
      })
      .catch(failed);
  const saveZone = () =>
    change("/v1/settings", "SettingsView", { tax_time_zone: zone.trim(), quiet_start: quiet.start.trim(), quiet_end: quiet.end.trim() })
      .then((v) => {
        setView(v);
        next();
      })
      .catch(failed);
  const autostart = () =>
    change("/v1/engine/autostart", "Done", {}, { method: "POST" })
      .then((d) => {
        setNote(d.detail);
        void load();
      })
      .catch(failed);

  return (
    <div className="wizard">
      <h1>Set up IKARS</h1>
      <p className="muted">IKARS opens this once, when it finds no settings. Each step checks what it can and says what to do; nothing is sent anywhere, and IKARS never shows a key.</p>
      <ol className="steps" aria-label="Steps">
        {STEPS.map((name, i) => (
          <li key={name} className={i < step ? "done" : i === step ? "now" : ""} aria-current={i === step ? "step" : undefined}>
            {i + 1} · {name}
            {i < step ? " ✓" : ""}
          </li>
        ))}
      </ol>
      <section className="card" aria-label={STEPS[step]}>
        <h2>
          {step + 1} · {STEPS[step]}
        </h2>
        {step === 0 && (
          <>
            {keyLines(view.key).map((line) => (
              <div key={line.label} className="check">
                <span className={line.ok === null ? "" : line.ok ? "gain" : "loss"}>●</span> {line.label}: {line.text}
              </div>
            ))}
            {!keyReady(view.key) && (
              <p className="muted small">Set the two user environment variables (System settings, Environment Variables), sign out and in, then check again.</p>
            )}
            <div className="actions">
              <button type="button" onClick={() => void load()}>
                Check again
              </button>
              <button type="button" className="primary push" onClick={next}>
                Continue
              </button>
            </div>
          </>
        )}
        {step === 1 && (
          <>
            <div className="check">
              <span className={health?.feed.connected ? "gain" : "loss"}>●</span> The engine runs and its Kalshi feed is {health?.feed.connected ? "connected" : "not connected yet"}
            </div>
            <div className="check">
              <span className={health?.clock_status === "ok" ? "gain" : ""}>●</span> The clock is {health?.clock_status ?? "not measured yet"}
            </div>
            <div className="check">
              <span className={health?.ledger.verified ? "gain" : ""}>●</span> The ledger is {health?.ledger.verified ? "verified" : "not checked yet"}
            </div>
            <div className="actions">
              <button type="button" onClick={() => setStep(0)}>
                Back
              </button>
              <button type="button" className="primary push" onClick={next}>
                Continue
              </button>
            </div>
          </>
        )}
        {step === 2 && (
          <>
            <p className="muted small">The ledger and the tax packages are copied each night to a share, in its own ikars\ folder with an IKARS marker. You can skip this and set it later in Settings.</p>
            <label htmlFor="wizard-share">Share</label>
            <input id="wizard-share" value={share} onChange={(e) => setShare(e.target.value)} />
            <div className="small" data-testid="wizard-share-status">
              {shareNote}
            </div>
            <div className="actions">
              <button type="button" onClick={() => setStep(1)}>
                Back
              </button>
              <button type="button" onClick={() => void testShare()} disabled={!share.trim()}>
                Test
              </button>
              <button type="button" onClick={next}>
                Skip for now
              </button>
              <button type="button" className="primary push" onClick={() => void saveShare()} disabled={!share.trim()}>
                Save and continue
              </button>
            </div>
          </>
        )}
        {step === 3 && (
          <>
            <label htmlFor="wizard-zone">Tax time zone (empty: the workstation&apos;s, {view.tax_zone_effective})</label>
            <input id="wizard-zone" value={zone} onChange={(e) => setZone(e.target.value)} />
            <label htmlFor="wizard-quiet-start">Quiet hours from</label>
            <input id="wizard-quiet-start" value={quiet.start} onChange={(e) => setQuiet({ ...quiet, start: e.target.value })} />
            <label htmlFor="wizard-quiet-end">Quiet hours to</label>
            <input id="wizard-quiet-end" value={quiet.end} onChange={(e) => setQuiet({ ...quiet, end: e.target.value })} />
            <div className="actions">
              <button type="button" onClick={() => setStep(2)}>
                Back
              </button>
              <button type="button" className="primary push" onClick={() => void saveZone()}>
                Save and continue
              </button>
            </div>
          </>
        )}
        {step === 4 && (
          <>
            <div className="check">
              <span className={keyReady(view.key) ? "gain" : "loss"}>●</span> Kalshi key {keyReady(view.key) ? "ready" : "not ready: see Settings"}
            </div>
            <div className="check">
              <span className={view.backup_share ? "gain" : ""}>●</span> Backups {view.backup_share ? "to the share" : "skipped (set them in Settings)"}
            </div>
            <div className="check">
              <span className="gain">●</span> Tax zone {view.tax_zone_effective}; quiet hours {view.quiet_start} to {view.quiet_end}
            </div>
            <div className="check">
              <span className={view.autostart_installed ? "gain" : ""}>●</span> Start at sign-in {view.autostart_installed ? "installed" : "not installed"}
            </div>
            <div className="actions">
              {!view.autostart_installed && (
                <button type="button" onClick={() => void autostart()}>
                  Start at sign-in
                </button>
              )}
              <button type="button" className="primary push" onClick={() => navigator.go("/overview")}>
                Open the Overview
              </button>
            </div>
          </>
        )}
        {note && (
          <p className="muted small" data-testid="wizard-note">
            {note}
          </p>
        )}
      </section>
    </div>
  );
}
