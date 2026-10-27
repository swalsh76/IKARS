// The golden test (PLAN D3 as amended): every example the engine exports passes its contract here,
// and an unknown field fails it, so neither side can drift without a test failing.

import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { ApiFailure, checked } from "./client";
import { STREAM_EVENTS, type ContractName } from "./contracts";
import { validators } from "./validate";

const folder = join(__dirname, "..", "..", "..", "contracts", "examples");
const examples = readdirSync(folder)
  .filter((name) => name.endsWith(".json"))
  .map((name) => [name.replace(/\.json$/, "") as ContractName, JSON.parse(readFileSync(join(folder, name), "utf8")) as unknown] as const);

describe("the golden examples", () => {
  it("cover every contract", () => {
    expect(examples.map(([name]) => name).sort()).toEqual(Object.keys(validators).sort());
  });

  it.each(examples)("%s passes its contract", (name, value) => {
    expect(checked(name, value)).toBe(value);
  });

  it.each(examples.filter(([, v]) => typeof v === "object" && v !== null && !Array.isArray(v)))(
    "%s with an unknown field is refused",
    (name, value) => {
      expect(() => checked(name, { ...(value as object), made_up_field: 1 })).toThrow(ApiFailure);
    },
  );

  it("refuses a wrong type and says where", () => {
    const quote = examples.find(([n]) => n === "QuoteUpdate")?.[1] as Record<string, unknown>;
    expect(() => checked("QuoteUpdate", { ...quote, yes_bid_e6: "41" })).toThrow(/QuoteUpdate .*\/yes_bid_e6/);
  });

  it("names a contract for every stream event", () => {
    for (const kind of Object.values(STREAM_EVENTS)) expect(validators[kind]).toBeTypeOf("function");
  });
});
