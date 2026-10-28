// What Health and Settings say about the program's version and updates (M1.9b): plain functions,
// tested without a browser.

import type { UpdateView } from "../api/contracts";

/** The version and what is new, in words. */
export function updateLine(v: UpdateView): string {
  if (!v.installed) return "a development copy: no updates (installed IKARS updates itself)";
  const version = `version ${v.current ?? "?"}`;
  switch (v.state) {
    case "ready":
      return `${version} · ${v.available ?? "a newer version"} is downloaded and ready`;
    case "checking":
      return `${version} · checking for updates…`;
    case "failed":
      return `${version} · the last check failed: ${v.problem ?? "no reason given"}`;
    default:
      return `${version} · up to date${v.checked_at_utc ? ` (checked ${v.checked_at_utc.slice(0, 16).replace("T", " ")} UTC)` : ""}`;
  }
}

/** Whether Restart to update can be offered. */
export function canApply(v: UpdateView): boolean {
  return v.installed && v.state === "ready" && v.available !== null;
}
