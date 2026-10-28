import { useEffect, useState, type ReactNode } from "react";
import { ApiFailure, change, get } from "../api/client";
import type { SettingsView, ShareCheckView } from "../api/contracts";
import { SKINS, applyTheme, isTheme, type Theme } from "../app/theme";
import { changedCount, changes, formOf, keyLines, restartNote, type Form } from "../view/settings";
import { Layouts } from "./Layouts";

function Row({ label, htmlFor, children }: { label: string; htmlFor?: string; children: ReactNode }) {
  return (
    <div className="setting">
      <label htmlFor={htmlFor}>{label}</label>
      <div>{children}</div>
    </div>
  );
}

export function shareLine(check: ShareCheckView): string {
  if (!check.reachable) return check.problem ?? "the share is not reachable";
  if (check.marker && check.writable) return "reachable · IKARS folder and marker found · writable";
  return `reachable · ${check.problem ?? "IKARS's folder is not set up yet"}`;
}

/** The Settings screen (the approved mock-up, docs/design/mockups/Settings.html). */
export function Settings() {
  const [view, setView] = useState<SettingsView | null>(null);
  const [form, setForm] = useState<Form | null>(null);
  const [note, setNote] = useState<string>("");
  const [share, setShare] = useState<string>("");

  useEffect(() => {
    const controller = new AbortController();
    get("/v1/settings", "SettingsView", { signal: controller.signal })
      .then((v) => {
        setView(v);
        setForm(formOf(v));
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) setNote(error instanceof ApiFailure ? error.message : "could not load the settings");
      });
    return () => controller.abort();
  }, []);

  if (!view || !form) return <p className="muted">{note || "Loading the settings…"}</p>;

  const field = (name: keyof Form, label: string, extra?: ReactNode) => (
    <Row label={label} htmlFor={`setting-${name}`}>
      <input id={`setting-${name}`} value={form[name]} onChange={(e) => setForm({ ...form, [name]: e.target.value })} />
      {extra}
    </Row>
  );

  const saved = (v: SettingsView, words: string) => {
    setView(v);
    setForm(formOf(v));
    setNote(words);
  };
  const failed = (error: unknown) => setNote(error instanceof ApiFailure ? error.message : "not saved");

  const chooseSkin = (skin: Theme) => {
    applyTheme(skin);
    change("/v1/settings", "SettingsView", { skin })
      .then((v) => setView(v))
      .catch(failed);
  };
  const save = () => {
    const found = changes(form, view);
    if ("problem" in found) return setNote(found.problem);
    if (Object.keys(found.change).length === 0) return setNote("Nothing to save.");
    change("/v1/settings", "SettingsView", found.change)
      .then((v) => saved(v, "Saved (the settings file was backed up first)."))
      .catch(failed);
  };
  const testShare = () => {
    setShare("testing…");
    change("/v1/settings/share-check", "ShareCheckView", { share: form.backup_share }, { method: "POST" })
      .then((check) => setShare(shareLine(check)))
      .catch((error: unknown) => setShare(error instanceof ApiFailure ? error.message : "could not test the share"));
  };
  const restart = () => {
    change("/v1/engine/restart", "Done", {}, { method: "POST" })
      .then((done) => {
        if (!done.done) return setNote(done.detail);
        // a new port: the window stays on the old one, so it cannot reconnect (merge review 5)
        const moved = view.restart_needed.includes("port");
        setNote(moved ? `${done.detail}; it comes back on port ${view.port}: reopen the terminal with ikars open.` : `${done.detail}; this window reconnects by itself.`);
      })
      .catch(failed);
  };
  const pending = changedCount(form, view);
  const restartWords = restartNote(view);

  return (
    <div className="grid settings">
      <h1 className="span-3">Settings</h1>

      <section className="card" aria-label="Appearance">
        <h2>Appearance</h2>
        <Row label="Skin" htmlFor="setting-skin">
          <select id="setting-skin" value={view.skin} onChange={(e) => isTheme(e.target.value) && chooseSkin(e.target.value)}>
            {SKINS.map((s) => (
              <option key={s.id} value={s.id}>
                {s.label}
              </option>
            ))}
          </select>
        </Row>
        <p className="muted small">Ctrl+Shift+L switches between Dark and Light. Saved with the engine&apos;s settings, so it survives a new browser profile.</p>
      </section>

      <section className="card" aria-label="Kalshi key">
        <h2>Kalshi key</h2>
        {keyLines(view.key).map((line) => (
          <Row key={line.label} label={line.label}>
            <span className={line.ok === null ? "" : line.ok ? "gain" : "loss"}>{line.text}</span>
          </Row>
        ))}
        <p className="muted small">IKARS never shows a key, its id or its path. Change them in Windows&apos; user environment variables, then restart the engine.</p>
      </section>

      <section className="card" aria-label="Alerts settings">
        <h2>Alerts</h2>
        {field("quiet_start", "Quiet hours from")}
        {field("quiet_end", "Quiet hours to")}
        {field("throttle_minutes", "Same alert again after (minutes)")}
        {field("digest_minutes", "Digest every (minutes)")}
        <p className="muted small">In quiet hours only urgent alerts pop up; the rest wait for the digest.</p>
      </section>

      <section className="card" aria-label="Ledger and backups">
        <h2>Ledger and backups</h2>
        {field("tax_time_zone", "Tax time zone", <div className="muted small">empty: the workstation&apos;s ({view.tax_zone_effective})</div>)}
        {field(
          "backup_share",
          "Backup share",
          <div className="small">
            <button type="button" className="link" onClick={testShare}>
              Test the share
            </button>{" "}
            <span data-testid="share-status">{share}</span>
          </div>,
        )}
      </section>

      <section className="card" aria-label="Feed and storage">
        <h2>Feed and storage</h2>
        {field("watched_max_markets", "Watched markets (books)")}
        {field("read_share_percent", "Share of Kalshi's read budget (%)")}
        {field("storage_budget_gb", "Storage budget for recordings (GB)")}
      </section>

      <section className="card" aria-label="Terminal settings">
        <h2>Terminal</h2>
        {field("port", "Port")}
        {field("session_days", "Stay signed in for (days)")}
        <Row label="Layouts">
          <Layouts />
        </Row>
      </section>

      <div className="span-3 actions">
        <button type="button" className="primary" onClick={save}>
          Save
        </button>
        <button type="button" onClick={() => saved(view, "Changes discarded.")}>
          Discard changes
        </button>
        <span className="muted small" data-testid="settings-note">
          {note || (pending ? `${pending} change${pending === 1 ? "" : "s"} not saved.` : "")} {restartWords}
        </span>
        <button type="button" className="push" onClick={restart}>
          Restart the engine
        </button>
      </div>
    </div>
  );
}
