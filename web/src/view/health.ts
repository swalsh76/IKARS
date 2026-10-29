// What the Health screen and the footer strip say, from the engine's typed health (contracts 2.0).
// Plain functions, so they are tested without a browser; the screens only draw them.

import type { GapView, HealthView } from "../api/contracts";
import type { StreamState } from "../api/stream";
import type { Reading } from "../api/meter";

export type Level = "ok" | "watch" | "problem" | "unknown";

export interface Card {
  title: string;
  level: Level;
  state: string;
  big: string;
  lines: string[];
  action?: "backup"; // the card carries Back up now (M1.11)
}


const STATE: Record<Level, string> = { ok: "OK", watch: "WATCH", problem: "PROBLEM", unknown: "NOT YET" };

function card(title: string, level: Level, big: string, lines: string[], state = STATE[level]): Card {
  return { title, level, state, big, lines };
}

function seconds(value: number | null): string {
  if (value === null) return "no frame yet";
  return value < 60 ? `last frame ${value.toFixed(1)} s ago` : `last frame ${Math.round(value / 60)} min ago`;
}

function yes(value: boolean | null, word: string, not: string): string {
  return value === null ? "not checked yet" : value ? word : not;
}

export function healthCards(h: HealthView): Card[] {
  const feed = h.feed;
  const feedLevel: Level = feed.connected === null ? "unknown" : feed.connected ? "ok" : "problem";
  const ledgerLevel: Level =
    h.ledger.verified === false ? "problem" : h.ledger.reconciled_exact === false ? "watch" : h.ledger.verified === null ? "unknown" : "ok";
  const backup = h.backup;
  const backupLevel: Level = !backup.configured
    ? "watch"
    : backup.last_error !== null
      ? "problem"
      : backup.hours_since === null
        ? "unknown"
        : backup.hours_since > 26
          ? "problem"
          : "ok";
  const storageLevel: Level = h.storage_state === null ? "unknown" : h.storage_state === "ok" ? "ok" : "watch";
  const clockLevel: Level = h.clock_status === null || h.clock_status === "unmeasured" ? "unknown" : h.clock_status === "ok" ? "ok" : "watch";
  const plugins = Object.entries(h.plugins);
  const paused = plugins.filter(([, state]) => state !== "running").length;
  // setup mode (M1.9a): no usable key, so no feed at all; the card says why and what to do
  const feedCard =
    h.setup_reason !== null
      ? card("Kalshi feed", "problem", "Setup mode", [h.setup_reason, "set the key in Windows, then restart the engine (Settings)"], "SETUP")
      : card("Kalshi feed", feedLevel, feed.connected ? "Live" : feed.connected === null ? "Starting" : "Disconnected", [
          seconds(feed.seconds_since_last_frame),
          feed.trading_active === null ? "exchange state not read yet" : feed.trading_active ? "trading is open" : "trading is closed",
          `${feed.open_gaps ?? "?"} open gaps`,
        ], feedLevel === "ok" ? "LIVE" : STATE[feedLevel]);
  return [
    feedCard,
    card("Order books", feed.books_ready === null ? "unknown" : "ok", `${feed.books_ready ?? "?"} ready`, [
      `${feed.watched_markets ?? "?"} markets watched`,
    ]),
    card("Clock", clockLevel, h.clock_offset_ms === null ? "Not measured" : `${h.clock_offset_ms >= 0 ? "+" : ""}${h.clock_offset_ms.toFixed(0)} ms vs Kalshi`, [
      `status: ${h.clock_status ?? "not measured"}`,
    ]),
    card("Ledger", ledgerLevel, h.ledger.reconciled_exact ? "Reconciled exactly" : h.ledger.reconciled_exact === false ? "Not reconciled" : "Not reconciled yet", [
      `chain ${yes(h.ledger.verified, "verified", "broken: nothing is appended")}`,
      `${h.ledger.records ?? "?"} records · ${h.ledger.errors ?? "?"} import errors`,
    ]),
    {
      ...card(
        "Backups",
        backupLevel,
        !backup.configured ? "No share set" : backup.hours_since === null ? "None yet" : `Last copy ${backup.hours_since} h ago`,
        [
          backup.last_error === null ? "no errors" : `last error: ${backup.last_error}`,
          // the engine says when (merge review 10): 0 means at its next hourly check
          ...(backup.configured && backup.next_in_h != null ? [`next automatic copy in ${backup.next_in_h} h`] : []),
        ],
        backup.running ? "BACKING UP" : STATE[backupLevel],
      ),
      action: "backup",
    },
    card("Storage", storageLevel, h.storage_state === null ? "Not checked" : h.storage_state === "ok" ? "Within budget" : "Near its budget", [
      `state: ${h.storage_state ?? "not checked"}`,
    ]),
    card("Plugins", paused ? "watch" : "ok", `${plugins.length - paused} running · ${paused} paused`, plugins.length
      ? plugins.map(([name, state]) => `${name}: ${state}`)
      : ["none installed yet (the first arrives in M1.12)"]),
    card("Terminal", h.port_fallback ? "problem" : "ok", `${h.api_streams ?? 0} open streams`, [
      h.port_fallback ? "the terminal's port was taken: reopen with ikars open" : "on its fixed port",
    ]),
    card("GPU", h.gpu ? "ok" : "unknown", h.gpu ?? "None found", ["used from M2 (embeddings, a local model)"]),
  ];
}

export interface StripItem {
  label: string;
  level: Level;
}

/** The footer strip and the header pill's words, coloured by the same rules as the Health cards
 * (one place decides a level: M1.8a-web-1b merge review 6). */
export function strip(h: HealthView | null, connection: StreamState): StripItem[] {
  if (connection === "signed-out") return [{ label: "Signed out: run ikars open", level: "problem" }];
  if (h === null) return [{ label: connection === "open" ? "Engine connected" : "Connecting to the engine", level: "unknown" }];
  const levels = new Map(healthCards(h).map((c) => [c.title, c.level]));
  const level = (title: string): Level => levels.get(title) ?? "unknown";
  const since = h.feed.seconds_since_last_frame;
  const feed: StripItem =
    h.setup_reason !== null
      ? { label: "Setup mode: no Kalshi key", level: "problem" }
      : h.feed.connected === null
      ? { label: "Feed starting", level: "unknown" }
      : h.feed.connected
        ? { label: `Feed live · ${since === null ? "–" : `${since.toFixed(1)} s`}`, level: level("Kalshi feed") }
        : { label: "Feed down", level: level("Kalshi feed") };
  return [
    connection === "open" ? feed : { label: "Engine reconnecting", level: "watch" },
    { label: `Books ${h.feed.books_ready ?? "?"} ready`, level: level("Order books") },
    { label: h.clock_offset_ms === null ? "Clock not measured" : `Clock ${h.clock_offset_ms >= 0 ? "+" : ""}${h.clock_offset_ms.toFixed(0)} ms`, level: level("Clock") },
    { label: h.ledger.reconciled_exact ? "Ledger reconciled" : "Ledger not reconciled", level: level("Ledger") },
    { label: !h.backup.configured ? "Backups off" : h.backup.last_error ? "Backup failing" : `Backup ${h.backup.hours_since ?? "–"} h ago`, level: level("Backups") },
    { label: `Storage ${h.storage_state ?? "–"}`, level: level("Storage") },
    { label: `GPU: ${h.gpu ?? "none"}`, level: level("GPU") },
  ];
}

export interface GapRow {
  when: string;
  what: string;
  length: string;
  recovered: string;
}

/** A duration for people: ``3.8 s``, ``41 min``, ``2 h 5 min``. */
export function duration(ms: number): string {
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} s`;
  const minutes = Math.round(ms / 60_000);
  return minutes < 60 ? `${minutes} min` : `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
}

/** A UTC time in the workstation's zone, ``Oct 27 03:12``. */
export function local(utc: string, zone?: string): string {
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    ...(zone ? { timeZone: zone } : {}),
  }).format(new Date(utc));
}

export function gapRow(gap: GapView, zone?: string): GapRow {
  const length = Date.parse(gap.end_utc) - Date.parse(gap.start_utc);
  return {
    when: local(gap.start_utc, zone),
    what: `${gap.channel}: ${gap.reason}`,
    length: duration(Math.max(0, length)),
    recovered: `${gap.status}${gap.detail ? ` · ${gap.detail}` : ""}`,
  };
}

/** The update-to-screen card (PLAN D20 J: p99 at most 33 ms while 100 markets stream at 10 Hz). */
export const TARGET_MS = 33;

export function meterCard(r: Reading): Card {
  if (r.samples === 0 || r.p50 === null || r.p99 === null) {
    return card("Update to screen", "unknown", "No updates yet", ["open a screen with live markets to measure", `target p99 ≤ ${TARGET_MS} ms`]);
  }
  const level: Level = r.p99 <= TARGET_MS ? "ok" : "watch";
  const work = r.workP99 === null ? "–" : `${r.workP99.toFixed(1)} ms`;
  const engine = r.engineP50 === null || r.engineP99 === null ? "–" : `p50 ${r.engineP50.toFixed(0)} ms · p99 ${r.engineP99.toFixed(0)} ms`;
  return card("Update to screen", level, `p50 ${r.p50.toFixed(0)} ms · p99 ${r.p99.toFixed(0)} ms`, [
    `target p99 ≤ ${TARGET_MS} ms, from applying a change to the frame after it shows`,
    `the page's own work p99 ${work}`,
    `from the engine receiving it: ${engine} (with the 10 Hz batching on both sides)`,
    `over the last ${r.samples} screen updates`,
  ]);
}
