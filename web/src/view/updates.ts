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
