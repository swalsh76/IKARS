// What Health and Settings say about the program's version and updates (M1.9b): plain functions,
// tested without a browser.

import type { UpdateView } from "../api/contracts";

/** The version (the first line) and what is new (the second), in words. */
export function updateParts(v: UpdateView): { version: string; status: string } {
  if (!v.installed) return { version: "Development copy", status: "No updates: installed IKARS updates itself." };
  const version = `Version ${v.current ?? "?"}`;
  const checked = v.checked_at_utc ? ` · checked ${v.checked_at_utc.slice(0, 16).replace("T", " ")} UTC` : "";
  switch (v.state) {
    case "ready":
      return { version, status: `${v.available ?? "A newer version"} is downloaded and ready to install.` };
    case "checking":
      return { version, status: "Checking for updates…" };
    case "downloading":
      return { version, status: `Downloading ${v.available ?? "a newer version"}: ${downloadText(v)}` };
    case "failed":
      return { version, status: `The last check failed: ${v.problem ?? "no reason given"}${checked}` };
    default:
      return { version, status: `Up to date${checked}` };
  }
}

/** Whether Restart to update can be offered. */
export function canApply(v: UpdateView): boolean {
  return v.installed && v.state === "ready" && v.available !== null;
}

/** Whether the engine now runs ``target`` (the window reloads into it). */
export function updatedTo(v: UpdateView, target: string): boolean {
  return v.installed && v.current === target;
}

/** The Update window's phases, in order (each one a step on screen). */
export const PHASES = ["download", "stop", "install", "start", "reload"] as const;
export type Phase = (typeof PHASES)[number] | "done" | "failed" | "cannot";

export interface Step {
  label: string;
  mark: "done" | "now" | "next";
}

const LABELS: Record<(typeof PHASES)[number], string> = {
  download: "Download",
  stop: "Engine stops",
  install: "Install",
  start: "Engine starts",
  reload: "Terminal reloads",
};

/** The steps, each done, under way or still to come. */
export function steps(phase: Phase, failedAt: (typeof PHASES)[number] | null = null): Step[] {
  const at = phase === "done" ? PHASES.length : phase === "failed" || phase === "cannot" ? PHASES.indexOf(failedAt ?? "download") : PHASES.indexOf(phase);
  return PHASES.map((p, i) => ({ label: LABELS[p], mark: i < at ? "done" : i === at ? "now" : "next" }));
}

/** The download in words: "41% of 64.5 MB". */
export function downloadText(v: UpdateView): string {
  const size = v.size_bytes === null ? "" : ` of ${(v.size_bytes / 1e6).toFixed(1)} MB`;
  return v.progress === null ? `starting${size}` : `${v.progress}%${size}`;
}

/** Seconds as m:ss. */
export function clock(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}
