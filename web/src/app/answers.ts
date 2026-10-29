// The owner's answer to a recommendation and Copy order, the same on the Overview's highlighted row and
// on the Recommendation screen (M1.12d). Recommend-only (D6): the copied order is placed on Kalshi by hand.

import type { RecommendationView } from "../api/contracts";
import { ApiFailure, change } from "../api/client";
import { orderText, type Answer } from "../view/recommendations";

export interface Answered {
  value: RecommendationView | null; // the engine's copy after the change; null when it was not saved
  note: string;
}

/** Saves ``answer`` for ``r``; the same answer again takes it back. */
export async function answer(r: RecommendationView, answer: Answer | null): Promise<Answered> {
  const next = r.response === answer ? null : answer;
  try {
    const value = await change(`/v1/recommendations/${encodeURIComponent(r.id)}/response`, "RecommendationView", { answer: next });
    return { value, note: next === null ? "Answer taken back." : `Marked ${next}.` };
  } catch (error) {
    return { value: null, note: error instanceof ApiFailure ? error.message : "Not saved." };
  }
}

/** Puts ``r``'s order on the clipboard; the note says what was copied, or that it was refused. */
export async function copyOrder(r: RecommendationView, clipboard: Pick<Clipboard, "writeText"> = window.navigator.clipboard): Promise<string> {
  const text = orderText(r);
  try {
    await clipboard.writeText(text);
    return `Copied: ${text}`;
  } catch {
    return "The clipboard refused it.";
  }
}
