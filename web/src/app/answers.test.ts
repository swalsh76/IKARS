import { afterEach, describe, expect, it, vi } from "vitest";
import type { RecommendationView } from "../api/contracts";
import { ApiFailure, change } from "../api/client";
import { answer, copyOrder, newerThanAnswer } from "./answers";

vi.mock("../api/client", async (original) => ({ ...(await original<typeof import("../api/client")>()), change: vi.fn() }));
const changed = vi.mocked(change);
afterEach(() => changed.mockReset());

// only the fields an answer and Copy order read
const rec = (response: RecommendationView["response"] = null) =>
  ({ id: "momentum-KXA 1", ticker: "KXA", side: "yes", count_e2: 1_000, entry_price_e6: 460_000, response }) as RecommendationView;

describe("an answer", () => {
  it("saves it, and the same answer again takes it back", async () => {
    changed.mockResolvedValueOnce(rec("taken"));
    expect(await answer(rec(), "taken")).toEqual({ value: rec("taken"), note: "Marked taken." });
    expect(changed).toHaveBeenLastCalledWith("/v1/recommendations/momentum-KXA%201/response", "RecommendationView", { answer: "taken" });
    changed.mockResolvedValueOnce(rec());
    expect((await answer(rec("taken"), "taken")).note).toBe("Answer taken back.");
    expect(changed).toHaveBeenLastCalledWith(expect.any(String), "RecommendationView", { answer: null });
  });

  it("says why it was not saved", async () => {
    changed.mockRejectedValueOnce(new ApiFailure("refused", "unknown recommendation", 404));
    expect(await answer(rec(), "dismissed")).toEqual({ value: null, note: "unknown recommendation" });
    changed.mockRejectedValueOnce(new Error("boom"));
    expect((await answer(rec(), "ignored")).note).toBe("Not saved.");
  });
});

describe("Copy order", () => {
  it("puts the order on the clipboard, or says it was refused", async () => {
    const clipboard = { writeText: vi.fn().mockResolvedValue(undefined) };
    expect(await copyOrder(rec(), clipboard)).toBe("Copied: KXA YES 10 @ 46¢");
    expect(clipboard.writeText).toHaveBeenCalledWith("KXA YES 10 @ 46¢");
    expect(await copyOrder(rec(), { writeText: vi.fn().mockRejectedValue(new Error("denied")) })).toBe("The clipboard refused it.");
  });
});

describe("a refresh after an answer", () => {
  it("replaces it only when it was asked for after the answer was saved (M1.R3 D9)", () => {
    expect(newerThanAnswer(1_000, 2_000)).toBe(false); // in flight before: the answer stays
    expect(newerThanAnswer(2_000, 2_000)).toBe(true);
    expect(newerThanAnswer(3_000, 2_000)).toBe(true);
    expect(newerThanAnswer(undefined, 2_000)).toBe(false);
  });
});
