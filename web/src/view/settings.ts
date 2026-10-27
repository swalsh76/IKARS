// What the Settings screen and the first-run wizard show and send (M1.8b-1): plain functions, tested
// without a browser. The engine checks every value again; these only shape what the owner sees.

import type { KeyStatus, SettingsChange, SettingsView } from "../api/contracts";

/** The form's fields, as typed (text boxes hold text). */
export interface Form {
  quiet_start: string;
  quiet_end: string;
  throttle_minutes: string;
  digest_minutes: string;
  tax_time_zone: string;
  backup_share: string;
  watched_max_markets: string;
  read_share_percent: string;
  storage_budget_gb: string;
  port: string;
  session_days: string;
}

const NUMBERS = [
  "throttle_minutes",
  "digest_minutes",
  "watched_max_markets",
  "read_share_percent",
  "storage_budget_gb",
  "port",
  "session_days",
] as const;
const TEXTS = ["quiet_start", "quiet_end", "tax_time_zone", "backup_share"] as const;

/** The form as the engine's settings stand. */
export function formOf(v: SettingsView): Form {
  return {
    quiet_start: v.quiet_start,
    quiet_end: v.quiet_end,
    throttle_minutes: String(v.throttle_minutes),
    digest_minutes: String(v.digest_minutes),
    tax_time_zone: v.tax_time_zone,
    backup_share: v.backup_share,
    watched_max_markets: String(v.watched_max_markets),
    read_share_percent: String(v.read_share_percent),
    storage_budget_gb: String(v.storage_budget_gb),
    port: String(v.port),
    session_days: String(v.session_days),
  };
}

/** Only what the owner changed, ready to send; or the first field that is not a whole number. */
export function changes(form: Form, v: SettingsView): { change: SettingsChange } | { problem: string } {
  const change: Record<string, string | number> = {};
  for (const name of TEXTS) {
    if (form[name].trim() !== v[name]) change[name] = form[name].trim();
  }
  for (const name of NUMBERS) {
    const text = form[name].trim();
    if (!/^\d+$/.test(text)) return { problem: `${name.replaceAll("_", " ")} must be a whole number` };
    if (Number(text) !== v[name]) change[name] = Number(text);
  }
  return { change: change as SettingsChange };
}

export function changedCount(form: Form, v: SettingsView): number {
  const found = changes(form, v);
  return "change" in found ? Object.keys(found.change).length : 1;
}

export interface KeyLine {
  label: string;
  ok: boolean | null;
  text: string;
}

/** The Kalshi key as yes or no only (never a key, its id or its path). */
export function keyLines(k: KeyStatus): KeyLine[] {
  const restart = k.needs_restart ? " (set in Windows since the engine started: restart the engine to use it)" : "";
  const file = !k.path_set
    ? "the path's variable is not set"
    : !k.file_found
      ? "the variable is set but no key file is there"
      : k.owner_only === null
        ? "set · file found"
        : k.owner_only
          ? "set · file found · readable by you only"
          : "set · file found · other accounts can read it";
  return [
    { label: "IKARS_KALSHI_KEY_ID", ok: k.id_set, text: k.id_set ? "set" : "not set" },
    { label: "IKARS_KALSHI_PRIVATE_KEY_PATH", ok: k.path_set && k.file_found && k.owner_only !== false, text: file },
    { label: "Environment", ok: null, text: `${k.environment === "production" ? "Production" : "Demo"} (read-only key)${restart}` },
  ];
}

/** Whether the key is ready for the engine to use. */
export function keyReady(k: KeyStatus): boolean {
  return k.id_set && k.path_set && k.file_found;
}

const NAMES: Record<string, string> = {
  tax_time_zone: "the tax zone",
  backup_share: "the share",
  watched_max_markets: "watched markets",
  read_share_percent: "the read budget",
  storage_budget_gb: "the storage budget",
  port: "the port",
  session_days: "the session length",
};

/** Which saved changes wait for an engine restart, in words; null when none do. */
export function restartNote(v: SettingsView): string | null {
  if (v.restart_needed.length === 0) return null;
  const names = v.restart_needed.map((n) => NAMES[n] ?? n);
  return `${names.join(", ")} ${names.length === 1 ? "applies" : "apply"} after an engine restart.`;
}
